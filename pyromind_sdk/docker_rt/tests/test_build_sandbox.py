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
import bz2
import gzip
import io
import json
import lzma
import re
import tarfile
from types import SimpleNamespace
from typing import Any

import pytest

from ..backend import build_sandbox, context_staging

GOOD_DIGEST = "sha256:" + "a" * 64

#: The push password every test uses. Deliberately distinctive: tests assert it
#: never reaches the sandbox argv in clear text, and "pat" is a substring of the
#: ``--tar-path`` flag name, which made that assertion a false positive.
PUSH_PASSWORD = "registry-pw-9c1f"


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
        "DOCKER_RT_BUILD_SANDBOX_SWEEP",
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
    # The push-reachability probe adds an exec right after the sandbox is ready,
    # which would shift every scripted response queue. Tests that are about the
    # probe set this back to ``fail``/``warn`` themselves.
    monkeypatch.setenv("DOCKER_RT_BUILD_PUSH_CHECK", "off")


def _set_push_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Satisfy the push-credential precondition.

    ``build_executor()`` has a default and the credential check is a hard
    pre-sandbox error, so any test that wants to reach the sandbox at all must
    call this — otherwise it aborts on "No push credentials configured" before
    the code under test ever runs.
    """
    monkeypatch.setenv("DOCKER_RT_REGISTRY_USERNAME", "lvniqi")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_PASSWORD", PUSH_PASSWORD)


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

    ``build_executor()`` always resolves something (a per-cluster default), so the
    only way to exercise that guard is to patch the accessor itself.
    """
    from ..backend import build_sandbox

    monkeypatch.setattr(build_sandbox, "build_executor", lambda: "")


def _images_mount() -> dict[str, Any]:
    """The archive directory mount every build requests."""
    return {
        "Source": "/workspace/docker_images",
        "Target": "/workspace/docker_images",
        "ReadOnly": False,
    }


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


def test_build_executor_follows_the_cluster(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default must be per cluster, not one hard-coded registry.

    A node can only start a sandbox from an image it can pull, and the two mirrors
    are mutually unreachable: the Shanghai ACR address is VPC-internal, Docker Hub
    cannot be reached from Shanghai. One hard-coded default therefore breaks every
    build on the other cluster with an image-pull failure.

    The mapping lives in ``build_executor`` itself (one function, one place).
    """
    from ..backend import build_sandbox

    hub = "docker.io/pyrominddynamics/kaniko-executor-pyromind:0.0.4"
    acr = (
        "pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind/"
        "kaniko-executor-pyromind:0.0.4"
    )

    for cluster, expected in (
        ("", hub),  # unknown cluster: the Docker Hub mirror
        ("us-west-1", hub),
        ("us-west-1#pre", hub),
        ("us-west-2", hub),
        ("cn-east-1", acr),
        ("cn-east-1#pre", acr),
        ("cn-east-1#pre2", acr),
    ):
        _clear(monkeypatch)
        if cluster:
            monkeypatch.setenv("PYROMIND_CLUSTER", cluster)
        assert build_sandbox.build_executor() == expected, cluster

    # The daemon-level variable wins over the platform one, and the stage suffix
    # is dropped in both.
    _clear(monkeypatch)
    monkeypatch.setenv("PYROMIND_CLUSTER", "us-west-1#pre")
    monkeypatch.setenv("DOCKER_RT_CLUSTER", "cn-east-1#pre2")
    assert build_sandbox.build_executor() == acr


def test_the_build_executor_ignores_the_push_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Picking the builder image is not a registry concern.

    ``DOCKER_RT_REGISTRY_CLUSTER`` exists to choose a push profile; the builder
    image answers a different question ("which registry can this cluster's nodes
    pull from"). Sharing that variable would make a push-profile tweak silently
    move every build onto an image the cluster cannot reach.
    """
    from ..backend import build_sandbox

    hub = "docker.io/pyrominddynamics/kaniko-executor-pyromind:0.0.4"

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "cn-east-1")
    assert build_sandbox.build_executor() == hub  # not the ACR mirror

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_REGISTRY_CLUSTER", "cn-east-1")
    monkeypatch.setenv("PYROMIND_CLUSTER", "cn-east-1")
    assert "cn-shanghai" in build_sandbox.build_executor()  # the platform cluster decides

    # A kube context is not a cluster identity here either.
    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_KUBE_CONTEXT", "arn:aws:eks:cn-east-1:1:cluster/x")
    assert build_sandbox.build_executor() == hub


def test_build_executor_still_honours_the_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("PYROMIND_CLUSTER", "cn-east-1")
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "my.registry/builders/kaniko:v9-debug")
    assert build_sandbox.build_executor() == "my.registry/builders/kaniko:v9-debug"


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
    monkeypatch.setenv("DOCKER_RT_REGISTRY_PASSWORD", PUSH_PASSWORD)

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
    assert PUSH_PASSWORD not in script

    assert events[-1]["docker_rt"]["digest"] == GOOD_DIGEST
    assert "--destination=reg.example.com/rt/myapp:latest" in script
    # The same build also archives the image into the mounted workspace dir.
    assert (
        "--tar-path=/workspace/docker_images/reg.example.com_rt_myapp_latest.tar"
        in script
    )
    assert PUSH_PASSWORD not in script

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
    assert PUSH_PASSWORD not in script  # the password is inlined only as base64

    final = events[-1]
    assert (
        final["docker_rt"]["aliases"]["docker.io/lvniqi/pyromind-console:dev"]
        == "docker.io/lvniqi/pyromind-console:dev"
    )
    assert sandbox.cleaned


