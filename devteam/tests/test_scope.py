"""Hard scope enforcement (owner, 2026-10-17): scope gate on the diff, protected paths,
anti-bypass checks, clean-checkout checks, violations shown to the owner. Includes a replay of
A7 (dev_app needed backend/ and shipped it as a .patch file under docs/)."""

import copy
import sqlite3
import time
from pathlib import Path

import pytest
from conftest import CFG, git, remote_of
from langgraph.checkpoint.sqlite import SqliteSaver

from devteam import dashboard as D
from devteam import scope as SC
from devteam.agents import FakeAgents
from devteam.graph import Team, build_graph
from devteam.report import write_report


class Fast(FakeAgents):
    delay = 0


def make(repo, agents, fake=True, **cfg_over):
    cfg = copy.deepcopy(CFG)
    cfg.update(cfg_over)
    team = Team(repo, cfg, {}, agents, fake=fake, remote=remote_of(repo))
    graph = build_graph(team, SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False)))
    return team, graph


def task(team, tid):
    return next(t for t in team.load_backlog() if t["id"] == tid)


def only(tid, dev):
    def plan(team, todo, feedback=None, devs=None):
        return [{"task_id": tid, "assignee": dev, "plan": "p", "criteria": ["stub file exists"]}]

    return plan


def run(graph):
    graph.invoke({}, {"configurable": {"thread_id": "r"}})


def diff(path: str, minus=(), plus=(), new=False) -> str:
    head = f"diff --git a/{path} b/{path}\n" + ("--- /dev/null\n" if new else f"--- a/{path}\n")
    head += f"+++ b/{path}\n@@ -1 +1 @@\n"
    return head + "".join(f"-{x}\n" for x in minus) + "".join(f"+{x}\n" for x in plus)


# --- pure checks -----------------------------------------------------------------------------
def test_scope_and_protected_paths_follow_the_config():
    paths = ["web/src/a.ts", "backend/app/roi.py", "docs/notes/x.md", "devteam/backlog.yaml"]
    assert SC.check_paths(paths, CFG, "dev_app", {}) == {
        "protected": ["devteam/backlog.yaml"],
        "out_of_scope": ["backend/app/roi.py", "docs/notes/x.md"],  # dev_app: web/ only
    }
    ok = ["pipeline/a.py", "backend/app/roi.py", "infra/x.yml", "docs/notes/y.md"]
    assert SC.check_paths(ok, CFG, "dev_pipeline", {}) == {"protected": [], "out_of_scope": []}
    assert (
        SC.check_paths(["data/runs/x/m.json"], CFG, "dev_pipeline", {"writable": ["data/runs"]})[
            "out_of_scope"
        ]
        == []
    )
    for p in [".github/workflows/ci.yml", "CLAUDE.md", "AGENTS.md", "devteam/config.yaml",
              "pipeline/qarau_pipeline/benchmark_set.toml"]:  # fmt: skip
        assert SC.is_protected(p, CFG), p
    assert SC.is_protected("web/vite.config.ts", {"gates": {"protected": ["web/vite.config.*"]}})
    assert SC.foreign("backend/app/roi.py", CFG, "dev_app")
    assert not SC.foreign("notes.txt", CFG, "dev_app")


@pytest.mark.parametrize(
    "line",
    [
        "  it.skip('draws a box', () => {",
        "  test.only('x', () => {})",
        "describe.skip('roi', () => {",
        "xit('x', () => {})",
        "@pytest.mark.skip(reason='later')",
        "@pytest.mark.xfail",
        "    pytest.skip('no gpu')",
    ],
)
def test_skipped_or_focused_tests_are_refused(line):
    (f,) = SC.bypass(diff("web/src/roi.test.ts", plus=[line]), ["web/src/roi.test.ts"])
    assert f.startswith("skips or focuses a test in web/src/roi.test.ts")


def test_skip_checks_have_no_false_alarms_seen_on_real_prs():
    data_skip = "  test.skip(!fs.existsSync(realManifestPath), 'needs a Notre-Dame bundle');"
    assert SC.bypass(diff("web/e2e/far.spec.ts", plus=[data_skip]), ["web/e2e/far.spec.ts"]) == []
    code = "    return fit(tracks, times, usable, diag, prm, cap)[0]"  # P2b, not a test
    assert SC.bypass(diff("pipeline/qarau_pipeline/k.py", plus=[code]), ["x.py"]) == []
    assert SC.bypass(diff("docs/notes/p.md", plus=["it.only matters"]), ["docs/notes/p.md"]) == []


