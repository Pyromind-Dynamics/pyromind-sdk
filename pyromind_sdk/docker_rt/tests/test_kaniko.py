"""Unit tests for the kaniko command/script assembly (no cluster involved)."""

from __future__ import annotations

import base64
import shlex

import pytest


def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every kaniko-related env var so defaults are exercised."""
    for name in (
        "DOCKER_RT_KANIKO_BIN",
        "DOCKER_RT_BUILD_CONTEXT_DIR",
        "DOCKER_RT_KANIKO_DOCKER_CONFIG_DIR",
        "DOCKER_RT_KANIKO_CONTEXT_ARCHIVE",
        "DOCKER_RT_BUILD_CACHE",
        "DOCKER_RT_BUILD_CACHE_REPO",
        "DOCKER_RT_BUILD_REGISTRY_INSECURE",
        "DOCKER_RT_KANIKO_USE_NEW_RUN",
        "DOCKER_RT_KANIKO_SNAPSHOT_MODE",
        "DOCKER_RT_KANIKO_EXTRA_FLAGS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_kaniko_args_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import kaniko

    _fresh(monkeypatch)
    args = kaniko.kaniko_args(
        destinations=["reg.example.com/rt/proj_web:latest"],
        dockerfile="Dockerfile",
        buildargs={"FOO": "bar"},
        labels={"maintainer": "rt"},
        target="builder",
        digest_file=kaniko.digest_file_path(),
        cache=False,
        new_run=False,
        insecure=False,
    )
    joined = " ".join(args)
    # Paths are asserted through the module's own accessors so moving the
    # workdir (it lives under /kaniko, which survives kaniko's per-stage
    # filesystem wipe) cannot silently drift away from the tests.
    assert f"--context={kaniko.context_uri()}" in joined
    assert "--dockerfile=Dockerfile" in joined
    assert "--destination=reg.example.com/rt/proj_web:latest" in joined
    assert "--build-arg=FOO=bar" in joined
    assert "--label=maintainer=rt" in joined
    assert "--target=builder" in joined
    assert f"--digest-file={kaniko.digest_file_path()}" in joined
    assert "--cache=false" in joined
    assert "--no-push" not in joined
    assert kaniko.build_workdir().startswith("/kaniko/")


def test_kaniko_args_repeats_every_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import kaniko

    _fresh(monkeypatch)
    args = kaniko.kaniko_args(
        destinations=["reg.example.com/rt/a:1", "reg.example.com/rt/a:2"],
        cache=False,
        new_run=False,
        insecure=False,
    )
    dests = [a for a in args if a.startswith("--destination=")]
    assert dests == ["--destination=reg.example.com/rt/a:1", "--destination=reg.example.com/rt/a:2"]


def test_kaniko_args_requires_destination_when_pushing(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import kaniko

    _fresh(monkeypatch)
    with pytest.raises(ValueError):
        kaniko.kaniko_args(destinations=[], push=True)


def test_kaniko_args_no_push_writes_a_tarball(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import kaniko

    _fresh(monkeypatch)
    args = kaniko.kaniko_args(destinations=[], push=False, cache=False, new_run=False, insecure=False)
    joined = " ".join(args)
    assert "--no-push" in joined
    assert f"--tar-path={kaniko.tar_path()}" in joined
    assert "--destination=" not in joined


def test_kaniko_args_cache_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import kaniko

    _fresh(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_CACHE", "true")
    monkeypatch.setenv("DOCKER_RT_BUILD_CACHE_REPO", "reg.example.com/rt/cache")
    args = kaniko.kaniko_args(destinations=["reg.example.com/rt/a:1"], new_run=False, insecure=False)
    joined = " ".join(args)
    assert "--cache=true" in joined
    assert "--cache-repo=reg.example.com/rt/cache" in joined


def test_kaniko_args_insecure_triple_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import kaniko

    _fresh(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_BUILD_REGISTRY_INSECURE", "true")
    args = kaniko.kaniko_args(destinations=["reg.example.com/rt/a:1"], cache=False, new_run=False)
    joined = " ".join(args)
    assert "--insecure" in joined
    assert "--skip-tls-verify" in joined
    assert "--skip-tls-verify-pull" in joined


def test_kaniko_extra_flags_are_shlex_split(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import kaniko

    _fresh(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_KANIKO_EXTRA_FLAGS", "--snapshot-mode=redo --verbosity=debug")
    args = kaniko.kaniko_args(destinations=["reg.example.com/rt/a:1"], cache=False, new_run=False)
    assert "--snapshot-mode=redo" in args
    assert "--verbosity=debug" in args


def test_kaniko_extra_flags_with_bad_quotes_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import kaniko

    _fresh(monkeypatch)
    monkeypatch.setenv("DOCKER_RT_KANIKO_EXTRA_FLAGS", "--verbosity='unbalanced")
    args = kaniko.kaniko_args(destinations=["reg.example.com/rt/a:1"], cache=False, new_run=False)
    assert all(not a.startswith("--verbosity") for a in args)


def test_build_script_writes_config_owner_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import kaniko

    _fresh(monkeypatch)
    payload = '{"auths":{"docker.io":{"username":"lvniqi","password":"s3cret-pat"}}}'
    encoded = base64.b64encode(payload.encode()).decode()
    script = kaniko.build_script(
        args=kaniko.kaniko_args(
            destinations=["docker.io/lvniqi/app:1"],
            digest_file=kaniko.digest_file_path(),
            cache=False,
            new_run=False,
            insecure=False,
        ),
        docker_config_b64=encoded,
    )
    assert "umask 077" in script
    assert "chmod 600 /kaniko/.docker/config.json" in script
    assert "export DOCKER_CONFIG=/kaniko/.docker" in script
    assert "/kaniko/executor" in script
    # Credentials travel base64-encoded, never in plaintext.
    assert shlex.quote(encoded) in script
    assert "s3cret-pat" not in script


def test_build_script_without_credentials_skips_docker_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import kaniko

    _fresh(monkeypatch)
    script = kaniko.build_script(args=["--context=tar:///x", "--dockerfile=Dockerfile"])
    assert "DOCKER_CONFIG" not in script
    assert "config.json" not in script
    assert "umask 077" in script


def test_build_script_echoes_digest_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import kaniko

    _fresh(monkeypatch)
    script = kaniko.build_script(args=["--dockerfile=Dockerfile"])
    assert "docker-rt-digest:" in script
    assert "set -eu" in script


def test_tar_path_is_the_workspace_images_directory() -> None:
    """One constant that means the same thing inside the sandbox and outside it.

    The build sandbox mounts the user's images directory at exactly this path
    (``build_sandbox.images_mount_spec``), so ``--tar-path`` needs no translation
    between the two views.
    """
    from ..backend import kaniko

    assert kaniko.DEFAULT_IMAGES_DIR == "/workspace/docker_images"
    assert kaniko.tar_path(["pyromind-console:dev"]) == (
        "/workspace/docker_images/pyromind-console_dev.tar"
    )
    assert kaniko.tar_path(["reg.example.com/rt/myapp:latest"]) == (
        "/workspace/docker_images/reg.example.com_rt_myapp_latest.tar"
    )
    # A build with no destination is only reachable from a direct call here.
    assert kaniko.tar_path([]) == "/workspace/docker_images/image.tar"


def test_tar_path_names_are_filesystem_and_shell_safe() -> None:
    """The path reaches both a shell command and a mounted filesystem."""
    from ..backend import kaniko

    path = kaniko.tar_path(["evil/../$(rm -rf /):1;echo x"])
    name = path.rsplit("/", 1)[-1]
    assert path.startswith("/workspace/docker_images/")
    for char in ("$", "(", ")", ";", " "):
        assert char not in name, name


def test_kaniko_args_archives_with_and_without_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--tar-path` is not tied to `--no-push`: kaniko writes the tarball first.

    Archiving is unconditional, so a build that pushes still leaves its image in
    the workspace (``DoPush`` writes the tar before the push loop).
    """
    from ..backend import kaniko

    _fresh(monkeypatch)
    pushing = " ".join(
        kaniko.kaniko_args(
            destinations=["reg.example.com/rt/app:dev"], push=True, cache=False
        )
    )
    assert "--no-push" not in pushing
    assert "--destination=reg.example.com/rt/app:dev" in pushing
    assert "--tar-path=/workspace/docker_images/reg.example.com_rt_app_dev.tar" in pushing

    plain = " ".join(
        kaniko.kaniko_args(destinations=["app:dev"], push=False, cache=False)
    )
    assert "--no-push" in plain
    assert "--tar-path=/workspace/docker_images/app_dev.tar" in plain


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("sha256:abc\n", "sha256:abc"),
        ("docker-rt-digest: sha256:abc\n", "sha256:abc"),
        ("noise\ndocker-rt-digest: sha256:abc\n", "sha256:abc"),
        ("", ""),
        ("noise only\n", ""),
        ("sha256:", ""),
    ],
)
def test_parse_digest(raw: str, expected: str) -> None:
    from ..backend import kaniko

    assert kaniko.parse_digest(raw) == expected


