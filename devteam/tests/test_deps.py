"""Owner 2026-10-16 (P3b blocked on a viewer problem): task dependencies, resuming a task's
earlier branch, dev screenshots to compare with, scratch output outside the repo."""

import socket
from pathlib import Path

import pytest
import yaml
from conftest import CFG, git, real_backlog, remote_of
from test_fixes import Quick, dev_backlog, make, p, statuses

from devteam import gates as G
from devteam.graph import validate_plan


def run(graph, thread):
    graph.invoke({}, {"configurable": {"thread_id": thread}})


def msgs(team) -> list[str]:
    return [e["msg"] for e in yaml.safe_load((team.home / "events.log").read_text())]


def recording(agents):
    """Records what the brain was offered and the plan each developer got."""
    seen, plans, plan = [], {}, agents.plan

    def record_plan(team, todo, feedback=None, devs=None):
        seen.append({t["id"]: t for t in todo})
        return plan(team, todo, feedback, devs)

    develop = agents.develop

    def record_develop(team, s):
        plans[s["task"]["id"]] = s["plan"]
        return develop(team, s)

    agents.plan, agents.develop = record_plan, record_develop
    return seen, plans


def test_brain_never_plans_a_task_whose_dependencies_are_not_done(repo):
    team, graph = make(repo)
    team.update_task("T-001", depends_on=["T-002"])
    seen, _ = recording(team.agents)
    run(graph, "r1")  # T-002 merges in this run, T-001 had to wait
    assert "T-001" not in seen[0] and statuses(team)["T-001"] == "todo"
    assert "skipping tasks whose depends_on are not all done: ['T-001']" in msgs(team)
    run(graph, "r2")
    assert "T-001" in seen[1] and statuses(team)["T-001"] == "done"


def test_blocked_task_goes_back_to_todo_on_dev_when_its_dependencies_are_done(repo):
    team, graph = make(repo)
    team.update_task("T-003", status="blocked", depends_on=["T-002"], blocked_reason="wait")
    team.push_status("T-003", "backlog: T-003 blocked")
    assert dev_backlog(repo)["T-003"]["status"] == "blocked"
    run(graph, "r1")  # T-001 and T-002 merge; T-003 is not planned while blocked
    t = next(t for t in team.load_backlog() if t["id"] == "T-003")
    assert t["status"] == "todo" and "blocked_reason" not in t and "run" not in t
    on_dev = dev_backlog(repo)["T-003"]
    assert on_dev["status"] == "todo" and "blocked_reason" not in on_dev
    log = git(repo, "--git-dir", remote_of(repo), "log", "--format=%s", "dev")
    assert "backlog: T-003 todo (dependencies done)" in log
    assert team.unblock_ready() == []  # idempotent


def test_blocked_task_with_unfinished_dependencies_stays_blocked(repo):
    team, _ = make(repo)
    team.update_task("T-003", status="blocked", depends_on=["T-001", "T-002"])
    team.update_task("T-001", status="done")
    assert team.unblock_ready() == [] and statuses(team)["T-003"] == "blocked"
    team.update_task("T-002", status="done")
    assert team.unblock_ready() == ["T-003"] and statuses(team)["T-003"] == "todo"


def test_a_task_with_earlier_work_continues_on_its_branch(repo, tmp_path):
    """devteam/<id> exists only on the remote (pushed earlier, worktree gone) and dev moved."""
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", "-b", "dev", remote_of(repo), str(other))
    git(other, "checkout", "-qb", "devteam-fake/T-001")
    (other / "pipeline" / "earlier.py").write_text("x = 1\n")
    git(other, "add", ".")
    git(other, "commit", "-qm", "T-001: earlier round")
    git(other, "push", "-q", "origin", "devteam-fake/T-001")
    git(other, "checkout", "-q", "dev")
    (other / "README.md").write_text("dev moved\n")
    git(other, "commit", "-qam", "dev moved")
    git(other, "push", "-q", "origin", "dev")

    team, graph = make(repo)
    team.only = {"T-001"}
    seen, plans = recording(team.agents)
    run(graph, "r1")
    assert "1 commit(s) on devteam-fake/T-001 not in dev yet" in seen[0]["T-001"]["previous_work"]
    assert plans["T-001"].startswith("Previous work is already on this task's branch")
    assert statuses(team)["T-001"] == "done"
    files = git(repo, "--git-dir", remote_of(repo), "ls-tree", "-r", "--name-only", "dev")
    assert "pipeline/earlier.py" in files.split()  # built on, not started over
    readme = git(repo, "--git-dir", remote_of(repo), "show", "dev:README.md")
    assert readme == "dev moved\n"  # dev was merged in, nothing of it lost


