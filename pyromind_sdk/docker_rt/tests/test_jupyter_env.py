"""Unit tests for the JupyterLab web-terminal detection.

The detector reads ``/proc``, so the tests build a fake ``/proc`` tree — that
also keeps them honest on macOS, where there is no ``/proc`` at all and the
answer must simply be "no" rather than an exception.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ..backend import jupyter_env

#: A pid that is not running anywhere the tests can observe. Both macOS and
#: Linux answer ESRCH for it.
DEAD_PID = 80566


def _fake_proc(root: Path, tree: dict[int, tuple[int, str | list[str]]]) -> str:
    """Materialise ``tree`` ({pid: (ppid, cmdline)}) as a /proc-like directory."""
    proc = root / "proc"
    for pid, (ppid, cmdline) in tree.items():
        entry = proc / str(pid)
        entry.mkdir(parents=True, exist_ok=True)
        argv = cmdline if isinstance(cmdline, list) else [cmdline]
        # NUL-separated, trailing NUL — exactly what the kernel writes.
        (entry / "cmdline").write_bytes(
            b"".join(part.encode() + b"\0" for part in argv)
        )
        # The comm field is parenthesised; a space inside it must not shift the
        # ppid, which is field 4 overall.
        (entry / "stat").write_text(f"{pid} (proc{pid}) S {ppid} 1 1\n")
    return str(proc)


# pid 900 is "the daemon", 800 its shell, 700 the terminal server, 1 the
# entrypoint — the shape of a real pod.
_POD_TREE: dict[int, tuple[int, str | list[str]]] = {
    1: (0, ["sleep", "infinity"]),
    700: (1, ["node", "/opt/conda/bin/jupyter-lab", "--no-browser"]),
    800: (700, ["bash"]),
    900: (800, ["python", "-m", "docker_rt"]),
}


def test_detects_a_jupyterlab_ancestor(tmp_dir: Path) -> None:
    root = _fake_proc(tmp_dir, _POD_TREE)

    assert jupyter_env.is_jupyterlab_terminal(900, proc_root=root) is True


def test_a_chain_without_jupyter_is_not_one(tmp_dir: Path) -> None:
    tree = {
        1: (0, ["sleep", "infinity"]),
        800: (1, ["bash"]),
        900: (800, ["python", "-m", "docker_rt"]),
    }
    root = _fake_proc(tmp_dir, tree)

    assert jupyter_env.is_jupyterlab_terminal(900, proc_root=root) is False


def test_the_daemon_itself_counts_too(tmp_dir: Path) -> None:
    """Parity with the ``pid=$$`` one-liner: the walk starts at self."""
    tree = {1: (0, ["sleep"]), 900: (1, ["python", "-m", "docker_rt"])}
    root = _fake_proc(tmp_dir, tree)

    assert jupyter_env.is_jupyterlab_terminal(900, proc_root=root) is False
    # …and a self that *is* the server (single-process deployment) matches.
    tree[900] = (1, ["jupyter-lab"])
    root = _fake_proc(tmp_dir, tree)
    assert jupyter_env.is_jupyterlab_terminal(900, proc_root=root) is True


@pytest.mark.parametrize(
    "server_argv",
    [
        ["jupyter-lab"],
        ["/opt/conda/bin/jupyter-lab", "--no-browser"],
        ["python", "-m", "jupyterlab"],
        ["jupyter", "lab", "--no-browser"],
        ["Jupyter-Lab"],  # case-insensitive
    ],
)
def test_every_spelling_of_the_server_matches(
    tmp_dir: Path, server_argv: list[str]
) -> None:
    tree = dict(_POD_TREE)
    tree[700] = (1, server_argv)
    root = _fake_proc(tmp_dir, tree)

    assert jupyter_env.is_jupyterlab_terminal(900, proc_root=root) is True


def test_an_unrelated_jupyter_process_is_not_an_ancestor(tmp_dir: Path) -> None:
    """The ``ps -ef | grep jupyter-lab`` trap: a sibling must never match.

    That one-liner reports "yes" everywhere because the grep's *own* command
    line shows up in the listing. Walking ancestors only — pid 500 here is a
    sibling of the shell, not its parent — is what makes this detector sound.
    """
    tree = {
        1: (0, ["sleep", "infinity"]),
        800: (1, ["bash"]),
        900: (800, ["python", "-m", "docker_rt"]),
        500: (1, ["grep", "jupyter-lab"]),
    }
    root = _fake_proc(tmp_dir, tree)

    assert jupyter_env.is_jupyterlab_terminal(900, proc_root=root) is False


def test_pid_one_is_inspected_because_the_server_is_often_the_entrypoint(
    tmp_dir: Path,
) -> None:
    """A container whose CMD *is* ``jupyter lab``: the server is PID 1."""
    tree = {
        1: (0, ["jupyter", "lab", "--ip=0.0.0.0"]),
        800: (1, ["bash"]),
        900: (800, ["python", "-m", "docker_rt"]),
    }
    root = _fake_proc(tmp_dir, tree)

    assert jupyter_env.is_jupyterlab_terminal(900, proc_root=root) is True


def test_the_walk_stops_at_the_depth_cap(tmp_dir: Path) -> None:
    """A pathological chain must not be walked forever — or past the cap.

    The cap trades a theoretical miss for a guaranteed return; a real pod chain
    is a handful of processes deep.
    """
    depth = jupyter_env._MAX_DEPTH + 10
    tree: dict[int, tuple[int, str | list[str]]] = {1: (0, ["proc1"])}
    for pid in range(2, depth + 2):
        tree[pid] = (pid - 1, [f"proc{pid}"])
    # The daemon sits at the top of a chain longer than the cap, with the
    # server at the very bottom: unreachable, so "no".
    tree[depth + 2] = (depth + 1, ["python", "-m", "docker_rt"])
    root = _fake_proc(tmp_dir, tree)

    assert jupyter_env.is_jupyterlab_terminal(depth + 2, proc_root=root) is False

    # Same chain, server moved up to just inside the cap: reachable, so "yes".
    within = depth + 2 - jupyter_env._MAX_DEPTH + 2
    tree[within] = (within - 1, ["jupyter-lab"])
    root = _fake_proc(tmp_dir, tree)

    assert jupyter_env.is_jupyterlab_terminal(depth + 2, proc_root=root) is True


def test_a_cycle_in_the_chain_terminates(tmp_dir: Path) -> None:
    tree = {
        100: (200, ["python", "-m", "docker_rt"]),
        200: (100, ["bash"]),
    }
    root = _fake_proc(tmp_dir, tree)

    assert jupyter_env.is_jupyterlab_terminal(100, proc_root=root) is False


def test_stat_parsing_survives_a_comm_with_spaces_and_parens(tmp_dir: Path) -> None:
    """``comm`` is parenthesised and may contain both — ppid comes after ")".

    A naive ``split()[3]`` reads the ppid out of the *name* here, which would
    walk the wrong parent (or none) and silently miss the server.
    """
    proc = tmp_dir / "proc"
    server = proc / "700"
    server.mkdir(parents=True)
    (server / "cmdline").write_bytes(b"jupyter-lab\0")
    (server / "stat").write_text("700 (weird (name) proc) S 1 1 1\n")
    daemon = proc / "900"
    daemon.mkdir(parents=True)
    (daemon / "cmdline").write_bytes(b"python\0-m\0docker_rt\0")
    (daemon / "stat").write_text("900 (python) S 700 1 1\n")
    one = proc / "1"
    one.mkdir(parents=True)
    (one / "cmdline").write_bytes(b"sleep\0")
    (one / "stat").write_text("1 (sleep) S 0 1 1\n")

    assert jupyter_env.is_jupyterlab_terminal(900, proc_root=str(proc)) is True


def test_a_missing_proc_root_is_simply_no(tmp_dir: Path) -> None:
    """macOS (no /proc) must answer "no", not raise."""
    missing = tmp_dir / "does-not-exist"

    assert jupyter_env.is_jupyterlab_terminal(proc_root=str(missing)) is False


def test_unreadable_entries_are_skipped_not_fatal(tmp_dir: Path) -> None:
    tree = dict(_POD_TREE)
    root = _fake_proc(tmp_dir, tree)
    # A stat file that is not parseable: the walk ends there, quietly.
    (Path(root) / "800" / "stat").write_text("garbage")

    assert jupyter_env.is_jupyterlab_terminal(900, proc_root=root) is False


def test_ancestor_cmdlines_yields_nearest_first(tmp_dir: Path) -> None:
    root = _fake_proc(tmp_dir, _POD_TREE)

    seen = list(jupyter_env.ancestor_cmdlines(900, proc_root=root))

    assert seen[0].endswith("docker_rt")
    assert seen[1] == "bash"
    assert "jupyter-lab" in seen[2]


def test_cmdline_nuls_become_spaces(tmp_dir: Path) -> None:
    root = _fake_proc(tmp_dir, {42: (1, ["a", "b", "c"])})

    assert jupyter_env.proc_cmdline(42, proc_root=root) == "a b c"


def test_proc_ppid_reads_field_four(tmp_dir: Path) -> None:
    root = _fake_proc(tmp_dir, {7: (3, "x"), 3: (1, "y")})

    assert jupyter_env.proc_ppid(7, proc_root=root) == 3
    assert jupyter_env.proc_ppid(3, proc_root=root) == 1
    assert jupyter_env.proc_ppid(999999, proc_root=root) is None


def test_the_live_process_chain_does_not_explode() -> None:
    """Smoke test against the real ``/proc`` (or its absence) — must not raise.

    The answer itself is environment-dependent: under a plain test runner it is
    ``False``, and were this ever run inside a JupyterLab terminal the value
    would flip without the code being wrong.
    """
    assert isinstance(jupyter_env.is_jupyterlab_terminal(), bool)