def test_removed_assertions_and_tests_are_refused():
    d = diff(
        "pipeline/tests/test_sky.py",
        minus=["def test_mask():", "    assert mask.sum() == 10", "    assert mask.any()"],
        plus=["def test_mask():", "    assert mask is not None"],
    )
    assert SC.bypass(d, ["pipeline/tests/test_sky.py"]) == [
        "removes test assertions: 2 removed, 1 added"
    ]
    gone = diff("web/src/a.test.ts", minus=["it('works', () => {", "  expect(x).toBe(1)"])
    assert SC.bypass(gone, ["web/src/a.test.ts"]) == [
        "removes test assertions: 1 removed, 0 added",
        "removes tests: 1 removed, 0 added",
    ]
    moved = diff("web/src/a.test.ts", minus=["  expect(x).toBe(1)"], plus=["  expect(y).toBe(2)"])
    assert SC.bypass(moved, ["web/src/a.test.ts"]) == []  # a changed assertion is fine


@pytest.mark.parametrize(
    ("old", "new", "loose"),
    [
        ("    assert registered_pct >= 90", "    assert registered_pct >= 50", True),
        ("    assert registered_pct >= 90", "    assert registered_pct >= 95", False),
        ("    assert err < 0.5", "    assert err < 2.0", True),
        ("  expect(fps).toBeGreaterThan(30)", "  expect(fps).toBeGreaterThan(10)", True),
        ("    assert x == approx(1.0, abs=1e-3)", "    assert x == approx(1.0, abs=0.1)", True),
        ("  expect(z).toBeCloseTo(1.25, 3)", "  expect(z).toBeCloseTo(1.25, 1)", True),
        ("    assert len(points) == 4", "    assert len(points) == 5", False),
    ],
)  # fmt: skip
def test_loosened_thresholds_are_refused(old, new, loose):
    found = SC.bypass(diff("pipeline/tests/test_x.py", [old], [new]), ["pipeline/tests/test_x.py"])
    assert bool(found) == loose, found
    if loose:
        assert found[0].startswith("loosens a test threshold in pipeline/tests/test_x.py")


def test_patch_files_and_code_under_docs_are_refused():
    paths = ["docs/patches/a7-backend-roi.patch", "docs/roi.py", "web/fix.diff"]
    assert SC.bypass("", paths) == [
        "adds a patch/diff file docs/patches/a7-backend-roi.patch: changes go in the code, "
        "never in a patch",
        "puts code under docs/: docs/roi.py",
        "adds a patch/diff file web/fix.diff: changes go in the code, never in a patch",
    ]
    long_block = ["```python"] + ["x = 1"] * 61 + ["```"]
    d = diff("docs/notes/roi.md", plus=["# ROI", *long_block], new=True)
    assert SC.bypass(d, ["docs/notes/roi.md"]) == [
        "puts a 61-line code block under docs/: docs/notes/roi.md"
    ]
    short = diff("docs/notes/roi.md", plus=["```json", '{"version": 1}', "```"], new=True)
    assert SC.bypass(short, ["docs/notes/roi.md"]) == []


# --- in the graph ------------------------------------------------------------------------------
class A7Replay(Fast):
    """What dev_app did on A7: wrote the backend, then parked it as a patch file."""

    def __init__(self, where):
        self.where, self.plan = where, only("T-002", "dev_app")

    def develop(self, team, s):
        wt = Path(s["worktree"])
        if self.where == "backend":
            (wt / "backend" / "app").mkdir(parents=True, exist_ok=True)
            (wt / "backend" / "app" / "roi.py").write_text("router = None\n")
        else:  # the patch inside its own folder: commit_round lets it through, the gate not
            (wt / "web" / "patches").mkdir(exist_ok=True)
            (wt / "web" / "patches" / "a7-backend-roi.patch").write_text("--- a/backend\n")
        return super().develop(team, s)


def test_a7_replay_backend_from_the_web_developer_ends_in_a_split(repo):
    team, graph = make(repo, A7Replay("backend"))
    run(graph)
    t = task(team, "T-002")
    assert t["status"] == "blocked"
    assert t["blocked_reason"].startswith("needs a split before it can be planned: out_of_scope")
    assert "backend/app/roi.py" in t["blocked_reason"]
    (v,) = team.violations(time.strftime("%Y-%m-%d"))
    assert (v["task"], v["agent"], v["kind"]) == ("T-002", "dev_app", "out_of_scope")
    assert "backend" not in git(repo, "show", "--stat", team.branch_for("T-002"))  # never committed