def test_a_task_without_earlier_work_starts_from_dev(repo):
    team, graph = make(repo)
    team.only = {"T-001"}
    seen, plans = recording(team.agents)
    run(graph, "r1")
    assert "previous_work" not in seen[0]["T-001"]
    assert not plans["T-001"].startswith("Previous work")


class Messy(Quick):
    """Leaves scratch output in the worktree, like P3b did."""

    def develop(self, team, s):
        for i in range(30):
            f = Path(s["worktree"]) / "scratch" / "p3b" / f"render-{i}.png"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("png")
        (Path(s["worktree"]) / "notes.txt").write_text("x\n")  # in nobody's folders: reverted
        return super().develop(team, s)


def test_scratch_lives_outside_the_worktree_and_reverts_are_logged_briefly(repo):
    team, graph = make(repo, agents=Messy())
    team.only = {"T-001"}
    run(graph, "r1")
    assert (team.home / "scratch" / "T-001").is_dir()
    reverted = [m for m in msgs(team) if m.startswith("touched forbidden files")]
    assert reverted  # one per round
    assert set(reverted) == {"touched forbidden files, reverted: 31 file(s) in notes.txt, scratch/"}
    wt = team.home / "worktrees" / "T-001"
    assert not (wt / "scratch").exists() or not any((wt / "scratch").rglob("*.png"))


def test_dev_screenshots_use_devs_cached_benchmark(repo, tmp_path):
    s_ = socket.socket()
    s_.bind(("127.0.0.1", 0))
    n = s_.getsockname()[1]
    s_.close()
    team, _ = make(repo)
    team.fetch("dev")
    team.cfg["developers"]["dev_pipeline"]["screenshots"] = {
        "serve": f"echo {{runs}} > {tmp_path}/runs; exec python3 -m http.server {n}",
        "url": f"http://127.0.0.1:{n}",
        "shoot": "echo png > {png}",
    }
    s = {"task": {"id": "P3b"}, "assignee": "dev_pipeline", "worktree": str(repo)}
    shots, problems = G.dev_screenshots(team, s, ["/viewer/notre-dame"])
    assert shots == [] and "no cached benchmark of devteam-fake/dev" in problems[0]
    cache = team.home / "bench" / G.bench_key(team, "devteam-fake/dev")
    cache.mkdir(parents=True)
    (cache / "results.json").write_text('{"videos": {}}')
    shots, problems = G.dev_screenshots(team, s, ["/viewer/notre-dame"])
    assert [Path(x).name for x in shots] == ["dev-viewer-notre-dame.png"] and problems == []
    assert (tmp_path / "runs").read_text().strip() == str(cache)
    # dev_app: its "before" is the page built from dev (test_run4), not a benchmark cache
    s["assignee"] = "dev_app"
    team.cfg["developers"]["dev_app"]["screenshots"] = None
    assert G.dev_screenshots(team, s, ["/"]) == ([], [])  # no screenshots config: nothing


@pytest.mark.backlog
def test_real_backlog_dependencies_name_existing_tasks():
    tasks = real_backlog()
    ids = {t["id"] for t in tasks}
    for t in tasks:
        assert set(t.get("depends_on") or []) <= ids - {t["id"]}, t["id"]
        assert "blocked_by" not in t, f"{t['id']}: the field is depends_on"


def test_resume_note_is_part_of_the_validated_plan():
    todo = [{"id": "T-001", "area": "core", "previous_work": "2 commit(s) on devteam/T-001"}]
    (a,) = validate_plan([p("T-001", "dev_pipeline")], todo, CFG)
    assert a["plan"].startswith("Previous work is already on this task's branch: 2 commit(s)")
    assert a["plan"].endswith("do it")
