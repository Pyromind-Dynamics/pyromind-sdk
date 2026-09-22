"""kaniko helpers: assemble the in-sandbox build command.

kaniko runs as a plain unprivileged container: it unpacks the base image into
*its own* container root and executes each Dockerfile instruction in userspace,
so it needs neither a daemon, nor ``CAP_SYS_ADMIN``, nor a privileged pod.

Three consequences that shape everything in this module:

* kaniko **must** run in a throwaway container — it overwrites whatever is at
  ``/`` in the container it runs in (see :mod:`docker_rt.backend.build_sandbox`).
* kaniko is only supported as the official ``kaniko-project/executor`` image,
  and the template pins ``command: ["sleep", "infinity"]``, so the executor is
  invoked by the script below over the exec channel rather than as the image
  entrypoint.
* the build **outlives the exec call that starts it** (see :func:`start_script`),
  so it has to report its own state from files on disk rather than by exiting.

The detached protocol, all of it inside the sandbox workdir::

    build.sh        the worker: runs kaniko, writes build.rc on the way out
    build.launched  the launcher finished forking (missing ⇒ the start failed)
    build.pid       PID of the worker shell (``kill -0`` ⇒ still alive)
    build.rc        worker's exit code; missing ⇒ never finished (killed / OOM)
    build.log       stdout + stderr of the worker, for the poller to tail
    image-digest    kaniko's ``--digest-file``

Everything here is a pure function so it can be unit-tested without a cluster.
"""

from __future__ import annotations

import base64
import logging
import os
import shlex

logger = logging.getLogger("docker_rt.kaniko")

DEFAULT_KANIKO_BIN = "/kaniko/executor"
# MUST stay under ``/kaniko``: kaniko deletes the container's root filesystem
# when it moves on to the next stage of a multi-stage build
# (``util.DeleteFilesystem``), and ``/kaniko`` is the one tree it preserves —
# that is where it keeps its own binary, its ``.docker/config.json`` and the
# extracted build context (``/kaniko/buildcontext``). A workdir under ``/tmp``
# is wiped mid-build by design: the launcher's files vanish, and the poller
# reports the misleading "the launcher did not reach the fork".
DEFAULT_WORKDIR = "/kaniko/docker-rt-build"
DEFAULT_DOCKER_CONFIG_DIR = "/kaniko/.docker"
DEFAULT_CONTEXT_ARCHIVE = "context.tar.gz"

DEFAULT_BUILD_SCRIPT = "build.sh"
DEFAULT_BUILD_LOG = "build.log"
DEFAULT_EXIT_CODE = "build.rc"
DEFAULT_PID = "build.pid"
DEFAULT_LAUNCHED = "build.launched"

# Machine-readable markers. The digest marker was already load-bearing (a
# truncated log must never look like success); the status marker lives on the
# exec's *stderr* channel so the poller can separate control from log bytes
# without inventing a separator the build log could collide with.
DIGEST_MARKER = "docker-rt-digest:"
STATUS_MARKER = "docker-rt-status:"

_TRUTHY = {"1", "true", "yes", "on"}


def _flag(name: str, default: str = "") -> bool:
    return (os.getenv(name) or default).strip().lower() in _TRUTHY


def kaniko_bin() -> str:
    """Executor binary inside the builder image."""
    return (os.getenv("DOCKER_RT_KANIKO_BIN") or "").strip() or DEFAULT_KANIKO_BIN


def build_workdir() -> str:
    """Scratch dir inside the builder sandbox."""
    return (os.getenv("DOCKER_RT_BUILD_CONTEXT_DIR") or "").strip() or DEFAULT_WORKDIR


def docker_config_dir() -> str:
    """Directory kaniko reads ``config.json`` from.

    kaniko resolves registry credentials through ``$DOCKER_CONFIG`` (default
    ``/kaniko/.docker``). This is *not* buildctl's ``$DOCKER_CONFIG`` path, but
    the env var name is the same.
    """
    return (os.getenv("DOCKER_RT_KANIKO_DOCKER_CONFIG_DIR") or "").strip() or (
        DEFAULT_DOCKER_CONFIG_DIR
    )


def context_archive_name() -> str:
    return (os.getenv("DOCKER_RT_KANIKO_CONTEXT_ARCHIVE") or "").strip() or (
        DEFAULT_CONTEXT_ARCHIVE
    )


