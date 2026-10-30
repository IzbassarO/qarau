"""Owner, 2026-10-22: product first, then metres. The demo chain is the brain's whole queue;
automatic defect detection and everything else off the chain is LATER (status `later`): never
planned, never un-blocked by dependencies, never resumed.

The `backlog`-marked tests never assert a status: Kerege rewrites statuses with every status
commit and the check must pass whatever it writes (test_run2). Order, dependencies, text only."""

import pytest
import yaml
from conftest import CFG, HERE, real_backlog
from test_deps import msgs, recording, run
from test_safety import make, statuses

from devteam.graph import outside_paths

CHAIN = [
    "B1", "U2", "S1", "DS1", "D1", "DEF1-api", "UI-inspections", "REV1-api", "UI-evidence",
    # owner, 2026-10-28 (LaunchPad demo, 2026-11-06): one queue per developer, in this order
    "ME1", "UI-report", "UI-evidence-b", "CONF-fix", "UI-evidence-c", "RPT1", "COV1", "COV1-b",
    "DEMO1", "TEST-v0008", "DEMO-cov", "DEMO1-ui",
    # owner, 2026-10-29: the demo needs the report, marking and event-code sign-in first
    "COV-ui",
    # owner, 2026-10-30: the full product; D2 becomes the permanent server, UI-mark LATER
    "AUTH1-a", "AUTH1-b", "ORG1", "STOR1", "GPU1", "LAB1-a", "LAB1-b", "MARK-photo", "SEC1",
    "SEC1-ci", "D2", "AUTH1-ui-a", "AUTH1-ui-b", "ORG1-ui", "LAB1-ui-a", "LAB1-ui-b",
    "UI-mark-photo", "DESIGN-skills",
]  # fmt: skip
# done by a Claude Code session with the owner (.github/, devteam/), never planned by Kerege
OWNER_ONLY = {"SEC1-ci", "DESIGN-skills"}
DEPS = {
    "B1": [],
    "U2": ["B1"],
    "S1": ["B1"],
    "DS1": [],
    "D1": ["S1"],
    "DEF1-api": ["B1"],
    "UI-inspections": ["DS1", "U2"],
    "REV1-api": ["DEF1-api"],
    "UI-evidence": ["DS1", "DEF1-api", "REV1-api"],
    # owner, 2026-10-27: UI-evidence split in three; its branch continues as the first part
    "ME1": [],
    "UI-evidence-b": ["UI-evidence", "ME1"],  # the role from GET /api/me (2026-10-28)
    "CONF-fix": [],
    "UI-evidence-c": ["UI-evidence-b"],
    "RPT1": ["REV1-api"],
    "UI-report": ["RPT1", "DS1"],
    "D2": ["S1", "U2"],
    "COV1": ["RPT1"],
    "COV1-b": ["COV1"],  # owner, 2026-10-28: COV1 split in two
    "DEMO1": [],
    "DEMO1-ui": ["DEMO1"],
    # owner, 2026-10-29: gaps from the `make local` walk (PR #97)
    "COV-ui": ["CONF-fix"],
    "TEST-v0008": [],
    "DEMO-cov": ["COV1-b"],
    # owner, 2026-10-30: the full product
    "AUTH1-a": [],
    "AUTH1-b": ["AUTH1-a"],
    "ORG1": ["AUTH1-b"],
    "STOR1": [],
    "GPU1": [],
    "LAB1-a": ["STOR1"],
    "LAB1-b": ["LAB1-a"],
    "MARK-photo": [],
    "SEC1": ["AUTH1-b"],
    "SEC1-ci": [],
    "AUTH1-ui-a": ["AUTH1-a"],
    "AUTH1-ui-b": ["AUTH1-b", "AUTH1-ui-a"],
    "ORG1-ui": ["ORG1", "AUTH1-ui-a"],
    "LAB1-ui-a": ["LAB1-a"],
    "LAB1-ui-b": ["LAB1-b", "LAB1-ui-a"],
    "UI-mark-photo": ["MARK-photo", "LAB1-ui-b"],
    "DESIGN-skills": [],
}
LATER = [
    "P5a-1", "P5a-2", "P7", "P4a-2b", "P4c", "A7a", "A7", "O1a", "O1", "P5b", "P6", "UX-kk",
    "UX-390", "A8", "ACC1",
    "UI-mark",  # owner, 2026-10-30: marking on a frame first (UI-mark-photo)
]  # fmt: skip
LATER_SINCE = {"UI-mark": "2026-10-30"}  # the others since 2026-10-22


def developer_of(task: dict) -> str:
    return next(d for d, c in CFG["developers"].items() if task["area"] in c["areas"])


@pytest.mark.backlog
def test_the_file_ends_with_the_demo_chain_then_later_in_the_owners_order():
    ids = [t["id"] for t in real_backlog() if not t.get("added_by")]  # the owner's entries
    assert ids[ids.index("B1") :] == CHAIN + LATER
    assert ids.index("P4a-3") == ids.index("P4a-2a-run") + 1  # finished tasks keep their place


