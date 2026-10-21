"""Owner, 2026-10-21, after the Kerege rehearsal cont-20261021-021001: UX1b round 2 weakened
its own e2e (expected date computed in the browser), kk dates broke unseen (screenshots were ru
only), Owner additions must reach the plan, P4a-2a-run is owner-only, stale cost reservations."""

import copy
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from conftest import CFG, HERE, real_backlog, remote_of
from langgraph.checkpoint.sqlite import SqliteSaver
from rich.console import Console

from devteam import costs, procs
from devteam import dashboard as D
from devteam import gates as G
from devteam import graph as GR
from devteam import real as R
from devteam.agents import FakeAgents
from devteam.graph import PlanError, Team, build_graph, owner_additions, validate_plan


class Fast(FakeAgents):
    delay = 0


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    monkeypatch.setattr(GR, "POLL_S", 0.1)


def make(repo, agents=None, **cfg_over):
    cfg = copy.deepcopy(CFG)
    cfg.update(cfg_over)
    team = Team(repo, cfg, {}, agents or Fast(), fake=True, remote=remote_of(repo))
    graph = build_graph(team, SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False)))
    return team, graph


# --- 1. test files are compared between rounds --------------------------------------------------
TWO = "def test_area():\n    assert area(2) == 4\n    assert area(3) == 9\n"
ONE = "def test_area():\n    assert area(2) == 4\n"
THREE = TWO + "    assert area(4) == 16\n"


def weakening_dev(later: str):
    """T-001 writes a test with two assertions in round 1, then `later` in every next round.
    The fake judge fails round 1 and passes from round 2 on."""

    class Dev(Fast):
        def develop(self, team, s):
            if s["task"]["id"] == "T-001":
                test = Path(s["worktree"]) / "pipeline" / "tests" / "test_area.py"
                test.parent.mkdir(exist_ok=True)
                test.write_text(TWO if not s.get("rounds") else later)
            return super().develop(team, s)

    return Dev()


def test_an_assertion_removed_after_a_send_back_fails_and_blocks_on_resubmit(repo):
    """Base -> HEAD shows the new test with one assertion: nothing removed. Only the diff
    between rounds shows it, and resubmitting it unchanged is the same reason again."""
    team, graph = make(repo, weakening_dev(ONE))
    graph.invoke({}, {"configurable": {"thread_id": "r"}})
    t = {t["id"]: t for t in team.load_backlog()}["T-001"]
    assert t["status"] == "blocked" and "twice in a row" in t["blocked_reason"]
    found = [v for v in team.violations(time.strftime("%Y-%m-%d")) if v["task"] == "T-001"]
    assert found and "since the last round: removes test assertions" in found[0]["detail"]


def test_a_test_made_stronger_after_a_send_back_passes(repo):
    team, graph = make(repo, weakening_dev(THREE))
    graph.invoke({}, {"configurable": {"thread_id": "r"}})
    assert {t["id"]: t for t in team.load_backlog()}["T-001"]["status"] == "done"


def judge_with(monkeypatch, verdict: dict, **state) -> tuple[dict, dict]:
    seen = {}

    def fake_llm_call(team, role, task, system, prompt, schema, **kw):
        seen.update(system=system, prompt=prompt, schema=schema, images=kw.get("images"))
        return copy.deepcopy(verdict)

    monkeypatch.setattr(R, "llm_call", fake_llm_call)
    team = SimpleNamespace(remote_name="origin", cfg=CFG, git=lambda *a, **k: "", redact=str)
    s = {"task": {"id": "T-1", "title": "t"}, "plan": "p", "criteria": ["c"], "worktree": "."}
    return R.RealAgents().judge(team, {**s, **state}, []), seen


PASS = {
    "passed": True,
    "criteria": [{"number": 1, "criterion": "c", "met": True, "note": ""}],
    "tests_weakened": True,
    "feedback": "",
    "summary": "",
    "how_to_try": "",
}


def test_the_judge_sees_the_test_diff_and_tests_weakened_fails_the_task(monkeypatch):
    diff = "-    expect(date).toBe('16 қаз.')\n+    expect(date).toBe(intlFormat(d))\n"
    v, seen = judge_with(monkeypatch, PASS, test_changes=diff)
    assert "<test_changes_since_last_round>\n" + diff in seen["prompt"]
    assert "tests_weakened" in seen["schema"]["required"]
    assert v["passed"] is False and "weaken the tests" in v["feedback"]
    # no test diff (round 1): the flag alone cannot fail it
    v, seen = judge_with(monkeypatch, PASS)
    assert v["passed"] is True and "test_changes_since_last_round" not in seen["prompt"]


