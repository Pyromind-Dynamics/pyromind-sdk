from __future__ import annotations

import json
import asyncio

from pytest import MonkeyPatch

from .. import register_context as mod


def test_restore_main_switches_to_previous_context(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(mod, "CONTEXT_STATE_FILE", tmp_path / "state.json")
    mod.CONTEXT_STATE_FILE.write_text(
        json.dumps({"previous": "desktop-linux"}),
        encoding="utf-8",
    )
    calls = []

    monkeypatch.setattr(mod, "_current_context", lambda: "docker-rt")
    monkeypatch.setattr(mod, "_context_exists", lambda name: name == "desktop-linux")
    monkeypatch.setattr(
        mod,
        "_run",
        lambda argv: calls.append(argv) or 0,
    )

    assert mod.restore_main() == 0
    assert ["docker", "context", "use", "desktop-linux"] in calls


def test_restore_main_noop_when_not_using_docker_rt(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(mod, "_current_context", lambda: "desktop-linux")
    calls = []
    monkeypatch.setattr(
        mod,
        "_run",
        lambda argv: calls.append(argv) or 0,
    )

    assert mod.restore_main() == 0
    assert calls == []


def test_daemon_cleanup_restores_docker_context(
    monkeypatch: MonkeyPatch,
) -> None:
    from .. import aio_server as aio_mod
    from ..backend.store import ContainerStore

    calls = []
    monkeypatch.setattr(
        aio_mod,
        "restore_docker_context",
        lambda: calls.append("restore"),
    )

    class FakeApp(dict):
        def __init__(self):
            super().__init__(store=ContainerStore())

    asyncio.run(aio_mod.on_cleanup(FakeApp()))

    assert calls == ["restore"]


def test_main_restore_flag_calls_restore(monkeypatch: MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(mod, "restore_main", lambda: calls.append("restore") or 0)

    assert mod.main(["--restore"]) == 0
    assert calls == ["restore"]


def test_build_parser_accepts_restore() -> None:
    args = mod.build_parser().parse_args(["--restore"])

    assert args.restore is True


def test_save_previous_context_backs_up_current(
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(mod, "CONTEXT_STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(mod, "_current_context", lambda: "desktop-linux")

    mod.save_previous_context()

    data = json.loads(mod.CONTEXT_STATE_FILE.read_text(encoding="utf-8"))
    assert data["previous"] == "desktop-linux"


def test_activate_docker_rt_context_creates_and_switches(
    monkeypatch: MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(mod, "_context_exists", lambda name: False)
    monkeypatch.setattr(
        mod,
        "_run",
        lambda argv: calls.append(argv) or 0,
    )

    assert mod.activate_docker_rt_context() == 0
    assert ["docker", "context", "use", "docker-rt"] in calls
    assert any(
        argv[:3] == ["docker", "context", "create"] and argv[3] == "docker-rt"
        for argv in calls
    )


def test_ensure_docker_rt_context_noop_when_active(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(mod, "_current_context", lambda: "docker-rt")
    calls = []
    monkeypatch.setattr(
        mod,
        "activate_docker_rt_context",
        lambda: calls.append("activate") or 0,
    )

    assert mod.ensure_docker_rt_context() == 0
    assert calls == []


def test_ensure_docker_rt_context_activates_when_missing(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(mod, "_current_context", lambda: "desktop-linux")
    monkeypatch.setattr(mod, "activate_docker_rt_context", lambda: 0)

    assert mod.ensure_docker_rt_context() == 0