def context_archive_path() -> str:
    return f"{build_workdir()}/{context_archive_name()}"


def context_uri() -> str:
    """kaniko ``--context`` value.

    We ship the build context as a single gzipped tar (kaniko's "local tar"
    scheme) because that is one file transfer instead of one per context file.
    """
    return f"tar://{context_archive_path()}"


def digest_file_path() -> str:
    return f"{build_workdir()}/image-digest"


def worker_script_path() -> str:
    """The worker shell script the launcher writes and then detaches."""
    return f"{build_workdir()}/{DEFAULT_BUILD_SCRIPT}"


def build_log_path() -> str:
    """Worker stdout + stderr; the poller tails this by byte offset."""
    return f"{build_workdir()}/{DEFAULT_BUILD_LOG}"


def exit_code_path() -> str:
    """Where the worker's exit code lands. Absent ⇒ it never finished."""
    return f"{build_workdir()}/{DEFAULT_EXIT_CODE}"


def pid_path() -> str:
    """The worker's own PID. Used by ``kill -0`` to catch a silent kill."""
    return f"{build_workdir()}/{DEFAULT_PID}"


def launched_path() -> str:
    """Proof the launcher got as far as forking the worker."""
    return f"{build_workdir()}/{DEFAULT_LAUNCHED}"


def tar_path() -> str:
    return f"{build_workdir()}/image.tar"


def cache_enabled() -> bool:
    return _flag("DOCKER_RT_BUILD_CACHE", "false")


def cache_repo() -> str:
    return (os.getenv("DOCKER_RT_BUILD_CACHE_REPO") or "").strip().rstrip("/")


def registry_insecure() -> bool:
    return _flag("DOCKER_RT_BUILD_REGISTRY_INSECURE", "false")


def use_new_run() -> bool:
    return _flag("DOCKER_RT_KANIKO_USE_NEW_RUN", "false")


def snapshot_mode() -> str:
    return (os.getenv("DOCKER_RT_KANIKO_SNAPSHOT_MODE") or "").strip()


def extra_flags() -> list[str]:
    """Escape hatch: raw extra kaniko flags, e.g. ``--skip-tls-verify-pull``."""
    raw = (os.getenv("DOCKER_RT_KANIKO_EXTRA_FLAGS") or "").strip()
    if not raw:
        return []
    try:
        return shlex.split(raw)
    except ValueError as exc:  # unbalanced quotes
        logger.warning("ignoring DOCKER_RT_KANIKO_EXTRA_FLAGS (%s): %s", exc, raw)
        return []


def kaniko_args(
    *,
    destinations: list[str],
    dockerfile: str = "Dockerfile",
    context: str | None = None,
    buildargs: dict[str, str] | None = None,
    labels: dict[str, str] | None = None,
    target: str | None = None,
    push: bool = True,
    digest_file: str | None = None,
    cache: bool | None = None,
    cache_repo_ref: str | None = None,
    insecure: bool | None = None,
    new_run: bool | None = None,
    snapshot: str | None = None,
    extra: list[str] | None = None,
    platform: str | None = None,
) -> list[str]:
    """Assemble the ``kaniko`` argv for one build.

    ``dockerfile`` is interpreted by kaniko **relative to the build context**.
    """
    dests = [d for d in (destinations or []) if (d or "").strip()]
    if push and not dests:
        raise ValueError("kaniko requires at least one --destination when pushing")

    args: list[str] = []
    args.append(f"--context={context or context_uri()}")
    args.append(f"--dockerfile={dockerfile or 'Dockerfile'}")
    for dest in dests:
        args.append(f"--destination={dest}")

    for key, value in (buildargs or {}).items():
        args.append(f"--build-arg={key}={value}")
    for key, value in (labels or {}).items():
        args.append(f"--label={key}={value}")
    if target:
        args.append(f"--target={target}")

    if not push:
        args.append("--no-push")
        args.append(f"--tar-path={tar_path()}")

    if digest_file:
        args.append(f"--digest-file={digest_file}")

    cache_on = cache_enabled() if cache is None else bool(cache)
    args.append("--cache=true" if cache_on else "--cache=false")
    if cache_on:
        repo = (cache_repo_ref if cache_repo_ref is not None else cache_repo()).strip()
        if repo:
            args.append(f"--cache-repo={repo}")

    new_run_on = use_new_run() if new_run is None else bool(new_run)
    if new_run_on:
        args.append("--use-new-run")

    mode = snapshot if snapshot is not None else snapshot_mode()
    if mode:
        args.append(f"--snapshot-mode={mode}")

    insecure_on = registry_insecure() if insecure is None else bool(insecure)
    if insecure_on:
        args.append("--insecure")
        args.append("--skip-tls-verify")
        args.append("--skip-tls-verify-pull")

    if platform:
        args.append(f"--customPlatform={platform}")

    args.extend(extra if extra is not None else extra_flags())
    return args