@pytest.mark.asyncio
async def test_a_failed_push_still_reports_the_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A push failure must not look like "nothing was produced".

    kaniko writes the tarball before it pushes, so the image is on disk even when
    the push fails (which, with ``--skip-push-permission-check``, is now where a
    broken push target surfaces). The daemon checks and says where it is.
    """
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    _set_push_credentials(monkeypatch)

    sandbox = _FakeBuildSandbox(
        stdout="error checking push permission for \"reg.example.com\": i/o timeout\n",
        returncode=1,
    )
    sandbox.exec_output = "yes\n"  # the archive is on disk
    _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(
            tar_bytes=_context_tar(), tags=["myapp"], namespace="ns"
        )
    )
    text = "".join(event.get("stream") or "" for event in events)

    assert [event for event in events if event.get("error")]
    assert (
        "was archived to /workspace/docker_images/reg.example.com_rt_myapp_latest.tar"
        in text
    )
    # The failure still fails the build: the alias must not be registered.
    assert not any(event.get("docker_rt") for event in events)


@pytest.mark.asyncio
async def test_a_failed_build_does_not_claim_an_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Dockerfile that failed wrote no tarball, so no archive line may appear."""
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    _set_push_credentials(monkeypatch)

    sandbox = _FakeBuildSandbox(stdout="error: no space left on device\n", returncode=1)
    sandbox.exec_output = ""  # nothing was written
    _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(
            tar_bytes=_context_tar(), tags=["myapp"], namespace="ns"
        )
    )
    text = "".join(event.get("stream") or "" for event in events)

    assert [event for event in events if event.get("error")]
    assert "was archived to" not in text


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


class _Clock:
    """A hand-wound ``time.monotonic``, so silence can be asserted exactly."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now


class _TickingSandbox(_FakeBuildSandbox):
    """Advances the fake clock once per exec, so the poll loop sees time pass.

    The real clock cannot be used: the heartbeat and probe thresholds are tens
    of seconds apart, and a test must not wait for them.
    """

    def __init__(self, clock: _Clock, step: float, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._clock = clock
        self._step = step

    async def iter_exec_stream(self, cmd: Any, **kwargs: Any):
        async for chunk in super().iter_exec_stream(cmd, **kwargs):
            yield chunk
        self._clock.now += self._step


async def _build_with_clock(
    monkeypatch: pytest.MonkeyPatch,
    *,
    step: float,
    responses: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], _TickingSandbox]:
    """Drive one build on a fake clock and return its emitted text."""
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    monkeypatch.setenv("DOCKER_RT_BUILD_POLL_INTERVAL_S", "0.001")
    _set_push_credentials(monkeypatch)

    clock = _Clock()
    monkeypatch.setattr(build_sandbox, "time", clock)
    sandbox = _TickingSandbox(clock, step, responses=list(responses))
    _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(
            tar_bytes=_context_tar(), tags=["myapp"], namespace="ns"
        )
    )
    return "".join(event.get("stream") or "" for event in events), events, sandbox


@pytest.mark.asyncio
async def test_silence_heartbeat_reports_total_silence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The heartbeat must report how long the log has really been quiet.

    Regression: the message used to re-arm its own timer on every print, so it
    always said "15-16s" — a build silent for twelve minutes read as "output is
    still trickling in". That is what got a genuine hang misdiagnosed.
    """
    from ..backend import build_sandbox

    monkeypatch.setattr(build_sandbox, "SILENCE_PROBE_AFTER_S", 1e9)  # probe off
    text, events, _ = await _build_with_clock(
        monkeypatch,
        step=20.0,
        responses=[
            _launch(),
            _poll(""),
            _poll(""),
            _poll(f"docker-rt-digest: {GOOD_DIGEST}\n", state="done rc=0"),
        ],
    )

    # First heartbeat: silence has crossed the threshold once.
    assert "no new log output for 20s" in text, text
    # Second heartbeat: a *larger* total, not the interval all over again.
    assert "no new log output for 40s" in text, text
    assert not [event for event in events if event.get("error")], events


@pytest.mark.asyncio
async def test_a_silent_build_is_sampled_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silence alone cannot tell "busy" from "stuck" — so the daemon measures.

    kaniko writes nothing while it snapshots, so the verdict has to come from
    somewhere other than its log: a ``/proc`` sample of the executor itself.
    """
    from ..backend import build_sandbox

    monkeypatch.setattr(build_sandbox, "SILENCE_PROBE_AFTER_S", 0.0)
    sample = "pid=42 state=R wchan=0 cpu_ticks=298 rss_kb=831488 rchar=0 disk_read=0 write=0\n"
    text, events, sandbox = await _build_with_clock(
        monkeypatch,
        step=20.0,
        responses=[
            _launch(),
            _poll(""),
            {"stdout": sample},
            _poll(f"docker-rt-digest: {GOOD_DIGEST}\n", state="done rc=0"),
        ],
    )

    assert "live sample: pid=42 state=R" in text, text
    assert "kaniko has produced nothing for 20s" in text, text
    # A first sample has nothing to diff against, so it stays a plain reading...
    assert "over " not in text.split("live sample:")[-1], text
    # ...and the sample must come from the probe, not be invented by the daemon.
    assert any("cpu_ticks=" in script for script in sandbox.scripts), sandbox.scripts
    assert not [event for event in events if event.get("error")], events


def test_describe_sample_reports_rates_not_counters() -> None:
    """Two readings become a rate; the subtraction cannot live in the sandbox.

    The executor image has no ``awk`` and no ``sleep``, so the probe takes one
    instantaneous reading and the daemon does the arithmetic.
    """
    from ..backend.build_sandbox import _describe_sample

    first = {
        "pid": 42,
        "state": "R",
        "wchan": "0",
        "rss_kb": 1024,
        "cpu_ticks": 100,
        "rchar": 10,
        "disk_read": 20,
        "write": 30,
    }
    walking = dict(first, cpu_ticks=140, rchar=12, disk_read=20, write=30)
    line = _describe_sample(walking, first, seconds=60)

    assert line.startswith("pid=42 state=R wchan=0 rss=1.0 MiB over 60s:"), line
    assert "cpu_ticks=+40" in line, line
    assert "read=+2" in line, line
    assert "disk_read=+0" in line, line
    assert "write=+0" in line, line

    # A single reading still has to say something useful on its own.
    alone = _describe_sample(first, {}, seconds=0)
    assert alone == "pid=42 state=R wchan=0 rss=1.0 MiB", alone

    # And "no executor" must not look like a hung one.
    missing = _describe_sample({"pid": 0}, first, seconds=60)
    assert "not found" in missing, missing


@pytest.mark.asyncio
async def test_a_failing_probe_does_not_fail_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe runs mid-build; it must never be able to change the outcome."""
    from ..backend import build_sandbox

    monkeypatch.setattr(build_sandbox, "SILENCE_PROBE_AFTER_S", 0.0)

    class _ProbeExplodes(_TickingSandbox):
        async def iter_exec_stream(self, cmd: Any, **kwargs: Any):
            if "cpu_ticks" in cmd[2]:
                raise RuntimeError("websocket went away")
            async for chunk in super().iter_exec_stream(cmd, **kwargs):
                yield chunk

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    monkeypatch.setenv("DOCKER_RT_BUILD_POLL_INTERVAL_S", "0.001")
    _set_push_credentials(monkeypatch)

    clock = _Clock()
    monkeypatch.setattr(build_sandbox, "time", clock)
    sandbox = _ProbeExplodes(
        clock,
        20.0,
        responses=[
            _launch(),
            _poll(""),
            _poll(f"docker-rt-digest: {GOOD_DIGEST}\n", state="done rc=0"),
        ],
    )
    _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(
            tar_bytes=_context_tar(), tags=["myapp"], namespace="ns"
        )
    )
    text = "".join(event.get("stream") or "" for event in events)

    assert not [event for event in events if event.get("error")], events
    # The plain heartbeat still gets through...
    assert "still building" in text, text
    # ...and the dead probe says so, instead of looking like "nothing to report".
    assert "probe ran but produced no sample" in text, text
    assert "RuntimeError" in text, text


