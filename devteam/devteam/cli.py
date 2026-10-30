"""Kerege CLI: devteam run [--continuous]|resume [task]|stop [task]|status|costs|report|release|
cleanup|dashboard|rehearse (--fake: no cost, local remote). `run` / `resume` go through the
watchdog (devteam/watchdog.py): the orchestrator runs in a child process."""

import argparse
import os
import queue
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import yaml
from dotenv import dotenv_values
from langgraph.checkpoint.sqlite import SqliteSaver
from rich.console import Console
from rich.live import Live
from rich.table import Table

from devteam import costs
from devteam import watchdog as W
from devteam.agents import FakeAgents
from devteam.continuous import Continuous, summary_lines
from devteam.costs import BudgetExceeded, SubscriptionLimit
from devteam.gates import EMPTY_BENCH, release
from devteam.graph import AGENTS, StopRun, Team, build_graph
from devteam.report import write_report, write_reports

ROOT = Path(__file__).resolve().parents[2]
COLORS = {
    "brain": "magenta",
    "architect": "blue",
    "analyst": "bright_magenta",
    "dev_pipeline": "cyan",
    "dev_core": "bright_cyan",
    "dev_app": "green",
    "tester": "yellow",
    "reviewer": "bold white",
    "gates": "yellow",
}
console = Console()


def make_team(fake: bool) -> Team:
    cfg = yaml.safe_load((ROOT / "devteam" / "config.yaml").read_text())
    secrets = {k: v for k, v in dotenv_values(ROOT / "devteam" / ".env").items() if v}
    if fake:  # "no cost" includes CPU: the real benchmark set takes ~70 min per commit
        cfg["gates"]["benchmark"]["command"] = EMPTY_BENCH
        agents = FakeAgents()
    else:
        from devteam.real import RealAgents

        agents = RealAgents()
    return Team(ROOT, cfg, secrets, agents, fake=fake)


def refuse(msg: str):
    """Did not start: not a crash, the watchdog does not resume it."""
    print(msg, file=sys.stderr)
    sys.exit(W.REFUSED)


def status_table(team: Team, working: dict) -> Table:
    t = Table(box=None, show_header=False, padding=(0, 2), title="Kerege", title_justify="left")
    for a in AGENTS:
        tasks = working.get(a)
        state = f"[bold]working[/] on {', '.join(sorted(tasks))}" if tasks else "[dim]idle[/]"
        t.add_row(f"[{COLORS[a]}]{a}[/]", state)
    spent = costs.spent_24h(team.home)
    t.add_row("[dim]API 24 h[/]", f"${spent:.2f} / ${team.cfg['daily_budget_usd']:.2f}")
    return t


def show_event(team: Team, live: Live, working: dict, e: dict):
    a = e["agent"]
    if e["status"] in ("working", "waiting"):
        working.setdefault(a, set()).add(e["task"] or "backlog")
    elif e["status"] == "idle":
        working.get(a, set()).discard(e["task"] or "backlog")
    arrow = f" → [{COLORS.get(e['to'], 'white')}]{e['to']}[/]" if e["to"] else ""
    task = f"[dim]{e['task']}[/] " if e["task"] else ""
    live.console.print(
        f"[dim]{e['t']}[/] [{COLORS.get(a, 'white')}]{a}[/]{arrow} {task}{e['msg']}",
        markup=True,
        highlight=False,
    )
    live.update(status_table(team, working))


def watch(team: Team, graph, inp, config):
    working: dict[str, set] = {}
    with Live(status_table(team, working), console=console, refresh_per_second=4) as live:
        for _ns, e in graph.stream(inp, config, stream_mode="custom", subgraphs=True):
            show_event(team, live, working, e)
    console.print()