def test_a7_replay_patch_file_is_sent_back_then_blocked(repo):
    team, graph = make(repo, A7Replay("patch"))
    run(graph)
    t = task(team, "T-002")
    assert t["status"] == "blocked" and "twice in a row for the same reason" in t["blocked_reason"]
    assert "a7-backend-roi.patch" in t["blocked_reason"]
    assert {v["kind"] for v in team.violations(time.strftime("%Y-%m-%d"))} == {"bypass"}
    assert team.gh.prs == {}  # never reached the gates


class Edits(Fast):
    def __init__(self, path, text="x\n"):
        self.path, self.text, self.plan = path, text, only("T-001", "dev_pipeline")

    def develop(self, team, s):
        f = Path(s["worktree"]) / self.path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(self.text)
        return super().develop(team, s)


@pytest.mark.parametrize("path", ["CLAUDE.md", "devteam/backlog.yaml", ".github/workflows/x.yml"])
def test_touching_a_protected_path_is_a_hard_fail(repo, path):
    team, graph = make(repo, Edits(path, "tasks:\n- {id: T-001, status: done}\n"))
    run(graph)
    t = task(team, "T-001")  # the developer cannot mark its own task done
    assert t["status"] == "blocked" and t["blocked_reason"].startswith("protected: dev_pipeline")
    assert path in t["blocked_reason"]
    assert git(remote_of(repo), "log", "--format=%s", "dev").count("T-001 done") == 0


def prior_commit(repo, team, tid, path):
    """Earlier work on the task branch that already holds `path` (e.g. from before the gate)."""
    branch = team.branch_for(tid)
    git(repo, "branch", branch, "dev")
    wt = repo.parent / f"wt-{tid}"
    git(repo, "worktree", "add", "-q", str(wt), branch)
    (wt / path).parent.mkdir(parents=True, exist_ok=True)
    (wt / path).write_text("x = 1\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", "earlier work")
    git(repo, "worktree", "remove", str(wt))


@pytest.mark.parametrize(
    ("path", "start"),
    [
        ("backend/app/roi.py", "needs a split before it can be planned: out_of_scope"),
        ("devteam/devteam/graph.py", "protected: dev_app"),
    ],
)
def test_scope_gate_reads_the_whole_branch_diff(repo, path, start):
    agents = Fast()
    agents.plan = only("T-002", "dev_app")
    team, graph = make(repo, agents)
    prior_commit(repo, team, "T-002", path)
    run(graph)
    t = task(team, "T-002")
    assert t["status"] == "blocked" and t["blocked_reason"].startswith(start)
    assert path in t["blocked_reason"]


def test_checks_run_on_a_clean_checkout_not_the_developers_worktree(repo):
    """An ignored leftover in the developer's worktree must not help a check pass."""

    class Leftover(Fast):
        def develop(self, team, s):
            (Path(s["worktree"]) / "data").mkdir(exist_ok=True)
            (Path(s["worktree"]) / "data" / "built.flag").write_text("x")  # ignored
            return super().develop(team, s)

    agents = Leftover()
    agents.plan = only("T-001", "dev_pipeline")
    devs = copy.deepcopy(CFG["developers"])
    devs["dev_pipeline"].update(setup=[], checks=["test -e data/built.flag", "git status"])
    devs["dev_pipeline"].pop("screenshots", None)
    team, graph = make(repo, agents, fake=False, developers=devs, max_fix_loops=0)
    run(graph)
    packet = (team.home / "packets" / "T-001.md").read_text()
    assert "FAIL: `test -e data/built.flag`" in packet
    assert not (team.home / "check-wt" / "T-001").exists()  # the checkout is removed


def test_web_developer_writing_docs_is_reverted_not_failed(repo):
    """dev_app owns web/ only; a plan it writes to docs/ (as CLAUDE.md asks) is dropped."""
    agents = Edits("docs/notes/t-002-plan.md")
    agents.plan = only("T-002", "dev_app")
    team, graph = make(repo, agents)
    run(graph)
    assert task(team, "T-002")["status"] == "done"
    assert "docs/notes" not in git(repo, "show", "--stat", team.branch_for("T-002"))
    assert team.violations(time.strftime("%Y-%m-%d")) == []


def test_violations_reach_the_dashboard_and_the_report(repo):
    team, graph = make(repo, A7Replay("backend"))
    run(graph)
    d = D.collect(team, None)
    assert any(k == "violation" and "out_of_scope" in t for k, t, _u in d["attention"])
    report = write_report(team).read_text()
    assert "## Scope and bypass violations" in report
    assert "T-002 (dev_app) **out_of_scope**" in report
