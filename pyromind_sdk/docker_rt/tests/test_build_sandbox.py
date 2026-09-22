"""Unit tests for the build-sandbox orchestration.

A ``_FakeBuildSandbox`` stands in for the k8s-middleware adapter. It deliberately
**refuses non-argv command shapes** — handing ``iter_exec_stream`` a bare string
makes ``list(cmd)`` explode it into one argv element per character and the
sandbox then tries to exec a program called ``s``. A permissive fake would hide
that bug entirely, which is exactly how it slipped through once before.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import io
import json
import re
import tarfile
from types import SimpleNamespace
from typing import Any

import pytest

from ..backend import context_staging

GOOD_DIGEST = "sha256:" + "a" * 64


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DOCKER_RT_BUILD_IMAGE",
        "DOCKER_RT_BUILD_EXECUTOR",
        "DOCKER_RT_BUILD_REGISTRY",
        "DOCKER_RT_REGISTRY_NAMESPACE",
        "DOCKER_RT_REGISTRY_CLUSTER",
        "DOCKER_RT_CLUSTER",
        "PYROMIND_CLUSTER",
        "DOCKER_RT_KUBE_CONTEXT",
        "DOCKER_RT_REGISTRY_USERNAME",
        "DOCKER_RT_REGISTRY_PASSWORD",
        "DOCKER_RT_REGISTRY_DOCKERCONFIG",
        "DOCKER_RT_BUILD_PUSH",
        "DOCKER_RT_BUILD_SANDBOX_CPU",
        "DOCKER_RT_BUILD_SANDBOX_MEMORY",
        "DOCKER_RT_BUILD_SANDBOX_KEEP",
        "DOCKER_RT_BUILD_TIMEOUT",
        "DOCKER_RT_BUILD_POLL_INTERVAL_S",
        "DOCKER_RT_BUILD_CONTEXT_WARN_MB",
        "DOCKER_RT_BUILD_CACHE",
        "DOCKER_RT_KANIKO_EXTRA_FLAGS",
        "DOCKER_RT_ACR_ACCESS_KEY_ID",
        "DOCKER_RT_ACR_ACCESS_KEY_SECRET",
        "DOCKER_RT_ACR_INSTANCE_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    # Staging is deliberately *not* left unset: the module default is ``auto``,
    # which resolves storage credentials and opens a connection. The autouse
    # fixture below pins these tests to the direct-upload path; tests that
    # describe staging opt back in through ``_install_staging``.
    monkeypatch.setenv("DOCKER_RT_BUILD_CONTEXT_MODE", "upload")


def _set_push_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Satisfy the push-credential precondition.

    ``builder_image()`` has a default and the credential check is a hard
    pre-sandbox error, so any test that wants to reach the sandbox at all must
    call this — otherwise it aborts on "No push credentials configured" before
    the code under test ever runs.
    """
    monkeypatch.setenv("DOCKER_RT_REGISTRY_USERNAME", "lvniqi")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_PASSWORD", "pat")


@pytest.fixture(autouse=True)
def _direct_upload_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the storage route out of every test unless it opts in.

    Workspace staging is the production default (``auto``) and it resolves
    credentials, then opens a connection. Pinning the mode keeps the real
    accessors in play — the staging tests flip it back with
    :func:`_install_staging`. This is deliberately an environment variable and
    not a patched accessor, so ``staging_enabled`` / ``staging_required`` are
    exercised as written. ``_clear()`` has to re-pin it for the same reason: it
    deletes environment variables from inside test bodies.
    """
    monkeypatch.setenv("DOCKER_RT_BUILD_CONTEXT_MODE", "upload")


def _hide_builder_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the "no builder image" branch.

    ``DOCKER_RT_BUILD_IMAGE`` now ships with a default, and ``_env`` treats an
    empty value as "unset", so the only way to exercise the guard is to patch
    the accessor.
    """
    from ..backend import build_sandbox

    monkeypatch.setattr(build_sandbox, "builder_image", lambda: "")


class _FakeBuildSandbox:
    """Minimal async stand-in for ``PyromindSDK`` used by the build path.

    A detached build makes **two or more** exec calls (a launch, then one poll
    per interval), so responses are consumed from a queue. Tests that only care
    about the outcome can omit ``responses`` and get the conventional pair: a
    successful launch followed by a single poll that reports
    ``done rc=<returncode>`` with ``stdout`` as the build log.

    ``staged_bytes`` inserts the storage-staging round trip in front of that
    pair: the first exec is the "copy the staged context in" call, which answers
    with the size its verification step expects.
    """

    def __init__(
        self,
        *,
        stdout: str = "",
        returncode: int = 0,
        raise_on_exec: Exception | None = None,
        raise_on_put: Exception | None = None,
        responses: list[dict[str, Any]] | None = None,
        staged_bytes: int | None = None,
        stage_in_error: str = "",
        raise_on_stage_in: Exception | None = None,
    ) -> None:
        self.sandbox_id = "sb-build-1"
        self.staged_bytes = staged_bytes
        self.stage_in_error = stage_in_error
        self.raise_on_stage_in = raise_on_stage_in
        self.stdout = stdout
        self.returncode = returncode
        self.raise_on_exec = raise_on_exec
        self.raise_on_put = raise_on_put
        self.responses = list(responses) if responses is not None else None
        self.waited = False
        self.cleaned = False
        self.archives: list[tuple[str, bytes]] = []
        self.argv: list[str] | None = None
        self.exec_timeout: int | None = None
        self.exec_output = ""
        # Every exec call, in order: the launch first, then the polls.
        self.calls: list[dict[str, Any]] = []

    @property
    def scripts(self) -> list[str]:
        return [call["script"] for call in self.calls]

    async def wait_until_running(self) -> None:
        self.waited = True

    async def put_archive(self, dest_path: str, tar_bytes: bytes) -> None:
        if self.raise_on_put is not None:
            raise self.raise_on_put
        self.archives.append((dest_path, tar_bytes))

    async def iter_exec_stream(self, cmd: Any, *, tty: bool = False, cwd: str = "",
                               timeout: int | None = None):
        if not isinstance(cmd, list) or not all(isinstance(part, str) for part in cmd):
            raise AssertionError(
                f"exec command must be argv, got {type(cmd)!r}: {cmd!r}"
            )
        if len(cmd) != 3 or cmd[0] != "sh" or cmd[1] != "-c":
            raise AssertionError(f"unexpected argv shape: {cmd!r}")
        self.argv = list(cmd)
        self.exec_timeout = timeout
        self.calls.append({"script": cmd[2], "timeout": timeout})
        if self.raise_on_exec is not None:
            raise self.raise_on_exec
        if self.raise_on_stage_in is not None and len(self.calls) == 1:
            # Only the stage-in call dies (a dropped websocket / the exec
            # deadline); the build itself must still be able to proceed.
            raise self.raise_on_stage_in

        if self.responses is not None:
            if not self.responses:
                raise AssertionError(
                    "the build made more exec calls than the test scripted "
                    f"({len(self.calls)} so far)"
                )
            response = self.responses.pop(0)
        elif self.staged_bytes is not None and len(self.calls) == 1:
            # The "copy the staged context in" round trip, which carries the
            # size its verification step compares against.
            response = {
                "stdout": f"{context_staging.SIZE_MARKER}{self.staged_bytes}\n",
                "returncode": 9 if self.stage_in_error else 0,
                "stderr": self.stage_in_error,
            }
        elif len(self.calls) == 1:
            response = {"stderr": "docker-rt-status: launched\n"}
        else:
            response = {
                "stdout": self.stdout,
                "stderr": f"docker-rt-status: done rc={self.returncode}\n",
            }

        for kind in ("stdout", "stderr"):
            payload = response.get(kind, "")
            if payload:
                yield SimpleNamespace(type=kind, data=payload, returncode=None)
        yield SimpleNamespace(
            type="exit", data="", returncode=response.get("returncode", 0)
        )

    async def execute(self, action: dict[str, Any], cwd: str = "", *, timeout: int | None = None):
        return {"output": self.exec_output, "returncode": 0, "exception_info": ""}

    async def cleanup(self) -> None:
        self.cleaned = True


