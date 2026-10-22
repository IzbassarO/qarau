"""Watchdog (owner, 2026-10-22: Kerege crashed at 20:29, the owner found out at 01:50).

`devteam run` / `resume` start the orchestrator as a child process and look at how it ended:
finished (the child notifies with its one-line result), stopped by the owner (Ctrl+C,
`devteam stop`), halted by the budget cap or a subscription limit, refused to start: nothing
more. Anything else (an unhandled exception, killed by a signal) is a crash:
.state/last_crash.json, a notification, the steps it left running are killed, `resume` once
after RETRY_WAIT_S; a second crash (or a refused resume) stops with "needs you".
The child writes .state/heartbeat every HEARTBEAT_S; the dashboard shows its age.
Notifications: a macOS banner (osascript), and an email when DEVTEAM_NOTIFY_EMAIL is set in
devteam/.env (DEVTEAM_SMTP_HOST, _PORT, _USER, _PASSWORD there); each goes to
.state/notifications.log too.
"""

import json
import os
import shutil
import signal
import smtplib
import subprocess
import sys
import threading
import time
import traceback
from email.message import EmailMessage

from devteam import procs

FINISHED, REFUSED, HALTED, STOPPED = 0, 2, 75, 130  # child exit codes that are not a crash
RETRY_WAIT_S = 60
HEARTBEAT_S = 30
HEARTBEAT_STALE_S = 300
WATCHED = "DEVTEAM_WATCHED"  # set in the child: it runs the orchestrator, not another watchdog


# --- notifications -----------------------------------------------------------------------
def notify(team, text: str):
    """Never raises: a failed banner or email must not stop a run."""
    title = "Kerege (fake)" if team.fake else "Kerege"
    text = procs.clean_text(team.redact(text))[:400]
    sent = []
    if osa := shutil.which("osascript"):
        script = "on run argv\ndisplay notification (item 1 of argv) with title (item 2 of argv)"
        try:  # text and title as arguments, never inside the script: no quoting to get wrong
            ok = subprocess.run(
                [osa, "-e", script + "\nend run", text, title], capture_output=True, timeout=15
            )
            sent.append("banner" if ok.returncode == 0 else f"banner failed ({ok.returncode})")
        except (OSError, subprocess.TimeoutExpired) as e:
            sent.append(f"banner failed ({e})")
    if to := team.secrets.get("DEVTEAM_NOTIFY_EMAIL"):
        try:
            _email(team.secrets, to, f"{title}: {text[:90]}", text)
            sent.append("email")
        except (OSError, smtplib.SMTPException) as e:
            sent.append(f"email failed ({team.redact(str(e))[:120]})")
    else:
        sent.append("email skipped: DEVTEAM_NOTIFY_EMAIL not set in devteam/.env")
    try:
        with open(team.home / "notifications.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {text} [{', '.join(sent)}]\n")
    except OSError:
        pass


def _email(secrets: dict, to: str, subject: str, body: str):
    host, port = secrets["DEVTEAM_SMTP_HOST"], int(secrets.get("DEVTEAM_SMTP_PORT") or 587)
    user, password = secrets.get("DEVTEAM_SMTP_USER"), secrets.get("DEVTEAM_SMTP_PASSWORD")
    msg = EmailMessage()
    msg["From"] = secrets.get("DEVTEAM_NOTIFY_FROM") or user or to
    msg["To"], msg["Subject"] = to, subject
    msg.set_content(body)
    smtp = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    with smtp(host, port, timeout=30) as s:
        if port != 465:
            s.starttls()
        if user:
            s.login(user, password or "")
        s.send_message(msg)


def run_line(results: list[dict], usd: float, why: str = "") -> str:
    """The one line a finished run is notified with."""

    def part(status: str) -> str:
        ids = [r["id"] for r in results if r["status"] == status]
        return f"{len(ids)} {status}" + (f" ({', '.join(ids)})" if ids else "")

    return f"run finished: {part('done')}, {part('blocked')}, API ${usd:.2f}" + (
        f"; {why}" if why else ""
    )


# --- in the child ------------------------------------------------------------------------
def heartbeat(team):
    def beat():
        while True:
            try:
                (team.home / "heartbeat").write_text(f"{time.time():.0f} {os.getpid()}\n")
            except OSError:
                pass
            time.sleep(HEARTBEAT_S)

    threading.Thread(target=beat, name="heartbeat", daemon=True).start()


