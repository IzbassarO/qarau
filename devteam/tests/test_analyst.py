"""Architect and Analyst (Kerege v3 K2) on the three real cases of the night of 2026-10-27
(tests/fixtures/night-2026-10-27.yaml, word for word): UI-evidence-b needed GET /api/me that the
backend lacked, COV1 was too big, UI-report hit a tester API error. The model's answers are
stand-ins; what is tested is what Kerege does with them."""

import json
import shutil
import sqlite3
import time
from pathlib import Path

import pytest
import yaml
from conftest import CFG, HERE, git, real_backlog, remote_of, team_for
from langgraph.checkpoint.sqlite import SqliteSaver

from devteam import analyst as AN
from devteam import architect as AR
from devteam.agents import FakeAgents
from devteam.continuous import pause_reason
from devteam.dashboard import now_rows
from devteam.graph import build_graph

NIGHT = HERE / "tests" / "fixtures" / "night-2026-10-27.yaml"
ME = {  # what the Analyst should ask for: the route UI-evidence-b's role needs
    "title": "Current user endpoint: GET /api/me",
    "area": "core",
    "goal": "Today the web app guesses the signed-in role from 403 texts.\n\nBuild (backend/ "
    "only): GET /api/me returns the user's name and role.\n\nNot in this task: the web app."
    "\n\nDone when: an API test asserts each seeded role; the route is in the OpenAPI schema.\n",
}
ANSWERS = {
    "UI-evidence-b": {
        "diagnosis": "missing_dependency",
        "why": "fetchSessionRole guesses the role from 403 texts: the backend has no GET /api/me.",
        "action": "add_prerequisite",
        "prerequisite": ME,
    },
    "COV1": {
        "diagnosis": "too_big",
        "why": "The round used 16.5 M tokens of 16 M: pipeline step, migration, API and report.",
        "action": "split",
        "pieces": [
            {
                "title": "Coverage step and regions.json",
                "intro": "Build the step.",
                "criteria": [1, 2],
            },
            {"title": "Coverage API and the report", "intro": "Build the API.", "criteria": [3, 4]},
        ],
    },
    "UI-report": {
        "diagnosis": "tester_or_kerege_error",
        "why": "The tester's request had an image over 3000 px with many images: a Kerege bug.",
        "action": "report",
        "prompt": "Fix real.py png_data: scale every image to fit the many-image limit.",
    },
}


class Night(FakeAgents):
    delay = 0

    def __init__(self, answers=None):
        self.answers, self.asked = answers or ANSWERS, []

    def ask(self, team, role, task, system, prompt, schema):
        self.asked.append((role, task))
        if role == "architect":
            return {"brief": "web/src/auth/auth.ts reads the role.", "missing": ["GET /api/me"],
                    "prerequisite": ME}  # fmt: skip
        empty = {"prerequisite": {}, "pieces": [], "hint": "", "prompt": ""}
        return {**empty, **self.answers[task]}


@pytest.fixture
def night(repo):
    """The repo's dev (and the fake remote's) backlog at 00:46 that night."""
    shutil.copy(NIGHT, repo / "devteam" / "backlog.yaml")
    git(repo, "commit", "-qam", "backlog at 00:46")
    git(repo, "push", "-q", remote_of(repo), "dev")
    team = team_for(repo)
    team.agents = Night()
    return team


def on_dev(repo) -> dict:
    git(repo, "fetch", "-q", remote_of(repo), "dev:refs/remotes/devteam-fake/dev")
    text = git(repo, "show", "devteam-fake/dev:devteam/backlog.yaml")
    return {t["id"]: t for t in yaml.safe_load(text)["tasks"]}


def last_commit(repo) -> tuple[str, str, list[str]]:
    git(repo, "fetch", "-q", remote_of(repo), "dev:refs/remotes/devteam-fake/dev")
    sha = git(repo, "rev-parse", "devteam-fake/dev").strip()
    subject = git(repo, "log", "-1", "--format=%s", sha).strip()
    files = git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()
    return sha, subject, files


