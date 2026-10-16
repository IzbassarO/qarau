"""devteam run|resume|stop|status|costs|report|release|cleanup  (--fake: no cost, local remote)."""

import argparse
import os
import signal
import sqlite3
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
from devteam.agents import FakeAgents
from devteam.gates import EMPTY_BENCH, release
from devteam.graph import AGENTS, Team, build_graph
from devteam.report import write_report

ROOT = Path(__file__).resolve().parents[2]
COLORS = {
    "brain": "magenta",
    "dev_pipeline": "cyan",
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


def status_table(team: Team, working: dict) -> Table:
    t = Table(box=None, show_header=False, padding=(0, 2))
    for a in AGENTS:
        tasks = working.get(a)
        state = f"[bold]working[/] on {', '.join(sorted(tasks))}" if tasks else "[dim]idle[/]"
        t.add_row(f"[{COLORS[a]}]{a}[/]", state)
    spent = costs.spent_today(team.home)
    t.add_row("[dim]API today[/]", f"${spent:.2f} / ${team.cfg['daily_budget_usd']:.2f}")
    return t


def watch(team: Team, graph, inp, config):
    working: dict[str, set] = {}
    with Live(status_table(team, working), console=console, refresh_per_second=4) as live:
        for _ns, e in graph.stream(inp, config, stream_mode="custom", subgraphs=True):
            a = e["agent"]
            if e["status"] == "working":
                working.setdefault(a, set()).add(e["task"] or "backlog")
            elif e["status"] == "idle":
                working.get(a, set()).discard(e["task"] or "backlog")
            arrow = f" → [{COLORS.get(e['to'], 'white')}]{e['to']}[/]" if e["to"] else ""
            task = f"[dim]{e['task']}[/] " if e["task"] else ""
            live.console.print(
                f"[dim]{e['t']}[/] [{COLORS[a]}]{a}[/]{arrow} {task}{e['msg']}",
                markup=True,
                highlight=False,
            )
            live.update(status_table(team, working))
    console.print()


def run_graph(team: Team, resume: bool):
    thread_file = team.home / "thread"
    db = sqlite3.connect(team.home / "checkpoints.sqlite", check_same_thread=False)
    graph = build_graph(team, SqliteSaver(db))
    if resume:
        if not thread_file.exists():
            sys.exit("nothing to resume")
        config = {"configurable": {"thread_id": thread_file.read_text()}}
        if not graph.get_state(config).next:
            sys.exit("the last run finished; nothing to resume")
        inp = None
    else:
        if thread_file.exists():
            old = {"configurable": {"thread_id": thread_file.read_text()}}
            if graph.get_state(old).next:
                sys.exit(
                    "the last run did not finish: `devteam resume` (or delete "
                    f"{thread_file} to drop it)"
                )
        thread_file.write_text(time.strftime("run-%Y%m%d-%H%M%S"))
        config = {"configurable": {"thread_id": thread_file.read_text()}}
        inp = {}
    pid_file = team.home / "run.pid"
    pid_file.write_text(str(os.getpid()))

    def on_stop(signum, frame):  # Ctrl-C or `devteam stop`: kill agents, checks, benchmark
        team.kill_all()
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_stop)
    signal.signal(signal.SIGINT, on_stop)
    try:
        watch(team, graph, inp, config)
    except KeyboardInterrupt:
        team.kill_all()
        print(f"\nstopped. Report: {write_report(team)}. Continue with `devteam resume`.")
        sys.stdout.flush()
        os._exit(130)  # don't wait for worker threads; every finished step is checkpointed
    except Exception as e:  # usage limit, budget cap, crash: the checkpoint keeps the progress
        console.print(f"[red]stopped:[/] {team.redact(str(e))}")
        write_report(team)
        sys.exit("Fix the cause, then `devteam resume` continues where it stopped.")
    finally:
        pid_file.unlink(missing_ok=True)
    results = graph.get_state(config).values.get("results", [])
    for r in results:
        console.print(f"{r['id']}: [bold]{r['status']}[/] {r.get('pr', '')}")
    if not results:
        console.print("nothing to do: no todo tasks in the backlog")
    if release_due(team):
        console.print(release(team))
    console.print(f"report: {write_report(team)}")


def release_due(team: Team) -> bool:
    last = team.home / "last_release"
    if not last.exists():
        return True
    age = time.time() - time.mktime(time.strptime(last.read_text().strip(), "%Y-%m-%d"))
    return age >= team.cfg["release_every_days"] * 86400


def stop(team: Team):
    pid_file = team.home / "run.pid"
    if not pid_file.exists():
        sys.exit("no devteam run is active")
    try:
        os.kill(int(pid_file.read_text()), signal.SIGTERM)
        print("stop sent; finished steps are kept, `devteam resume` continues later")
    except ProcessLookupError:
        pid_file.unlink()
        print("the run was already gone")


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
        choices=["run", "resume", "stop", "status", "costs", "report", "release", "cleanup"],
    )
    p.add_argument("--fake", action="store_true", help="fake agents, no cost, separate state")
    p.add_argument("--only", default="", help="run: only these task ids, e.g. --only P1,A1a")
    args = p.parse_args()
    team = make_team(args.fake)
    team.only = {t for t in args.only.split(",") if t}
    if args.command == "run" and not args.fake and (drift := team.backlog_drift()):
        sys.exit(  # statuses kept only in the working tree get lost: get them into dev first
            "backlog.yaml status fields differ from origin/dev; merge them into dev first:\n"
            + "\n".join(drift)
        )
    if args.command in ("run", "resume"):
        run_graph(team, resume=args.command == "resume")
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


if __name__ == "__main__":
    main()
