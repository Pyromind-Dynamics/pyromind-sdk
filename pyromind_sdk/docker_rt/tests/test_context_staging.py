"""Unit tests for the workspace-storage build-context staging.

The mapping these tests encode was established by probing a real cluster and is
what the whole optimisation rests on::

    storage object key "<rel>"  ==  /workspace/<rel>  in a Pod
    VolumeMount("/workspace/<rel>")  ->  JuiceFS subPath "<uid>/<rel>"

Notable, and enforced below: ``host_path`` must be absolute (the sandbox API
rejects relative paths with "path must be an absolute path starting with /") and
``/workspace`` is the outermost mount that exists (``/`` is rejected with "path
cannot be empty").
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest

_STAGING_VARS = (
    "DOCKER_RT_BUILD_CONTEXT_MODE",
    "DOCKER_RT_BUILD_STAGING_MOUNT",
    "DOCKER_RT_BUILD_STAGING_PREFIX",
    "DOCKER_RT_BUILD_STAGING_WORKSPACE",
    "DOCKER_RT_BUILD_STAGING_PARALLEL",
    "DOCKER_RT_STORAGE_CLUSTER",
    "DOCKER_RT_CLUSTER",
    "PYROMIND_CLUSTER",
    "PYROMIND_STORAGE_BUCKET",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _STAGING_VARS:
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------
# knobs
# --------------------------------------------------------------------------


def test_mode_defaults_to_auto_and_ignores_nonsense() -> None:
    from ..backend import context_staging as cs

    assert cs.staging_mode() == "auto"
    assert cs.staging_enabled() is True
    assert cs.staging_required() is False
    os.environ["DOCKER_RT_BUILD_CONTEXT_MODE"] = "upload"
    try:
        assert cs.staging_enabled() is False
    finally:
        del os.environ["DOCKER_RT_BUILD_CONTEXT_MODE"]


def test_unknown_mode_falls_back_to_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import context_staging as cs

    monkeypatch.setenv("DOCKER_RT_BUILD_CONTEXT_MODE", "turbo")
    assert cs.staging_mode() == "auto"


def test_storage_mode_makes_staging_mandatory(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import context_staging as cs

    monkeypatch.setenv("DOCKER_RT_BUILD_CONTEXT_MODE", "storage")
    assert cs.staging_required() is True
    # ...and upload mode is the pre-staging behaviour, so it must not even try.
    monkeypatch.setenv("DOCKER_RT_BUILD_CONTEXT_MODE", "upload")
    assert cs.staging_enabled() is False


def test_defaults_land_under_kaniko_and_on_the_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mount target must stay under ``/kaniko``.

    kaniko deletes the container rootfs when a multi-stage build moves to the
    next stage and preserves only ``/kaniko``; a mount elsewhere could be wiped
    mid-build — and the mount source is the user's workspace, so a wipe there
    would delete their files.
    """
    from ..backend import context_staging as cs

    assert cs.staging_mount_path().startswith("/kaniko/")
    assert cs.staging_workspace() == "/workspace"
    assert cs.staging_prefix() == ".docker-rt-build"


def test_prefix_and_workspace_are_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import context_staging as cs

    monkeypatch.setenv("DOCKER_RT_BUILD_STAGING_PREFIX", "/.rt//build/")
    monkeypatch.setenv("DOCKER_RT_BUILD_STAGING_WORKSPACE", "workspace/")
    assert cs.staging_prefix() == ".rt//build".strip("/")
    assert cs.staging_workspace() == "/workspace"


def test_cluster_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import context_staging as cs

    assert cs.storage_cluster() is None
    monkeypatch.setenv("PYROMIND_CLUSTER", "us-west-1#pre")
    assert cs.storage_cluster() == "us-west-1#pre"
    monkeypatch.setenv("DOCKER_RT_CLUSTER", "cn-east-1")
    assert cs.storage_cluster() == "cn-east-1"
    monkeypatch.setenv("DOCKER_RT_STORAGE_CLUSTER", "us-west-2")
    assert cs.storage_cluster() == "us-west-2"