def _launch() -> dict[str, Any]:
    """The launcher's reply: it forked the worker and is done."""
    return {"stderr": "docker-rt-status: launched\n"}


def _poll(stdout: str = "", *, state: str = "running") -> dict[str, Any]:
    """One poll's reply. State goes on stderr, log bytes on stdout."""
    return {"stdout": stdout, "stderr": f"docker-rt-status: {state}\n"}


def _worker_script(sandbox: _FakeBuildSandbox) -> str:
    """Decode the worker the launcher embedded (base64, so quoting can't bite).

    The launch call carries no kaniko flags itself — it only writes this script
    and detaches it — so any assertion about the argv kaniko will see has to go
    through here. It doubles as the check that the real argv reaches the sandbox.
    """
    launch = sandbox.calls[0]["script"]
    match = re.search(r"printf '%s' ([A-Za-z0-9+/=]+) \| base64 -d", launch)
    assert match, launch
    return base64.b64decode(match.group(1)).decode()


def _install_fake(
    monkeypatch: pytest.MonkeyPatch, sandbox: _FakeBuildSandbox
) -> dict[str, Any]:
    from ..backend import runtime

    captured: dict[str, Any] = {}

    async def fake_start(**kwargs: Any) -> _FakeBuildSandbox:
        captured.update(kwargs)
        return sandbox

    monkeypatch.setattr(runtime, "start_kube_environment", fake_start)
    return captured


class _FakeStager:
    """Stands in for ``StorageStager``: records calls, never touches the network."""

    def __init__(
        self,
        plan: Any,
        *,
        upload_error: Exception | None = None,
        cleanup_ok: bool = True,
    ) -> None:
        self.plan = plan
        self.upload_error = upload_error
        self.cleanup_ok = cleanup_ok
        self.payload: bytes | None = None
        self.cleanup_calls = 0

    def describe(self) -> str:
        return self.plan.object_key

    def upload(self, payload: bytes) -> None:
        if self.upload_error is not None:
            raise self.upload_error
        self.payload = payload

    def cleanup(self) -> bool:
        self.cleanup_calls += 1
        return self.cleanup_ok

    @property
    def cleaned(self) -> bool:
        return self.cleanup_calls > 0

    @property
    def cleaned_ok(self) -> bool:
        return self.cleanup_calls > 0 and self.cleanup_ok


class _StagingRecorder:
    """The stagers a build created, readable *after* the build has run.

    A value would not do: ``build_in_sandbox`` constructs the stager itself, so
    the object only exists once the generator has been driven far enough.
    """

    def __init__(self) -> None:
        self.created: list[_FakeStager] = []

    def add(self, stager: _FakeStager) -> _FakeStager:
        self.created.append(stager)
        return stager

    @property
    def stager(self) -> _FakeStager:
        assert len(self.created) == 1, (
            f"expected exactly one stager, got {len(self.created)}"
        )
        return self.created[0]

    @property
    def count(self) -> int:
        return len(self.created)


def _install_staging(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str = "auto",
    upload_error: Exception | None = None,
    cleanup_ok: bool = True,
) -> _StagingRecorder:
    """Turn workspace staging on; records every stager the build creates."""
    from ..backend import context_staging as cs

    monkeypatch.setenv("DOCKER_RT_BUILD_CONTEXT_MODE", mode)
    recorder = _StagingRecorder()

    def factory(plan: Any, **_kwargs: Any) -> _FakeStager:
        return recorder.add(
            _FakeStager(plan, upload_error=upload_error, cleanup_ok=cleanup_ok)
        )

    monkeypatch.setattr(cs, "StorageStager", factory)
    return recorder


async def _collect(agen: Any) -> list[dict[str, Any]]:
    return [event async for event in agen]


def _context_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        payload = b"FROM scratch\n"
        info = tarfile.TarInfo("Dockerfile")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


# --------------------------------------------------------------------------
# pure helpers
# --------------------------------------------------------------------------


def test_the_script_is_sent_as_argv_not_as_a_bare_string() -> None:
    from ..backend.build_sandbox import shell_argv

    argv = shell_argv("set -eu\necho hi\n")
    assert argv == ["sh", "-c", "set -eu\necho hi\n"]
    assert argv[0] != "s"


