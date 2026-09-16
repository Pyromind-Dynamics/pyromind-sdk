from __future__ import annotations

import os

from pytest import MonkeyPatch


def test_server_parser_accepts_daemon_flags() -> None:
    from ..daemon import prepare_server_parser

    args = prepare_server_parser().parse_args(
        [
            "--daemon",
            "--sock",
            "/tmp/docker-rt-test.sock",
            "--log-file",
            "/tmp/docker-rt-test.log",
            "--pid-file",
            "/tmp/docker-rt-test.pid",
            "--ready-timeout",
            "900",
        ]
    )

    assert args.daemon is True
    assert args.sock == "/tmp/docker-rt-test.sock"
    assert args.log_file == "/tmp/docker-rt-test.log"
    assert args.pid_file == "/tmp/docker-rt-test.pid"
    assert args.ready_timeout == 900


def test_server_parser_accepts_stop_flag() -> None:
    from ..daemon import prepare_server_parser

    args = prepare_server_parser().parse_args(["--stop"])

    assert args.stop is True


def test_server_parser_accepts_credentials_flags() -> None:
    from ..daemon import prepare_server_parser

    args = prepare_server_parser().parse_args(
        ["--apikey", "key123", "--cluster", "us-west-1#pre"]
    )
    assert args.api_key == "key123"
    assert args.cluster == "us-west-1#pre"

    alias = prepare_server_parser().parse_args(["--api-key", "key456"])
    assert alias.api_key == "key456"