def test_push_registry_hosts_finds_the_registry_to_dial() -> None:
    from ..backend.build_sandbox import push_registry_hosts

    # ``docker.io`` is an alias: kaniko dials the index, so the probe must too.
    assert push_registry_hosts(["docker.io/u/i:t"]) == ["index.docker.io:443"]
    # A bare ``name:tag`` has no registry at all — that colon is the tag.
    assert push_registry_hosts(["myimg:latest"]) == ["index.docker.io:443"]
    assert push_registry_hosts(["reg.example.com:5000/a/b:1"]) == ["reg.example.com:5000"]
    assert push_registry_hosts(["localhost:5000/x"]) == ["localhost:5000"]
    # De-duplicated, order preserved.
    assert push_registry_hosts(["a/r:1", "b/r:1", "a/r:2"]) == ["index.docker.io:443"]
    assert push_registry_hosts([]) == []


def test_push_probe_script_never_hardcodes_one_busybox_path() -> None:
    """``/bin`` in a build sandbox is the *target* image's, not the executor's.

    kaniko unpacks the image being built over ``/``; the executor's own busybox
    is at ``/busybox`` (upstream even declares it a ``VOLUME`` so it survives).
    At probe time ``/bin`` contains only ``sh``, so a hardcoded ``/bin/busybox``
    would silently produce nothing.
    """
    from ..backend.build_sandbox import push_probe_script

    script = push_probe_script(["index.docker.io:443"])
    assert "for c in busybox /busybox/busybox /bin/busybox" in script
    assert "run() {" in script
    # Applets go through the shim, never by bare name.
    assert "run nc -z -w " in script
    assert "run awk " in script
    assert "run nslookup " in script
    assert "BB=" not in script
    assert "index.docker.io:443" in script


def test_parse_push_probe_never_invents_a_verdict() -> None:
    from ..backend.build_sandbox import parse_push_probe

    # A probe that produced nothing must never read as "unreachable".
    assert parse_push_probe("") == {}
    assert parse_push_probe("/bin/sh: nc: not found\n") == {}

    got = parse_push_probe(
        "TARGET index.docker.io 443\n"
        "ADDR 69.171.224.36\n"
        "TCP index.docker.io 443 fail\n"
    )
    assert got["index.docker.io:443"] == {
        "host": "index.docker.io",
        "port": "443",
        "ip": "69.171.224.36",
        "tcp": "fail",
    }


def _hints_text(lines: list[str]) -> str:
    return "\n".join(lines)


def test_push_fix_hints_name_the_clusters_own_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """上海集群的报错必须直接给出该设的**具体值**，而不是"换成自家 ACR 吧"。

    用户原话（2026-10-09）："上海集群，推送需要更新哪些环境变量也要提示啊"。
    集群自己的 registry 本来就在 profile 里，所以这里没有理由让用户自己去猜前缀。
    """
    from ..backend.build_sandbox import push_fix_hints

    _clear(monkeypatch)
    monkeypatch.setenv("PYROMIND_CLUSTER", "cn-east-1#pre")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "docker.io/jiangwc3439")

    text = _hints_text(push_fix_hints())
    assert (
        "DOCKER_RT_BUILD_REGISTRY=pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind"
        in text
    )
    # 说清"现在是什么"，否则用户不知道自己错在哪
    assert "docker.io/jiangwc3439" in text
    assert "DOCKER_RT_REGISTRY_USERNAME" in text
    assert "DOCKER_RT_REGISTRY_PASSWORD" in text
    assert "DOCKER_RT_REGISTRY_DOCKERCONFIG" in text
    # 两个退路 + 改完要重启
    assert "DOCKER_RT_BUILD_PUSH=false" in text
    assert "DOCKER_RT_BUILD_PUSH_CHECK=warn" in text
    assert "restart" in text