def test_ui_evidence_b_gets_a_backend_prerequisite_then_runs_again_and_undo_reverts(repo, night):
    assert AN.on_blocked(night, "UI-evidence-b") == "add_prerequisite"
    sha, subject, files = last_commit(repo)
    assert subject.startswith("backlog: analyst: add_prerequisite UI-evidence-b: waits for new")
    assert files == ["devteam/backlog.yaml"]  # its own checkpoint commit, backlog only
    for by in (on_dev(repo), {t["id"]: t for t in night.load_backlog()}):  # dev and this file
        pre, task = by["UI-evidence-b-pre"], by["UI-evidence-b"]
        assert (pre["area"], pre["status"], pre["added_by"]) == ("core", "todo", "analyst")
        assert task["depends_on"] == ["UI-evidence", "UI-evidence-b-pre"]
        assert task["blocked_reason"].startswith("waits for UI-evidence-b-pre")
        assert task["goal"] == yaml.safe_load(NIGHT.read_text())["tasks"][4]["goal"]  # unchanged
    assert AN.backlog_problems(night.load_backlog(), CFG) == []
    night.update_task("UI-evidence-b-pre", status="done")
    assert night.unblock_ready() == ["UI-evidence-b"]  # back to todo once the route merged

    night.update_task("UI-evidence-b-pre", status="todo")
    night.update_task("UI-evidence-b", status="blocked", blocked_reason="waits for x")
    night.push_status("UI-evidence-b", "backlog: UI-evidence-b blocked")
    night.push_status("UI-evidence-b-pre", "backlog: UI-evidence-b-pre todo")
    night.undo(sha)  # a new commit; history is not rewritten
    undo_sha, subject, files = last_commit(repo)
    assert subject.startswith(f"backlog: undo {sha[:9]}") and files == ["devteam/backlog.yaml"]
    assert git(repo, "merge-base", "--is-ancestor", sha, undo_sha) == ""
    for by in (on_dev(repo), {t["id"]: t for t in night.load_backlog()}):
        assert "UI-evidence-b-pre" not in by and by["UI-evidence-b"]["depends_on"] == [
            "UI-evidence"
        ]
        assert by["UI-evidence-b"]["blocked_reason"] == "waits for x"  # later changes stay
    with pytest.raises(ValueError, match="not a plan change"):
        night.undo(undo_sha)


def test_cov1_is_split_without_losing_a_criterion_and_piece_1_keeps_its_accepted_parts(repo, night):
    parts = night.home / "parts" / "COV1.json"
    parts.parent.mkdir(exist_ok=True)
    parts.write_text(json.dumps({"goal": "old", "parts": [{"title": "step"}], "base": "abc"}))
    assert AN.on_blocked(night, "COV1") == "split"
    by = on_dev(repo)
    cov1 = by["COV1"]
    assert cov1["status"] == "split" and cov1["split_into"] == ["COV1-s1", "COV1-s2"]
    assert cov1["goal"] == yaml.safe_load(NIGHT.read_text())["tasks"][3]["goal"]  # unchanged
    one, two = by["COV1-s1"], by["COV1-s2"]
    assert (one["continues"], one["depends_on"], two["depends_on"]) == (
        "COV1",
        ["RPT1"],
        ["COV1-s1"],
    )
    original = AN.criteria(cov1["goal"])
    assert len(original) == 4  # "(a synthetic test ...; a real run ...)" counts as two, as in plans
    assert AN.criteria(one["goal"]) == original[:2] and AN.criteria(two["goal"]) == original[2:]
    assert AN.backlog_problems(list(by.values()), CFG) == []
    kept = json.loads((night.home / "parts" / "COV1-s1.json").read_text())
    assert kept["parts"] == [{"title": "step"}] and kept["goal"] == one["goal"]

    two["goal"] = two["goal"].replace("tests for", "no tests for")  # a criterion changed
    assert AN.backlog_problems(list(by.values()), CFG) == [
        "COV1: its pieces do not take each of its criteria exactly once"
    ]


@pytest.mark.parametrize(
    "pieces",
    [
        [
            {"title": "a", "intro": "x", "criteria": [1]},
            {"title": "b", "intro": "y", "criteria": [2]},
        ],
        [
            {"title": "a", "intro": "x", "criteria": [1, 2]},
            {"title": "b", "intro": "y", "criteria": [2, 3]},
        ],
        [{"title": "a", "intro": "x", "criteria": [1, 2, 3]}],
    ],
)
def test_a_split_that_loses_or_repeats_a_criterion_goes_to_the_owner(repo, night, pieces):
    night.agents = Night({"COV1": {**ANSWERS["COV1"], "pieces": pieces}})
    before = git(repo, "rev-parse", "HEAD")
    assert AN.on_blocked(night, "COV1") == ""
    assert on_dev(repo)["COV1"]["status"] == "blocked"
    assert "exactly once" in (night.home / "analyst" / "COV1.md").read_text() or len(pieces) == 1
    assert git(repo, "rev-parse", "HEAD") == before


def test_ui_report_tester_error_goes_to_the_owner_with_a_ready_prompt(repo, night, banners):
    assert AN.on_blocked(night, "UI-report") == ""  # the owner acts: the developer may pause
    md = (night.home / "analyst" / "UI-report.md").read_text()
    assert md.startswith("# UI-report: tester_or_kerege_error")
    assert "## Prompt for Claude Code" in md and "Fix real.py png_data" in md
    assert "Rules: new git worktree off origin/dev" in md
    assert "UI-report: tester_or_kerege_error, needs you" in banners.read_text()
    assert on_dev(repo)["UI-report"] == yaml.safe_load(NIGHT.read_text())["tasks"][5]


