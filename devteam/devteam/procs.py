"""No step leaves a process or a listening port behind (owner rule, 2026-10-17).

Every step runs in its own process group with KEREGE_STEP=<id> in its environment. When the
step ends, `reap` finds what it started (the group, plus anything that moved to a new session
but still carries the marker), reports their listening TCP ports, and kills them all.
Uses `ps` and `lsof`, present on macOS and the Ubuntu CI runners.
"""

import os
import signal
import socket
import subprocess
import time
import uuid

MARKER = "KEREGE_STEP"


def new_marker() -> str:
    return uuid.uuid4().hex


def _ps(*args: str) -> str:
    return subprocess.run(["ps", *args], capture_output=True, text=True).stdout


def step_pids(pgid: int, marker: str) -> set[int]:
    """Live processes of the step: its process group, and any process with its marker."""
    me = os.getpid()
    pids = {
        int(pid)
        for pid, group in (line.split() for line in _ps("-A", "-o", "pid=,pgid=").splitlines())
        if int(group) == pgid
    }
    tag = f"{MARKER}={marker}"
    for line in _ps("axeww", "-o", "pid=,command=").splitlines():  # BSD + procps: env shown
        pid, _, rest = line.strip().partition(" ")
        if tag in rest.split():
            pids.add(int(pid))
    return pids - {me}


def _cpu_s(t: str) -> float:
    """`ps -o time=`: [dd-]hh:mm:ss (procps) or m:ss.cc (BSD)."""
    days, _, t = t.rpartition("-")
    secs = 0.0
    for part in t.split(":"):
        secs = secs * 60 + float(part)
    return secs + int(days or 0) * 86400


def cpu_times(pgid: int, marker: str) -> dict[int, float]:
    """CPU seconds so far of each live process of the step (the supervisor's progress signal)."""
    pids = step_pids(pgid, marker)
    if not pids:
        return {}
    out = _ps("-o", "pid=,time=", "-p", ",".join(map(str, pids)))
    return {int(pid): _cpu_s(t) for pid, t in (line.split() for line in out.splitlines())}


def free_port() -> int:
    """A TCP port nobody listens on right now (for a step's own fresh server)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def listening(pids: set[int]) -> list[tuple[int, int]]:
    """(port, pid) of TCP sockets these processes listen on."""
    if not pids:
        return []
    out = subprocess.run(
        ["lsof", "-nP", "-a", "-iTCP", "-sTCP:LISTEN", "-p", ",".join(map(str, pids)), "-Fpn"],
        capture_output=True,
        text=True,
    ).stdout
    found, pid = set(), 0
    for line in out.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("n") and ":" in line:
            port = line.rsplit(":", 1)[1]
            if port.isdigit():
                found.add((int(port), pid))
    return sorted(found)


def _kill(pids: set[int], pgid: int):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        for pid in pids:
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
        for _ in range(20):  # up to 2 s for a clean exit before SIGKILL
            if not any(_alive(p) for p in pids):
                return
            time.sleep(0.1)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # a zombie is dead for our purpose (its parent reaps it)
    return "Z" not in _ps("-o", "stat=", "-p", str(pid)).strip()


def reap(pgid: int, marker: str) -> list[tuple[int, int]]:
    """Kill everything the step started. Returns the (port, pid) it left listening."""
    pids = step_pids(pgid, marker)
    ports = listening(pids)
    if pids:
        _kill(pids, pgid)
    return ports