def test_push_fix_hints_do_not_re_suggest_the_registry_already_in_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已经推自家 ACR 了就别再让人"换成自家 ACR"。"""
    from ..backend.build_sandbox import push_fix_hints

    _clear(monkeypatch)
    monkeypatch.setenv("PYROMIND_CLUSTER", "cn-east-1#pre")
    monkeypatch.setenv(
        "DOCKER_RT_BUILD_REGISTRY",
        "pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind",
    )

    text = _hints_text(push_fix_hints())
    assert "DOCKER_RT_BUILD_REGISTRY=" not in text
    assert "DOCKER_RT_BUILD_PUSH=false" in text


def test_push_fix_hints_warn_that_the_credentials_may_belong_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """凭据是**按 registry** 的：账号是 Docker Hub 的，换到 ACR 就得换一份。"""
    from ..backend.build_sandbox import push_fix_hints

    _clear(monkeypatch)
    monkeypatch.setenv("PYROMIND_CLUSTER", "cn-east-1#pre")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "docker.io/jiangwc3439")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_USERNAME", "jiangwc3439")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_PASSWORD", "hunter2")

    text = _hints_text(push_fix_hints())
    assert "belong to the current target" in text
    assert "username_password" in text  # registry_push 给的来源标识


def test_push_fix_hints_mark_the_dockerconfig_as_optional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``DOCKER_RT_REGISTRY_DOCKERCONFIG`` 是**二选一**的备选，不是必填项。

    2026-10-09 用户把三行读成了"都得配"，原话："这个是可选的吧，，，不用必须配置这个吧"。
    代码里的优先级是 ``USERNAME+PASSWORD`` > 显式 dockerconfig > 默认的
    ``/etc/docker-image/.dockerconfigjson``（存在就用），所以三种情况都不需要全设。
    """
    from ..backend.build_sandbox import push_fix_hints

    _clear(monkeypatch)
    monkeypatch.setenv("PYROMIND_CLUSTER", "cn-east-1#pre")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "docker.io/jiangwc3439")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_USERNAME", "u")
    monkeypatch.setenv("DOCKER_RT_REGISTRY_PASSWORD", "p")

    text = _hints_text(push_fix_hints())
    assert "DOCKER_RT_REGISTRY_DOCKERCONFIG" in text
    assert "only one of the two is needed" in text
    assert "Neither is mandatory" in text
    assert "/etc/docker-image/.dockerconfigjson" in text


def test_push_fix_hints_always_offer_the_escape_hatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """就算集群解析不出来（或推的就是自家 registry），退路也必须在。"""
    from ..backend.build_sandbox import push_fix_hints

    _clear(monkeypatch)  # 完全没有集群信息

    text = _hints_text(push_fix_hints())
    assert "DOCKER_RT_BUILD_PUSH=false" in text
    assert "DOCKER_RT_BUILD_PUSH_CHECK=warn" in text


def test_push_fix_hints_never_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """写不出提示不能把构建带崩 —— 这只该是"多给一行信息"。"""
    from ..backend import registry_push
    from ..backend.build_sandbox import push_fix_hints

    _clear(monkeypatch)

    def boom(*args: object, **kwargs: object) -> str:
        raise RuntimeError("cluster lookup exploded")

    monkeypatch.setattr(registry_push, "current_cluster", boom)
    text = _hints_text(push_fix_hints())
    assert "DOCKER_RT_BUILD_PUSH=false" in text


@pytest.mark.asyncio
async def test_an_unreachable_push_target_stops_before_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two clusters, same image, same code — one just cannot dial the registry.

    That has to be said before the build, not by kaniko's very last line: waiting
    for it makes a *successful* build look like a hung one.
    """
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "docker.io/jiangwc3439")
    monkeypatch.setenv("DOCKER_RT_BUILD_PUSH_CHECK", "fail")
    # 上海集群这个形状：推 Docker Hub，而集群只到得了自家 ACR。
    monkeypatch.setenv("PYROMIND_CLUSTER", "cn-east-1#pre")
    _set_push_credentials(monkeypatch)

    sandbox = _FakeBuildSandbox(
        responses=[
            # The reachability probe execs *before* the launcher.
            {
                "stdout": (
                    "TARGET index.docker.io 443\n"
                    "ADDR 69.171.224.36\n"
                    "TCP index.docker.io 443 fail\n"
                )
            },
        ]
    )
    _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(
            tar_bytes=_context_tar(), tags=["myapp"], namespace="ns"
        )
    )
    errors = [event for event in events if event.get("error")]
    text = "".join(event.get("stream") or "" for event in events)

    assert errors, events
    assert "unreachable from this build sandbox" in errors[0]["error"], errors[0]
    assert "index.docker.io" in errors[0]["error"]
    assert "69.171.224.36" in errors[0]["error"]
    # 报错里要**直接给出该改的环境变量**，不能只说"换成能通的 registry"。
    assert (
        "DOCKER_RT_BUILD_REGISTRY=pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind"
        in errors[0]["error"]
    ), errors[0]
    assert "DOCKER_RT_REGISTRY_USERNAME" in errors[0]["error"]
    assert "DOCKER_RT_BUILD_PUSH=false" in errors[0]["error"]
    assert "DOCKER_RT_BUILD_PUSH_CHECK=warn" in errors[0]["error"]
    # Fail *before* the expensive part: no context copy, no kaniko.
    assert "Starting kaniko" not in text, text


@pytest.mark.asyncio
async def test_a_reachable_push_target_lets_the_build_proceed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    monkeypatch.setenv("DOCKER_RT_BUILD_PUSH_CHECK", "fail")
    _set_push_credentials(monkeypatch)

    sandbox = _FakeBuildSandbox(
        responses=[
            # probe (before the launcher), then launch, then one poll.
            {
                "stdout": (
                    "TARGET reg.example.com 443\n"
                    "ADDR 10.0.0.1\n"
                    "TCP reg.example.com 443 ok\n"
                )
            },
            _launch(),
            _poll(f"docker-rt-digest: {GOOD_DIGEST}\n", state="done rc=0"),
        ]
    )
    _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(
            tar_bytes=_context_tar(), tags=["myapp"], namespace="ns"
        )
    )
    text = "".join(event.get("stream") or "" for event in events)

    assert "tcp=ok" in text, text
    assert "Starting kaniko" in text, text
    assert not [event for event in events if event.get("error")], events


@pytest.mark.asyncio
async def test_warn_mode_builds_even_when_the_push_target_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The archive is still worth having, so there has to be a way to say so."""
    from ..backend import build_sandbox

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:1-debug")
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "docker.io/jiangwc3439")
    monkeypatch.setenv("DOCKER_RT_BUILD_PUSH_CHECK", "warn")
    _set_push_credentials(monkeypatch)

    sandbox = _FakeBuildSandbox(
        responses=[
            {"stdout": "TARGET index.docker.io 443\nTCP index.docker.io 443 fail\n"},
            _launch(),
            _poll(f"docker-rt-digest: {GOOD_DIGEST}\n", state="done rc=0"),
        ]
    )
    _install_fake(monkeypatch, sandbox)

    events = await _collect(
        build_sandbox.build_in_sandbox(
            tar_bytes=_context_tar(), tags=["myapp"], namespace="ns"
        )
    )
    text = "".join(event.get("stream") or "" for event in events)

    assert not [event for event in events if event.get("error")], events
    assert "unreachable from this build sandbox" in text, text
    assert "Starting kaniko" in text, text


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

    # The sandbox was created with that directory mounted, and writable — plus
    # the archive directory the image is written to.
    assert captured["mounts"] == [
        _images_mount(),
        context_staging.mount_spec(stager.plan),
    ]

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
        mounts = kwargs.get("mounts") or []
        attempts.append(mounts)
        # Only the staged-context mount is refused — that is the cluster-layout
        # dependency; the archive directory is a plain workspace path.
        if any(".docker-rt-build" in str(m.get("Source", "")) for m in mounts):
            raise RuntimeError("subPath .docker-rt-build not found")
        return sandbox

    monkeypatch.setattr(runtime, "start_kube_environment", fake_start)

    events = await _collect(
        build_sandbox.build_in_sandbox(tar_bytes=context, tags=["myapp"], namespace="ns")
    )
    text = "".join(event.get("stream", "") for event in events)
    stager = staging.stager

    # The retry drops the staged-context mount and keeps the archive directory:
    # without the former the build still works, without the latter the image has
    # nowhere to land.
    assert attempts == [
        [_images_mount(), context_staging.mount_spec(stager.plan)],
        [_images_mount()],
    ]
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
    # No staged-context mount, but the archive directory still is one: the image
    # has to land somewhere even when the context takes the slow route.
    assert captured["mounts"] == [_images_mount()]
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

    assert captured["mounts"] == [_images_mount()]
    assert len(sandbox.archives) == 1
    assert any(event.get("docker_rt") for event in events)
    assert staging.count == 0