def test_pack_build_context_gzips(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend.build_sandbox import pack_build_context

    packed = pack_build_context(b"hello")
    assert gzip.decompress(packed) == b"hello"
    # mtime=0 keeps the archive byte-stable across builds.
    assert pack_build_context(b"hello") == packed


def test_context_is_uploaded_as_a_single_file(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend.build_sandbox import _single_file_tar

    blob = _single_file_tar("context.tar.gz", b"payload")
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        members = tar.getmembers()
    assert [m.name for m in members] == ["context.tar.gz"]
    assert members[0].isfile()


def test_build_timeout_and_poll_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """``DOCKER_RT_BUILD_TIMEOUT`` is a wall-clock budget, not an exec timeout.

    It used to be clamped down to the sandbox's 600 s single-exec cap, which
    silently capped every build at ten minutes. The budget now bounds the poll
    loop, and the calls it bounds are short by construction.
    """
    from ..backend import build_sandbox

    _clear(monkeypatch)
    assert build_sandbox.build_timeout() == 3600

    monkeypatch.setenv("DOCKER_RT_BUILD_TIMEOUT", "99999")
    assert build_sandbox.build_timeout() == 99999

    monkeypatch.setenv("DOCKER_RT_BUILD_TIMEOUT", "not-a-number")
    assert build_sandbox.build_timeout() == 3600

    _clear(monkeypatch)
    assert build_sandbox.poll_interval() == 2.0
    monkeypatch.setenv("DOCKER_RT_BUILD_POLL_INTERVAL_S", "0.5")
    assert build_sandbox.poll_interval() == 0.5
    monkeypatch.setenv("DOCKER_RT_BUILD_POLL_INTERVAL_S", "0")
    assert build_sandbox.poll_interval() >= 0.1  # never a busy loop
    monkeypatch.setenv("DOCKER_RT_BUILD_POLL_INTERVAL_S", "junk")
    assert build_sandbox.poll_interval() == 2.0


def test_both_exec_calls_stay_under_the_sandbox_cap() -> None:
    """The whole point of splitting: no single call may approach 600 s."""
    from ..backend import build_sandbox

    assert build_sandbox.START_TIMEOUT_S <= build_sandbox.SANDBOX_EXEC_CAP
    assert build_sandbox.POLL_TIMEOUT_S <= build_sandbox.SANDBOX_EXEC_CAP


def test_context_size_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_CONTEXT_WARN_MB", "1")
    assert build_sandbox.context_size_warning(b"x" * 1024) == ""

    warning = build_sandbox.context_size_warning(b"x" * (2 * 1024 * 1024))
    assert warning
    assert "MiB" in warning
    assert ".dockerignore" in warning

    # 0 disables the check entirely.
    monkeypatch.setenv("DOCKER_RT_BUILD_CONTEXT_WARN_MB", "0")
    assert build_sandbox.context_size_warning(b"x" * (64 * 1024 * 1024)) == ""


def test_resolve_targets_keeps_fully_qualified_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend.build_sandbox import resolve_targets

    aliases, error = resolve_targets(
        ["myapp", "docker.io/library/other:1"], registry="reg.example.com/rt"
    )
    assert error is None
    assert aliases["myapp:latest"] == "reg.example.com/rt/myapp:latest"
    assert aliases["docker.io/library/other:1"] == "docker.io/library/other:1"


def test_resolve_targets_rejects_an_empty_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend.build_sandbox import resolve_targets

    aliases, error = resolve_targets([""], registry="reg.example.com/rt")
    assert aliases == {}
    assert error


def test_resolve_targets_does_not_re_resolve_an_empty_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``registry=""`` means "no prefix", not "go ask the environment again".

    Passing ``registry or None`` here made ``normalize_image_ref`` look the prefix
    up a second time, so the FQ-tag path blew up on a cluster whose push prefix
    cannot be resolved.
    """
    from ..backend.build_sandbox import resolve_targets

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "us-west-1")  # host, no namespace

    aliases, error = resolve_targets(["docker.io/lvniqi/app:1"], registry="")
    assert error is None
    assert aliases == {"docker.io/lvniqi/app:1": "docker.io/lvniqi/app:1"}

    # A short tag with no prefix stays unprefixed rather than raising.
    aliases, error = resolve_targets(["myapp"], registry="")
    assert error is None
    assert aliases["myapp:latest"] == "myapp:latest"


def test_prerequisites_error_lists_every_missing_item(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend.build_sandbox import build_prerequisites_error

    _clear(monkeypatch)
    _hide_builder_image(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_PUSH", "true")
    message = build_prerequisites_error(["myapp"])
    assert message
    assert "DOCKER_RT_BUILD_IMAGE" in message
    assert "DOCKER_RT_BUILD_REGISTRY" in message


def test_prerequisites_error_passes_with_a_fully_qualified_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ..backend.build_sandbox import build_prerequisites_error

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:v1-debug")
    assert build_prerequisites_error(["reg.example.com/rt/app:1"]) is None


def test_fully_qualified_tag_needs_no_prefix_on_a_profiled_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``PYROMIND_CLUSTER=us-west-1#pre`` + Docker Hub profile + no namespace.

    The Docker Hub profile has a host but no default namespace, so resolving the
    push prefix raises. A fully-qualified ``-t`` never consults that prefix, so it
    must not be blocked — the earlier code resolved it eagerly and rejected the
    build with a ``DOCKER_RT_REGISTRY_NAMESPACE`` error that had nothing to do
    with the command the user typed.
    """
    from ..backend.build_sandbox import build_prerequisites_error

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:v1-debug")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "us-west-1")
    monkeypatch.setenv("DOCKER_RT_BUILD_PUSH", "true")
    assert build_prerequisites_error(["docker.io/lvniqi/pyromind-console:dev"]) is None


def test_short_tag_still_requires_the_prefix_on_a_profiled_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The relaxation above must not leak onto short tags, which *do* need it."""
    from ..backend.build_sandbox import build_prerequisites_error

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:v1-debug")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "us-west-1")
    monkeypatch.setenv("DOCKER_RT_BUILD_PUSH", "true")
    message = build_prerequisites_error(["pyromind-console:dev"])
    assert message
    assert "DOCKER_RT_REGISTRY_NAMESPACE" in message

    # ...and a namespace (or an explicit prefix) makes it pass.
    monkeypatch.setenv("DOCKER_RT_REGISTRY_NAMESPACE", "lvniqi")
    _set_push_credentials(monkeypatch)
    assert build_prerequisites_error(["pyromind-console:dev"]) is None


def test_one_short_tag_among_fully_qualified_ones_still_requires_the_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ..backend.build_sandbox import build_prerequisites_error

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:v1-debug")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "us-west-1")
    monkeypatch.setenv("DOCKER_RT_BUILD_PUSH", "true")
    # NB: use a tag with no colon — ``shortone:1`` looks like ``host:port`` and is
    # (deliberately) classified as fully qualified.
    message = build_prerequisites_error(["docker.io/lvniqi/app:1", "shortone"])
    assert message
    assert "DOCKER_RT_REGISTRY_NAMESPACE" in message


def test_prerequisites_error_rejects_an_unimplemented_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ..backend.build_sandbox import build_prerequisites_error

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:v1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_EXECUTOR", "buildkit")
    message = build_prerequisites_error(["reg.example.com/rt/app:1"])
    assert message and "DOCKER_RT_BUILD_EXECUTOR" in message


def test_build_script_uses_the_tar_context(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import kaniko
    from ..backend.build_sandbox import build_script

    _clear(monkeypatch)
    script = build_script(
        destinations=["reg.example.com/rt/app:1"], dockerfile="Dockerfile"
    )
    assert f"--context={kaniko.context_uri()}" in script
    assert "--destination=reg.example.com/rt/app:1" in script
    assert "docker-rt-digest:" in script
    # The workdir has to survive kaniko's per-stage root-filesystem wipe, which
    # only spares /kaniko. A /tmp workdir made every multi-stage build report
    # "the launcher did not reach the fork".
    assert kaniko.build_workdir().startswith("/kaniko/")


# --------------------------------------------------------------------------
# end-to-end through the fake sandbox
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_in_sandbox_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1.24.0-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_USERNAME", "lvniqi")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_PASSWORD", "pat")

    sandbox = _FakeBuildSandbox(stdout=f"INFO building\ndocker-rt-digest: {GOOD_DIGEST}\n")
    captured = _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(
            tar_bytes=_context_tar(),
            tags=["myapp"],
            namespace="custom-user-1",
        )
    )

    assert not any("error" in event for event in events)
    assert captured["image"] == "reg.example.com/rt/kaniko:1.24.0-debug"
    assert captured["namespace"] == "custom-user-1"
    assert sandbox.waited and sandbox.cleaned

    # Context landed as exactly one file, gzipped.
    from ..backend import kaniko as kaniko_mod

    assert len(sandbox.archives) == 1
    dest, blob = sandbox.archives[0]
    assert dest == kaniko_mod.build_workdir()
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        members = tar.getmembers()
    assert [m.name for m in members] == ["context.tar.gz"]
    payload = tarfile.open(fileobj=io.BytesIO(blob)).extractfile(members[0]).read()
    assert gzip.decompress(payload) == _context_tar()

    # The script was delivered as argv and the credentials were base64-inlined.
    assert sandbox.argv is not None
    script = _worker_script(sandbox)
    assert f"--context={kaniko_mod.context_uri()}" in script
    assert f"--digest-file={kaniko_mod.digest_file_path()}" in script
    # Credentials are inlined as a base64 blob, never as plain text.
    match = re.search(r"printf '%s' ([A-Za-z0-9+/=]+) \| base64 -d", script)
    assert match, script
    auths = json.loads(base64.b64decode(match.group(1)))["auths"]
    assert "reg.example.com" in auths
    assert "pat" not in script

    assert events[-1]["docker_rt"]["digest"] == GOOD_DIGEST
    assert "--destination=reg.example.com/rt/myapp:latest" in script
    assert "pat" not in script

    final = events[-1]
    assert final["docker_rt"]["aliases"]["myapp:latest"] == "reg.example.com/rt/myapp:latest"
    assert final["docker_rt"]["digest"] == GOOD_DIGEST
    assert {"aux": {"ID": GOOD_DIGEST}} in events


@pytest.mark.asyncio
async def test_build_in_sandbox_reports_failure_and_still_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    _set_push_credentials(monkeypatch)

    sandbox = _FakeBuildSandbox(stdout="error: no space left on device\n", returncode=1)
    _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(tar_bytes=_context_tar(), tags=["myapp"], namespace="ns")
    )

    errors = [event for event in events if event.get("error")]
    assert errors, events
    # Both fields must be present, otherwise the classic builder prints nothing
    # and exits 0.
    assert errors[0]["errorDetail"]["message"] == errors[0]["error"]
    assert sandbox.cleaned
    assert not any("docker_rt" in event for event in events)


@pytest.mark.asyncio
async def test_build_in_sandbox_fully_qualified_tag_without_a_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full path for ``-t docker.io/lvniqi/app:dev`` on ``us-west-1`` (no namespace).

    Guards three places that used to re-resolve the push prefix and abort:
    ``build_prerequisites_error``, ``resolve_targets`` and the credential load.
    """
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("PYROMIND_CLUSTER", "us-west-1#pre")
    _set_push_credentials(monkeypatch)

    sandbox = _FakeBuildSandbox(stdout=f"INFO building\ndocker-rt-digest: {GOOD_DIGEST}\n")
    captured = _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(
            tar_bytes=_context_tar(),
            tags=["docker.io/lvniqi/pyromind-console:dev"],
            namespace="custom-user-1",
        )
    )

    assert not [event for event in events if event.get("error")], events
    assert captured["image"] == "reg.example.com/rt/kaniko:1-debug"
    assert sandbox.argv is not None
    script = _worker_script(sandbox)
    assert "--destination=docker.io/lvniqi/pyromind-console:dev" in script

    # The credential blob was still injected, keyed by the image's own host.
    match = re.search(r"printf '%s' ([A-Za-z0-9+/=]+) \| base64 -d", script)
    assert match, script
    auths = json.loads(base64.b64decode(match.group(1)))["auths"]
    assert "docker.io" in auths
    assert "pat" not in script  # the password is inlined only as base64

    final = events[-1]
    assert (
        final["docker_rt"]["aliases"]["docker.io/lvniqi/pyromind-console:dev"]
        == "docker.io/lvniqi/pyromind-console:dev"
    )
    assert sandbox.cleaned


@pytest.mark.asyncio
async def test_build_in_sandbox_missing_builder_image_never_starts_a_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ..backend import build_sandbox
    from ..backend import runtime

    _clear(monkeypatch)
    _hide_builder_image(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")

    async def boom(**kwargs: Any):  # pragma: no cover - must not run
        raise AssertionError("must not create a sandbox")

    monkeypatch.setattr(runtime, "start_kube_environment", boom)

    events = await _collect(
        build_sandbox.build_in_sandbox(tar_bytes=_context_tar(), tags=["myapp"], namespace="ns")
    )
    assert events[0]["error"]
    assert "DOCKER_RT_BUILD_IMAGE" in events[0]["error"]


@pytest.mark.asyncio
async def test_build_in_sandbox_short_tag_without_registry_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ..backend import build_sandbox
    from ..backend import runtime

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_PUSH", "true")

    async def boom(**kwargs: Any):  # pragma: no cover - must not run
        raise AssertionError("must not create a sandbox")

    monkeypatch.setattr(runtime, "start_kube_environment", boom)

    events = await _collect(
        build_sandbox.build_in_sandbox(tar_bytes=_context_tar(), tags=["myapp"], namespace="ns")
    )
    assert events[0]["error"]
    assert "DOCKER_RT_BUILD_REGISTRY" in events[0]["error"]


@pytest.mark.asyncio
async def test_build_in_sandbox_keep_flag_skips_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    _set_push_credentials(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_SANDBOX_KEEP", "true")

    sandbox = _FakeBuildSandbox(stdout=f"docker-rt-digest: {GOOD_DIGEST}\n")
    _install_fake(monkeypatch, sandbox)

    await _collect(
        build_sandbox.build_in_sandbox(
            tar_bytes=_context_tar(), tags=["myapp"], namespace="ns"
        )
    )
    assert not sandbox.cleaned


@pytest.mark.asyncio
async def test_build_in_sandbox_reports_upload_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    _set_push_credentials(monkeypatch)

    sandbox = _FakeBuildSandbox(raise_on_put=RuntimeError("kaboom"))
    _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(tar_bytes=_context_tar(), tags=["myapp"], namespace="ns")
    )
    assert any("cannot upload build context" in (e.get("error") or "") for e in events)
    assert sandbox.argv is None
    assert sandbox.cleaned


@pytest.mark.asyncio
async def test_build_in_sandbox_reports_exec_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    _set_push_credentials(monkeypatch)

    sandbox = _FakeBuildSandbox(raise_on_exec=RuntimeError("stream died"))
    _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(tar_bytes=_context_tar(), tags=["myapp"], namespace="ns")
    )
    assert any(
        "cannot start the build in the sandbox" in (e.get("error") or "") for e in events
    )
    assert sandbox.cleaned


@pytest.mark.asyncio
async def test_build_in_sandbox_aborts_before_the_sandbox_when_acr_repo_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ..backend import build_sandbox
    from ..backend import registry_push
    from ..backend import runtime

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "cn-east-1")
    _set_push_credentials(monkeypatch)

    def fake_ensure(refs: list[str], **_kwargs: Any):
        return registry_push.EnsureResult(error="cannot create ACR repository 'x': NoPrivilege")

    monkeypatch.setattr(registry_push, "ensure_repositories", fake_ensure)

    async def boom(**kwargs: Any):  # pragma: no cover - must not run
        raise AssertionError("must not create a sandbox")

    monkeypatch.setattr(runtime, "start_kube_environment", boom)

    events = await _collect(
        build_sandbox.build_in_sandbox(
            tar_bytes=_context_tar(),
            tags=["pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind/app:1"],
            namespace="ns",
        )
    )
    assert events[0]["error"] == "cannot create ACR repository 'x': NoPrivilege"