def test_fixtures_rule_is_in_the_dev_tester_and_brain_prompts():
    rule = "Expected values come from fixtures, never from the code under test"
    assert rule in R.TESTER_SYSTEM
    assert rule in Path(R.__file__).read_text().split("def _brief", 1)[1].split("def _claude")[0]
    assert "never values computed by the code\n  under test" in R.BRAIN_SYSTEM


def test_images_over_the_request_limit_are_named_never_silently_cut(monkeypatch, tmp_path):
    shots = []
    for name in ("root-ru.png", "root-kk.png", "dev-root.png"):
        (tmp_path / name).write_bytes(b"x" * 100)
        shots.append(str(tmp_path / name))
    monkeypatch.setattr(R, "MAX_IMAGES", 2)
    _v, seen = judge_with(monkeypatch, {**PASS, "tests_weakened": False}, screenshots=shots)
    assert seen["images"] == shots[:2]
    assert "dev-root.png: not attached (API request size limit)" in seen["prompt"]


# --- 2. dev_app screenshots in every locale -------------------------------------------------------
def test_variants_cover_every_locale_and_the_before_shots_only_the_first():
    sc = {"viewports": ["1440x900", "390x844"], "themes": ["light", "dark"],
          "locales": ["ru", "kk", "en"]}  # fmt: skip
    assert G.variants(sc) == [
        ("1440x900", "light", "ru"),
        ("1440x900", "dark", "ru"),
        ("390x844", "light", "ru"),
        ("390x844", "dark", "ru"),
        ("1440x900", "light", "kk"),
        ("390x844", "light", "kk"),
        ("1440x900", "light", "en"),
        ("390x844", "light", "en"),
    ]
    assert G.variants(sc, before=True) == G.variants(sc)[:4]
    assert G.variants({}) == [("1440x900", "", ""), ("390x844", "", "")]  # no locales: as before


def test_dev_app_is_shot_in_ru_kk_and_en(repo, tmp_path):
    args = tmp_path / "args.txt"
    port = procs.free_port()
    devs = copy.deepcopy(CFG["developers"])
    devs["dev_app"]["setup"] = []
    devs["dev_app"]["screenshots"] = {
        "serve": f"exec python3 -m http.server {port} --bind 127.0.0.1",
        "url": f"http://127.0.0.1:{port}",
        "shoot": f"echo {{size}} {{theme}} {{lang}} >> {args}; touch {{png}}",
        "viewports": ["1440x900"],
        "locales": ["ru", "kk", "en"],
    }
    team, _ = make(repo, developers=devs)
    s = {"task": {"id": "T-002"}, "assignee": "dev_app", "worktree": str(repo), "routes": ["/"]}
    shots, problems = G.screenshots(team, s, s["routes"])
    assert problems == [] and G.expected_shots(team.cfg, s) == 3
    assert [Path(p).name for p in shots] == [
        "root-1440x900-ru.png",
        "root-1440x900-kk.png",
        "root-1440x900-en.png",
    ]
    # no theme: still an (empty) argument, so the locale stays shoot.mjs's 6th
    assert args.read_text().split("\n")[:3] == ["1440,900  ru", "1440,900  kk", "1440,900  en"]


def test_config_and_shoot_script_set_the_app_locale():
    cfg = yaml.safe_load((HERE / "config.yaml").read_text())
    app = cfg["developers"]["dev_app"]["screenshots"]
    assert app["locales"] == ["ru", "kk", "en"] and app["shoot"].endswith(" {theme} {lang}")
    assert len(G.variants(app)) == 8  # 4 in ru (light/dark x 2 viewports), 2 each in kk, en
    text = G.SHOOT.read_text()
    assert "localStorage.setItem('qarau.lang', l)" in text and "addInitScript" in text
    i18n = (HERE.parent / "web" / "src" / "i18n" / "index.ts").read_text()
    assert "'qarau.lang'" in i18n  # the key the app really reads


# --- 3. Owner additions reach the plan word for word ---------------------------------------------
GOAL = """web/ only.


Owner additions (2026-10-21):

- The side panel shows at least 12 inspections at 1440x900 without scrolling it.

- No "Example" badge next to the "Example inspection" link.

"""