def run_continuous(team: Team):
    """Developers loop on their own; the first Ctrl+C finishes the current steps cleanly."""
    db = sqlite3.connect(team.home / "checkpoints.sqlite", check_same_thread=False)
    cont = Continuous(team, build_graph(team, SqliteSaver(db)))
    if not team.fake and (drift := team.backlog_drift()):
        mine = cont.ignored_drift()  # claims of rounds this run resumes
        if drift := [d for d in drift if d.split(".", 1)[0] not in mine]:
            refuse(
                "backlog.yaml status fields differ from origin/dev; merge them into dev first:\n"
                + "\n".join(drift)
            )
    team.unblock_ready()
    pid_file = team.home / "run.pid"
    pid_file.write_text(str(os.getpid()))
    W.heartbeat(team)
    presses = []

    def on_int(signum, frame):
        presses.append(1)
        if len(presses) == 1:
            console.print(
                "[yellow]Kerege: finishing the current steps, then stopping. "
                "Ctrl+C again kills everything now.[/]"
            )
            cont.halt("Ctrl+C")
            return
        hard_stop("Ctrl+C twice")

    def hard_stop(reason: str):
        team.kill_all()
        cont.stop_reason = cont.stop_reason or reason
        finish(cont.finish())
        pid_file.unlink(missing_ok=True)
        sys.stdout.flush()
        os._exit(130)  # every finished step is checkpointed; the next run resumes the rest

    def finish(summary: dict):
        console.print()
        for line in summary_lines(summary):
            console.print(line, markup=False, highlight=False)
        console.print(f"report: {write_reports(team, cont.t0)}")

    signal.signal(signal.SIGINT, on_int)
    signal.signal(signal.SIGTERM, lambda *_: hard_stop("devteam stop"))
    working: dict[str, set] = {}
    try:
        threads = cont.start()
        with Live(status_table(team, working), console=console, refresh_per_second=4) as live:
            while any(t.is_alive() for t in threads) or not cont.events.empty():
                try:
                    show_event(team, live, working, cont.events.get(timeout=0.25))
                except queue.Empty:
                    pass
        s = cont.finish()
        finish(s)
        if cont.crash:  # main records it, the watchdog resumes
            raise cont.crash
        usd = sum(x["usd"] for x in s["spend"] if x["billed"])
        W.notify(team, W.run_line(s["tasks"], usd, s["stop_reason"]))
    finally:
        pid_file.unlink(missing_ok=True)


def run_graph(team: Team, resume: bool):
    thread_file = team.home / "thread"
    db = sqlite3.connect(team.home / "checkpoints.sqlite", check_same_thread=False)
    graph = build_graph(team, SqliteSaver(db))
    if resume:
        if not thread_file.exists():
            refuse("nothing to resume")
        config = {"configurable": {"thread_id": thread_file.read_text()}}
        snap, inp = graph.get_state(config), None
        if snap.metadata is None:  # it crashed before its first checkpoint: start it again
            inp = {}
        elif not snap.tasks:  # not .next: it hides a step whose writes were saved, not applied
            refuse("the last run finished; nothing to resume")
    else:
        if thread_file.exists():
            old = {"configurable": {"thread_id": thread_file.read_text()}}
            if graph.get_state(old).tasks:
                refuse(
                    "the last run did not finish: `devteam resume` (or delete "
                    f"{thread_file} to drop it)"
                )
        thread_file.write_text(time.strftime("run-%Y%m%d-%H%M%S"))
        config = {"configurable": {"thread_id": thread_file.read_text()}}
        inp = {}
    pid_file = team.home / "run.pid"
    pid_file.write_text(str(os.getpid()))
    W.heartbeat(team)
    mark = costs.last_row(team.home)
    since = time.mktime(time.strptime(config["configurable"]["thread_id"], "run-%Y%m%d-%H%M%S"))

    def on_stop(signum, frame):  # Ctrl-C or `devteam stop`: kill agents, checks, benchmark
        team.kill_all()
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_stop)
    signal.signal(signal.SIGINT, on_stop)
    try:
        watch(team, graph, inp, config)
    except KeyboardInterrupt:
        team.kill_all()
        print(f"\nstopped. Report: {write_reports(team, since)}. Continue with `devteam resume`.")
        sys.stdout.flush()
        os._exit(130)  # don't wait for worker threads; every finished step is checkpointed
    except (BudgetExceeded, SubscriptionLimit, StopRun) as e:  # the checkpoint keeps the progress
        console.print(f"[red]stopped:[/] {team.redact(str(e))}")
        write_reports(team, since)
        results = graph.get_state(config).values.get("results", [])
        W.notify(team, W.run_line(results, run_usd(team, mark), f"stopped: {e}"))
        console.print("Fix the cause, then `devteam resume` continues where it stopped.")
        sys.exit(W.HALTED)
    except Exception:  # a crash: main records it, the watchdog resumes once
        write_reports(team, since)
        raise
    finally:
        pid_file.unlink(missing_ok=True)
    results = graph.get_state(config).values.get("results", [])
    for r in results:
        console.print(f"{r['id']}: [bold]{r['status']}[/] {r.get('pr', '')}")
    if not results:
        console.print("nothing to do: no todo tasks in the backlog")
    if release_due(team):
        console.print(release(team))
    console.print(f"report: {write_reports(team, since)}")
    W.notify(team, W.run_line(results, run_usd(team, mark)))


