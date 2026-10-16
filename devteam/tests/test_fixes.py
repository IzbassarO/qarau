"""One test per owner-requested fix (Codex review of v0): #1 budget, #2 branch guard,
#3 cleanup, #4 ownership, #5 checkpoint before backlog, #6 plan validation, #7 checks gate."""

import copy
import sqlite3
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import pytest
import yaml
from conftest import CFG, git, remote_of
from langgraph.checkpoint.sqlite import SqliteSaver

from devteam import costs
from devteam.agents import FakeAgents
from devteam.graph import Team, Violation, build_graph, validate_plan
from devteam.real import llm_call


class Quick(FakeAgents):
    delay = 0


def make(repo, agents=None, fake=True, **cfg_over):
    cfg = copy.deepcopy(CFG)
    cfg.update(cfg_over)
    team = Team(repo, cfg, {}, agents or Quick(), fake=fake, remote=remote_of(repo))
    graph = build_graph(team, SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False)))
    return team, graph


def statuses(team):
    return {t["id"]: t["status"] for t in team.load_backlog()}


def add_worktree(team, tid="T-001"):
    path = team.home / "worktrees" / tid
    team.git("worktree", "add", "-b", team.branch_for(tid), str(path), "dev")
    return {"task": {"id": tid, "title": "t"}, "assignee": "dev_pipeline", "worktree": str(path)}


# --- #1 budget -------------------------------------------------------------------------------
def test_reservations_are_atomic_across_threads(tmp_path):
    ok, refused = [], []

    def go():
        try:
            ok.append(costs.reserve(tmp_path, 1.0, "brain", "m", "T", 0.3))
        except costs.BudgetExceeded:
            refused.append(1)

    threads = [threading.Thread(target=go) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ok) == 3 and len(refused) == 7
    assert costs.spent_today(tmp_path) == pytest.approx(0.9)


def test_unsettled_reservation_survives_a_crash_and_settle_reconciles(tmp_path):
    rid = costs.reserve(tmp_path, 3.0, "brain", "m", "T", 2.5)  # then "crash": never settled
    with pytest.raises(costs.BudgetExceeded):
        costs.reserve(tmp_path, 3.0, "tester", "m", "T", 1.0)
    costs.settle(tmp_path, rid, 1000, 100, 0.05)
    assert costs.spent_today(tmp_path) == pytest.approx(0.05)
    costs.reserve(tmp_path, 3.0, "tester", "m", "T", 1.0)


class StubClient:
    def __init__(self, error=None):
        self.calls, self.error = 0, error
        self.messages = self

    def stream(self, **kw):
        outer = self

        class Ctx:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get_final_message(self):
                return outer.create(**kw)

        if self.error:
            self.calls += 1
            raise self.error
        return Ctx()

    def create(self, **kw):
        self.calls += 1
        if self.error:
            raise self.error
        usage = SimpleNamespace(
            input_tokens=1000,
            output_tokens=500,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        return SimpleNamespace(
            usage=usage,
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text='{"ok": true}')],
        )


def test_llm_call_reserves_settles_and_refuses_over_budget(repo):
    team, _ = make(repo)
    client = StubClient()
    assert llm_call(team, "brain", "T-001", "sys", "hi", {"type": "object"}, client=client) == {
        "ok": True
    }
    assert costs.spent_today(team.home) == pytest.approx((1000 * 4 + 500 * 20) / 1e6)
    team.cfg["daily_budget_usd"] = 0.01
    with pytest.raises(costs.BudgetExceeded):
        llm_call(team, "brain", "T-001", "sys", "hi", {"type": "object"}, client=client)
    assert client.calls == 1  # the refused call never reached the API


