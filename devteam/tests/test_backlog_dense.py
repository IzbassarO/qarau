"""Owner, 2026-10-21, after the first real COLMAP dense run (P4a-2a-run): the viewer shows the
dense layer (UX-dense), dense gets speed knobs (P4a-3)."""

import pytest
from conftest import CFG, real_backlog

from devteam.graph import outside_paths, owner_additions


@pytest.mark.backlog
def test_ux_dense_and_p4a_3_are_plannable_by_their_developers():
    tasks = real_backlog()
    by = {t["id"]: t for t in tasks}
    ids = [t["id"] for t in tasks]
    assert ids.index("P4a-3") == ids.index("P4a-2a-run") + 1
    assert "UX-dense" in ids  # UX-390 went LATER on 2026-10-22, UX-dense stays where it merged
    for tid, dev in (("UX-dense", "dev_app"), ("P4a-3", "dev_pipeline")):
        t = by[tid]
        assert t["area"] in CFG["developers"][dev]["areas"] and not t.get("depends_on")
        assert outside_paths([t["goal"]], dev, CFG, t) == []  # no split needed


@pytest.mark.backlog
def test_ux_dense_keeps_the_owners_line_and_the_real_run_to_look_at():
    goal = {t["id"]: t for t in real_backlog()}["UX-dense"]["goal"]
    assert owner_additions(goal) == [
        "The viewer shows the dense layer from manifest.dense (with frame↔point links)."
    ]
    assert "/viewer/notre-dame-p3e-modal" in goal and "fixed expected" in goal


@pytest.mark.backlog
def test_p4a_3_never_asks_kerege_for_a_paid_run_and_records_the_measurement():
    goal = {t["id"]: t for t in real_backlog()}["P4a-3"]["goal"]
    assert "no paid call" in goal and "Kerege never runs Modal" in goal
    for fact in ("11.6 min", "15.0 min", "1681 s", "< 30 min", "300 frames"):
        assert fact in goal
    for knob in ("max_image_size", "num_iterations", "window_step", "source frames"):
        assert knob in goal