def run_usd(team: Team, mark: int) -> float:
    return sum(usd or 0 for _t, _a, billed, _n, usd, _i, _o in costs.spend_since(team.home, mark)
               if billed)  # fmt: skip


def release_due(team: Team) -> bool:
    last = team.home / "last_release"
    if not last.exists():
        return True
    age = time.time() - time.mktime(time.strptime(last.read_text().strip(), "%Y-%m-%d"))
    return age >= team.cfg["release_every_days"] * 86400


def stop(team: Team):
    """The run, and its watchdog (which then never resumes; it may be waiting to)."""
    sent = False
    for name in ("run.pid", "watchdog.pid"):
        pid_file = team.home / name
        try:
            os.kill(int(pid_file.read_text()), signal.SIGTERM)
            sent = True
        except (FileNotFoundError, ValueError):
            pass
        except ProcessLookupError:
            pid_file.unlink()
    if not sent:
        sys.exit("no devteam run is active")
    print("stop sent; finished steps are kept, `devteam resume` continues later")


def rehearse(cmd: list[str] | None = None) -> int:
    """`devteam rehearse`: `run --fake --only` the first plannable todo task of each developer,
    through the watchdog, on a fresh fake state (fake backlog = devteam/backlog.yaml, fake remote
    = origin/dev, no devteam-fake/* branches or worktrees). PASS: the run exited cleanly, every
    task is done, nothing crashed."""
    home = ROOT / "devteam" / ".state" / "fake"
    for name in ("run.pid", "watchdog.pid"):
        try:
            os.kill(int((home / name).read_text()), 0)
            refuse("a --fake run is active: `devteam stop --fake` first")
        except (FileNotFoundError, ValueError, ProcessLookupError):
            pass
    shutil.rmtree(home, ignore_errors=True)  # only fake-run state lives there
    git = ["git", "-C", str(ROOT)]
    subprocess.run([*git, "worktree", "prune"], check=True)
    refs = subprocess.run(
        [*git, "for-each-ref", "--format=delete %(refname)", "refs/heads/devteam-fake/",
         "refs/remotes/devteam-fake/"], check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    subprocess.run([*git, "update-ref", "--stdin"], input=refs, check=True, text=True)
    team = make_team(True)  # seeds the fake backlog and remote again

    def plannable() -> list[str]:
        tasks = team.load_backlog()
        done = {t["id"] for t in tasks if t["status"] == "done"}
        ready = [
            t
            for t in tasks
            if t["status"] == "todo"
            and not t.get("owner_only")
            and set(t.get("depends_on") or []) <= done
        ]
        picked: list[str] = []  # one task per developer, so dev_core gets its own core task
        for d in team.cfg["developers"].values():
            mine = [t["id"] for t in ready if t.get("area") in d["areas"]]
            if tid := next((x for x in mine if x not in picked), None):
                picked.append(tid)
        return picked

    pick = plannable()
    if not pick:  # every open task waits on a blocked one (2026-10-23: S1, DEF1-api and U2
        # blocked by limits held the whole chain): the machinery is rehearsed on the fixture
        # backlog instead; fake agents never read a task's text anyway
        fixture = Path(__file__).parents[1] / "tests" / "backlog.yaml"
        console.print(f"rehearse: nothing plannable in devteam/backlog.yaml; using {fixture}")
        shutil.copy(fixture, team.backlog_path)
        pick = plannable()
    if not pick:
        refuse("nothing to rehearse: no plannable todo task")
    only, t0 = ",".join(pick), time.time()
    console.print(f"rehearse: `devteam run --fake --only {only}` on a fresh fake state")
    code = subprocess.run(
        [*(cmd or [sys.executable, sys.argv[0]]), "run", "--fake", "--only", only]
    ).returncode
    status = {t["id"]: t["status"] for t in team.load_backlog() if t["id"] in pick}
    crashed = (team.home / "last_crash.json").exists()
    ok = code == 0 and not crashed and all(v == "done" for v in status.values())
    line = ", ".join(f"{k} {v}" for k, v in status.items())
    print(
        f"rehearse: {'PASS' if ok else 'FAIL'} in {(time.time() - t0) / 60:.1f} min "
        f"(exit {code}; {line}"
        + ("; crashed, see .state/fake/last_crash.json" if crashed else "")
        + ")"
    )
    return 0 if ok else 1


def control_task(team: Team, kind: str, tid: str):
    """`devteam stop <task>` / `devteam resume <task>`: one task, the others keep going."""
    if not any(t["id"] == tid for t in team.load_backlog()):
        sys.exit(f"no task {tid} in the backlog")
    team.request(kind, tid)
    from devteam.dashboard import run_active

    if run_active(team):
        print(
            f"{kind} {tid} sent to the running Kerege: "
            + (
                "its current step is killed within seconds and the task is blocked"
                if kind == "stop"
                else "back to todo when the brain next plans"
            )
        )
        return
    for line in team.apply_controls(running=False):
        print(line)


def show_status(team: Team):
    t = Table("id", "status", "owner", "title", "PR / reason")
    for task in team.load_backlog():
        t.add_row(
            task["id"],
            task["status"],
            task.get("owner", ""),
            task["title"],
            task.get("pr") or task.get("blocked_reason", "")[:60],
        )
    console.print(t)


def main():
    p = argparse.ArgumentParser(prog="devteam")
    p.add_argument(
        "command",
        choices=[
            "run",
            "resume",
            "stop",
            "status",
            "costs",
            "report",
            "release",
            "cleanup",
            "dashboard",
            "rehearse",
            "undo",
        ],
    )
    p.add_argument(
        "task",
        nargs="?",
        help="stop / resume: only this task, e.g. A7; undo: a plan change's commit",
    )
    p.add_argument(
        "--continuous",
        action="store_true",
        help="run: keep planning per developer until a stop condition (config: continuous)",
    )
    p.add_argument("--port", type=int, default=8765, help="dashboard: local port")
    p.add_argument("--fake", action="store_true", help="fake agents, no cost, separate state")
    p.add_argument("--only", default="", help="run: only these task ids, e.g. --only P1,A1a")
    args = p.parse_args()
    if args.command == "rehearse":
        sys.exit(rehearse())
    team = make_team(args.fake)
    team.only = {t for t in args.only.split(",") if t}
    if args.command in ("run", "resume", "costs", "report"):  # the dashboard only reads
        costs.mark_stale(team.home)
    if args.task and args.command in ("stop", "resume"):
        return control_task(team, args.command, args.task)
    if args.command == "undo":  # a new commit on dev that reverts one plan change
        try:
            sha = team.undo(args.task or "")
        except (ValueError, RuntimeError) as e:
            sys.exit(f"undo: {e}")
        console.print(f"undo: {sha[:9]} pushed to {team.cfg['base_branch']}")
        return
    if args.task:
        sys.exit(f"`devteam {args.command}` takes no task id")
    if args.command in ("run", "resume"):
        if not os.environ.get(W.WATCHED):
            sys.exit(W.supervise(team, sys.argv[1:]))
        try:
            orchestrate(team, args)
        except Exception as e:  # the orchestrator itself crashed (a task's crash blocks only it)
            team.kill_all()
            W.record_crash(team, e)
            raise
    elif args.command == "dashboard":
        from devteam.dashboard import serve

        serve(team, args.port)
    elif args.command == "stop":
        stop(team)
    elif args.command == "report":
        console.print(f"report: {write_report(team)}")
    elif args.command == "release":
        console.print(release(team))
    elif args.command == "status":
        show_status(team)
    elif args.command == "costs":
        costs.report(team.home, console)
    elif args.command == "cleanup":
        for line in team.cleanup() or ["nothing to clean: no done/dropped task has a worktree"]:
            console.print(line)


def orchestrate(team: Team, args):
    if args.command == "run" and args.continuous:
        return run_continuous(team)
    if args.command == "run" and not args.fake and (drift := team.backlog_drift()):
        refuse(  # statuses kept only in the working tree get lost: get them into dev first
            "backlog.yaml status fields differ from origin/dev; merge them into dev first:\n"
            + "\n".join(drift)
        )
    if args.command == "run":  # dependencies finished outside a run (e.g. done by hand)
        team.unblock_ready()
    run_graph(team, resume=args.command == "resume")


if __name__ == "__main__":
    main()
