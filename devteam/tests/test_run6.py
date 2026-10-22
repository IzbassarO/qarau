"""Owner, 2026-10-21 20:29: UX-dense's tester crashed on a lone surrogate (its diff has a binary
fixture, `dense.color.u8`, that git() decoded with surrogateescape), and the crash stopped the
whole run although the log said "the others go on"."""

import copy
import sqlite3
from pathlib import Path

from conftest import CFG, remote_of
from langgraph.checkpoint.sqlite import SqliteSaver
from test_fixes import StubClient

from devteam.agents import FakeAgents
from devteam.graph import Team, build_graph
from devteam.real import llm_call

BROKEN = b"\xdc2\x1e\x1e\xdc22\x1e\xdc\xdc\xc8\x1e"  # the first bytes of UX-dense's fixture


def make(repo, agents):
    team = Team(repo, copy.deepcopy(CFG), {}, agents, fake=True, remote=remote_of(repo))
    graph = build_graph(team, SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False)))
    return team, graph


class BinaryFixture(FakeAgents):
    """dev_app commits a binary file; the judge sends the branch diff to the API as the real
    tester does, and its verdict carries "\\udcdc" (a \\udcxx escape in an agent's JSON)."""

    delay = 0

    def develop(self, team, s):
        if s["assignee"] == "dev_app":
            (Path(s["worktree"]) / "web" / "dense.color.u8").write_bytes(BROKEN)
        return super().develop(team, s)

    def judge(self, team, s, checks):
        base = f"{team.remote_name}/{team.cfg['base_branch']}"
        diff = team.git("diff", f"{base}...HEAD", cwd=s["worktree"])
        llm_call(team, "tester", s["task"]["id"], "sys", diff, {}, client=StubClient())
        v = super().judge(team, s, checks)
        return {**v, "summary": v["summary"] + " tester output \udcdc"}


def test_a_binary_file_in_the_diff_reaches_the_tester_and_the_packet(repo):
    team, graph = make(repo, BinaryFixture())
    graph.invoke({}, {"configurable": {"thread_id": "r"}})
    tasks = {t["id"]: t["status"] for t in team.load_backlog()}
    assert tasks["T-001"] == tasks["T-002"] == "done"
    for f in ("events.log", "packets/T-002.md"):  # strict UTF-8 reads
        assert "CRASHED" not in (team.home / f).read_text(encoding="utf-8")
    assert "tester output ?" in (team.home / "packets" / "T-002.md").read_text()


class TesterCrash(FakeAgents):
    """dev_app's tester step crashes exactly as UX-dense's did; dev_pipeline's task is fine."""

    delay = 0

    def judge(self, team, s, checks):
        if s["assignee"] == "dev_app":
            "tester output \udcdc".encode()  # UnicodeEncodeError: surrogates not allowed
        return super().judge(team, s, checks)


def test_a_crashing_task_is_blocked_and_the_other_task_finishes(repo):
    team, graph = make(repo, TesterCrash())
    config = {"configurable": {"thread_id": "r"}}
    graph.invoke({}, config)  # no exception: the run ends normally
    state = graph.get_state(config)
    assert not state.next  # nothing left to resume
    results = {r["id"]: r for r in state.values["results"]}
    assert results["T-001"]["status"] == "done"
    assert results["T-002"]["status"] == "blocked"
    assert results["T-002"]["reason"].startswith("CRASHED in test at test_run6.py:")
    assert "surrogates not allowed" in results["T-002"]["reason"]
    tasks = {t["id"]: t for t in team.load_backlog()}
    assert tasks["T-001"]["status"] == "done" and tasks["T-002"]["status"] == "blocked"
    assert tasks["T-002"]["blocked_reason"] == results["T-002"]["reason"]
    assert (team.home / "packets" / "T-002.md").exists()