def heartbeat_age(team, now: float) -> float | None:
    try:
        return now - float((team.home / "heartbeat").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def record_crash(team, e: BaseException):
    """The orchestrator's own unhandled exception, for the watchdog and the owner."""
    at = traceback.extract_tb(e.__traceback__)[-1] if e.__traceback__ else None
    _write(team, {
        "pid": os.getpid(),
        "error": team.redact(f"{type(e).__name__}: {e}")[:1000],
        "where": f"{os.path.basename(at.filename)}:{at.lineno}" if at else "",
        "traceback": team.redact("".join(traceback.format_exception(e))[-4000:]),
    })  # fmt: skip


def _write(team, info: dict):
    info = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **info}
    tmp = team.home / "last_crash.json.tmp"
    tmp.write_text(json.dumps(procs.clean(info), indent=1))
    os.replace(tmp, team.home / "last_crash.json")


# --- the watchdog (parent) ---------------------------------------------------------------
def retry_args(args: list[str]) -> list[str]:
    """`run` continues with `resume`; `run --continuous` resumes its pending rounds itself."""
    if "--continuous" in args:
        return args
    return ["resume" if a == "run" else a for a in args]


def reap_steps(team):
    """Steps a killed orchestrator left running (it records each one in .state/steps/): an
    agent still editing a worktree must not meet the resumed round there."""
    for f in (team.home / "steps").glob("*-*"):
        pgid, marker = f.name.split("-", 1)
        procs.reap(int(pgid), marker)
        f.unlink(missing_ok=True)


def supervise(team, args: list[str], cmd: list[str] | None = None, wait_s=RETRY_WAIT_S) -> int:
    """Runs `cmd + args` (default: this same CLI) and returns its exit code."""
    cmd = cmd or [sys.executable, sys.argv[0]]
    stop = threading.Event()  # Ctrl+C, `devteam stop`, a closed terminal: never resume after it

    def on_signal(signum, frame):  # the child gets Ctrl+C / `devteam stop` itself
        stop.set()

    sigs = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    old = {sig: signal.signal(sig, on_signal) for sig in sigs}
    pid_file = team.home / "watchdog.pid"
    pid_file.write_text(str(os.getpid()))
    if not team.secrets.get("DEVTEAM_NOTIFY_EMAIL"):
        print("watchdog: email notifications off (DEVTEAM_NOTIFY_EMAIL not set in devteam/.env)")
    try:
        for attempt in (1, 2):
            if attempt == 2:
                args = retry_args(args)
            child = subprocess.Popen([*cmd, *args], env={**os.environ, WATCHED: "1"})
            code = child.wait()
            if code in (FINISHED, HALTED, STOPPED) or (attempt == 1 and code == REFUSED):
                return code
            why = _crashed(team, child.pid, code, args, attempt)
            if attempt == 2 or stop.is_set():
                notify(team, f"needs you: {why}. Not resumed: fix the cause, then "
                       "`devteam resume`. Details: devteam/.state/last_crash.json")  # fmt: skip
                return code
            notify(team, f"crashed: {why}. Resuming in {wait_s:g} s.")
            print(f"watchdog: {why}; `devteam {' '.join(retry_args(args))}` in {wait_s:g} s "
                  "(Ctrl+C or `devteam stop` cancels)", flush=True)  # fmt: skip
            if stop.wait(wait_s):
                print("watchdog: resume cancelled")
                return code
        return code
    finally:
        pid_file.unlink(missing_ok=True)
        for sig, h in old.items():
            signal.signal(sig, h)


def _crashed(team, pid: int, code: int, args: list[str], attempt: int) -> str:
    """Records the crash (with the child's own details when it wrote them) and kills the steps
    it left. Returns the reason in one line."""
    try:
        info = json.loads((team.home / "last_crash.json").read_text())
    except (OSError, json.JSONDecodeError):
        info = {}
    if info.get("pid") != pid:  # killed: it could write nothing itself
        how = f"killed by {signal.Signals(-code).name}" if code < 0 else f"exited with {code}"
        info = {"pid": pid, "error": f"the orchestrator {how}", "where": ""}
    if attempt == 2 and code == REFUSED:
        info["error"] = "the resume refused to start (see the terminal)"
    info.update(exit_code=code, command="devteam " + " ".join(args), attempt=attempt)
    _write(team, info)
    (team.home / "run.pid").unlink(missing_ok=True)  # a killed child could not remove it
    reap_steps(team)
    where = f" at {info['where']}" if info.get("where") else ""
    return f"{info['error'][:200]}{where}"
