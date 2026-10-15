"""devteam run | resume | status | costs | cleanup  (--fake: no-cost team, separate state)."""

import argparse
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
from devteam.graph import AGENTS, Team, build_graph

ROOT = Path(__file__).resolve().parents[2]
COLORS = {
    "brain": "magenta",
    "dev_pipeline": "cyan",
    "dev_app": "green",
    "tester": "yellow",
    "reviewer": "bold white",
}
console = Console()


def make_team(fake: bool) -> Team:
    cfg = yaml.safe_load((ROOT / "devteam" / "config.yaml").read_text())
    secrets = {k: v for k, v in dotenv_values(ROOT / "devteam" / ".env").items() if v}
    if fake:
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
    try:
        watch(team, graph, inp, config)
    except KeyboardInterrupt:
        sys.exit("\nstopped. Continue with `devteam resume`.")
    except Exception as e:  # usage limit, budget cap, crash: the checkpoint keeps the progress
        console.print(f"[red]stopped:[/] {team.redact(str(e))}")
        sys.exit("Fix the cause, then `devteam resume` continues where it stopped.")
    results = graph.get_state(config).values.get("results", [])
    for r in results:
        console.print(f"{r['id']}: [bold]{r['status']}[/] — {Path(r['packet']).relative_to(ROOT)}")
    if not results:
        console.print("nothing to do: no todo tasks in the backlog")


def show_status(team: Team):
    t = Table("id", "status", "owner", "title")
    for task in team.load_backlog():
        t.add_row(task["id"], task["status"], task.get("owner", ""), task["title"])
    console.print(t)


def main():
    p = argparse.ArgumentParser(prog="devteam")
    p.add_argument("command", choices=["run", "resume", "status", "costs", "cleanup"])
    p.add_argument("--fake", action="store_true", help="fake agents, no cost, separate state")
    args = p.parse_args()
    team = make_team(args.fake)
    if args.command in ("run", "resume"):
        run_graph(team, resume=args.command == "resume")
    elif args.command == "status":
        show_status(team)
    elif args.command == "costs":
        costs.report(team.home, console)
    elif args.command == "cleanup":
        for line in team.cleanup() or ["nothing to clean: no done/dropped task has a worktree"]:
            console.print(line)


if __name__ == "__main__":
    main()