def test_the_build_sandbox_mounts_the_images_directory() -> None:
    """Mount source and target are the same path, and it is kaniko's constant.

    Mounting it at the workspace path (rather than somewhere kaniko-ish) is safe:
    kaniko adds every mount point from ``/proc/self/mountinfo`` to its ignore list,
    and ``DeleteFilesystem`` skips those directories wholesale, so the wipe it does
    between the stages of a multi-stage build cannot reach the user's images.
    """
    from ..backend import build_sandbox, kaniko

    spec = build_sandbox.images_mount_spec()
    assert spec == {
        "Source": "/workspace/docker_images",
        "Target": "/workspace/docker_images",
        "ReadOnly": False,
    }
    # One path for both sides, so the write target needs no translation.
    assert spec["Source"] == spec["Target"] == kaniko.DEFAULT_IMAGES_DIR


# --------------------------------------------------------------------------
# build-sandbox identity, and sweeping what a `kill -9` leaves running
# --------------------------------------------------------------------------


class _FakeSandboxListing:
    """A stand-in for ``AsyncSandboxClient`` covering only what the sweep uses."""

    def __init__(
        self,
        sandboxes: list[Any] | None = None,
        *,
        list_error: Exception | None = None,
        delete_error: Exception | None = None,
    ) -> None:
        self.sandboxes = list(sandboxes or [])
        self.list_error = list_error
        self.delete_error = delete_error
        self.deleted: list[str] = []
        self.closed = False

    async def list(self) -> list[Any]:
        if self.list_error is not None:
            raise self.list_error
        return list(self.sandboxes)

    async def delete(self, sandbox_id: str, **_kwargs: Any) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append(sandbox_id)

    async def close(self) -> None:
        self.closed = True


def _sandbox(name: str) -> SimpleNamespace:
    return SimpleNamespace(id=f"sb-{name}", name=name)


def _build_name(suffix: str = "a1b2c3") -> str:
    """A build-sandbox name shaped the way this module mints them."""
    return f"{build_sandbox.BUILD_SANDBOX_NAME_STEM}{suffix}"


def test_build_sandbox_names_are_unique_and_carry_the_prefix() -> None:
    """The name is the only handle the post-crash cleanup has on a sandbox."""
    name = build_sandbox.new_build_sandbox_name()
    assert name != build_sandbox.new_build_sandbox_name()
    assert build_sandbox.is_build_sandbox_name(name)


def test_build_sandbox_names_are_dns_safe() -> None:
    """It becomes a Pod name, so it has to survive the DNS-1123 rules."""
    name = build_sandbox.new_build_sandbox_name()
    assert re.fullmatch(r"[a-z0-9-]+", name)
    assert len(name) <= 63