@pytest.mark.asyncio
async def test_build_in_sandbox_fails_fast_when_configured_credentials_are_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from ..backend import build_sandbox
    from ..backend import runtime

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    # Point the dockerconfig source at a file that does not exist so the test does
    # not depend on what this machine happens to have under /etc.
    monkeypatch.setenv("DOCKER_RT_REGISTRY_DOCKERCONFIG", str(tmp_path / "absent.json"))

    async def boom(**kwargs: Any):  # pragma: no cover - must not run
        raise AssertionError("must not create a sandbox")

    monkeypatch.setattr(runtime, "start_kube_environment", boom)

    events = await _collect(
        build_sandbox.build_in_sandbox(tar_bytes=_context_tar(), tags=["myapp"], namespace="ns")
    )
    assert events[0]["error"]
    assert "dockerconfig secret not found" in events[0]["error"]


@pytest.mark.asyncio
async def test_build_in_sandbox_fails_before_the_sandbox_when_no_credentials_are_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No push credentials is a hard pre-sandbox error, not a warning.

    Pushing without credentials cannot succeed against either Docker Hub or ACR,
    so failing before the sandbox costs the user nothing instead of several
    minutes plus a 401.
    """
    from ..backend import build_sandbox
    from ..backend import runtime

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")

    async def boom(**kwargs: Any):  # pragma: no cover - must not run
        raise AssertionError("must not create a sandbox")

    monkeypatch.setattr(runtime, "start_kube_environment", boom)

    events = await _collect(
        build_sandbox.build_in_sandbox(tar_bytes=_context_tar(), tags=["myapp"], namespace="ns")
    )
    assert events[0]["error"]
    assert "credentials" in events[0]["error"]
    assert not any(e.get("stream") for e in events)


def test_build_event_payload_is_json_serialisable(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import buildkit

    event = buildkit.error_event("boom")
    assert json.loads(json.dumps(event))["errorDetail"]["message"] == "boom"


@pytest.mark.asyncio
async def test_build_in_sandbox_launches_detached_then_polls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The build is started and then polled; no single call spans the build.

    This is the contract that removes the 600 s ceiling: call #1 only forks a
    worker, and every call after it is a short status read whose output is
    incremental (each byte is streamed exactly once).
    """
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    monkeypatch.setenv("DOCKER_RT_BUILD_POLL_INTERVAL_S", "0.01")
    _set_push_credentials(monkeypatch)

    first, second = "INFO step 1\n", "INFO step 2\n"
    sandbox = _FakeBuildSandbox(
        responses=[
            _launch(),
            _poll(stdout=first),
            _poll(stdout=second),
            _poll(
                stdout=f"INFO done\ndocker-rt-digest: {GOOD_DIGEST}\n",
                state="done rc=0",
            ),
        ]
    )
    _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(tar_bytes=_context_tar(), tags=["myapp"], namespace="ns")
    )

    assert not [event for event in events if event.get("error")], events
    assert len(sandbox.calls) == 4

    launch, *polls = sandbox.calls
    assert launch["timeout"] == build_sandbox.START_TIMEOUT_S
    assert "nohup" in launch["script"] and "setsid" in launch["script"]
    # The launcher must not run kaniko inline — that is the whole point.
    assert "--destination=" not in launch["script"]

    for poll in polls:
        assert poll["timeout"] == build_sandbox.POLL_TIMEOUT_S
        assert "docker-rt-status" in poll["script"]

    # Each poll asks only for the bytes it has not seen (tail -c is 1-based).
    assert "tail -c +1 " in polls[0]["script"]
    assert f"tail -c +{len(first.encode()) + 1} " in polls[1]["script"]
    assert f"tail -c +{len(first.encode()) + len(second.encode()) + 1} " in polls[2]["script"]

    streamed = "".join(event.get("stream") or "" for event in events)
    assert streamed.count("INFO step 1") == 1
    assert streamed.count("INFO step 2") == 1
    assert streamed.count("INFO done") == 1
    assert events[-1]["docker_rt"]["digest"] == GOOD_DIGEST