@pytest.mark.backlog
def test_demo_chain_dependencies_are_the_owners_and_never_point_off_the_chain():
    by = {t["id"]: t for t in real_backlog()}
    added = {tid for tid, t in by.items() if t.get("added_by")}  # Analyst / Architect, checked
    owners = {tid: [d for d in by[tid].get("depends_on", []) if d not in added] for tid in CHAIN}
    assert owners == DEPS  # in test_analyst
    parked = set(LATER) | {"P5a"}  # LATER and dropped
    for tid in CHAIN:
        assert not set(by[tid].get("depends_on") or []) & parked, tid
        assert bool(by[tid].get("owner_only")) == (tid in OWNER_ONLY), tid
    # Owner 2026-10-24: v8 landed and is the UI's source of truth; DS1 applies it and may not
    # write docs/design/ (owner-only, config gates.protected).
    assert "source of truth" in by["DS1"]["goal"] and "writable" not in by["DS1"]


@pytest.mark.backlog
def test_later_tasks_say_why_and_acc1_replaces_p7():
    by = {t["id"]: t for t in real_backlog()}
    for tid in LATER:
        since = LATER_SINCE.get(tid, "2026-10-22")
        assert by[tid].get("blocked_reason", "").startswith(f"LATER (owner {since})"), tid
    assert by["ACC1"]["depends_on"] == ["P5a-2"] and "Replaces P7" in by["ACC1"]["goal"]
    assert by["A7"]["branch"] == "devteam/A7"  # earlier work is never lost
    # the splits of 2026-10-19/21 survive the move (from test_run2, test_supervisor; #65)
    assert by["O1"]["depends_on"] == ["O1a"] and by["A7"]["depends_on"] == ["A7a"]
    assert "U2" in by["A8"]["depends_on"]  # no new viewer features before upload
    assert "P4a-2" not in by and by["P4a-2b"]["depends_on"] == ["P4a-2a-run"]
    for tid in ("UX-kk", "UX-390"):  # web/ polish waits for the v2 design, no dependency
        assert not by[tid].get("depends_on")


@pytest.mark.backlog
def test_every_chain_and_later_goal_stays_in_its_developers_folders():
    """A goal naming another developer's folder makes the brain split the task (A7, 2026-10-17)."""
    by = {t["id"]: t for t in real_backlog()}
    for tid in [t for t in CHAIN + LATER if t not in OWNER_ONLY]:
        t = by[tid]
        dev = developer_of(t)
        assert outside_paths([t["goal"]], dev, CFG, t) == [], (tid, dev)


@pytest.mark.backlog
def test_every_demo_chain_goal_has_done_when_checks_and_a_scope():
    by = {t["id"]: t for t in real_backlog()}
    for tid in CHAIN:
        goal = by[tid]["goal"]
        assert "Done when" in goal and "Not in this task" in goal, tid
        assert len(goal.splitlines()) <= 30, (tid, len(goal.splitlines()))
    for tid in ("B1", "DEF1-api", "UI-evidence"):  # metres only with a scale
        assert "scale" in by[tid]["goal"].lower()
    assert "STOPPED until Kubernetes" in by["DEF1-api"]["goal"]


@pytest.mark.backlog
def test_ui_evidence_split_keeps_its_branch_and_bans_bundled_findings():
    """Owner 2026-10-27: part 1 alone was 32 files and used every fix loop, and it bundled a
    fabricated finding into the client for the screenshots."""
    by = {t["id"]: t for t in real_backlog()}
    a = by["UI-evidence"]
    assert a["branch"] == "devteam/UI-evidence"  # earlier work continues as part 1
    assert "remove src/fixtures/evidence.ts" in a["goal"] and "retryOnMount" in a["goal"]
    assert "window.__qarau.pointsLoaded" in a["goal"] and "Demo site" in a["goal"]
    for tid in ("UI-evidence", "UI-evidence-b", "UI-evidence-c"):
        assert "Owner 2026-10-27:" in by[tid]["goal"], tid


@pytest.mark.backlog
def test_demo1_purges_only_an_event_organization_with_an_immutable_flag():
    """Owner 2026-10-28: decision and audit_log stay append-only in every other organization."""
    goal = {t["id"]: t for t in real_backlog()}["DEMO1"]["goal"]
    assert "except one created by `app.event create`" in goal
    assert "immutable afterwards" in goal and "a DB trigger rejects" in goal
    assert "normal organization still cannot be deleted or updated" in goal
    assert "flag cannot be changed after creation" in goal


@pytest.mark.backlog
def test_p4a_2a_run_records_its_pr_and_the_notes_survive_a_redump():
    by = {t["id"]: t for t in real_backlog()}
    t = by["P4a-2a-run"]
    assert t["pr"].endswith("/pull/58") and t["done_at"] == "2026-10-21"
    assert "blocked_reason" not in t
    data = yaml.safe_load((HERE / "backlog.yaml").read_text())
    assert "Demo chain (owner, 2026-10-22)" in data["notes"]  # a key: update_task keeps it


def test_later_tasks_are_never_planned_unblocked_or_resumed(repo):
    team, graph = make(repo)
    team.update_task("T-003", status="later", depends_on=["T-001"], blocked_reason="LATER: owner")
    seen, _ = recording(team.agents)
    run(graph, "r1")  # T-001 and T-002 merge
    assert "T-003" not in seen[0] and statuses(team)["T-001"] == "done"
    assert team.unblock_ready() == [] and statuses(team)["T-003"] == "later"
    team.request("resume", "T-003")
    assert team.apply_controls(running=False) == ["T-003: later, nothing to resume"]
    assert statuses(team)["T-003"] == "later"
    assert not any("T-003" in m for m in msgs(team) if "skipping" in m)
