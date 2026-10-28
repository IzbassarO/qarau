"""Coverage on real footage: the notre-dame-p3e run (121 registered frames, 42 748 points).
Runs in the default `pytest -q` wherever the run exists. The run, its absent-run condition
(NO_RUN) and the chapel-facade polygon come from test_findings_real.py, one definition for both
real-run tests. The run's own files are never written: the test links them into a tmp run dir,
adds the facade finding there (the run has no findings/ of the project stage, so this manual
finding, selected by findings.select_points, is its only finding) and writes coverage/ into
that tmp dir."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from test_findings_real import FACADE, NO_RUN, RUN

from qarau_pipeline import workflow
from qarau_pipeline.coverage import run_coverage
from qarau_pipeline.findings import load_bundle, select_points


@NO_RUN
def test_notre_dame_regions_show_all_three_states(tmp_path: Path) -> None:
    run = tmp_path / "notre-dame-p3e"
    run.mkdir()
    for p in RUN.iterdir():
        if p.name not in ("findings", "coverage"):
            (run / p.name).symlink_to(p)
    pos = load_bundle(run).positions
    lab = np.zeros(len(pos), "<u2")
    lab[select_points(pos, FACADE)] = 1
    (run / "findings").mkdir()
    lab.tofile(run / "findings" / "points.finding.u16")

    summary = run_coverage(run, workflow.load_config())
    doc = json.loads((run / "coverage" / "regions.json").read_text())
    assert doc["summary"] == {k: summary[k] for k in ("inspected_ok", "finding", "not_seen")}
    states = [r["state"] for r in doc["regions"]]
    for st in ("inspected_ok", "finding", "not_seen"):
        assert st in states, (st, doc["summary"])
    for r in doc["regions"]:
        assert set(r) >= {"bounds", "state", "frame_count"}
        assert len(r["bounds"]["min"]) == len(r["bounds"]["max"]) == 3
        assert r["state"] != "inspected_ok" or r["frame_count"] >= 3
    assert all(r["finding_ids"] == [1] for r in doc["regions"] if r["state"] == "finding")