def test_llm_call_releases_on_4xx_and_keeps_on_unknown_billing(repo):
    team, _ = make(repo)
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    bad = anthropic.BadRequestError("bad", response=httpx.Response(400, request=req), body=None)
    with pytest.raises(anthropic.BadRequestError):
        llm_call(team, "tester", "T", "s", "p", {}, client=StubClient(bad))
    assert costs.spent_today(team.home) == 0
    with pytest.raises(anthropic.APIConnectionError):
        llm_call(
            team,
            "tester",
            "T",
            "s",
            "p",
            {},
            client=StubClient(anthropic.APIConnectionError(request=req)),
        )
    assert costs.spent_today(team.home) > 0.1  # held at the worst case


def test_llm_call_retries_transient_errors_not_rejections(repo):
    """Seen in a real run: the A2a tester crashed on one "Connection error"."""
    team, _ = make(repo)
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def status(cls, code, body=None):
        return cls("x", response=httpx.Response(code, request=req), body=body)

    class Flaky(StubClient):
        def __init__(self, errors):
            super().__init__()
            self.errors = errors

        def stream(self, **kw):
            if self.errors:
                self.calls += 1
                raise self.errors.pop(0)
            return super().stream(**kw)

    overloaded = {"type": "error", "error": {"type": "overloaded_error"}}  # mid-stream, HTTP 200
    client = Flaky(
        [
            anthropic.APIConnectionError(request=req),
            status(anthropic.RateLimitError, 429),
            status(anthropic.APIStatusError, 200, overloaded),
        ]
    )
    assert llm_call(team, "tester", "T", "s", "p", {}, client=client) == {"ok": True}
    assert client.calls == 4
    assert (team.home / "events.log").read_text().count("retrying in") == 3

    client = Flaky([status(anthropic.BadRequestError, 400)] * 2)
    with pytest.raises(anthropic.BadRequestError):
        llm_call(team, "tester", "T", "s", "p", {}, client=client)
    assert client.calls == 1  # a rejection is not retried

    client = Flaky([status(anthropic.InternalServerError, 529)] * 4)
    with pytest.raises(anthropic.InternalServerError):
        llm_call(team, "tester", "T", "s", "p", {}, client=client)
    assert client.calls == 4  # gives up after RETRY_WAITS, then the node crashes loudly


# --- #2 branch guard -------------------------------------------------------------------------
def test_worktree_must_be_registered_and_on_its_task_branch(repo):
    team, _ = make(repo)
    s = add_worktree(team)
    team.verify_worktree(s)
    git(s["worktree"], "checkout", "-q", "-b", "other")
    with pytest.raises(Violation):
        team.verify_worktree(s)
    (Path(s["worktree"]) / "pipeline" / "a.py").write_text("a = 2\n")
    with pytest.raises(Violation):
        team.commit_round(s, git(s["worktree"], "rev-parse", "HEAD").strip(), {})
    assert git(repo, "log", "--oneline", "other").count("\n") == 1  # nothing committed
    with pytest.raises(Violation):
        team.verify_worktree({**s, "worktree": str(repo)})  # main checkout is on dev
    with pytest.raises(Violation):
        team.verify_worktree({**s, "worktree": str(repo / "nowhere")})


# --- #4 ownership ----------------------------------------------------------------------------
def test_only_validated_paths_are_committed(repo):
    team, _ = make(repo)
    s = add_worktree(team)
    wt = Path(s["worktree"])
    head, snap = git(wt, "rev-parse", "HEAD").strip(), team.snapshot(str(wt))
    (wt / "pipeline" / "a.py").write_text("a = 2\n")
    odd = "pipeline/we ird\nname.txt"
    (wt / odd).write_text("ok\n")
    (wt / "web" / "app.ts").write_text("hack\n")  # another developer's folder
    git(wt, "mv", "README.md", "pipeline/README.md")  # rename across the boundary
    (wt / "pipeline" / ".env").write_text("SECRET=1\n")  # protected, git-ignored
    (wt / "data").mkdir()
    (wt / "data" / "big.bin").write_text("x")  # protected, git-ignored
    refused = team.commit_round(s, head, snap)["refused"]

    files = set(git(wt, "show", "--name-only", "--format=", "HEAD").split("\n")) - {""}
    names = set(git(wt, "show", "-z", "--name-only", "--format=", "HEAD").split("\0")) - {""}
    assert names == {"pipeline/a.py", odd} and len(files) >= 2
    assert {
        "web/app.ts",
        "README.md",
        "pipeline/README.md",
        "pipeline/.env",
        "data/big.bin",
    } <= set(refused)
    assert (wt / "README.md").read_text() == "x\n" and not (wt / "pipeline/README.md").exists()
    assert (wt / "web" / "app.ts").read_text() == "export {}\n"
    assert not (wt / "pipeline" / ".env").exists() and not (wt / "data" / "big.bin").exists()
    assert git(wt, "status", "--porcelain") == ""