def test_context_uri_is_a_gzipped_tar(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import kaniko

    _fresh(monkeypatch)
    assert kaniko.context_uri().startswith("tar://")
    assert kaniko.context_archive_path().endswith("context.tar.gz")


def test_kaniko_bin_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import kaniko

    monkeypatch.setenv("DOCKER_RT_KANIKO_BIN", "/usr/local/bin/executor")
    script = kaniko.build_script(args=["--dockerfile=Dockerfile"])
    assert "/usr/local/bin/executor" in script


# --------------------------------------------------------------------------
# the detached protocol: launch / status / decode
# --------------------------------------------------------------------------


def _embedded_worker(script: str) -> str:
    """Pull the base64 worker back out of a launcher script."""
    import re

    match = re.search(r"printf '%s' ([A-Za-z0-9+/=]+) \| base64 -d", script)
    assert match, script
    return base64.b64decode(match.group(1)).decode()


def test_worker_script_records_its_own_pid_and_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """These two files are the only way a detached build can report anything."""
    from ..backend import kaniko

    _fresh(monkeypatch)
    script = kaniko.build_script(args=["--dockerfile=Dockerfile"])

    assert f"$$ > {shlex.quote(kaniko.pid_path())}" in script
    assert f"rm -f {shlex.quote(kaniko.exit_code_path())}" in script
    # The EXIT trap fires on `set -e` aborts too, not just on a clean return.
    assert "trap" in script and "EXIT" in script
    assert f"> {shlex.quote(kaniko.exit_code_path())}' EXIT" in script
    # Order matters: a stale rc must be cleared before the trap is armed, or a
    # previous build's verdict could be read as this one's.
    assert script.index("rm -f") < script.index("trap")


def test_start_script_detaches_and_reports_launched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ..backend import kaniko

    _fresh(monkeypatch)
    script = kaniko.start_script(args=["--dockerfile=Dockerfile"])

    assert "setsid" in script and "nohup" in script
    assert "</dev/null" in script
    assert f"> {shlex.quote(kaniko.build_log_path())} 2>&1" in script
    assert f"touch {shlex.quote(kaniko.launched_path())}" in script
    assert f"printf '{kaniko.STATUS_MARKER} launched\\n' >&2" in script
    # The launcher must not run kaniko itself: it writes it and detaches it.
    assert "/kaniko/executor" not in script
    # What it does write is the very same worker build_script() produces.
    assert _embedded_worker(script) == kaniko.build_script(args=["--dockerfile=Dockerfile"])


def test_start_script_clears_previous_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reused workdir must not be able to report the previous build's outcome."""
    from ..backend import kaniko

    _fresh(monkeypatch)
    script = kaniko.start_script(args=["--dockerfile=Dockerfile"])

    for stale in (kaniko.exit_code_path(), kaniko.pid_path(), kaniko.launched_path()):
        assert shlex.quote(stale) in script.split("printf")[0]


def test_status_script_tails_from_the_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import kaniko

    _fresh(monkeypatch)
    assert "tail -c +1 " in kaniko.status_script()
    assert "tail -c +11 " in kaniko.status_script(offset=10)
    # tail -c is 1-based; a nonsense offset must not become `tail -c +0`.
    assert "tail -c +1 " in kaniko.status_script(offset=-5)


def test_status_script_reports_every_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from ..backend import kaniko

    _fresh(monkeypatch)
    script = kaniko.status_script()
    for branch in ("done rc=", "running", "gone", "not-launched"):
        assert branch in script
    # State rides on stderr so stdout can be pure log bytes for tail.
    assert f"printf '{kaniko.STATUS_MARKER} running\\n' >&2" in script
    assert "[ -s " in script and "kill -0" in script


@pytest.mark.parametrize(
    ("raw", "state", "rc"),
    [
        (None, "", None),
        ("", "", None),
        ("noise\n", "", None),
        ("docker-rt-status: running\n", "running", None),
        ("docker-rt-status: done rc=0\n", "done", 0),
        ("docker-rt-status: done rc=137\n", "done", 137),
        ("build output\ndocker-rt-status: gone\n", "gone", None),
        ("docker-rt-status: not-launched\n", "not-launched", None),
        ("docker-rt-status: done rc=junk\n", "done", None),
        # The last marker wins if a transport ever replays a frame.
        ("docker-rt-status: running\ndocker-rt-status: done rc=1\n", "done", 1),
    ],
)
def test_parse_status(raw: str | None, state: str, rc: int | None) -> None:
    from ..backend import kaniko

    assert kaniko.parse_status(raw) == (state, rc)


def test_strip_status_lines_keeps_log_bytes_intact() -> None:
    from ..backend import kaniko

    text = "line one\ndocker-rt-status: running\nline two\n"
    assert kaniko.strip_status_lines(text) == "line one\nline two\n"
    # No marker -> returned unchanged, so the common path costs nothing.
    plain = "INFO[0001] building\n"
    assert kaniko.strip_status_lines(plain) is plain


def test_status_script_checks_the_workdir_before_blaming_the_launcher() -> None:
    """Ordering is the whole point of the ``wiped`` state.

    The launcher creates the workdir before anything else, so a missing workdir
    means the *container* changed. Checking ``build.launched`` first made every
    multi-stage build report "the launcher did not reach the fork", because
    kaniko deletes the root filesystem between stages.
    """
    from ..backend import kaniko

    script = kaniko.status_script(offset=0)
    assert kaniko.build_workdir() in script
    assert "[ ! -d" in script
    assert script.index("[ ! -d") < script.index("not-launched")
    assert "wiped" in script


def test_the_workdir_lives_where_kaniko_cannot_delete_it() -> None:
    """kaniko spares ``/kaniko`` — its own binary, config and build context.

    Anything we keep elsewhere under ``/`` is deleted by design when kaniko
    moves to the next stage.
    """
    from ..backend import kaniko

    assert kaniko.build_workdir().startswith("/kaniko/")
    assert kaniko.context_archive_path().startswith("/kaniko/")
    assert kaniko.digest_file_path().startswith("/kaniko/")
    assert kaniko.build_log_path().startswith("/kaniko/")
    assert kaniko.docker_config_dir().startswith("/kaniko/")


def test_parse_status_reads_the_wiped_state() -> None:
    from ..backend import kaniko

    assert kaniko.parse_status("docker-rt-status: wiped\n") == ("wiped", None)
    assert kaniko.parse_status("docker-rt-status: done rc=137\n") == ("done", 137)