@pytest.mark.asyncio
async def test_build_in_sandbox_survives_a_failed_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dropped poll must not discard a build that is still running.

    The old design put the whole build inside one exec, so a transport error
    ended the build and threw away a kaniko run that had already done the work.
    """
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    monkeypatch.setenv("DOCKER_RT_BUILD_POLL_INTERVAL_S", "0.01")
    _set_push_credentials(monkeypatch)

    class _FlakySandbox(_FakeBuildSandbox):
        async def iter_exec_stream(self, cmd, *, tty=False, cwd="", timeout=None):
            if len(self.calls) == 1:  # the second call: first poll
                self.calls.append({"script": cmd[2], "timeout": timeout})
                raise RuntimeError("websocket closed")
                yield  # pragma: no cover - makes this an async generator
            async for chunk in super().iter_exec_stream(
                cmd, tty=tty, cwd=cwd, timeout=timeout
            ):
                yield chunk

    sandbox = _FlakySandbox(
        responses=[
            _launch(),
            _poll(stdout=f"docker-rt-digest: {GOOD_DIGEST}\n", state="done rc=0"),
        ]
    )
    _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(tar_bytes=_context_tar(), tags=["myapp"], namespace="ns")
    )

    assert not [event for event in events if event.get("error")], events
    assert events[-1]["docker_rt"]["digest"] == GOOD_DIGEST


@pytest.mark.asyncio
async def test_build_in_sandbox_reports_a_vanished_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PID gone with no exit code = killed (OOM), not "still working"."""
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    monkeypatch.setenv("DOCKER_RT_BUILD_POLL_INTERVAL_S", "0.01")
    _set_push_credentials(monkeypatch)

    sandbox = _FakeBuildSandbox(
        responses=[_launch()] + [_poll(state="gone")] * build_sandbox.GONE_STRIKES
    )
    _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(tar_bytes=_context_tar(), tags=["myapp"], namespace="ns")
    )

    errors = [event for event in events if event.get("error")]
    assert errors, events
    assert "disappeared" in errors[0]["error"]
    # One strike is not enough: the exit-code file lands a moment after the PID goes.
    assert len(sandbox.calls) == build_sandbox.GONE_STRIKES + 1


