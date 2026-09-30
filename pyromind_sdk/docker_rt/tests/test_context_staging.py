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
    "DOCKER_RT_BUILD_STAGING_SWEEP",
    "DOCKER_RT_BUILD_STAGING_SWEEP_MAX_AGE_S",
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


# --------------------------------------------------------------------------
# build ids: what the sweeper relies on
# --------------------------------------------------------------------------


def test_build_ids_stay_unique_even_inside_one_frozen_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The counter, not the clock, is what keeps concurrent builds apart.

    One daemon process serves every concurrent build, so ``time`` and ``pid``
    are identical across them. Freezing the clock makes that explicit: if the
    ids ever collide here, they can collide in production.
    """
    from ..backend import context_staging as cs

    # Replace the module reference, not the global ``time`` module, so nothing
    # else in the process sees a frozen clock.
    frozen = SimpleNamespace(
        strftime=lambda *_a, **_k: "20260923-023343",
        gmtime=cs.time.gmtime,
        time=cs.time.time,
    )
    monkeypatch.setattr(cs, "time", frozen)
    ids = [cs.new_build_id() for _ in range(200)]

    assert len(set(ids)) == 200
    assert all(cs.parse_build_id_pid(value) == os.getpid() for value in ids)


def test_build_id_names_the_pid_that_minted_it() -> None:
    from ..backend import context_staging as cs

    assert cs.parse_build_id_pid(cs.new_build_id()) == os.getpid()


def test_build_id_without_the_counter_field_still_parses() -> None:
    """Ids minted before the counter existed are already out in the wild."""
    from ..backend import context_staging as cs

    assert cs.parse_build_id_pid("20260923-023343-80566-cd92f9") == 80566
    assert cs.parse_build_id_pid("20260923-023343-80566-0-cd92f9") == 80566


def test_unattributable_build_ids_parse_to_none() -> None:
    from ..backend import context_staging as cs

    for value in (
        "",
        "garbage",
        "20260923-023343-notapid-ab",  # pid is not digits
        "20260923-023343-80566",  # no salt
        "1234-123456-1-a",  # stamp is not 8+6 digits
        "20260923-023343-80566-0-cd92f9-extra",  # trailing field
    ):
        assert cs.parse_build_id_pid(value) is None


def test_pid_alive_sees_this_process() -> None:
    from ..backend import context_staging as cs

    assert cs._pid_alive(os.getpid()) is True
    assert cs._pid_alive(0) is False
    assert cs._pid_alive(None) is False


# --------------------------------------------------------------------------
# sweeping leftovers from a killed daemon
# --------------------------------------------------------------------------


class _FakeListingStorage:
    """Just enough ``StorageClient`` for the sweeper: list + delete."""

    def __init__(self, entries: list[dict[str, Any]], *, list_error: Exception | None = None) -> None:
        self.entries = entries
        self.list_error = list_error
        self.raise_on_delete: Exception | None = None
        self.deleted: list[tuple[str, str]] = []
        self.listed: tuple[Any, ...] | None = None

    def list_files(
        self, folder: str, bucket_name: str | None = None, recursive: bool = True
    ) -> list[dict[str, Any]]:
        if self.list_error is not None:
            raise self.list_error
        self.listed = (folder, bucket_name, recursive)
        return list(self.entries)

    def delete_folder(
        self, folder: str, bucket_name: str | None = None
    ) -> dict[str, Any]:
        if self.raise_on_delete is not None:
            raise self.raise_on_delete
        self.deleted.append((bucket_name or "", folder))
        return {"deleted": 1}


def _dir(name: str, *, age_s: float = 0.0, now: float = 1_800_000_000.0) -> dict[str, Any]:
    """A listing entry for a directory, ``age_s`` seconds old."""
    from datetime import datetime, timezone

    stamp = datetime.fromtimestamp(now - age_s, tz=timezone.utc).isoformat()
    return {"object_name": name, "type": "folder", "size": 0, "last_modified": stamp}


_NOW = 1_800_000_000.0


def _sweep(storage: _FakeListingStorage, **kwargs: Any) -> list[str]:
    from ..backend import context_staging as cs

    return cs.sweep_stale_staging(
        storage=storage, bucket="1000001514", now=_NOW, **kwargs
    )


def test_sweep_removes_what_the_dead_daemon_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: a ``kill -9`` skips the daemon's ``finally``."""
    from ..backend import context_staging as cs

    stale = "20260923-023343-80566-0-cd92f9"
    storage = _FakeListingStorage([_dir(f".docker-rt-build/{stale}/")])
    monkeypatch.setattr(cs, "_pid_alive", lambda _pid: False)

    assert _sweep(storage, dead_pid=80566) == [stale]
    assert storage.deleted == [("1000001514", f".docker-rt-build/{stale}/")]
    # Non-recursive: only the build directories themselves are candidates.
    assert storage.listed == (".docker-rt-build", "1000001514", False)


def test_sweep_keeps_a_directory_whose_daemon_is_still_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second docker-rt daemon on the same workspace is not ours to delete."""
    from ..backend import context_staging as cs

    other = "20260923-023343-99999-0-cd92f9"
    storage = _FakeListingStorage([_dir(f".docker-rt-build/{other}/")])
    monkeypatch.setattr(cs, "_pid_alive", lambda _pid: True)

    assert _sweep(storage, dead_pid=80566) == []
    assert storage.deleted == []


def test_sweep_removes_an_older_crash_without_being_told_the_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead pid is proof enough, even if this watcher never watched it."""
    from ..backend import context_staging as cs

    old = "20260101-000000-4242-0-abcdef"
    storage = _FakeListingStorage([_dir(f".docker-rt-build/{old}/")])
    monkeypatch.setattr(cs, "_pid_alive", lambda _pid: False)

    assert _sweep(storage) == [old]