def test_parallel_uploads_defaults_to_the_measured_knee(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4 connections left the uplink half idle; 8 measured ~2.6x faster."""
    from ..backend import context_staging as cs

    assert cs.parallel_uploads() == cs.DEFAULT_PARALLEL_UPLOADS == 8
    monkeypatch.setenv("DOCKER_RT_BUILD_STAGING_PARALLEL", "12")
    assert cs.parallel_uploads() == 12
    # Nonsense must not break a build, and extremes must stay sane.
    monkeypatch.setenv("DOCKER_RT_BUILD_STAGING_PARALLEL", "many")
    assert cs.parallel_uploads() == cs.DEFAULT_PARALLEL_UPLOADS
    monkeypatch.setenv("DOCKER_RT_BUILD_STAGING_PARALLEL", "0")
    assert cs.parallel_uploads() == 1
    monkeypatch.setenv("DOCKER_RT_BUILD_STAGING_PARALLEL", "9999")
    assert cs.parallel_uploads() == 32


# --------------------------------------------------------------------------
# the plan
# --------------------------------------------------------------------------


def test_plan_paths_agree_across_the_mount_boundary() -> None:
    """Every path the plan hands out must describe the *same* file."""
    from ..backend import context_staging as cs

    plan = cs.plan_staging(archive="context.tar.gz")

    # What the user's workspace shows.
    assert plan.rel_dir == f".docker-rt-build/{plan.build_id}"
    assert plan.object_dir == plan.rel_dir
    # What storage receives: bucket root == /workspace, so the key is the
    # workspace-relative path — no uid prefix (the subPath already carries it).
    assert plan.object_key == f".docker-rt-build/{plan.build_id}/context.tar.gz"
    assert not plan.object_key.startswith("/")

    # What the sandbox mounts (absolute: the API rejects relative paths).
    assert plan.host_path == "/workspace/.docker-rt-build"
    assert plan.host_path.startswith("/")
    assert plan.mount_path == "/kaniko/docker-rt-stage"

    # ...and where the sandbox finds the archive: mount root == the prefix, so
    # only the build id is left below it.
    assert plan.staged_path == (
        f"/kaniko/docker-rt-stage/{plan.build_id}/context.tar.gz"
    )
    assert plan.staged_dir == f"/kaniko/docker-rt-stage/{plan.build_id}"


def test_build_ids_are_unique_and_path_safe() -> None:
    from ..backend import context_staging as cs

    ids = {cs.new_build_id() for _ in range(50)}
    assert len(ids) == 50
    for value in ids:
        assert value
        assert "/" not in value
        assert cs._NAME_SAFE.sub("-", value) == value


def test_mount_spec_is_writable() -> None:
    """The sandbox deletes its own staged directory, so read-only is not an option."""
    from ..backend import context_staging as cs

    plan = cs.plan_staging(archive="context.tar.gz")
    spec = cs.mount_spec(plan)
    assert spec == {
        "Source": "/workspace/.docker-rt-build",
        "Target": "/kaniko/docker-rt-stage",
        "ReadOnly": False,
    }


# --------------------------------------------------------------------------
# the stage-in script
# --------------------------------------------------------------------------


def test_stage_in_script_copies_verifies_then_deletes() -> None:
    from ..backend import context_staging as cs

    plan = cs.plan_staging(archive="context.tar.gz")
    script = cs.stage_in_script(plan, workdir="/kaniko/docker-rt-build", expected_size=4096)

    assert "set -e" in script
    assert f"mkdir -p /kaniko/docker-rt-build" in script
    assert f"cp -f {plan.staged_path} /kaniko/docker-rt-build/context.tar.gz" in script
    # The size check is what makes a truncated copy fail loudly instead of
    # surfacing later as an unrelated kaniko error.
    assert 'if [ "$n" != "4096" ]' in script
    assert "exit 9" in script
    # Stage-1 cleanup: the data is gone as soon as it has been consumed.
    assert f"rm -rf {plan.staged_dir}" in script
    # The copy must happen before the delete.
    assert script.index("cp -f") < script.index("rm -rf")
    # ...and the delete comes after the size gate.
    assert script.index('if [ "$n" !=') < script.index("rm -rf")


def test_stage_in_script_quotes_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mount path with a space must not turn into two shell words.

    The mount target is an operator knob, and the script is handed to ``sh -c``
    as a single string — unquoted interpolation there silently splits the path.
    """
    from ..backend import context_staging as cs

    monkeypatch.setenv("DOCKER_RT_BUILD_STAGING_MOUNT", "/kaniko/rt stage")
    plan = cs.plan_staging(archive="context.tar.gz")
    script = cs.stage_in_script(plan, workdir="/kaniko/wd", expected_size=1)
    assert f"cp -f '{plan.staged_path}'" in script
    assert f"rm -rf '{plan.staged_dir}'" in script


def test_parse_staged_size_reads_the_marker() -> None:
    from ..backend import context_staging as cs

    assert cs.parse_staged_size(f"noise\n{cs.SIZE_MARKER}1234\n") == 1234
    assert cs.parse_staged_size(f"{cs.SIZE_MARKER}7") == 7
    assert cs.parse_staged_size("") is None
    assert cs.parse_staged_size("nothing here") is None
    assert cs.parse_staged_size(f"{cs.SIZE_MARKER}not-a-number") is None


# --------------------------------------------------------------------------
# bucket resolution
# --------------------------------------------------------------------------


class _FakeMinio:
    def __init__(self, buckets: list[str] | Exception) -> None:
        self._buckets = buckets

    def list_buckets(self) -> Any:
        if isinstance(self._buckets, Exception):
            raise self._buckets
        return [SimpleNamespace(name=name) for name in self._buckets]


def _storage(buckets: list[str] | Exception, *, current: str | None = None) -> Any:
    return SimpleNamespace(client=_FakeMinio(buckets), current_bucket=current)


def test_bucket_prefers_the_configured_default() -> None:
    from ..backend import context_staging as cs

    info = SimpleNamespace(uid="1000001514")
    assert cs.resolve_bucket(_storage(["other", "x"], current="mine"), info) == "mine"


def test_bucket_uses_the_single_visible_bucket() -> None:
    """Real deployments return exactly one bucket and deny listing its contents."""
    from ..backend import context_staging as cs

    info = SimpleNamespace(uid=None)
    assert cs.resolve_bucket(_storage(["1000001514"]), info) == "1000001514"


def test_bucket_falls_back_to_the_uid() -> None:
    from ..backend import context_staging as cs

    info = SimpleNamespace(uid="1000001514")
    assert cs.resolve_bucket(_storage(OSError("denied")), info) == "1000001514"


def test_bucket_picks_the_only_numeric_candidate() -> None:
    from ..backend import context_staging as cs

    info = SimpleNamespace(uid=None)
    assert cs.resolve_bucket(_storage(["shared", "assets", "1000001514"]), info) == (
        "1000001514"
    )


def test_bucket_refuses_to_guess() -> None:
    from ..backend import context_staging as cs

    with pytest.raises(cs.StagingError, match="PYROMIND_STORAGE_BUCKET"):
        cs.resolve_bucket(_storage(["a", "b", "c"]), SimpleNamespace(uid=None))
    with pytest.raises(cs.StagingError, match="no storage bucket"):
        cs.resolve_bucket(_storage(OSError("denied")), SimpleNamespace(uid=None))


# --------------------------------------------------------------------------
# the stager
# --------------------------------------------------------------------------


class _FakeStorageClient:
    def __init__(self, *, fail: Exception | None = None) -> None:
        self.uploads: list[dict[str, Any]] = []
        self.deleted: list[tuple[str, str]] = []
        self.raise_on_delete: Exception | None = None
        self.fail = fail

    def upload_file(self, file_obj: Any, object_name: str, **kwargs: Any) -> dict[str, Any]:
        if self.fail is not None:
            raise self.fail
        payload = file_obj.read()
        self.uploads.append({"object_name": object_name, "payload": payload, **kwargs})
        return {"object_name": object_name, "size": len(payload)}

    def delete_folder(self, folder: str, bucket_name: str | None = None) -> dict[str, Any]:
        if self.raise_on_delete is not None:
            raise self.raise_on_delete
        self.deleted.append((bucket_name or "", folder))
        return {"deleted": 1}


def _stager(
    monkeypatch: pytest.MonkeyPatch,
    storage: _FakeStorageClient,
    *,
    bucket: str = "1000001514",
) -> Any:
    """A stager wired to a fake storage client (no profile call, no network)."""
    from ..backend import context_staging as cs

    stager = cs.StorageStager(cs.plan_staging(archive="context.tar.gz"))
    monkeypatch.setattr(stager, "_storage", storage)
    monkeypatch.setattr(stager, "_bucket", bucket)
    return stager


def test_upload_sends_the_packed_bytes_under_the_planned_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ..backend import context_staging as cs

    storage = _FakeStorageClient()
    stager = _stager(monkeypatch, storage)

    stager.upload(b"packed-bytes")

    assert stager.uploaded is True
    (call,) = storage.uploads
    assert call["object_name"] == stager.plan.object_key
    assert call["payload"] == b"packed-bytes"
    assert call["bucket_name"] == "1000001514"
    assert call["content_type"] == "application/gzip"
    # Parallel multipart is the entire point of this route.
    assert call["num_parallel_uploads"] >= 2


def test_upload_failure_is_a_staging_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import context_staging as cs

    storage = _FakeStorageClient(fail=RuntimeError("network down"))
    stager = _stager(monkeypatch, storage)

    with pytest.raises(cs.StagingError, match="network down"):
        stager.upload(b"x")
    assert stager.uploaded is False


def test_cleanup_deletes_the_build_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import context_staging as cs

    storage = _FakeStorageClient()
    stager = _stager(monkeypatch, storage)
    stager.upload(b"x")

    assert stager.cleanup() is True
    assert storage.deleted == [("1000001514", f"{stager.plan.object_dir}/")]
    assert stager.uploaded is False
    # Idempotent: a second call is a no-op, not a second delete.
    assert stager.cleanup() is True
    assert len(storage.deleted) == 1


def test_cleanup_is_a_no_op_when_nothing_was_uploaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _FakeStorageClient()
    stager = _stager(monkeypatch, storage)
    assert stager.cleanup() is True
    assert storage.deleted == []


def test_cleanup_reports_failure_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It runs in a ``finally`` — raising there would lose the build result."""
    storage = _FakeStorageClient()
    storage.raise_on_delete = RuntimeError("denied")
    stager = _stager(monkeypatch, storage)
    stager.upload(b"x")

    assert stager.cleanup() is False
    assert stager.uploaded is True  # still needs cleaning up