@pytest.mark.asyncio
async def test_build_in_sandbox_reports_a_launcher_that_never_forked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    _set_push_credentials(monkeypatch)

    sandbox = _FakeBuildSandbox(
        responses=[_launch(), _poll(state="not-launched")],
    )
    _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(tar_bytes=_context_tar(), tags=["myapp"], namespace="ns")
    )

    errors = [event for event in events if event.get("error")]
    assert errors, events
    assert "never started" in errors[0]["error"]


@pytest.mark.asyncio
async def test_build_in_sandbox_enforces_the_wall_clock_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A build that never finishes is cut off by DOCKER_RT_BUILD_TIMEOUT, not by exec."""
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    monkeypatch.setenv("DOCKER_RT_BUILD_TIMEOUT", "0")
    monkeypatch.setenv("DOCKER_RT_BUILD_POLL_INTERVAL_S", "0.01")
    _set_push_credentials(monkeypatch)

    sandbox = _FakeBuildSandbox(responses=[_launch(), _poll(state="running")])
    _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(tar_bytes=_context_tar(), tags=["myapp"], namespace="ns")
    )

    errors = [event for event in events if event.get("error")]
    assert errors, events
    assert "DOCKER_RT_BUILD_TIMEOUT" in errors[0]["error"]


@pytest.mark.asyncio
async def test_build_in_sandbox_warns_about_a_large_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fat context warns before the sandbox is created (no .dockerignore hint otherwise)."""
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    monkeypatch.setenv("DOCKER_RT_BUILD_CONTEXT_WARN_MB", "1")
    _set_push_credentials(monkeypatch)

    sandbox = _FakeBuildSandbox(stdout=f"docker-rt-digest: {GOOD_DIGEST}\n")
    _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(
            tar_bytes=_context_tar() + b"\0" * (2 * 1024 * 1024),
            tags=["myapp"],
            namespace="ns",
        )
    )

    assert any(".dockerignore" in (event.get("stream") or "") for event in events), events
    assert not [event for event in events if event.get("error")], events


# --------------------------------------------------------------------------
# progress reporting and log collapsing
# --------------------------------------------------------------------------


def test_human_size() -> None:
    from ..backend.build_sandbox import human_size

    assert human_size(0) == "0 B"
    assert human_size(999) == "999 B"
    assert human_size(1024) == "1.0 KiB"
    assert human_size(1536) == "1.5 KiB"
    assert human_size(95_570_000).endswith("MiB")


def test_log_collapser_keeps_stage_lines_and_summarises_step_output() -> None:
    """kaniko's own lines are the build's skeleton; a RUN step's output is not."""
    from ..backend.build_sandbox import _LogCollapser

    collapser = _LogCollapser(head=2)
    payload = (
        "INFO[0001] RUN npm run build\n"
        "transforming...\n"
        "rendering chunks...\n"
        "dist/a.js 1kB\n"
        "dist/b.js 2kB\n"
        "dist/c.js 3kB\n"
        "\u2713 built in 12s\n"
        "INFO[0002] Pushing image to reg/app:1\n"
    )
    out = collapser.feed(payload) + collapser.drain()

    assert "INFO[0001] RUN npm run build" in out
    assert "INFO[0002] Pushing image to reg/app:1" in out
    # The opening lines of the step survive...
    assert "transforming..." in out
    assert "rendering chunks..." in out
    # ...the middle is summarised...
    assert "dist/b.js 2kB" not in out
    assert "4 line(s) of step output hidden" in out
    # ...and the verdict line is kept, because that is the one people look for.
    assert "\u2713 built in 12s" in out


def test_log_collapser_carries_a_partial_line_across_polls() -> None:
    """Polls deliver byte ranges, so a line can straddle two of them."""
    from ..backend.build_sandbox import _LogCollapser

    collapser = _LogCollapser(head=1)
    first = collapser.feed("INFO[0001] half")
    second = collapser.feed(" a line\nstep noise\n")
    assert "INFO[0001] half a line" in first + second + collapser.drain()
    assert "step noise" in first + second


def test_log_collapser_disabled_streams_everything() -> None:
    from ..backend.build_sandbox import _LogCollapser

    collapser = _LogCollapser(head=1, enabled=False)
    payload = "a\nb\nc\n"
    assert collapser.feed(payload) == payload
    assert collapser.drain() == ""


def test_full_log_mode_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend.build_sandbox import full_log_requested

    _clear(monkeypatch)
    assert full_log_requested() is False
    monkeypatch.setenv("DOCKER_RT_BUILD_LOG", "full")
    assert full_log_requested() is True


def test_raw_tail_bounds_the_slice() -> None:
    from ..backend.build_sandbox import _raw_tail

    raw = "".join(f"line {i}\n" for i in range(500))
    tail, count = _raw_tail(raw, limit=10)
    assert count == 10
    assert "line 499" in tail
    assert "line 489" not in tail
    assert _raw_tail("") == ("", 0)


@pytest.mark.asyncio
async def test_build_in_sandbox_reports_a_wiped_workdir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vanished workdir means the container changed — not a failed launcher.

    Regression for the multi-stage bug: kaniko deletes the root filesystem when
    it moves to the next stage, and the old code read that as "the launcher did
    not reach the fork", which sent the investigation in the wrong direction.
    """
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    monkeypatch.setenv("DOCKER_RT_BUILD_POLL_INTERVAL_S", "0.01")
    _set_push_credentials(monkeypatch)

    # The launcher succeeds and output flows, then the filesystem disappears.
    sandbox = _FakeBuildSandbox(
        responses=[
            _launch(),
            _poll("INFO[0004] Deleting filesystem...\n"),
            _poll(state="wiped"),
        ]
    )
    _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(
            tar_bytes=_context_tar(), tags=["myapp"], namespace="ns"
        )
    )

    errors = [event for event in events if event.get("error")]
    assert errors, events
    assert "lost its working directory" in errors[0]["error"]
    assert "DOCKER_RT_BUILD_SANDBOX_MEMORY" in errors[0]["error"]
    assert "did not reach the fork" not in errors[0]["error"]


@pytest.mark.asyncio
async def test_build_in_sandbox_reports_each_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every step the user waits on has to be visible, upload included."""
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    monkeypatch.setenv("DOCKER_RT_BUILD_POLL_INTERVAL_S", "0.01")
    _set_push_credentials(monkeypatch)

    sandbox = _FakeBuildSandbox(
        stdout=f"INFO[0001] building\ndocker-rt-digest: {GOOD_DIGEST}\n"
    )
    _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(
            tar_bytes=_context_tar(), tags=["myapp"], namespace="ns"
        )
    )
    text = "".join(event.get("stream", "") for event in events)

    for expected in (
        "==> Packing the build context",
        "==> Build context packed",
        "==> Creating the build sandbox",
        "waiting for it to run",
        "==> Build sandbox ready after",
        "==> Uploading the build context",
        "==> Build context uploaded after",
        "==> Starting kaniko",
        "==> kaniko is running",
        "==> kaniko finished after",
        f"==> Pushed {GOOD_DIGEST}",
        "push target: reg.example.com/rt/myapp:latest",
    ):
        assert expected in text, expected
    assert not [event for event in events if event.get("error")], events