def test_sweep_trusts_the_watched_pid_over_a_recycled_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pid may be reused between the death and this sweep.

    The caller watched that exact pid exit, so its directories are gone by
    definition — even if ``os.kill`` now says the number is in use again.
    """
    from ..backend import context_staging as cs

    mine = "20260923-023343-80566-0-cd92f9"
    storage = _FakeListingStorage([_dir(f".docker-rt-build/{mine}/")])
    monkeypatch.setattr(cs, "_pid_alive", lambda _pid: True)

    assert _sweep(storage, dead_pid=80566) == [mine]


def test_sweep_waits_for_an_unattributable_directory_to_age_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Naming is the only thing this can reason about; without it, be patient."""
    from ..backend import context_staging as cs

    fresh = _FakeListingStorage([_dir(".docker-rt-build/not-ours/", age_s=60)])
    ancient = _FakeListingStorage([_dir(".docker-rt-build/not-ours/", age_s=48 * 3600)])
    monkeypatch.setattr(cs, "_pid_alive", lambda _pid: False)

    assert _sweep(fresh, max_age_s=3600) == []
    assert fresh.deleted == []
    assert _sweep(ancient, max_age_s=3600) == ["not-ours"]


def test_sweep_leaves_things_alone_when_it_cannot_tell_their_age(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ..backend import context_staging as cs

    entry = _dir(".docker-rt-build/not-ours/")
    entry["last_modified"] = None
    storage = _FakeListingStorage([entry])
    monkeypatch.setattr(cs, "_pid_alive", lambda _pid: False)

    assert _sweep(storage, max_age_s=0) == []


def test_sweep_ignores_the_prefix_entry_and_stray_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only directories under the prefix are ours; never guess about the rest."""
    from ..backend import context_staging as cs

    stray = {"object_name": ".docker-rt-build/stray.txt", "type": "file", "size": 3,
             "last_modified": "2026-01-01T00:00:00+00:00"}
    storage = _FakeListingStorage([_dir(".docker-rt-build/"), stray])
    monkeypatch.setattr(cs, "_pid_alive", lambda _pid: False)

    assert _sweep(storage, max_age_s=0) == []
    assert storage.deleted == []


def test_sweep_is_disabled_by_the_knob(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disabled means "do not even talk to storage".

    Asserting on a raised error would not prove that: the sweep swallows every
    listing failure, so an exception would look exactly like a clean run.
    """
    from ..backend import context_staging as cs

    monkeypatch.setenv("DOCKER_RT_BUILD_STAGING_SWEEP", "false")

    assert cs.sweep_enabled() is False
    assert _sweep(_FakeListingStorage([_dir(".docker-rt-build/whatever/")])) == []
    assert cs.sweep_max_age_s() > 0  # the knob is only about the sweep itself


def test_sweep_does_not_list_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import context_staging as cs

    monkeypatch.setenv("DOCKER_RT_BUILD_STAGING_SWEEP", "off")
    storage = _FakeListingStorage([_dir(".docker-rt-build/whatever/")])

    cs.sweep_stale_staging(
        storage=storage, bucket="1000001514", now=_NOW, dead_pid=80566
    )

    assert storage.listed is None
    assert storage.deleted == []


def test_sweep_never_raises_when_listing_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Storage unreachable is a warning, not a crash — the watcher must finish."""
    storage = _FakeListingStorage([], list_error=OSError("AccessDenied"))

    assert _sweep(storage) == []


def test_sweep_keeps_going_when_one_delete_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ..backend import context_staging as cs

    a = "20260923-023343-1-0-aaaaaa"
    b = "20260923-023343-2-0-bbbbbb"
    storage = _FakeListingStorage(
        [_dir(f".docker-rt-build/{a}/"), _dir(f".docker-rt-build/{b}/")]
    )
    storage.raise_on_delete = OSError("quota locked")
    monkeypatch.setattr(cs, "_pid_alive", lambda _pid: False)

    assert _sweep(storage) == []


def test_sweep_survives_a_storage_client_that_cannot_be_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No credentials (or no minio at all) must not break the watcher."""
    from ..backend import context_staging as cs

    def boom(_cluster: Any = None) -> Any:
        raise cs.StagingError("cannot resolve storage credentials: no API key")

    monkeypatch.setattr(cs, "connect_storage", boom)

    assert cs.sweep_stale_staging(dead_pid=1) == []


def test_sweep_max_age_defaults_and_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import context_staging as cs

    assert cs.sweep_max_age_s() == cs.DEFAULT_SWEEP_MAX_AGE_S
    monkeypatch.setenv("DOCKER_RT_BUILD_STAGING_SWEEP_MAX_AGE_S", "-5")
    assert cs.sweep_max_age_s() == 0
    monkeypatch.setenv("DOCKER_RT_BUILD_STAGING_SWEEP_MAX_AGE_S", "not-a-number")
    assert cs.sweep_max_age_s() == cs.DEFAULT_SWEEP_MAX_AGE_S


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