def build_script(
    *,
    args: list[str],
    docker_config_b64: str = "",
    workdir: str | None = None,
    config_dir: str | None = None,
) -> str:
    """The worker's shell body — the script that actually runs kaniko.

    ``args`` is spliced in as a single shell word each, so it must already be
    fully formed (see :func:`kaniko_args`).

    Written to be run **detached**, hence the ``EXIT`` trap: it is the only
    thing that tells the poller "done, code N". A ``SIGKILL`` (the OOM killer)
    skips traps entirely, which is why the poller also watches the PID rather
    than trusting this file's absence to mean "still going".
    """
    wd = workdir or build_workdir()
    cfg_dir = config_dir or docker_config_dir()
    cfg_file = f"{cfg_dir}/config.json"
    rc_file = shlex.quote(exit_code_path())

    lines = [
        "set -eu",
        # config.json carries registry credentials: keep it owner-only.
        "umask 077",
        # Clear a previous run's verdict *before* arming the trap, so a stale
        # rc can never be read as this build's outcome.
        f"rm -f {rc_file}",
        f"trap 'printf \"%s\\n\" \"$?\" > {rc_file}' EXIT",
        f"printf '%s\\n' $$ > {shlex.quote(pid_path())}",
        f"mkdir -p {shlex.quote(wd)} {shlex.quote(cfg_dir)}",
    ]
    if docker_config_b64:
        lines.append(
            f"printf '%s' {shlex.quote(docker_config_b64)} | base64 -d > {shlex.quote(cfg_file)}"
        )
        lines.append(f"chmod 600 {shlex.quote(cfg_file)}")
        # 0600 is mandatory: kaniko refuses to read a world-readable config.
        lines.append(f"export DOCKER_CONFIG={shlex.quote(cfg_dir)}")
    digest_path = shlex.quote(digest_file_path())
    lines.append("cd %s" % shlex.quote(wd))
    lines.append("rm -f %s" % digest_path)
    lines.append(" ".join([shlex.quote(kaniko_bin()), *[shlex.quote(a) for a in args]]))
    # Echo a machine-readable marker so a truncated log can never be mistaken
    # for success (Docker's classic builder hides errors and exits 0).
    lines.append(f"if [ -s {digest_path} ]; then")
    lines.append(f"  printf '\\n{DIGEST_MARKER} %s\\n' \"$(cat {digest_path})\"")
    lines.append("fi")
    return "\n".join(lines) + "\n"


def start_script(
    *,
    args: list[str],
    docker_config_b64: str = "",
    workdir: str | None = None,
    config_dir: str | None = None,
) -> str:
    """The short script that starts the build and returns immediately.

    Two things make the split worth it:

    * **No call-length ceiling.** The sandbox agent rejects any single exec
      asking for more than 600 s, so one call could never cover a longer build.
      Here the exec only forks, and the polling calls that follow are seconds
      long.
    * **A dropped websocket stops costing the build.** Previously an exec-stream
      error discarded a kaniko run that was already most of the way through.

    ``setsid`` puts the worker in its own session so the session teardown that
    follows the exec cannot reach it with a ``SIGHUP``; ``nohup`` is the
    fallback for images without it (and covers ``SIGHUP`` the other way, by
    ignoring it). The worker is written from base64 rather than a heredoc so no
    quoting rule of the *outer* shell can corrupt it.
    """
    worker_b64 = base64.b64encode(
        build_script(
            args=args,
            docker_config_b64=docker_config_b64,
            workdir=workdir,
            config_dir=config_dir,
        ).encode("utf-8")
    ).decode("ascii")
    script_path = shlex.quote(worker_script_path())
    log_path = shlex.quote(build_log_path())

    return "\n".join(
        [
            "set -eu",
            f"mkdir -p {shlex.quote(build_workdir())}",
            "rm -f "
            + " ".join(
                shlex.quote(p)
                for p in (exit_code_path(), pid_path(), launched_path())
            ),
            f"printf '%s' {shlex.quote(worker_b64)} | base64 -d > {script_path}",
            f"chmod 700 {script_path}",
            "if command -v setsid >/dev/null 2>&1; then",
            f"  setsid sh {script_path} > {log_path} 2>&1 </dev/null &",
            "else",
            f"  nohup sh {script_path} > {log_path} 2>&1 </dev/null &",
            "fi",
            f"touch {shlex.quote(launched_path())}",
            f"printf '{STATUS_MARKER} launched\\n' >&2",
        ]
    ) + "\n"