def plan_with(*criteria: str) -> list[dict]:
    return [{"task_id": "T-002", "assignee": "dev_app", "plan": "p", "criteria": list(criteria),
             "routes": []}]  # fmt: skip


def test_owner_additions_are_parsed_from_the_goal():
    assert owner_additions(GOAL) == [
        "The side panel shows at least 12 inspections at 1440x900 without scrolling it.",
        'No "Example" badge next to the "Example inspection" link.',
    ]
    assert owner_additions("no additions here\n- a bullet of the goal itself") == []


def test_a_plan_that_drops_an_owner_addition_is_rejected():
    todo = [{"id": "T-002", "area": "app", "title": "t", "goal": GOAL}]
    first = "The side panel shows at least 12 inspections at 1440x900 without scrolling it."
    with pytest.raises(PlanError) as e:
        validate_plan(plan_with("Vitest green", first), todo, CFG)
    assert "drop 1 of the task's Owner additions" in str(e.value) and "Example" in str(e.value)
    ok = plan_with(  # case and line breaks may differ; extra words around it are fine
        "Vitest green",
        "the side panel shows at least 12 inspections at 1440x900\nwithout scrolling it.",
        'No "Example" badge next to the "Example inspection" link. Checked in en/ru/kk.',
    )
    assert validate_plan(ok, todo, CFG)[0]["criteria"] == ok[0]["criteria"]


@pytest.mark.backlog
def test_ux1b_owner_additions_parse_from_the_real_backlog():
    ux1b = next(t for t in real_backlog() if t["id"] == "UX1b")
    lines = owner_additions(ux1b["goal"])
    assert len(lines) == 3 and lines[0].startswith("The side panel shows at least 12")


# --- 4. owner-only tasks: never `devteam resume` --------------------------------------------------
def test_dashboard_never_suggests_resume_for_an_owner_only_task():
    reason = "waits for the owner: P4a-2a-code merged, then the owner runs it"
    t = {"id": "P4a-2a-run", "status": "blocked", "blocked_reason": reason}
    assert "devteam resume" in D.blocked_action(t, {})  # what it said before
    do = D.blocked_action({**t, "owner_only": True}, {})
    assert "devteam resume" not in do and "Owner-only" in do


def test_the_brain_never_plans_an_owner_only_task(repo):
    team, graph = make(repo)
    team.update_task("T-001", owner_only=True)
    graph.invoke({}, {"configurable": {"thread_id": "r"}})
    by = {t["id"]: t for t in team.load_backlog()}
    assert by["T-001"]["status"] == "todo" and by["T-002"]["status"] == "done"
    assert "skipping owner-only tasks" in (team.home / "events.log").read_text()


@pytest.mark.backlog
def test_p4a_2a_run_is_owner_only_in_the_backlog():
    run = next(t for t in real_backlog() if t["id"] == "P4a-2a-run")
    assert run["owner_only"] is True
    assert "devteam resume" not in D.blocked_action(run, {t["id"]: t for t in real_backlog()})


# --- 5. stale cost reservations ---------------------------------------------------------------
def test_a_reservation_left_open_past_two_hours_becomes_stale_at_its_worst_case(tmp_path):
    rid = costs.reserve(tmp_path, 10.0, "brain", "m", "T-1", 0.75)
    assert costs.mark_stale(tmp_path) == 0  # a call in flight now: left alone
    assert costs.mark_stale(tmp_path, now=time.time() + 3 * 3600) == 1
    db = costs._db(tmp_path)
    assert db.execute("select state, usd from usage where rowid=?", (rid,)).fetchone() == (
        "stale",
        0.75,
    )
    db.close()
    assert costs.spent_24h(tmp_path) == pytest.approx(0.75)  # money is never under-counted
    costs.release(tmp_path, rid)  # only open reservations are released
    assert costs.spent_24h(tmp_path) == pytest.approx(0.75)
    (row,) = costs.day_rows(tmp_path, costs._today())
    assert row[-1] == 1
    console = Console(record=True, width=200)
    costs.report(tmp_path, console)
    assert "1 stale: billing unknown, counted at max" in console.export_text()


def test_dashboard_and_report_label_stale_reservations(repo):
    team, _ = make(repo)
    costs.reserve(team.home, 10.0, "tester", "m", "T-001", 0.5)
    costs.mark_stale(team.home, now=time.time() + 3 * 3600)
    assert "1 stale: billing unknown, counted at max" in D.render(D.collect(team, None))
    from devteam.report import write_report

    assert "1 stale: billing unknown, counted at max" in write_report(team).read_text()
