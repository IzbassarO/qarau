"""Owner, 2026-10-21, after the Kerege rehearsal cont-20261021-021001: kk dates in Chrome with
honest tests (UX-kk), the viewer at 390 px (UX-390), mykolaiv's gate decision written down."""

import pytest
import yaml
from conftest import CFG, HERE, real_backlog

from devteam import gates as G
from devteam.graph import outside_paths

KK_SHORT = "қаң., ақп., нау., сәу., мам., мау., шіл., там., қыр., қаз., қар., жел."


@pytest.mark.backlog
def test_ux_kk_and_ux_390_come_right_after_ux1b_for_dev_app():
    tasks = real_backlog()
    app = [t["id"] for t in tasks if t["area"] == "app"]
    assert app[app.index("UX1b") + 1 : app.index("UX1b") + 3] == ["UX-kk", "UX-390"]
    by = {t["id"]: t for t in tasks}
    for tid in ("UX-kk", "UX-390"):
        t = by[tid]
        assert "web/ only" in t["goal"] and not t.get("depends_on")
        assert outside_paths([t["goal"]], "dev_app", CFG, t) == []  # no split needed


@pytest.mark.backlog
def test_ux_kk_fixes_the_expected_strings_and_the_month_table():
    goal = {t["id"]: t for t in real_backlog()}["UX-kk"]["goal"]
    assert KK_SHORT in goal and "желтоқсан" in goal
    for fixed in ('"16 Oct"', '"16 окт."', '"16 қаз."', '"16 қазан"'):
        assert fixed in goal
    assert "never computed with Intl" in goal and "control-center.spec.ts" in goal


@pytest.mark.backlog
def test_ux_390_names_what_overlaps_and_how_it_is_tested():
    goal = {t["id"]: t for t in real_backlog()}["UX-390"]["goal"]
    for part in (".viewer-hint.orbit", ".viewer-gizmo", ".viewer-timeline", "390x844"):
        assert part in goal
    assert "en, ru and kk" in goal and "never loosened" in goal


def test_mykolaiv_stays_ungated_by_the_owners_decision():
    cfg = yaml.safe_load((HERE / "config.yaml").read_text())["gates"]["benchmark"]
    assert not G.gated(cfg, "mykolaiv")
    why = cfg["per_video"]["mykolaiv"]["why"]
    assert "owner 2026-10-21" in why and "P3c" in why
    assert "gate again after P3" not in (HERE / "config.yaml").read_text()