@pytest.mark.asyncio
async def test_a_failed_build_replays_what_the_collapsing_hid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collapsing must never cost the explanation of a failure."""
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    monkeypatch.setenv("DOCKER_RT_BUILD_POLL_INTERVAL_S", "0.01")
    _set_push_credentials(monkeypatch)

    noisy = "".join(f"step noise {i}\n" for i in range(20))
    sandbox = _FakeBuildSandbox(
        responses=[
            _launch(),
            {
                "stdout": noisy,
                "stderr": "docker-rt-status: done rc=1\n",
                "returncode": 1,
            },
        ]
    )
    _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(
            tar_bytes=_context_tar(), tags=["myapp"], namespace="ns"
        )
    )
    text = "".join(event.get("stream", "") for event in events)
    errors = [event for event in events if event.get("error")]

    assert errors and "exited with code 1" in errors[0]["error"]
    assert "line(s) of step output hidden" in text
    assert "step noise 19" in text


@pytest.mark.asyncio
async def test_heartbeat_reports_progress_while_a_slow_await_runs() -> None:
    """A long silent wait must say something, or it reads as a hang."""
    from ..backend.build_sandbox import _heartbeat

    async def slow() -> str:
        await asyncio.sleep(0.05)
        return "ok"

    task = asyncio.ensure_future(slow())
    events = [
        event
        async for event in _heartbeat(
            task, label="the build sandbox to start", interval=0.01
        )
    ]
    assert await task == "ok"

    assert events, "a slow wait produced no heartbeat"
    assert all(
        "still waiting for the build sandbox to start" in event["stream"]
        for event in events
    )


@pytest.mark.asyncio
async def test_heartbeat_propagates_the_underlying_failure() -> None:
    """Heartbeating must not swallow the error the wait was going to raise."""
    from ..backend.build_sandbox import _heartbeat

    async def boom() -> None:
        await asyncio.sleep(0.01)
        raise RuntimeError("sandbox never started")

    task = asyncio.ensure_future(boom())
    _ = [event async for event in _heartbeat(task, label="x", interval=0.005)]
    with pytest.raises(RuntimeError, match="sandbox never started"):
        await task



# --------------------------------------------------------------------------
# workspace-storage context staging
# --------------------------------------------------------------------------

_STAGED_STDOUT = f"INFO built\ndocker-rt-digest: {GOOD_DIGEST}\n"


def _staging_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """The usual preconditions for reaching the sandbox at all."""
    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    _set_push_credentials(monkeypatch)


@pytest.mark.asyncio
async def test_context_is_staged_through_the_workspace_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No context bytes travel through the exec channel when staging works.

    ``put_archive`` is the slow path this replaces, so "it was never called" is
    the point of the test, not an implementation detail.
    """
    from ..backend import build_sandbox

    _staging_ready(monkeypatch)
    context = _context_tar()
    packed = build_sandbox.pack_build_context(context)
    staging = _install_staging(monkeypatch)
    sandbox = _FakeBuildSandbox(stdout=_STAGED_STDOUT, staged_bytes=len(packed))
    captured = _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(tar_bytes=context, tags=["myapp"], namespace="ns")
    )
    text = "".join(event.get("stream", "") for event in events)
    stager = staging.stager

    # What went to storage is the gzipped context, under the planned key.
    assert stager.payload == packed

    # The sandbox was created with that directory mounted, and writable.
    assert captured["mounts"] == [context_staging.mount_spec(stager.plan)]

    # The first exec is the stage-in copy — ahead of the launcher, so the mount
    # only has to survive until the copy has been verified.
    stage_in = sandbox.calls[0]["script"]
    assert stager.plan.staged_path in stage_in
    assert f'if [ "$n" != "{len(packed)}" ]' in stage_in
    assert f"rm -rf {stager.plan.staged_dir}" in stage_in
    assert "docker-rt-status" not in stage_in
    assert sandbox.calls[0]["timeout"] == build_sandbox.STAGE_IN_TIMEOUT_S
    # ...and nothing later in the build touches the mount again.
    assert all(stager.plan.mount_path not in c["script"] for c in sandbox.calls[1:])

    assert sandbox.archives == []
    assert stager.cleaned_ok is True
    assert any(event.get("docker_rt") for event in events)
    assert "Build context copied in" in text
    assert "Uploading the build context" not in text


@pytest.mark.asyncio
async def test_a_refused_mount_falls_back_to_a_direct_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mount is the only cluster-dependent part, so it must not be fatal.

    A subPath the CSI driver refuses (missing directory, a storage layout the
    mount cannot express) would otherwise turn a working build into a failure
    over an optimisation.
    """
    from ..backend import build_sandbox, runtime

    _staging_ready(monkeypatch)
    context = _context_tar()
    staging = _install_staging(monkeypatch)
    sandbox = _FakeBuildSandbox(stdout=_STAGED_STDOUT)
    attempts: list[Any] = []

    async def fake_start(**kwargs: Any) -> _FakeBuildSandbox:
        attempts.append(kwargs.get("mounts"))
        if kwargs.get("mounts"):
            raise RuntimeError("subPath .docker-rt-build not found")
        return sandbox

    monkeypatch.setattr(runtime, "start_kube_environment", fake_start)

    events = await _collect(
        build_sandbox.build_in_sandbox(tar_bytes=context, tags=["myapp"], namespace="ns")
    )
    text = "".join(event.get("stream", "") for event in events)
    stager = staging.stager

    assert attempts == [[context_staging.mount_spec(stager.plan)], None]
    assert "retrying without it" in text
    # The direct upload carried the context, and staging did not try again.
    assert len(sandbox.archives) == 1
    assert sandbox.archives[0][1]
    # Whatever was already uploaded still has to go.
    assert stager.cleaned_ok is True
    assert any(event.get("docker_rt") for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sandbox_kwargs,expected_reason",
    [
        # The script's own size gate failed: rc=9 plus its stderr message.
        (
            {
                "staged_bytes": 1,
                "stage_in_error": "staged context truncated: got 3 bytes, expected 9",
            },
            "truncated",
        ),
        # The copy call answered with something unparseable — no size marker at
        # all. Untrusted rather than assumed fine.
        ({"staged_bytes": None}, "the copy reported no size"),
        # The copy call itself died: dropped websocket or exec deadline.
        ({"raise_on_stage_in": RuntimeError("websocket dropped")}, "did not complete"),
    ],
)
async def test_an_unusable_staged_context_falls_back_to_a_direct_upload(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_kwargs: dict[str, Any],
    expected_reason: str,
) -> None:
    """A short file must be caught here, not hours later by kaniko."""
    from ..backend import build_sandbox

    _staging_ready(monkeypatch)
    context = _context_tar()
    staging = _install_staging(monkeypatch)
    # ``staged_bytes=None`` means "not the stage-in call", which is exactly what
    # the "no size marker" case wants: the fake answers with the launcher reply.
    sandbox = _FakeBuildSandbox(stdout=_STAGED_STDOUT, **sandbox_kwargs)
    _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(tar_bytes=context, tags=["myapp"], namespace="ns")
    )
    text = "".join(event.get("stream", "") for event in events)

    assert "Staged context unusable" in text
    assert expected_reason in text
    assert len(sandbox.archives) == 1
    assert any(event.get("docker_rt") for event in events)
    assert staging.stager.cleaned_ok is True


@pytest.mark.asyncio
async def test_staged_context_is_cleaned_up_when_the_build_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Six minutes of build must not leave a multi-GB object in the user's quota."""
    from ..backend import build_sandbox

    _staging_ready(monkeypatch)
    context = _context_tar()
    packed = build_sandbox.pack_build_context(context)
    staging = _install_staging(monkeypatch)
    sandbox = _FakeBuildSandbox(
        stdout="error: failed to solve", returncode=1, staged_bytes=len(packed)
    )
    _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(tar_bytes=context, tags=["myapp"], namespace="ns")
    )

    assert [event["error"] for event in events if event.get("error")]
    assert staging.stager.cleaned_ok is True