def test_at_most_three_plan_changes_a_day_and_one_resume_per_task(repo, night):
    hint = {"diagnosis": "developer_failure", "why": "w", "action": "resume_with_hint",
            "hint": "read the role from GET /api/me"}  # fmt: skip
    night.agents = Night({**ANSWERS, "UI-report": hint})
    assert AN.on_blocked(night, "UI-report") == "resume_with_hint"
    t = on_dev(repo)["UI-report"]
    assert t["status"] == "todo" and t["analyst_hint"] == "read the role from GET /api/me"
    night.update_task("UI-report", status="blocked", blocked_reason="sent back again")
    assert AN.on_blocked(night, "UI-report") == ""  # once per task
    assert "resumed UI-report once already" in (night.home / "analyst" / "UI-report.md").read_text()
    assert AN.on_blocked(night, "UI-evidence-b") == "add_prerequisite"
    assert AN.on_blocked(night, "COV1") == "split"
    assert AN.changes_left(night) == 0
    night.update_task("UI-evidence-b", status="blocked", blocked_reason="a new block")
    assert AN.on_blocked(night, "UI-evidence-b") == ""  # the cap: the owner decides
    assert (
        "3 plan changes were made in the last 24 h"
        in (night.home / "analyst" / "UI-evidence-b.md").read_text()
    )


def test_a_change_the_backlog_tests_refuse_is_not_made_anywhere(repo, night, monkeypatch):
    monkeypatch.setattr(night, "backlog_check", lambda text, ref: "FAILED test_backlog_x")
    assert AN.on_blocked(night, "UI-evidence-b") == ""
    assert "UI-evidence-b-pre" not in on_dev(repo)
    assert "UI-evidence-b-pre" not in {t["id"] for t in night.load_backlog()}
    assert "the backlog tests refused" in (night.home / "analyst" / "UI-evidence-b.md").read_text()


def test_switched_off_the_analyst_leaves_a_blocked_task_to_the_owner_as_in_v2(repo, night):
    night.cfg["models"]["analyst"]["enabled"] = False
    assert AN.on_blocked(night, "UI-evidence-b") == "" and night.agents.asked == []
    assert not (night.home / "analyst" / "actions.jsonl").exists()


def test_the_architect_finds_the_missing_route_before_planning_and_caches_its_brief(repo, night):
    night.update_task("UI-evidence-b", status="todo", blocked_reason=None)
    night.push_status("UI-evidence-b", "backlog: UI-evidence-b todo")
    t = next(x for x in night.load_backlog() if x["id"] == "UI-evidence-b")
    assert AR.brief(night, t) is None  # it waits on the new prerequisite
    pre = on_dev(repo)["UI-evidence-b-pre"]
    assert (pre["added_by"], pre["area"]) == ("architect", "core")
    assert on_dev(repo)["UI-evidence-b"]["depends_on"] == ["UI-evidence", "UI-evidence-b-pre"]
    assert last_commit(repo)[1].startswith("backlog: architect: add_prerequisite UI-evidence-b")

    night.agents = Night()
    night.agents.ask = lambda *a: {
        "brief": "auth.ts; GET /api/me",
        "missing": [],
        "prerequisite": {},
    }
    t = next(x for x in night.load_backlog() if x["id"] == "UI-report")
    assert AR.brief(night, t) == "auth.ts; GET /api/me"
    night.agents.ask = None  # cached for this goal and dev commit: no second call
    assert AR.brief(night, t) == "auth.ts; GET /api/me"


def test_a_split_task_continues_its_branch_in_piece_1_and_is_done_with_its_pieces(repo, night):
    wt = repo.parent / "cov1-wt"  # COV1's earlier work on its branch
    git(repo, "worktree", "add", "-q", "-b", "devteam-fake/COV1", str(wt), "dev")
    (wt / "pipeline" / "coverage.py").write_text("STEP = 1\n")
    git(wt, "add", ".")
    git(wt, "commit", "-qm", "COV1 round 1")
    earlier = git(wt, "rev-parse", "HEAD").strip()
    git(repo, "worktree", "remove", "--force", str(wt))
    assert AN.on_blocked(night, "COV1") == "split"
    night.update_task("RPT1", status="done")
    db = sqlite3.connect(night.home / "c.sqlite", check_same_thread=False)
    graph = build_graph(night, SqliteSaver(db))
    for n, piece in enumerate(["COV1-s1", "COV1-s2"]):
        night.only = {piece}
        graph.invoke({}, {"configurable": {"thread_id": f"r{n}"}})
        if n == 0:
            git(repo, "merge-base", "--is-ancestor", earlier, "devteam-fake/COV1-s1")
            assert {t["id"]: t["status"] for t in night.load_backlog()}["COV1"] == "split"
    status = {t["id"]: t["status"] for t in night.load_backlog()}
    assert (status["COV1-s1"], status["COV1-s2"], status["COV1"]) == ("done", "done", "done")


