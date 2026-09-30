from __future__ import annotations

import shutil
import stat
import subprocess

import pytest
from pytest import MonkeyPatch, fixture

from .. import install_wrapper as mod
from .. import site_hooks


@fixture(autouse=True)
def disable_sdk_timeout_hook(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        site_hooks,
        "ensure_docker_sdk_timeout_hook",
        lambda site_packages=None: None,
    )


def test_is_wrapper_installed_checks_version_marker(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "docker"
    path.write_text(f'WRAPPER_VERSION="{mod.WRAPPER_VERSION}"\n', encoding="utf-8")
    path.chmod(0o755)
    monkeypatch.setattr(mod, "WRAPPER_PATH", path)

    assert mod.is_wrapper_installed() is True

    path.write_text('WRAPPER_VERSION="1"\n', encoding="utf-8")
    assert mod.is_wrapper_installed() is False


def test_ensure_wrapper_installed_noninteractive_installs(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "docker"
    monkeypatch.setattr(mod, "WRAPPER_PATH", path)
    monkeypatch.setattr(mod, "find_real_docker", lambda: "/usr/local/bin/docker")

    def fake_install():
        path.write_text(
            f'WRAPPER_VERSION="{mod.WRAPPER_VERSION}"\n',
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path

    monkeypatch.setattr(mod, "install_wrapper", fake_install)

    assert mod.ensure_wrapper_installed(interactive=False) is True
    assert mod.is_wrapper_installed() is True


def test_ensure_wrapper_installed_decline_stops_startup(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "docker"
    monkeypatch.setattr(mod, "WRAPPER_PATH", path)
    monkeypatch.setattr(mod, "find_real_docker", lambda: "/usr/local/bin/docker")
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")

    assert mod.ensure_wrapper_installed(interactive=True) is False


def test_ensure_wrapper_installed_stale_version_prompts_update(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "docker"
    path.write_text('WRAPPER_VERSION="3"\n', encoding="utf-8")
    path.chmod(0o755)
    monkeypatch.setattr(mod, "WRAPPER_PATH", path)
    monkeypatch.setattr(mod, "find_real_docker", lambda: "/usr/local/bin/docker")
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")

    def fake_install():
        path.write_text(
            f'WRAPPER_VERSION="{mod.WRAPPER_VERSION}"\n',
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path

    monkeypatch.setattr(mod, "install_wrapper", fake_install)

    assert mod.ensure_wrapper_installed(interactive=True) is True
    assert mod.is_wrapper_installed() is True


def test_ensure_wrapper_installed_stale_version_decline_cancels(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "docker"
    path.write_text('WRAPPER_VERSION="3"\n', encoding="utf-8")
    path.chmod(0o755)
    monkeypatch.setattr(mod, "WRAPPER_PATH", path)
    monkeypatch.setattr(mod, "find_real_docker", lambda: "/usr/local/bin/docker")
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")

    assert mod.ensure_wrapper_installed(interactive=True) is False


def test_ensure_wrapper_installed_legacy_without_version_updates_directly(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "docker"
    path.write_text("# legacy wrapper without version marker\n", encoding="utf-8")
    path.chmod(0o755)
    monkeypatch.setattr(mod, "WRAPPER_PATH", path)
    monkeypatch.setattr(mod, "find_real_docker", lambda: "/usr/local/bin/docker")

    def fake_install():
        path.write_text(
            f'WRAPPER_VERSION="{mod.WRAPPER_VERSION}"\n',
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path

    monkeypatch.setattr(mod, "install_wrapper", fake_install)

    assert mod.ensure_wrapper_installed(interactive=True) is True
    assert mod.is_wrapper_installed() is True


def test_wrapper_in_path_compares_resolved_docker(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "docker"
    path.touch()
    path.chmod(0o755)
    monkeypatch.setattr(mod, "WRAPPER_PATH", path)
    monkeypatch.setattr(
        mod.shutil,
        "which",
        lambda name: str(path) if name == "docker" else None,
    )

    assert mod.wrapper_in_path() is True

    monkeypatch.setattr(
        mod.shutil,
        "which",
        lambda name: "/usr/local/bin/docker" if name == "docker" else None,
    )
    assert mod.wrapper_in_path() is False


def test_generated_wrapper_defers_ps_and_normalizes_rm(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "docker"
    monkeypatch.setattr(mod, "WRAPPER_PATH", path)
    monkeypatch.setattr(mod, "find_real_docker", lambda: "/usr/local/bin/docker")
    monkeypatch.setattr(mod, "_shell_rc_path", lambda: tmp_path / "rc")

    actual = mod.install_wrapper()
    text = actual.read_text(encoding="utf-8")
    assert '"$REAL_DOCKER" rm "${rm_opts[@]}" "$target" >"$_rm_out"' in text
    assert "rm_opts+=(--force)" in text
    assert "Force remove?" not in text
    assert "read -r -p" not in text
    assert "printf '%s deleted\\n' \"$target\"" in text
    # rm parallelizes (>5 targets) with up to 10 concurrent workers and waits per pid.
    # rm parallelizes (>5 targets) with configurable concurrency (default 20).
    assert "${DOCKER_RT_RM_CONCURRENCY:-20}" in text
    assert "concurrency=20" in text and 'wait "$_pid"' in text
    # docker ps is rendered natively by the real Docker CLI; no ps interception.
    assert '"$REAL_DOCKER" "${args[@]}" --no-trunc --format' not in text
    assert '"$REAL_DOCKER" "${args[@]}"' in text


def test_wrapper_allows_docker_build_and_forces_the_classic_builder(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "docker"
    monkeypatch.setattr(mod, "WRAPPER_PATH", path)
    monkeypatch.setattr(mod, "find_real_docker", lambda: "/usr/local/bin/docker")
    monkeypatch.setattr(mod, "_shell_rc_path", lambda: tmp_path / "rc")

    text = mod.install_wrapper().read_text(encoding="utf-8")

    # build is forwarded now, with BuildKit disabled so the CLI posts the context
    # tar to POST /build instead of dialling buildkitd over the socket.
    assert "export DOCKER_BUILDKIT=0" in text
    assert "does not support docker build / buildx build" not in text
    assert "does not support docker build." not in text


def test_wrapper_rejects_buildkit_only_build_flags(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "docker"
    monkeypatch.setattr(mod, "WRAPPER_PATH", path)
    monkeypatch.setattr(mod, "find_real_docker", lambda: "/usr/local/bin/docker")
    monkeypatch.setattr(mod, "_shell_rc_path", lambda: tmp_path / "rc")

    text = mod.install_wrapper().read_text(encoding="utf-8")

    # kaniko cannot honour these; rejecting is the only honest answer.
    #
    # NOTE: `--platform` is deliberately NOT in this list. It is forwarded so the
    # daemon can read it off the /build query string and pass it to kaniko as
    # `--customPlatform`. Assert on the case-label list rather than on a bare
    # substring, because the flag name also appears in the comments above the loop.
    rejection_case = text.split("--builder|--builder=*|", 1)[1].split(")", 1)[0]
    for flag in ("--secret", "--ssh", "--output", "--cache-to", "--load", "--push"):
        assert flag in rejection_case, flag
    assert "--platform" not in rejection_case
    assert "not supported by the cluster build sandbox (kaniko)" in text
    assert '"$REAL_DOCKER" "${filtered_args[@]}"' in text


def test_wrapper_disables_cli_next_steps_hooks(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    """`docker exec -it … ; exit` must not print Docker's "What's next" banner.

    With a TTY attached, docker 27 runs every installed CLI plugin's hook after
    the command (``cmd/docker/docker.go`` → ``manager.RunCLICommandHooks``); the
    docker-debug plugin answers with

        What's next:
            Try Docker Debug … → docker debug <cid>

    on stderr, which reads like the exec failed. ``DockerCli.HooksEnabled()``
    honours DOCKER_CLI_HINTS (legacy) and DOCKER_CLI_HOOKS, so the wrapper must
    export both before handing over — but only for docker-rt, so a real Docker
    context keeps its hints.
    """
    if shutil.which("bash") is None:
        pytest.skip("bash is not available")

    wrapper = tmp_path / "docker"
    fake_docker = tmp_path / "fake-docker"
    log = tmp_path / "calls.log"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "{\n"
        '  echo "HINTS=${DOCKER_CLI_HINTS:-<unset>}"\n'
        '  echo "HOOKS=${DOCKER_CLI_HOOKS:-<unset>}"\n'
        '} >> "$FAKE_LOG"\n',
        encoding="utf-8",
    )
    fake_docker.chmod(fake_docker.stat().st_mode | stat.S_IXUSR)

    monkeypatch.setattr(mod, "WRAPPER_PATH", wrapper)
    monkeypatch.setattr(mod, "WRAPPER_DIR", tmp_path)
    monkeypatch.setattr(mod, "find_real_docker", lambda: str(fake_docker))
    monkeypatch.setattr(mod, "_shell_rc_path", lambda: tmp_path / "rc")
    mod.install_wrapper()

    def run(*argv: str, docker_rt: bool = True) -> str:
        log.unlink(missing_ok=True)
        env = {
            "PATH": "/usr/bin:/bin",
            "FAKE_LOG": str(log),
            "DOCKER_HOST": (
                "unix:///tmp/docker-rt.sock"
                if docker_rt
                else "unix:///var/run/docker.sock"
            ),
        }
        subprocess.run(
            ["bash", str(wrapper), *argv],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return log.read_text(encoding="utf-8")

    on_docker_rt = run("ps")
    assert "HINTS=false" in on_docker_rt, on_docker_rt
    assert "HOOKS=false" in on_docker_rt, on_docker_rt

    # A real Docker context is handed over untouched: hints stay available.
    off_docker_rt = run("ps", docker_rt=False)
    assert "HINTS=<unset>" in off_docker_rt, off_docker_rt
    assert "HOOKS=<unset>" in off_docker_rt, off_docker_rt


def test_generated_wrapper_forwards_build_argv_verbatim(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    """Run the real wrapper and assert what the real docker CLI would receive.

    This is the behavioural counterpart to the text assertions above: it actually
    executes the generated bash, so a wrapper that silently drops or mangles build
    arguments fails here instead of only in the user's terminal.
    """
    if shutil.which("bash") is None:
        pytest.skip("bash is not available")

    wrapper = tmp_path / "docker"
    fake_docker = tmp_path / "fake-docker"
    log = tmp_path / "calls.log"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "{\n"
        '  echo "DOCKER_BUILDKIT=${DOCKER_BUILDKIT:-<unset>}"\n'
        '  for a in "$@"; do echo "ARG:$a"; done\n'
        '} >> "$FAKE_LOG"\n',
        encoding="utf-8",
    )
    fake_docker.chmod(fake_docker.stat().st_mode | stat.S_IXUSR)

    monkeypatch.setattr(mod, "WRAPPER_PATH", wrapper)
    monkeypatch.setattr(mod, "WRAPPER_DIR", tmp_path)
    monkeypatch.setattr(mod, "find_real_docker", lambda: str(fake_docker))
    monkeypatch.setattr(mod, "_shell_rc_path", lambda: tmp_path / "rc")
    mod.install_wrapper()

    def run(argv: list[str], *, docker_rt: bool = True) -> subprocess.CompletedProcess:
        log.unlink(missing_ok=True)
        env = {
            "PATH": "/usr/bin:/bin",
            "FAKE_LOG": str(log),
            "DOCKER_HOST": (
                "unix:///tmp/docker-rt.sock" if docker_rt else "unix:///var/run/docker.sock"
            ),
        }
        return subprocess.run(
            ["bash", str(wrapper), *argv],
            env=env,
            capture_output=True,
            text=True,
        )

    def forwarded() -> list[str]:
        if not log.exists():
            return []
        return [ln[4:] for ln in log.read_text().splitlines() if ln.startswith("ARG:")]

    # The user's exact shape: --platform + -f + -t + an out-of-tree context path.
    user_args = [
        "build",
        "--platform",
        "linux/amd64",
        "-f",
        "Dockerfile-dev",
        "-t",
        "pyromind-console:dev",
        "/Users/x/pyromind-console-1",
    ]
    proc = run(user_args)
    assert proc.returncode == 0, proc.stderr
    assert forwarded() == user_args
    assert "DOCKER_BUILDKIT=0" in log.read_text()

    # BuildKit-only flags are refused before the real docker is touched.
    for argv in (
        ["build", "--secret", "id=x", "-t", "app:dev", "."],
        ["build", "--load", "-t", "app:dev", "."],
    ):
        proc = run(argv)
        assert proc.returncode == 1
        assert "not supported by the cluster build sandbox" in proc.stderr
        assert forwarded() == []

    # Outside the docker-rt context the wrapper is a pure passthrough: even the
    # flags it would otherwise refuse must reach the real CLI untouched.
    passthrough = ["build", "--secret", "id=x", "--platform", "linux/arm64", "-t", "a", "."]
    proc = run(passthrough, docker_rt=False)
    assert proc.returncode == 0, proc.stderr
    assert forwarded() == passthrough
    assert "DOCKER_BUILDKIT=0" not in log.read_text()


def test_wrapper_still_gates_buildx_and_compose_build(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "docker"
    monkeypatch.setattr(mod, "WRAPPER_PATH", path)
    monkeypatch.setattr(mod, "find_real_docker", lambda: "/usr/local/bin/docker")
    monkeypatch.setattr(mod, "_shell_rc_path", lambda: tmp_path / "rc")

    text = mod.install_wrapper().read_text(encoding="utf-8")

    assert "does not support docker buildx build yet" in text
    assert "does not support docker compose build yet" in text


def test_wrapper_build_filters_stderr_instead_of_execing_docker(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "docker"
    monkeypatch.setattr(mod, "WRAPPER_PATH", path)
    monkeypatch.setattr(mod, "find_real_docker", lambda: "/usr/local/bin/docker")
    monkeypatch.setattr(mod, "_shell_rc_path", lambda: tmp_path / "rc")

    text = mod.install_wrapper().read_text(encoding="utf-8")

    # `exec` would hand the terminal straight to the real CLI, leaving no chance
    # to remove the legacy-builder banner it prints because of DOCKER_BUILDKIT=0.
    assert 'exec "$REAL_DOCKER" "${filtered_args[@]}"' not in text
    assert '_strip_legacy_builder_banner' in text
    assert (
        '"$REAL_DOCKER" "${filtered_args[@]}" 2>&1 1>&3 | _strip_legacy_builder_banner >&2'
        in text
    )
    # The real CLI's exit code must survive the pipeline.
    assert "_build_rc=${PIPESTATUS[0]}" in text
    assert "exit $_build_rc" in text


def test_generated_wrapper_drops_the_cli_legacy_builder_banner(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    """DOCKER_BUILDKIT=0 makes the real CLI print a banner users blame docker-rt for.

    The wrapper forces that variable (it is what routes the build to POST /build),
    so it must strip exactly the banner - both documented variants - while leaving
    docker's stdout, its other stderr lines and its exit code alone.
    """
    if shutil.which("bash") is None:
        pytest.skip("bash is not available")

    wrapper = tmp_path / "docker"
    fake_docker = tmp_path / "fake-docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        "echo 'step 1/2 : FROM scratch'\n"
        "echo 'Sending build context to Docker daemon  95.57MB' >&2\n"
        # The DOCKER_BUILDKIT=0 variant, exactly as docker/cli prints it.
        "echo 'DEPRECATED: The legacy builder is deprecated and will be removed in a future release.' >&2\n"
        "echo '            BuildKit is currently disabled; enable it by removing the DOCKER_BUILDKIT=0' >&2\n"
        "echo '            environment-variable.' >&2\n"
        "echo '' >&2\n"
        "echo 'ERROR: something the user must still see' >&2\n"
        # The sibling "buildx is missing" variant shares the same first line.
        "echo 'DEPRECATED: The legacy builder is deprecated and will be removed in a future release.' >&2\n"
        "echo '            Install the buildx component to build images with BuildKit:' >&2\n"
        "echo '            https://docs.docker.com/go/buildx/' >&2\n"
        "echo '' >&2\n"
        "echo 'still-visible-stderr' >&2\n"
        "exit 7\n",
        encoding="utf-8",
    )
    fake_docker.chmod(
        fake_docker.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )

    monkeypatch.setattr(mod, "WRAPPER_PATH", wrapper)
    monkeypatch.setattr(mod, "WRAPPER_DIR", tmp_path)
    monkeypatch.setattr(mod, "find_real_docker", lambda: str(fake_docker))
    monkeypatch.setattr(mod, "_shell_rc_path", lambda: tmp_path / "rc")
    mod.install_wrapper()

    proc = subprocess.run(
        ["bash", str(wrapper), "build", "-t", "app:dev", "."],
        env={"PATH": "/usr/bin:/bin", "DOCKER_HOST": "unix:///tmp/docker-rt.sock"},
        capture_output=True,
        text=True,
    )

    # The CLI's exit code is preserved even though it now runs through a pipeline.
    assert proc.returncode == 7, proc.stderr

    assert "DEPRECATED" not in proc.stderr
    assert "BuildKit is currently disabled" not in proc.stderr
    assert "environment-variable." not in proc.stderr
    assert "Install the buildx component" not in proc.stderr
    assert "https://docs.docker.com/go/buildx/" not in proc.stderr

    # Everything else docker wrote still reaches the user, on the right stream.
    assert "Sending build context to Docker daemon" in proc.stderr
    assert "ERROR: something the user must still see" in proc.stderr
    assert "still-visible-stderr" in proc.stderr
    assert "step 1/2 : FROM scratch" in proc.stdout
    assert "DEPRECATED" not in proc.stdout