def test_stop_daemon_kills_pid_and_removes_pid_file(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    from .. import daemon as daemon_mod

    pid_file = tmp_path / "docker-rt.pid"
    pid_file.write_text("12345\n", encoding="utf-8")
    calls = []

    def fake_kill(pid, sig):
        calls.append((pid, sig))
        if sig == 0:
            raise ProcessLookupError()

    monkeypatch.setattr(daemon_mod.os, "kill", fake_kill)

    assert daemon_mod.stop_daemon(pid_file=str(pid_file)) == 0
    assert (12345, daemon_mod.signal.SIGTERM) in calls
    assert not pid_file.exists()


def test_start_daemon_defaults_to_standard_inspect_mode(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    from .. import daemon as daemon_mod

    captured = {}

    class FakeProcess:
        pid = 12345
        returncode = None

        def poll(self):
            return None

        def terminate(self):
            pass

    class FakeSocket:
        def __init__(self, *args, **kwargs):
            pass

        def settimeout(self, timeout):
            pass

        def connect(self, address):
            pass

        def close(self):
            pass

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return FakeProcess()

    monkeypatch.delenv("DOCKER_RT_INSPECT_MODE", raising=False)
    monkeypatch.setenv("DOCKER_RT_HOST", "127.0.0.1")
    monkeypatch.setenv("DOCKER_RT_PORT", "2375")
    monkeypatch.setattr(daemon_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(daemon_mod, "spawn_watcher", lambda *args, **kwargs: None)
    monkeypatch.setattr(daemon_mod.socket, "socket", lambda *args, **kwargs: FakeSocket())

    assert (
        daemon_mod.start_daemon(
            log_file=str(tmp_path / "daemon.log"),
            pid_file=str(tmp_path / "daemon.pid"),
        )
        == 0
    )
    assert captured["env"]["DOCKER_RT_INSPECT_MODE"] == "standard"


def test_server_main_daemon_uses_background_start(
    monkeypatch: MonkeyPatch,
) -> None:
    import pyromind_sdk.docker_rt.bootstrap as bootstrap_mod
    import pyromind_sdk.docker_rt.daemon as daemon_mod
    import pyromind_sdk.docker_rt.install_wrapper as wrapper_mod
    import pyromind_sdk.docker_rt.register_context as rc_mod
    import pyromind_sdk.docker_rt.server as server_mod

    called = {}

    def fake_start_daemon(**kwargs):
        called.update(kwargs)
        return 7

    monkeypatch.setattr(bootstrap_mod, "check_docker_cli", lambda **kwargs: True)
    monkeypatch.setattr(
        wrapper_mod,
        "ensure_wrapper_installed",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(bootstrap_mod, "prepare_env", lambda **kwargs: {})
    monkeypatch.setattr(bootstrap_mod, "check_connection", lambda **kwargs: 0)
    monkeypatch.setattr(rc_mod, "save_previous_context", lambda: None)
    monkeypatch.setattr(rc_mod, "activate_docker_rt_context", lambda: 0)
    monkeypatch.setattr(rc_mod, "ensure_docker_rt_context", lambda: 0)
    monkeypatch.setattr(daemon_mod, "start_daemon", fake_start_daemon)
    monkeypatch.delenv("PYROMIND_DOCKER_RT_DAEMON_CHILD", raising=False)
    monkeypatch.delenv("PYROMIND_DOCKER_RT_BOOTSTRAPPED", raising=False)

    rc = server_mod.main(["--daemon", "--ready-timeout", "901"])

    assert rc == 7
    assert called["pid_file"] is None
    assert os.environ["DOCKER_RT_READY_TIMEOUT"] == "901"


def test_server_main_foreground_spawns_watcher(
    monkeypatch: MonkeyPatch,
) -> None:
    import pyromind_sdk.docker_rt.aio_server as aio_mod
    import pyromind_sdk.docker_rt.bootstrap as bootstrap_mod
    import pyromind_sdk.docker_rt.daemon as daemon_mod
    import pyromind_sdk.docker_rt.install_wrapper as wrapper_mod
    import pyromind_sdk.docker_rt.register_context as rc_mod
    import pyromind_sdk.docker_rt.server as server_mod

    spawned = []
    monkeypatch.setattr(bootstrap_mod, "check_docker_cli", lambda **kwargs: True)
    monkeypatch.setattr(
        wrapper_mod,
        "ensure_wrapper_installed",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(bootstrap_mod, "prepare_env", lambda **kwargs: {})
    monkeypatch.setattr(bootstrap_mod, "check_connection", lambda **kwargs: 0)
    monkeypatch.setattr(rc_mod, "save_previous_context", lambda: None)
    monkeypatch.setattr(rc_mod, "activate_docker_rt_context", lambda: 0)
    monkeypatch.setattr(rc_mod, "ensure_docker_rt_context", lambda: 0)
    monkeypatch.setattr(
        daemon_mod,
        "spawn_watcher",
        lambda pid, **kwargs: spawned.append(pid),
    )
    monkeypatch.setattr(aio_mod, "main", lambda: None)
    monkeypatch.delenv("PYROMIND_DOCKER_RT_DAEMON_CHILD", raising=False)
    monkeypatch.delenv("PYROMIND_DOCKER_RT_BOOTSTRAPPED", raising=False)
    monkeypatch.delenv("PYROMIND_DOCKER_RT_WATCHER_SPAWNED", raising=False)
    monkeypatch.delenv("DOCKER_RT_INSPECT_MODE", raising=False)

    assert server_mod.main([]) == 0
    assert spawned == [os.getpid()]
    assert os.environ["DOCKER_RT_INSPECT_MODE"] == "standard"


def test_server_main_daemon_child_does_not_spawn_watcher(
    monkeypatch: MonkeyPatch,
) -> None:
    import pyromind_sdk.docker_rt.aio_server as aio_mod
    import pyromind_sdk.docker_rt.bootstrap as bootstrap_mod
    import pyromind_sdk.docker_rt.daemon as daemon_mod
    import pyromind_sdk.docker_rt.install_wrapper as wrapper_mod
    import pyromind_sdk.docker_rt.register_context as rc_mod
    import pyromind_sdk.docker_rt.server as server_mod

    spawned = []
    monkeypatch.setattr(bootstrap_mod, "check_docker_cli", lambda **kwargs: True)
    monkeypatch.setattr(
        wrapper_mod,
        "ensure_wrapper_installed",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(bootstrap_mod, "prepare_env", lambda **kwargs: {})
    monkeypatch.setattr(bootstrap_mod, "check_connection", lambda **kwargs: 0)
    monkeypatch.setattr(rc_mod, "save_previous_context", lambda: None)
    monkeypatch.setattr(rc_mod, "activate_docker_rt_context", lambda: 0)
    monkeypatch.setattr(rc_mod, "ensure_docker_rt_context", lambda: 0)
    monkeypatch.setattr(
        daemon_mod,
        "spawn_watcher",
        lambda pid, **kwargs: spawned.append(pid),
    )
    monkeypatch.setattr(aio_mod, "main", lambda: None)
    monkeypatch.setenv("PYROMIND_DOCKER_RT_DAEMON_CHILD", "1")
    monkeypatch.setenv("PYROMIND_DOCKER_RT_BOOTSTRAPPED", "1")
    monkeypatch.setenv("PYROMIND_DOCKER_RT_WATCHER_SPAWNED", "1")

    assert server_mod.main([]) == 0
    assert spawned == []


def test_server_main_connection_failure_returns_before_context(
    monkeypatch: MonkeyPatch,
) -> None:
    import pyromind_sdk.docker_rt.bootstrap as bootstrap_mod
    import pyromind_sdk.docker_rt.install_wrapper as wrapper_mod
    import pyromind_sdk.docker_rt.register_context as rc_mod
    import pyromind_sdk.docker_rt.server as server_mod

    activated = []
    monkeypatch.setattr(bootstrap_mod, "check_docker_cli", lambda **kwargs: True)
    monkeypatch.setattr(
        wrapper_mod,
        "ensure_wrapper_installed",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(bootstrap_mod, "prepare_env", lambda **kwargs: {})

    def _fail_connection(**kwargs):
        raise RuntimeError("bad key")

    monkeypatch.setattr(bootstrap_mod, "check_connection", _fail_connection)
    monkeypatch.setattr(
        rc_mod,
        "activate_docker_rt_context",
        lambda: activated.append(True) or 0,
    )
    monkeypatch.delenv("PYROMIND_DOCKER_RT_DAEMON_CHILD", raising=False)
    monkeypatch.delenv("PYROMIND_DOCKER_RT_BOOTSTRAPPED", raising=False)

    assert server_mod.main([]) == 1
    assert activated == []