def test_a_developer_pauses_only_when_the_analyst_could_not_act():
    def r(tid, status="blocked", analyst=""):
        return {"id": tid, "dev": "dev_app", "status": status, "analyst": analyst, "reason": "x"}

    acted = [r("UI-evidence-b", analyst="add_prerequisite"), r("UI-report")]
    assert pause_reason(acted, "dev_app") == ""
    assert pause_reason([*acted, r("CONF-fix")], "dev_app") == (
        "dev_app paused: 2 of its tasks in a row ended blocked (UI-report, CONF-fix)"
    )
    assert pause_reason([r("A"), r("B", "done"), r("C")], "dev_app") == ""


def test_now_panel_shows_task_part_round_and_the_last_verdict():
    ts = lambda m: time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(1_800_000_000 + m * 60))  # noqa: E731
    events = [
        {"ts": ts(0), "agent": "dev_app", "task": "UI-report", "msg": "building part 1/2: page"},
        {"ts": ts(5), "agent": "tester", "task": "UI-report", "msg": "sent back (1/3): dark theme"},
        {"ts": ts(6), "agent": "dev_app", "task": "UI-report", "msg": "fixing: dark theme"},
        {"ts": ts(9), "agent": "dev_app", "task": "UI-report", "msg": "wrote ReportPage.tsx"},
    ]
    office = {"dev_app": {"task": "UI-report", "state": "working", "secs": 600},
              "analyst": {"task": "", "state": "idle", "secs": None}}  # fmt: skip
    rows = now_rows(events, office, CFG["roles"], 1_800_000_000 - 1)
    assert rows[0] == {
        "agent": "dev_app", "roles": ["design_developer"], "task": "UI-report",
        "state": "working", "part": "1/2", "round": 2, "secs": 600,
        "last": f"{ts(9)[11:16]} wrote ReportPage.tsx", "verdict": "sent back (1/3): dark theme",
    }  # fmt: skip
    assert rows[1]["roles"] == ["analyst"] and rows[1]["task"] == "" and rows[1]["round"] == 0


def test_every_role_has_an_executor():
    executors = set(CFG["models"]) | set(CFG["developers"])
    assert set(CFG["roles"].values()) <= executors
    assert {"core_developer", "backend_developer", "design_developer", "qa"} <= set(CFG["roles"])


@pytest.mark.backlog
def test_entries_kerege_added_are_whole_and_splits_keep_every_criterion():
    assert AN.backlog_problems(real_backlog(), CFG) == []


def test_fixture_is_the_real_backlog_of_that_night():
    """The fixture's three entries are dev's at 359375b (git history, when it is there)."""
    data = yaml.safe_load(NIGHT.read_text())["tasks"]
    reasons = {t["id"]: t.get("blocked_reason", "") for t in data}
    assert reasons["COV1"].startswith("supervisor: this round used 16.5 M subscription tokens")
    assert reasons["UI-evidence-b"].startswith("the tester sent it back twice in a row")
    assert "invalid_request_error" in reasons["UI-report"]
    assert Path(NIGHT).stat().st_size < 20_000


def test_the_architect_reads_the_goals_files_routes_api_clients_and_the_task_graph(repo, night):
    files = {
        "backend/app/users.py": '@router.get("/users")\ndef users(): ...\n',
        "web/src/lib/findings.ts": "export async function postDecision() {\n"
        "  return fetch('/api/findings/1/decisions');\n}\n",
        "web/src/lib/findings.test.ts": "fetch('/api/never-in-the-brief')\n",
    }
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "code")
    git(repo, "push", "-q", remote_of(repo), "dev")
    night.fetch("dev")
    t = next(x for x in night.load_backlog() if x["id"] == "UI-evidence-b")
    ctx = AR.context(night, t, "devteam-fake/dev")
    assert "<file path='web/src/lib/findings.ts'>\nexport async function postDecision()" in ctx
    assert 'backend/app/users.py:1:@router.get("/users")' in ctx  # the routes there are
    assert "web/src/lib/findings.ts:2:  return fetch('/api/findings/1/decisions');" in ctx
    assert "never-in-the-brief" not in ctx  # tests are not clients
    assert "- id: UI-report" in ctx and "<done_tasks>RPT1, DS1, UI-evidence</done_tasks>" in ctx