def test_developer_commits_are_unwound_and_checked(repo):
    team, _ = make(repo)
    s = add_worktree(team)
    wt = Path(s["worktree"])
    head = git(wt, "rev-parse", "HEAD").strip()
    (wt / "web" / "app.ts").write_text("hack\n")
    git(wt, "commit", "-qam", "sneaky")
    assert "web/app.ts" in team.commit_round(s, head, {})["refused"]
    assert git(wt, "rev-parse", "HEAD").strip() == head  # nothing allowed -> no commit


# --- #5 checkpoint before backlog ------------------------------------------------------------
class CountingPlans(Quick):
    plans = 0

    def plan(self, team, todo, feedback=None):
        CountingPlans.plans += 1
        return super().plan(team, todo, feedback)


def test_assignments_are_checkpointed_before_the_backlog_changes(repo, monkeypatch):
    team, graph = make(repo, CountingPlans())
    config = {"configurable": {"thread_id": "r1"}}
    real_update = Team.update_task
    monkeypatch.setattr(Team, "update_task", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        graph.invoke({}, config)
    assert set(statuses(team).values()) == {"todo"}  # brain wrote nothing
    snap = graph.get_state(config)
    assert snap.next == ("claim",) and len(snap.values["assignments"]) == 2
    monkeypatch.setattr(Team, "update_task", real_update)
    graph.invoke(None, config)
    assert CountingPlans.plans == 1  # resume did not re-plan
    assert statuses(team) == {"T-001": "done", "T-002": "done", "T-003": "todo"}
    assert {t.get("run") for t in team.load_backlog() if t["id"] != "T-003"} == {"r1"}


def test_in_progress_tasks_of_this_run_are_recovered(repo):
    team, graph = make(repo)
    team.update_task("T-001", status="in_progress", run="r2", owner="dev_pipeline")
    team.update_task("T-002", status="in_progress", run="someone-else")
    graph.invoke({}, {"configurable": {"thread_id": "r2"}})
    assert statuses(team) == {"T-001": "done", "T-002": "in_progress", "T-003": "todo"}


# --- #6 plan validation ----------------------------------------------------------------------
TODO = yaml.safe_load((Path(__file__).parent / "backlog.yaml").read_text())["tasks"]


def p(tid, dev, criteria=("c",)):
    return {"task_id": tid, "assignee": dev, "plan": "do it", "criteria": list(criteria)}


@pytest.mark.parametrize(
    "plans, why",
    [
        ([p("T-001", "dev_pipeline"), p("T-001", "dev_app")], "repeat"),
        ([p("T-001", "dev_pipeline"), p("T-003", "dev_pipeline")], "more than one"),
        ([p("T-999", "dev_pipeline")], "not an open task"),
        ([p("T-002", "dev_pipeline")], "area"),
        ([p("T-001", "dev_ghost")], "unknown developer"),
        ([p("T-001", "dev_pipeline", criteria=())], "must not be empty"),
    ],
)
def test_invalid_plans_are_rejected(plans, why):
    with pytest.raises(ValueError, match=why):
        validate_plan(plans, TODO, CFG)


def test_valid_plan_passes():
    out = validate_plan([p("T-001", "dev_pipeline"), p("T-002", "dev_app")], TODO, CFG)
    assert [a["task"]["id"] for a in out] == ["T-001", "T-002"]


class BadPlanner(Quick):
    def plan(self, team, todo, feedback=None):
        return [p("T-002", "dev_pipeline")]


def test_bad_plan_twice_stops_without_touching_the_backlog(repo):
    team, graph = make(repo, BadPlanner())
    with pytest.raises(ValueError, match="area"):
        graph.invoke({}, {"configurable": {"thread_id": "r3"}})
    assert set(statuses(team).values()) == {"todo"}


# --- #7 checks gate --------------------------------------------------------------------------
def only_core(team, todo, feedback=None):
    return [p("T-001", "dev_pipeline")]


def test_judge_pass_is_not_enough_when_a_check_fails(repo):
    cfg_devs = copy.deepcopy(CFG["developers"])
    cfg_devs["dev_pipeline"].update(setup=[], checks=["true", "false"])
    agents = Quick()
    agents.plan = only_core
    team, graph = make(repo, agents, fake=False, developers=cfg_devs, max_fix_loops=1)
    graph.invoke({}, {"configurable": {"thread_id": "r4"}})
    assert statuses(team)["T-001"] == "blocked"  # judge passed in round 2, `false` did not
    packet = (team.home / "packets" / "T-001.md").read_text()
    assert "judge: pass" in packet and "FAIL: `false`" in packet


def test_failed_setup_blocks_success_and_skips_checks(repo):
    team, _ = make(repo, fake=False)
    team.cfg["developers"]["dev_pipeline"].update(setup=["exit 3"], checks=["true"])
    s = add_worktree(team)
    checks = team.run_checks(s)
    assert [(c["kind"], c["ok"]) for c in checks] == [("setup", False)]


# --- #3 cleanup ------------------------------------------------------------------------------
def test_cleanup_removes_finished_worktrees_and_keeps_branches_and_packets(repo):
    team, graph = make(repo)
    graph.invoke({}, {"configurable": {"thread_id": "r5"}})
    assert not (team.home / "worktrees" / "T-001").exists()  # merged -> removed at once
    add_worktree(team, "T-003")
    team.update_task("T-003", status="dropped")
    out = team.cleanup()
    assert len(out) == 1 and "T-003: removed" in out[0]
    assert team.branch_for("T-003") in git(repo, "branch", "--list")
    assert team.branch_for("T-001") in git(repo, "branch", "--list")
    assert (team.home / "packets" / "T-001.md").exists()


# --- second Codex review ---------------------------------------------------------------------
def test_preexisting_ignored_protected_file_is_never_deleted(repo):
    team, _ = make(repo)
    s = add_worktree(team)
    wt = Path(s["worktree"])
    (wt / ".env").write_text("KEY=original\n")  # ignored, so git has no copy
    head, snap = git(wt, "rev-parse", "HEAD").strip(), team.snapshot(str(wt))
    (wt / ".env").write_text("KEY=changed\n")
    with pytest.raises(Violation, match="can't restore"):
        team.commit_round(s, head, snap)
    assert (wt / ".env").exists()  # left for the owner, not deleted
    (wt / ".env").write_text("KEY=original\n")  # owner restores it -> passes (hash, not mtime)
    assert team.commit_round(s, head, snap)["refused"] == []


def test_v0_cost_database_is_migrated(tmp_path):
    db = sqlite3.connect(tmp_path / "costs.sqlite")
    db.execute(
        "create table usage (day text, ts text, agent text, model text, task text, "
        "tokens_in int, tokens_out int, usd real, billed int)"
    )
    db.execute("insert into usage values (date('now','localtime'),'t','a','m','T',1,1,0.5,1)")
    db.commit()
    db.close()
    costs.reserve(tmp_path, 3.0, "brain", "m", "T", 1.0)
    assert costs.spent_today(tmp_path) == pytest.approx(1.5)


def test_carriage_return_file_names_survive(repo):
    team, _ = make(repo)
    s = add_worktree(team)
    wt = Path(s["worktree"])
    head = git(wt, "rev-parse", "HEAD").strip()
    (wt / "pipeline" / "a\rb.txt").write_text("x")
    assert team.commit_round(s, head, {})["refused"] == []
    raw = subprocess.run(
        ["git", "show", "-z", "--name-only", "--format=", "HEAD"],
        cwd=wt,
        capture_output=True,
        check=True,
    ).stdout  # bytes: no \r translation
    assert b"pipeline/a\rb.txt\0" in raw


def test_commit_hooks_run(repo):
    team, _ = make(repo)
    s = add_worktree(team)
    wt = Path(s["worktree"])
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\n! git diff --cached | grep -q SECRET\n")
    hook.chmod(0o755)
    head = git(wt, "rev-parse", "HEAD").strip()
    (wt / "pipeline" / "a.py").write_text("token = 'SECRET'\n")
    with pytest.raises(RuntimeError, match="git commit"):
        team.commit_round(s, head, {})


class CommitThenCrash(Quick):
    calls = 0

    def develop(self, team, s):
        CommitThenCrash.calls += 1
        if CommitThenCrash.calls == 1:
            wt = Path(s["worktree"])
            (wt / "web" / "app.ts").write_text("hack\n")
            git(wt, "commit", "-qam", "sneaky")
            raise RuntimeError("crash right after a forbidden commit")
        return super().develop(team, s)


def test_forbidden_commit_before_a_crash_is_unwound_on_resume(repo):
    agents = CommitThenCrash()
    agents.plan = only_core
    team, graph = make(repo, agents)
    config = {"configurable": {"thread_id": "r6"}}
    with pytest.raises(RuntimeError, match="crash"):
        graph.invoke({}, config)
    graph.invoke(None, config)
    branch = team.branch_for("T-001")
    assert git(repo, "show", f"{branch}:web/app.ts") == "export {}\n"
    assert "sneaky" not in git(repo, "log", "--format=%s", branch)


def test_supplied_client_gets_no_sdk_retries(repo):
    team, _ = make(repo)
    seen = {}

    class Retrying(StubClient):
        def with_options(self, **kw):
            seen.update(kw)
            return self

    llm_call(team, "tester", "T", "s", "p", {}, client=Retrying())
    assert seen == {"max_retries": 0}


def test_tasks_without_area_are_reported_not_silently_skipped(repo):
    team, graph = make(repo)
    data = yaml.safe_load(team.backlog_path.read_text())
    for t in data["tasks"]:
        t.pop("area")
    team.backlog_path.write_text(yaml.safe_dump(data))
    graph.invoke({}, {"configurable": {"thread_id": "r7"}})
    assert "skipping tasks without a known area" in (team.home / "events.log").read_text()


# --- 2026-10-16: P0 and A2a re-planned after the backlog lost their status ------------------
def test_brain_never_plans_done_or_already_merged_tasks(repo):
    team, graph = make(repo)
    graph.invoke({}, {"configurable": {"thread_id": "r1"}})  # T-001 and T-002 merge into dev
    assert statuses(team)["T-001"] == "done"
    team.update_task("T-001", status="todo")  # the status got lost, the branch is in dev
    seen = []
    agents = Quick()
    agents.plan = lambda team, todo, feedback=None: seen.extend(t["id"] for t in todo) or []
    team.agents = agents
    graph.invoke({}, {"configurable": {"thread_id": "r2"}})
    assert "T-001" not in seen and "T-002" not in seen and "T-003" in seen


def test_backlog_status_drift_from_dev_is_reported(repo):
    team, _ = make(repo)
    assert team.backlog_drift() == []
    team.update_task("T-001", status="done", pr="fake://pr/1")  # only in the working tree
    drift = team.backlog_drift()
    assert any(d.startswith("T-001.status: 'todo' on dev -> 'done'") for d in drift)
    assert any(d.startswith("T-001.pr:") for d in drift) and len(drift) == 2
