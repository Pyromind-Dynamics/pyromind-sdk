from __future__ import annotations

from pytest import MonkeyPatch


def _record_sweeps(monkeypatch: MonkeyPatch, calls: list[object]) -> None:
    """Record both sweeps without letting either touch storage or the API."""
    from .. import watcher as watcher_mod

    monkeypatch.setattr(
        watcher_mod,
        "sweep_leftover_builds",
        lambda pid: calls.append(("storage", pid)),
    )
    monkeypatch.setattr(
        watcher_mod,
        "sweep_leftover_sandboxes",
        lambda: calls.append("sandbox"),
    )


def test_watch_and_restore_restores_then_sweeps_then_exits(
    monkeypatch: MonkeyPatch,
) -> None:
    """Restoring the context comes first; both sweeps are follow-ups.

    The order is the contract: a sweep can block on unreachable storage or an
    unresponsive API server, and nothing about a crashed build is worth delaying
    (or risking) the one thing this process exists to do.
    """
    from .. import register_context as rc_mod
    from .. import watcher as watcher_mod

    calls: list[object] = []
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
    _record_sweeps(monkeypatch, calls)

    assert watcher_mod.watch_and_restore(123) == 0
    assert calls == [
        ("wait", 123),
        "restore",
        ("storage", 123),
        "sandbox",
    ]


def test_a_failed_restore_still_gets_both_sweeps(
    monkeypatch: MonkeyPatch,
) -> None:
    """The jobs are independent; no failure hides another."""
    from .. import register_context as rc_mod
    from .. import watcher as watcher_mod

    calls: list[object] = []
    monkeypatch.setattr(watcher_mod, "wait_for_pid", lambda pid: None)

    def boom() -> int:
        calls.append("restore")
        raise RuntimeError("docker CLI missing")

    monkeypatch.setattr(rc_mod, "restore_main", boom)
    _record_sweeps(monkeypatch, calls)

    assert watcher_mod.watch_and_restore(9) == 1
    assert calls == ["restore", ("storage", 9), "sandbox"]


def test_a_failed_storage_sweep_still_gets_the_sandbox_sweep(
    monkeypatch: MonkeyPatch,
) -> None:
    """A dead storage backend must not leave the *running* leftovers behind."""
    from .. import register_context as rc_mod
    from .. import watcher as watcher_mod

    calls: list[object] = []
    monkeypatch.setattr(watcher_mod, "wait_for_pid", lambda pid: None)
    monkeypatch.setattr(rc_mod, "restore_main", lambda: 0)

    def boom(pid: int) -> None:
        calls.append(("storage", pid))
        raise RuntimeError("no route to storage")

    monkeypatch.setattr(watcher_mod, "sweep_leftover_builds", boom)
    monkeypatch.setattr(
        watcher_mod,
        "sweep_leftover_sandboxes",
        lambda: calls.append("sandbox"),
    )

    assert watcher_mod.watch_and_restore(9) == 0
    assert calls == [("storage", 9), "sandbox"]


def test_sweep_leftover_builds_swallows_any_failure(
    monkeypatch: MonkeyPatch,
    capsys,
) -> None:
    """Storage being unreachable must not turn into a watcher failure."""
    from .. import watcher as watcher_mod
    from ..backend import context_staging

    def boom(**_kwargs):
        raise RuntimeError("no route to storage")

    monkeypatch.setattr(context_staging, "sweep_stale_staging", boom)

    watcher_mod.sweep_leftover_builds(7)  # must not raise

    assert "could not sweep staged build contexts" in capsys.readouterr().err


def test_sweep_leftover_builds_reports_what_it_removed(
    monkeypatch: MonkeyPatch,
    capsys,
) -> None:
    from .. import watcher as watcher_mod
    from ..backend import context_staging

    seen: list[int | None] = []
    monkeypatch.setattr(
        context_staging,
        "sweep_stale_staging",
        lambda **kwargs: seen.append(kwargs.get("dead_pid")) or ["a-1-b", "c-2-d"],
    )

    watcher_mod.sweep_leftover_builds(7)

    assert seen == [7]
    err = capsys.readouterr().err
    assert "removed 2 leftover staged build context(s)" in err


def test_sweep_leftover_sandboxes_takes_no_pid_and_reports_what_it_removed(
    monkeypatch: MonkeyPatch,
    capsys,
) -> None:
    """Build sandboxes are identified by name, so which process died is irrelevant."""
    from .. import watcher as watcher_mod
    from ..backend import build_sandbox

    calls: list[str] = []

    async def _sweep() -> list[str]:
        calls.append("called")
        return ["sandbox-docker-build-a1b2c3"]

    monkeypatch.setattr(build_sandbox, "sweep_stale_build_sandboxes", _sweep)

    watcher_mod.sweep_leftover_sandboxes()

    assert calls == ["called"]
    assert "deleted 1 leftover build sandbox(es)" in capsys.readouterr().err


def test_sweep_leftover_sandboxes_swallows_any_failure(
    monkeypatch: MonkeyPatch,
    capsys,
) -> None:
    """An unresponsive API server must not turn into a watcher failure."""
    from .. import watcher as watcher_mod
    from ..backend import build_sandbox

    async def boom() -> list[str]:
        raise RuntimeError("no route to host")

    monkeypatch.setattr(build_sandbox, "sweep_stale_build_sandboxes", boom)

    watcher_mod.sweep_leftover_sandboxes()  # must not raise

    assert "could not sweep leftover build sandboxes" in capsys.readouterr().err


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
