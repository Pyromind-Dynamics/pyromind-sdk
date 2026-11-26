"""Is this daemon running inside a JupyterLab web terminal?

Why this exists
---------------

``docker build`` normally ships the context to the build sandbox by uploading
it to the user's workspace object storage, from where the sandbox reads it
back through its JuiceFS mount (see :mod:`context_staging`). That upload is
pure overhead when the daemon **already sits on the filesystem the sandbox
mounts**: when docker-rt is started from a JupyterLab web terminal, the daemon
runs inside the user's workspace pod, and its ``/workspace`` *is* the same
JuiceFS the build sandbox mounts at ``/workspace``. The context can simply be
written to a local directory — no network hop at all.

This module answers one question for that decision: *is this process a
descendant of a JupyterLab server?* It walks the real parent chain (the same
logic as ``ps -o ppid=`` in a loop), so it can only match a process that is
genuinely an ancestor of the daemon — never an unrelated ``jupyter-lab``
running elsewhere on the box, and never the ``grep`` of a
``ps -ef | grep jupyter-lab`` one-liner (that process is a *sibling*, not an
ancestor).

Deliberately **not** a hard guarantee: the check is a heuristic, and the
caller treats a wrong "yes" as self-healing. A local stage-in is verified by
size inside the sandbox before the build starts, and a failure there falls
back to the direct upload — so a false positive costs one retry, never a
broken build. What the check does buy is the common case: started from the
JupyterLab terminal, the upload disappears.

Known blind spot: if the terminal that started the daemon has since been
closed, the daemon was re-parented to PID 1 and the chain no longer contains
the server. Detection then (correctly) says "no" and the build takes the
storage route. Operators who want the local route regardless can force it with
``DOCKER_RT_BUILD_CONTEXT_MODE=local``.

Implementation notes: pure standard library, reads ``/proc`` directly (every
Linux container has it; ``psutil`` is not a dependency of this package), and
never raises — an unreadable ``/proc`` entry is treated as "no match". On
platforms without ``/proc`` (a developer's macOS laptop) it simply reports
"not a JupyterLab terminal", which is the right answer there.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

#: Substrings that identify a JupyterLab server process. Matched
#: case-insensitively against the **joined** cmdline (NUL → space), so
#: ``jupyter lab`` (argv ``jupyter``, ``lab``), ``jupyter-lab`` and
#: ``python -m jupyterlab`` all match.
JUPYTER_MARKERS: tuple[str, ...] = ("jupyter-lab", "jupyterlab", "jupyter lab")

#: Safety caps. A parent chain is short in practice (pod PID 1 → server →
#: shell → daemon), but a cycle — or a pathological deep chain — must never
#: hang a build over what is an optimisation.
_MAX_DEPTH = 64

#: Default ``/proc`` root. Overridable so tests can build a fake tree.
DEFAULT_PROC_ROOT = "/proc"


def _read(path: str) -> str:
    """File contents, or ``""`` for anything unreadable."""
    try:
        with open(path, "rb") as handle:
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return ""


def proc_cmdline(pid: int, *, proc_root: str = DEFAULT_PROC_ROOT) -> str:
    """``/proc/<pid>/cmdline`` with NUL separators turned into spaces.

    Empty for kernel threads and for anything that cannot be read.
    """
    raw = _read(f"{proc_root}/{pid}/cmdline")
    if not raw:
        return ""
    return " ".join(part for part in raw.split("\0") if part).strip()


def proc_ppid(pid: int, *, proc_root: str = DEFAULT_PROC_ROOT) -> int | None:
    """Parent pid from ``/proc/<pid>/stat``, or ``None``.

    The ``comm`` field (field 2) is parenthesised and **may contain spaces and
    further parentheses** — ``(sd-pam)`` is a real-world example — so the ppid
    (field 4) is read after the *last* ``)``, not by naive field splitting.
    """
    raw = _read(f"{proc_root}/{pid}/stat")
    if not raw:
        return None
    close = raw.rfind(")")
    if close < 0:
        return None
    fields = raw[close + 1 :].split()
    # After the last ")": state, ppid, pgrp, … — ppid is the second field.
    if len(fields) < 2:
        return None
    try:
        return int(fields[1])
    except ValueError:
        return None


def ancestor_cmdlines(
    start_pid: int | None = None,
    *,
    proc_root: str = DEFAULT_PROC_ROOT,
) -> Iterator[str]:
    """Cmdlines of ``start_pid`` and its ancestors, nearest first.

    PID 1 **is** inspected, unlike the classic ``while [ $pid -ne 1 ]`` loop:
    in a container the JupyterLab server is frequently the entrypoint, i.e.
    PID 1 itself, and stopping one short of it would miss exactly the case this
    module exists for. The walk ends at PID 0, an unreadable parent, a cycle,
    or :data:`_MAX_DEPTH` — whichever comes first. Yields nothing on platforms
    without ``/proc``.
    """
    pid = os.getpid() if start_pid is None else int(start_pid)
    seen: set[int] = set()
    depth = 0
    while pid and pid > 0 and depth < _MAX_DEPTH:
        if pid in seen:
            return
        seen.add(pid)
        yield proc_cmdline(pid, proc_root=proc_root)
        parent = proc_ppid(pid, proc_root=proc_root)
        if parent is None:
            return
        pid = parent
        depth += 1


def is_jupyterlab_terminal(
    start_pid: int | None = None,
    *,
    proc_root: str = DEFAULT_PROC_ROOT,
) -> bool:
    """Whether ``start_pid`` (default: this process) descends from JupyterLab.

    Never raises. On any read error the answer is simply ``False``.
    """
    for cmdline in ancestor_cmdlines(start_pid, proc_root=proc_root):
        lowered = cmdline.lower()
        if any(marker in lowered for marker in JUPYTER_MARKERS):
            return True
    return False