@pytest.mark.asyncio
async def test_a_failed_cleanup_is_reported_and_does_not_lose_the_build(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Stage-2 cleanup runs in a ``finally``, so it must never raise."""
    from ..backend import build_sandbox

    _staging_ready(monkeypatch)
    context = _context_tar()
    packed = build_sandbox.pack_build_context(context)
    staging = _install_staging(monkeypatch, cleanup_ok=False)
    sandbox = _FakeBuildSandbox(stdout=_STAGED_STDOUT, staged_bytes=len(packed))
    _install_fake(monkeypatch, sandbox)

    with caplog.at_level("WARNING", logger="docker_rt.build_sandbox"):
        events = await _collect(
            build_sandbox.build_in_sandbox(
                tar_bytes=context, tags=["myapp"], namespace="ns"
            )
        )

    assert staging.stager.cleaned is True
    assert any(event.get("docker_rt") for event in events)
    assert "was left in storage" in caplog.text
    assert staging.stager.plan.object_dir in caplog.text


@pytest.mark.asyncio
async def test_storage_mode_fails_the_build_instead_of_falling_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``storage`` is the operator saying "the direct upload is unacceptable"."""
    from ..backend import build_sandbox

    _staging_ready(monkeypatch)
    staging = _install_staging(
        monkeypatch, mode="storage", upload_error=RuntimeError("credentials rejected")
    )
    sandbox = _FakeBuildSandbox()
    captured = _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(
            tar_bytes=_context_tar(), tags=["myapp"], namespace="ns"
        )
    )

    errors = [event for event in events if event.get("error")]
    assert len(errors) == 1
    assert errors[0]["error"] == "cannot stage the build context: credentials rejected"
    assert captured == {}
    assert sandbox.calls == []
    assert sandbox.archives == []
    # Nothing reached storage, so there is nothing to clean up.
    assert staging.count == 1
    assert staging.stager.cleaned is False


@pytest.mark.asyncio
async def test_storage_mode_fails_when_the_mount_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``storage`` must not quietly fall back when the mount cannot be created.

    ``auto`` treats a refused mount as an optimisation to give up on (see
    :func:`test_a_refused_mount_falls_back_to_a_direct_upload`); ``storage`` is
    the operator ruling that route out, so it has to surface as a failure.
    """
    from ..backend import build_sandbox, runtime

    _staging_ready(monkeypatch)
    staging = _install_staging(monkeypatch, mode="storage")
    sandbox = _FakeBuildSandbox(stdout=_STAGED_STDOUT)
    attempts: list[Any] = []

    async def fake_start(**kwargs: Any) -> _FakeBuildSandbox:
        attempts.append(kwargs.get("mounts"))
        raise RuntimeError("subPath .docker-rt-build not found")

    monkeypatch.setattr(runtime, "start_kube_environment", fake_start)

    events = await _collect(
        build_sandbox.build_in_sandbox(
            tar_bytes=_context_tar(), tags=["myapp"], namespace="ns"
        )
    )

    errors = [event for event in events if event.get("error")]
    assert len(errors) == 1
    assert "cannot mount the staged build context" in errors[0]["error"]
    # It never retried without the mount, and never queued a direct upload.
    assert len(attempts) == 1
    assert sandbox.archives == []
    # What was already uploaded still has to go.
    assert staging.stager.cleaned_ok is True


@pytest.mark.asyncio
async def test_storage_mode_fails_when_the_staged_copy_is_unusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A staged context that did not survive the copy is fatal in ``storage``.

    Quietly uploading it again would hide a broken mount behind a build that
    happens to work.
    """
    from ..backend import build_sandbox

    _staging_ready(monkeypatch)
    staging = _install_staging(monkeypatch, mode="storage")
    sandbox = _FakeBuildSandbox(stdout=_STAGED_STDOUT, staged_bytes=1)
    captured = _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(
            tar_bytes=_context_tar(), tags=["myapp"], namespace="ns"
        )
    )

    errors = [event for event in events if event.get("error")]
    assert len(errors) == 1
    assert "cannot use the staged build context" in errors[0]["error"]
    # The mount was still requested — that is how the context is meant to arrive.
    assert captured.get("mounts")
    assert sandbox.archives == []
    assert staging.stager.cleaned_ok is True


@pytest.mark.asyncio
async def test_auto_mode_falls_back_when_staging_the_context_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cluster without storage (or without the minio extra) must still build."""
    from ..backend import build_sandbox

    _staging_ready(monkeypatch)
    staging = _install_staging(
        monkeypatch, mode="auto", upload_error=RuntimeError("no such host")
    )
    sandbox = _FakeBuildSandbox(stdout=_STAGED_STDOUT)
    captured = _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(
            tar_bytes=_context_tar(), tags=["myapp"], namespace="ns"
        )
    )
    text = "".join(event.get("stream", "") for event in events)

    assert "Workspace staging unavailable" in text
    assert "no such host" in text
    assert captured["mounts"] is None
    assert len(sandbox.archives) == 1
    assert any(event.get("docker_rt") for event in events)
    assert staging.stager.payload is None
    # Nothing was uploaded, so cleanup is a no-op rather than a bogus delete.
    assert staging.stager.cleaned is False


@pytest.mark.asyncio
async def test_upload_mode_never_creates_a_stager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``upload`` is the escape hatch back to the pre-staging behaviour."""
    from ..backend import build_sandbox

    _staging_ready(monkeypatch)  # leaves DOCKER_RT_BUILD_CONTEXT_MODE=upload
    assert context_staging.staging_enabled() is False
    staging = _install_staging(monkeypatch, mode="upload")
    sandbox = _FakeBuildSandbox(stdout=_STAGED_STDOUT)

    def _explode(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("upload mode must not construct a stager")

    monkeypatch.setattr(context_staging, "StorageStager", _explode)
    captured = _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(
            tar_bytes=_context_tar(), tags=["myapp"], namespace="ns"
        )
    )

    assert captured["mounts"] is None
    assert len(sandbox.archives) == 1
    assert any(event.get("docker_rt") for event in events)
    assert staging.count == 0