def test_the_ownership_rule_is_the_prefix_and_nothing_else() -> None:
    """What a sweep may delete is decided here, so both edges are pinned.

    The user's own sandboxes are the case that matters: the platform labels a
    nameless one ``SANDBOX-<uuid>``, and it must never look deletable.
    """
    ours = [
        _build_name(),
        _build_name("012345"),
        _build_name("ABCdef"),
        # Names minted by the previous scheme still carry the prefix — which is
        # what lets this sweep reach leftovers from before it changed.
        _build_name("old-4242-host-a1b2c3"),
    ]
    not_ours = [
        "",
        None,
        "my-web-sandbox",
        "SANDBOX-2f1a9c",
        # The bare prefix, and a longer word that merely starts the same way.
        build_sandbox.BUILD_SANDBOX_NAME_PREFIX,
        f"{build_sandbox.BUILD_SANDBOX_NAME_PREFIX}x-a1b2c3",
        f"other-{_build_name()}",
    ]
    for name in ours:
        assert build_sandbox.is_build_sandbox_name(name) is True, name
    for name in not_ours:
        assert build_sandbox.is_build_sandbox_name(name) is False, name


def test_the_ownership_rule_ignores_surrounding_whitespace() -> None:
    """A padded name is still ours; the platform gives back what it stored."""
    assert build_sandbox.is_build_sandbox_name(f"  {_build_name()}  ") is True


@pytest.mark.asyncio
async def test_build_in_sandbox_names_the_sandbox_it_creates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unnamed sandbox is never swept, so the create has to name it."""
    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY", "reg.example.com/rt")
    _set_push_credentials(monkeypatch)
    sandbox = _FakeBuildSandbox(stdout=f"docker-rt-digest: {GOOD_DIGEST}\n")
    captured = _install_fake(monkeypatch, sandbox)

    await _collect(
        build_sandbox.build_in_sandbox(
            tar_bytes=_context_tar(), tags=["myapp"], namespace="ns"
        )
    )

    assert build_sandbox.is_build_sandbox_name(captured["container_name"])
    assert captured["container_name"] != build_sandbox.BUILD_SANDBOX_NAME_PREFIX



@pytest.mark.asyncio
async def test_sweep_deletes_every_build_sandbox_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prefix is the entire rule — which is exactly why it is pinned here."""
    _clear(monkeypatch)
    first = _build_name("a1b2c3")
    second = _build_name("d4e5f6")
    client = _FakeSandboxListing(
        [
            _sandbox(first),
            _sandbox("my-web-sandbox"),
            _sandbox("SANDBOX-2f1a9c"),
            _sandbox(second),
            SimpleNamespace(id="sb-9", name=None),
        ]
    )

    removed = await build_sandbox.sweep_stale_build_sandboxes(client=client)

    assert removed == [first, second]
    assert client.deleted == [f"sb-{first}", f"sb-{second}"]


@pytest.mark.asyncio
async def test_sweep_deletes_a_sandbox_a_live_build_owns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cost of a prefix rule, stated on purpose rather than discovered later.

    ``list()`` is scoped to the account, not to this machine, so the sweep
    cannot tell a leaked sandbox from one a *live* build — here or on another
    machine — is using. It deletes both. A build that loses its sandbox fails
    visibly and can be re-run, whereas a leaked one burns quota for ever, and
    that is the trade this rule is making.
    """
    _clear(monkeypatch)
    name = _build_name()
    client = _FakeSandboxListing([_sandbox(name)])

    assert await build_sandbox.sweep_stale_build_sandboxes(client=client) == [name]
    assert client.deleted == [f"sb-{name}"]


@pytest.mark.asyncio
async def test_sweep_ignores_names_it_cannot_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Everything the user named — or the platform named for them — is left alone."""
    _clear(monkeypatch)
    client = _FakeSandboxListing(
        [
            _sandbox("my-own-sandbox"),
            _sandbox("SANDBOX-2f1a9c"),
            _sandbox(""),
            SimpleNamespace(id="sb-1", name=None),
        ]
    )

    assert await build_sandbox.sweep_stale_build_sandboxes(client=client) == []
    assert client.deleted == []


