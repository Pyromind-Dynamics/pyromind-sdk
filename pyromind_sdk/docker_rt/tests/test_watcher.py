from __future__ import annotations

from pytest import MonkeyPatch


def test_watch_and_restore_restores_then_exits(
    monkeypatch: MonkeyPatch,
) -> None:
    from .. import register_context as rc_mod
    from .. import watcher as watcher_mod

    calls = []
    monkeypatch.setattr(
        watcher_mod,
        "wait_for_pid",
        lambda pid: calls.append(("wait", pid)),
    )
    monkeypatch.setattr(
        rc_mod,
        "restore_main",
        lambda: calls.append("restore") or 0,
    )

    assert watcher_mod.watch_and_restore(123) == 0
    assert calls == [("wait", 123), "restore"]


def test_watcher_main_exits_after_restore(
    monkeypatch: MonkeyPatch,
) -> None:
    from .. import watcher as watcher_mod

    calls = []
    monkeypatch.setattr(
        watcher_mod,
        "watch_and_restore",
        lambda pid: calls.append(pid) or 0,
    )

    assert watcher_mod.main(["--pid", "456"]) == 0
    assert calls == [456]
