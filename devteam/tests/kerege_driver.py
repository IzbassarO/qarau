"""`devteam <args>` on a test repo (KEREGE_REPO) with instant fake agents: what the watchdog and
rehearse tests start as the CLI. KEREGE_CRASH fails the orchestrator itself (its live view, not
a task) at the first event of each of the first KEREGE_CRASHES runs: raise | kill (SIGKILL) |
budget | limit | stop (`devteam stop`)."""

import copy
import os
import signal
from pathlib import Path

from conftest import CFG

from devteam import cli
from devteam.agents import FakeAgents
from devteam.costs import BudgetExceeded, SubscriptionLimit
from devteam.graph import Team


class Fast(FakeAgents):
    delay = 0


repo = Path(os.environ["KEREGE_REPO"])
cli.ROOT = repo
# a real --fake run: its own fake remote in .state/fake, seeded from the repo's dev and main
cli.make_team = lambda fake: Team(repo, copy.deepcopy(CFG), {}, Fast(), fake=True)
counter = repo.parent / "crashes"  # runs crashed so far
show, seen = cli.show_event, []


def show_event(*a):
    if not seen:
        seen.append(1)
        n = int(counter.read_text()) if counter.exists() else 0
        if n < int(os.environ.get("KEREGE_CRASHES", "0")):
            counter.write_text(str(n + 1))
            kind = os.environ["KEREGE_CRASH"]
            if kind in ("kill", "stop"):
                os.kill(os.getpid(), signal.SIGKILL if kind == "kill" else signal.SIGTERM)
            raise {
                "raise": RuntimeError("injected orchestrator bug"),
                "budget": BudgetExceeded("daily API budget reached"),
                "limit": SubscriptionLimit("usage limit reached", None),
            }[kind]
    return show(*a)


cli.show_event = show_event
cli.main()