@pytest.mark.asyncio
async def test_sweep_reports_a_list_that_came_back_entirely_nameless(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unattributable list must be visible, because it looks like a clean one.

    Every sandbox the platform returns carries a name — it mints a default label
    when the caller gave none. So a list in which nothing is named means ``name``
    is no longer reaching us, and the sweep would then delete nothing, for ever,
    with no other trace of why.
    """
    _clear(monkeypatch)
    client = _FakeSandboxListing(
        [SimpleNamespace(id="sb-1", name=None), SimpleNamespace(id="sb-2", name="")]
    )

    with caplog.at_level("WARNING", logger="docker_rt.build_sandbox"):
        removed = await build_sandbox.sweep_stale_build_sandboxes(client=client)

    assert removed == []
    assert "without a name" in caplog.text


@pytest.mark.asyncio
async def test_sweep_stays_quiet_when_the_list_is_merely_not_ours(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Named sandboxes belonging to the user are the ordinary case, not a fault."""
    _clear(monkeypatch)
    client = _FakeSandboxListing([_sandbox("my-own-sandbox")])

    with caplog.at_level("WARNING", logger="docker_rt.build_sandbox"):
        removed = await build_sandbox.sweep_stale_build_sandboxes(client=client)

    assert removed == []
    assert "without a name" not in caplog.text


@pytest.mark.asyncio
async def test_sweep_is_disabled_by_the_knob(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_SANDBOX_SWEEP", "false")
    client = _FakeSandboxListing([_sandbox(_build_name())])

    assert await build_sandbox.sweep_stale_build_sandboxes(client=client) == []
    assert client.deleted == []


@pytest.mark.asyncio
async def test_sweep_honours_keep_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    """``KEEP`` leaves a sandbox behind on purpose — even after a crash.

    It is also the escape hatch for the account-wide reach of the prefix rule: a
    machine that must not touch anyone else's builds sets it.
    """
    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_SANDBOX_KEEP", "true")
    client = _FakeSandboxListing([_sandbox(_build_name())])

    assert await build_sandbox.sweep_stale_build_sandboxes(client=client) == []
    assert client.deleted == []


@pytest.mark.asyncio
async def test_sweep_survives_a_list_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreachable API server must not turn into a watcher failure."""
    _clear(monkeypatch)
    client = _FakeSandboxListing(list_error=RuntimeError("no route to host"))

    assert await build_sandbox.sweep_stale_build_sandboxes(client=client) == []


@pytest.mark.asyncio
async def test_sweep_continues_after_a_delete_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    first = _build_name("a1b2c3")
    second = _build_name("d4e5f6")

    class _Flaky(_FakeSandboxListing):
        async def delete(self, sandbox_id: str, **_kwargs: Any) -> None:
            if sandbox_id == f"sb-{first}":
                raise RuntimeError("409 conflict")
            self.deleted.append(sandbox_id)

    client = _Flaky([_sandbox(first), _sandbox(second)])

    assert await build_sandbox.sweep_stale_build_sandboxes(client=client) == [second]


@pytest.mark.asyncio
async def test_sweep_skips_a_sandbox_without_an_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    client = _FakeSandboxListing([SimpleNamespace(id="", name=_build_name())])

    assert await build_sandbox.sweep_stale_build_sandboxes(client=client) == []
    assert client.deleted == []


@pytest.mark.asyncio
async def test_sweep_closes_a_client_it_opened_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The watcher is a short-lived process: it must not leak its connection pool."""
    from ..backend import pyromind_sdk_env

    _clear(monkeypatch)
    client = _FakeSandboxListing([_sandbox(_build_name())])
    monkeypatch.setattr(pyromind_sdk_env, "get_sandbox_client", lambda: client)

    removed = await build_sandbox.sweep_stale_build_sandboxes()

    assert removed
    assert client.closed is True


@pytest.mark.asyncio
async def test_sweep_leaves_a_client_it_was_given_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ownership is per-call: the daemon's long-lived client is not ours to close."""
    _clear(monkeypatch)
    client = _FakeSandboxListing([_sandbox(_build_name())])

    await build_sandbox.sweep_stale_build_sandboxes(client=client)

    assert client.closed is False


def test_a_host_only_acr_prefix_is_rejected_before_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ACR 的推送前缀少了命名空间 → **构建之前**就拒绝，别等构建跑完才 401。

    2026-10-09 用户的真实场景：`DOCKER_RT_BUILD_REGISTRY` 只给了 host，
    于是推 `…/pyromind-console:dev-5`（少了 `/pyromind`），kaniko 91 秒全成功、
    最后一行 `401 Unauthorized`；同一个 host 的 `docker login` 却是成功的。
    """
    from ..backend.build_sandbox import build_prerequisites_error

    _clear(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_IMAGE", "reg.example.com/rt/kaniko:v1-debug")
    monkeypatch.setenv("PYROMIND_CLUSTER", "cn-east-1#pre")
    monkeypatch.setenv(
        "DOCKER_RT_BUILD_REGISTRY", "pyromind-registry.cn-shanghai.cr.aliyuncs.com"
    )
    _set_push_credentials(monkeypatch)

    message = build_prerequisites_error(["pyromind-console:dev-5"], push=True)
    assert message
    assert "carries no namespace" in message
    assert "401" in message
    # 命名空间由**参数**给，代码不替用户填
    assert "Nothing is appended for you" in message

    # 补上命名空间就该放行
    monkeypatch.setenv(
        "DOCKER_RT_BUILD_REGISTRY",
        "pyromind-registry-vpc.cn-shanghai.cr.aliyuncs.com/pyromind",
    )
    assert build_prerequisites_error(["pyromind-console:dev-5"], push=True) is None


def test_push_rejected_hint_explains_an_acr_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """kaniko 的推送 401 只有状态码，这里要把可能的原因点出来。"""
    from ..backend.build_sandbox import push_rejected_hint

    _clear(monkeypatch)
    monkeypatch.setenv("PYROMIND_CLUSTER", "cn-east-1#pre")
    monkeypatch.setenv(
        "DOCKER_RT_BUILD_REGISTRY",
        "pyromind-registry.cn-shanghai.cr.aliyuncs.com/pyromind",
    )
    _set_push_credentials(monkeypatch)

    raw = (
        "INFO[0090] Pushing image to pyromind-registry.cn-shanghai.cr.aliyuncs.com"
        "/pyromind/pyromind-console:dev-5 \n"
        "error pushing image: failed to push to destination …: unexpected status "
        "code 401 Unauthorized (HEAD responses have no body, use GET for details)\n"
    )
    hint = push_rejected_hint(raw)
    assert hint
    assert "401/403" in hint
    # 说清"仓库路径"和"凭据属于哪个 host"这两件事
    assert "repo path must exist" in hint
    assert "the namespace is 'pyromind'" in hint
    assert "credentials must be valid for that host" in hint
    assert "username_password" in hint


def test_push_rejected_hint_stays_quiet_otherwise() -> None:
    """不是推送 401/403 就别插话。"""
    from ..backend.build_sandbox import push_rejected_hint

    assert push_rejected_hint("") is None
    # Dockerfile 自己失败：没有推送失败这回事
    assert push_rejected_hint("INFO[0012] error building image: exit status 1") is None
    # 推送失败但不是鉴权问题（比如超时）—— 那条由预检/网络提示管，别抢话
    assert (
        push_rejected_hint(
            "error pushing image: … dial tcp 104.244.46.5:443: i/o timeout"
        )
        is None
    )


def test_the_prefix_is_used_verbatim_no_namespace_is_ever_appended() -> None:
    """``registry`` 是逐字用的：告诉我们前缀是什么，我们就拼什么。

    用户 2026-10-09 明确要求："不要默认加吧，还像以前一样，在参数中，不然这样以后
    换命名空间或者推送不同的命名空间还要改代码"。所以这里把"代码不会替你补命名空间"
    钉成断言 —— 检查只负责**拒绝**一个没有命名空间的 ACR 前缀，绝不改写它。
    """
    from ..backend.build_sandbox import resolve_targets

    # 自定义命名空间：原样用，不会被换成 profile 的默认值
    aliases, error = resolve_targets(
        ["app:1"], registry="pyromind-registry.cn-shanghai.cr.aliyuncs.com/other-ns"
    )
    assert error is None
    assert aliases["app:1"] == (
        "pyromind-registry.cn-shanghai.cr.aliyuncs.com/other-ns/app:1"
    )

    # 多级命名空间也原样保留
    aliases, error = resolve_targets(
        ["app:1"], registry="reg.example.com/team/sub"
    )
    assert aliases["app:1"] == "reg.example.com/team/sub/app:1"

    # 就算前缀里**没有**命名空间，这里也照样原样拼 —— 是 build_prerequisites_error
    # 负责在构建之前把它拦下来，而不是这一层偷偷补上
    aliases, error = resolve_targets(
        ["app:1"], registry="pyromind-registry.cn-shanghai.cr.aliyuncs.com"
    )
    assert aliases["app:1"] == "pyromind-registry.cn-shanghai.cr.aliyuncs.com/app:1"


def test_push_rejected_hint_points_at_the_missing_acr_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ACR 对"仓库不存在"也回 401 —— 建仓被跳过时要把这条说出来。

    2026-10-09 用户的第二类 401：他把 ``DOCKER_RT_ACR_ACCESS_KEY_SECRET`` 写成了
    ``DOCKER_RT_ACR_SECRET``，建仓那步被静默跳过，构建跑完 216 秒后 ACR 回
    ``UNAUTHORIZED: authentication required``（仓库 ``pyromind/pyromind-console-1`` 不存在）。
    """
    from ..backend.build_sandbox import push_rejected_hint

    _clear(monkeypatch)
    monkeypatch.setenv("PYROMIND_CLUSTER", "cn-east-1#pre")
    monkeypatch.setenv(
        "DOCKER_RT_BUILD_REGISTRY",
        "pyromind-registry.cn-shanghai.cr.aliyuncs.com/pyromind",
    )
    _set_push_credentials(monkeypatch)
    # 名字写错了：ID 和实例 ID 都对，只有 secret 那个变量不存在
    monkeypatch.setenv("DOCKER_RT_ACR_ACCESS_KEY_ID", "LTAI5tG5os7P4Dqyasfmnwhe")
    monkeypatch.setenv("DOCKER_RT_ACR_INSTANCE_ID", "cri-3a7k1rh8eajwcae8")
    monkeypatch.setenv("DOCKER_RT_ACR_SECRET", "typo")

    raw = (
        "error pushing image: failed to push to destination "
        "pyromind-registry.cn-shanghai.cr.aliyuncs.com/pyromind/pyromind-console-1:dev-5: "
        "POST https://pyromind-registry.cn-shanghai.cr.aliyuncs.com/v2/pyromind/"
        "pyromind-console-1/blobs/uploads/: UNAUTHORIZED: authentication required\n"
    )
    hint = push_rejected_hint(
        raw, ["pyromind-registry.cn-shanghai.cr.aliyuncs.com/pyromind/pyromind-console-1:dev-5"]
    )
    assert hint
    assert "pyromind/pyromind-console-1:dev-5" in hint  # 精确指出推的是哪个仓库
    assert "created *first*" in hint
    assert "DOCKER_RT_ACR_ACCESS_KEY_SECRET" in hint
    # 顺带把"你还有个变量是错的"也带上
    assert "did you mean" in hint or "unrecognised" in hint


# --------------------------------------------------------------------------
# build context：客户端可能已经压过它
# --------------------------------------------------------------------------


def _a_real_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, content in (("Dockerfile", b"FROM scratch\n"), ("a.txt", b"hello\n")):
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


@pytest.mark.parametrize(
    "compress",
    [
        pytest.param(gzip.compress, id="gzip"),
        pytest.param(bz2.compress, id="bzip2"),
        pytest.param(lzma.compress, id="xz"),
    ],
)
def test_pack_build_context_unwraps_a_compressed_context(compress) -> None:
    """客户端发来的 context 可能**已经压过**，必须先解开再压给 kaniko。

    2026-10-09/10 实测：`docker compose build`（classic builder + `--compress`，
    或 context 写成 `.tar.gz` URL）发来一个 772 B 的 gzip 流，我们**又压了一层**，
    kaniko 于是只报 `error resolving source context: archive/tar: invalid tar header`，
    0.1 秒就挂 —— 看起来完全不像压缩问题。moby 的 daemon 是靠 magic 嗅探解压的
    （`archive.DecompressStream`），我们也得这么做。
    """
    from ..backend.build_sandbox import pack_build_context

    original = _a_real_tar()
    packed = pack_build_context(compress(original))
    # kaniko 拿到的是**单层** gzip 的真实 tar
    assert gzip.decompress(packed) == original


def test_pack_build_context_leaves_a_plain_tar_alone() -> None:
    """没压过的 context 原样处理（别把正常路径弄坏）。"""
    from ..backend.build_sandbox import pack_build_context

    original = _a_real_tar()
    assert gzip.decompress(pack_build_context(original)) == original


def test_context_compression_sniffs_the_families_moby_supports() -> None:
    from ..backend.build_sandbox import context_compression

    assert context_compression(b"") == ""
    assert context_compression(_a_real_tar()) == ""
    assert context_compression(gzip.compress(b"x")) == "gzip"
    assert context_compression(bz2.compress(b"x")) == "bzip2"
    assert context_compression(lzma.compress(b"x")) == "xz"
    assert context_compression(b"\x28\xb5\x2f\xfd" + b"junk") == "zstd"


def test_an_unsupported_context_compression_fails_with_something_actionable() -> None:
    """解不开的时候不能产出"坏 tar"，要直接说清怎么办。"""
    from ..backend.build_sandbox import pack_build_context

    # zstd：本仓库跑的 Python(<3.14) 没有自带支持时，报错必须点名这件事和退路
    with pytest.raises(ValueError) as caught:
        pack_build_context(b"\x28\xb5\x2f\xfd" + b"\x00" * 32)
    text = str(caught.value)
    assert "zstd" in text
    assert "--compress=false" in text or "zstandard" in text
