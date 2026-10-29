"""Dev seed coverage (DEMO-cov): the seed runs the pipeline's coverage step on the demo run and
stores its regions as the S1 workflow does. Fixture run: three clusters of 25 points in voxels
0_0_0 (tracked by 3 frames: inspected_ok), 1_0_0 (4 frames, the demo finding) and 3_0_0 (1 frame:
not_seen); 4 pinhole cameras at x = 0..3, 10 units in front of the points."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import bearer
from fastapi.testclient import TestClient
from qarau_pipeline.report.inputs import load_report_input, with_coverage
from qarau_pipeline.report.render import load_strings, render_html
from sqlalchemy.orm import Session
from test_seed import count, dev_env  # noqa: F401 - the fixture

from app import seed
from app.db import engine_for
from app.main import app
from app.models import CoverageRegion, Inspection, Role, Run, Site
from app.settings import get_settings

CLUSTERS = [(0.5, [0, 1, 2]), (1.5, [0, 1, 2, 3]), (3.5, [0])]  # x centre, frames tracking it
CAMERA = {"model": "PINHOLE", "params": [100, 100, 50, 50], "width": 100, "height": 100,
          "K": [100, 100, 50, 50]}  # fmt: skip
EXPECTED = [
    {"id": "0_0_0", "state": "inspected_ok", "frame_count": 3,
     "bounds": {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]}},
    {"id": "1_0_0", "state": "finding", "frame_count": 4,
     "bounds": {"min": [1.0, 0.0, 0.0], "max": [2.0, 1.0, 1.0]}},
    {"id": "3_0_0", "state": "not_seen", "frame_count": 1,
     "bounds": {"min": [3.0, 0.0, 0.0], "max": [4.0, 1.0, 1.0]}},
]  # fmt: skip
TOKENS = {r: f"{r.value}-token" for r in (Role.admin, Role.engineer, Role.viewer)}  # dev_env's


def write_run(run: Path, poses: bool = True) -> None:
    pts, links = [], []
    for cx, tracked in CLUSTERS:
        for k in range(25):
            pts.append([cx - 0.1 + 0.05 * (k % 5), 0.4 + 0.05 * (k // 5), 0.5])
            links.append(tracked)
    web = run / "web"
    web.mkdir(parents=True)
    np.array(pts, "<f4").tofile(web / "points.position.f32")
    np.cumsum([0] + [len(r) for r in links]).astype("<u4").tofile(web / "po.u32")
    np.array([f for r in links for f in r], "<u2").tofile(web / "pf.u16")
    frames = [{"i": i, "image": f"frames/{i:06d}.jpg", "registered": True, "camera": CAMERA,
               **({"pose": {"qvec": [1, 0, 0, 0], "tvec": [-i, -0.5, 10.0]}} if poses else {})}
              for i in range(4)]  # fmt: skip
    man = {"version": 2, "frames": frames,
           "points": {"count": len(pts), "position": "web/points.position.f32"},
           "tracks": {"point_offsets": "web/po.u32", "point_frames": "web/pf.u16"}}  # fmt: skip
    (web / "manifest.json").write_text(json.dumps(man))


@pytest.fixture
def demo_run(dev_env: str, runs_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:  # noqa: F811
    monkeypatch.setattr(seed, "DEMO_POINTS", 25)  # the finding: exactly the 1_0_0 cluster
    write_run(runs_root / seed.DEMO_RUN)
    return runs_root / seed.DEMO_RUN


def coverage(insp_id: int) -> dict[str, Any]:
    r = TestClient(app).get(f"/api/inspections/{insp_id}/coverage", headers=bearer("viewer"))
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def demo_inspection(db_url: str) -> int:
    with Session(engine_for(db_url)) as db:
        (insp,) = db.query(Inspection).all()
        return insp.id


def test_seed_stores_the_coverage_step_regions_once(
    dev_env: str,  # noqa: F811
    demo_run: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed.main()
    out = capsys.readouterr().out
    assert (
        "coverage of 'Demo site': 3 regions: inspected_ok 1 (33.3%), finding 1 (33.3%), "
        "not_seen 1 (33.3%)"
    ) in out
    insp_id = demo_inspection(dev_env)
    with Session(engine_for(dev_env)) as db:
        assert count(db, CoverageRegion) == 3
    c = coverage(insp_id)
    assert c["computed"] is True
    assert c["totals"] == {"inspected_ok": 33.3, "finding": 33.3, "not_seen": 33.3}
    assert c["regions"] == EXPECTED

    regions_json = (demo_run / "coverage" / "regions.json").read_bytes()
    seed.main()  # the second run adds nothing and rewrites nothing
    assert "coverage of 'Demo site': 3 regions" in capsys.readouterr().out
    with Session(engine_for(dev_env)) as db:
        assert count(db, CoverageRegion) == 3
    assert (demo_run / "coverage" / "regions.json").read_bytes() == regions_json
    assert coverage(insp_id)["regions"] == EXPECTED


def test_seed_backfills_an_existing_demo_site_without_coverage(
    dev_env: str,  # noqa: F811
    demo_run: Path,
) -> None:
    with Session(engine_for(dev_env)) as db:
        org = seed.seed_dev_users(db, TOKENS)
        assert seed.seed_demo(db, org, get_settings()) is not None  # the pre-DEMO-cov seed
        assert count(db, CoverageRegion) == 0
    insp_id = demo_inspection(dev_env)
    assert coverage(insp_id)["computed"] is False
    seed.main()
    with Session(engine_for(dev_env)) as db:
        assert count(db, CoverageRegion) == 3
    assert coverage(insp_id)["regions"] == EXPECTED


def test_a_failing_coverage_step_is_printed_and_the_seed_finishes(
    dev_env: str,  # noqa: F811
    runs_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_run(runs_root / seed.DEMO_RUN, poses=False)
    with Session(engine_for(dev_env)) as db:  # an existing demo site on a run without poses
        org = seed.seed_dev_users(db, TOKENS)
        site = Site(org_id=org.id, name=seed.DEMO_SITE)
        db.add(site)
        db.flush()
        insp = Inspection(site_id=site.id, mode="quick_scan", status="done")
        db.add(insp)
        db.flush()
        db.add(Run(id=seed.DEMO_RUN, inspection_id=insp.id, run_dir=seed.DEMO_RUN, status="done"))
        db.commit()
        insp_id = insp.id
    seed.main()  # no exception
    out = capsys.readouterr().out
    assert (
        "Coverage for 'Demo site' was not computed: the coverage step failed on run "
        "notre-dame-p3e (KeyError: 'pose')"
    ) in out
    assert "seeded organization 'dev'" in out
    with Session(engine_for(dev_env)) as db:
        assert count(db, CoverageRegion) == 0
    c = coverage(insp_id)
    assert (c["computed"], c["totals"], c["regions"]) == (False, {}, [])
    assert not (runs_root / seed.DEMO_RUN / "coverage").exists()


def test_the_demo_report_lists_the_not_seen_region(
    dev_env: str,  # noqa: F811
    demo_run: Path,
    runs_root: Path,
) -> None:
    seed.main()
    insp_id = demo_inspection(dev_env)
    when = datetime(2026, 10, 29, 12, tzinfo=UTC)
    inp = with_coverage(load_report_input(engine_for(dev_env), insp_id, when), runs_root)
    html = render_html(inp, load_strings("en"), "en")
    assert "Coverage not computed." not in html
    assert "Not inspected areas: 1." in html
    assert "<tr><td>3_0_0</td>" in html
    assert inp.coverage is not None
    assert [r.id for r in inp.coverage.not_seen] == ["3_0_0"]
    assert inp.coverage.not_seen[0].frames == ("000003.jpg", "000002.jpg", "000001.jpg")