def status_script(*, offset: int = 0) -> str:
    """One poll: worker state on **stderr**, unseen log bytes on **stdout**.

    Splitting the channels keeps the payload free of any separator a build log
    could contain, and the byte ``offset`` is baked into the script so each poll
    transfers only what the daemon has not emitted yet (``tail -c +N`` is
    1-based, hence ``offset + 1``).

    States: ``done`` (rc known) / ``running`` / ``gone`` (process vanished
    without writing ``build.rc`` — OOM-killed or the sandbox agent reaped it) /
    ``wiped`` (the workdir itself is gone: the container filesystem was replaced
    underneath us) / ``not-launched`` (the start script never got as far as
    forking).

    ``wiped`` is checked first on purpose. The launcher creates the workdir
    before it does anything else, so a missing workdir means the *container*
    changed — not that the launcher failed. Getting this order wrong made every
    multi-stage build look like "the launcher did not reach the fork".
    """
    start = max(0, int(offset)) + 1
    return "\n".join(
        [
            "set -u",
            f"if [ ! -d {shlex.quote(build_workdir())} ]; then",
            f"  printf '{STATUS_MARKER} wiped\\n' >&2",
            f"elif [ -s {shlex.quote(exit_code_path())} ]; then",
            f"  printf '{STATUS_MARKER} done rc=%s\\n' "
            f"\"$(cat {shlex.quote(exit_code_path())})\" >&2",
            f"elif [ -s {shlex.quote(pid_path())} ] && "
            f"kill -0 \"$(cat {shlex.quote(pid_path())})\" 2>/dev/null; then",
            f"  printf '{STATUS_MARKER} running\\n' >&2",
            f"elif [ -f {shlex.quote(launched_path())} ]; then",
            f"  printf '{STATUS_MARKER} gone\\n' >&2",
            "else",
            f"  printf '{STATUS_MARKER} not-launched\\n' >&2",
            "fi",
            f"tail -c +{start} {shlex.quote(build_log_path())} 2>/dev/null || true",
        ]
    ) + "\n"


def parse_status(raw: str | None) -> tuple[str, int | None]:
    """Read ``(state, returncode)`` out of a poll's stderr.

    Returns ``("", None)`` when no marker is present, which the caller must
    treat as "no news", never as "done".
    """
    for line in reversed((raw or "").splitlines()):
        line = line.strip()
        if not line.startswith(STATUS_MARKER):
            continue
        parts = line[len(STATUS_MARKER) :].split()
        state = parts[0].lower() if parts else ""
        returncode: int | None = None
        for part in parts[1:]:
            if part.startswith("rc="):
                try:
                    returncode = int(part[3:])
                except ValueError:
                    returncode = None
        return state, returncode
    return "", None


def strip_status_lines(text: str) -> str:
    """Drop status markers from log bytes.

    The exec protocol keeps stdout and stderr apart, so this is defensive: if a
    future transport merges the channels, the markers must not show up as build
    output.
    """
    if STATUS_MARKER not in text:
        return text
    return "".join(
        line
        for line in text.splitlines(keepends=True)
        if not line.lstrip().startswith(STATUS_MARKER)
    )


def parse_digest(raw: str | None) -> str:
    """Normalise a digest read back from the sandbox.

    Accepts either the bare ``sha256:…`` written by ``--digest-file`` or the
    ``docker-rt-digest: <digest>`` marker line echoed by :func:`build_script`.
    Returns ``""`` when no digest is present.
    """
    marker = DIGEST_MARKER
    for line in (raw or "").splitlines():
        line = line.strip()
        if line.startswith(marker):
            line = line[len(marker) :].strip()
        if line.startswith("sha256:") and len(line) > len("sha256:"):
            return line
    return ""
