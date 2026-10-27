"""PDF report pictures (RPT1 part 2, ADR 0032): the software map snapshot and the evidence
plates. Expected pixels, markers and SVG strings are literals worked out by hand from the
fixtures, never computed with the code under test."""

import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from pypdf import PdfReader

from qarau_pipeline.report import ReportInput, load_strings, render_pdf
from qarau_pipeline.report.inputs import Evidence, FindingInput, InspectionInput, VersionEntry
from qarau_pipeline.report.render import render_html
from qarau_pipeline.report.snapshot import (
    MAP_GROUND,
    TOP_DOWN,
    View,
    frame_cloud,
    load_visuals,
    map_snapshot,
    map_view,
    png,
    rasterize,
)

RED, GREEN, BLUE = (255, 0, 0), (0, 255, 0), (0, 0, 255)
# Listed so that a plain "last point drawn wins" would paint A's pixel red: the z-buffer must
# keep B, which is 1 above A and so nearer a camera looking down.
CLOUD = np.array([[0, 0, 1], [4, 2, 0], [0, 0, 0]], float)  # B green, C blue, A red
COLOURS = np.array([GREEN, BLUE, RED], np.uint8)


def test_top_down_pixels() -> None:
    """11 x 7 image, 1 px pad: the cloud spans 4 x 2 units, so 2 px per unit around pixel
    (5, 3); A and B land on (1, 5), C on (9, 1); each point is a 3 x 3 square."""
    img = rasterize(CLOUD, COLOURS, frame_cloud(CLOUD, TOP_DOWN, (11, 7), pad=1), dot=3)
    assert img.shape == (7, 11, 3)
    for x, y in [(1, 5), (0, 4), (2, 6), (0, 6), (2, 4)]:
        assert tuple(img[y, x]) == GREEN, (x, y)  # B over A
    for x, y in [(9, 1), (8, 0), (10, 2), (10, 0)]:
        assert tuple(img[y, x]) == BLUE, (x, y)
    for x, y in [(5, 3), (3, 5), (1, 3), (7, 1), (0, 0), (10, 6)]:
        assert tuple(img[y, x]) == (0x13, 0x13, 0x13), (x, y)  # v8 map-ground
    assert MAP_GROUND == (0x13, 0x13, 0x13)


def test_side_view_pixels() -> None:
    """From +x, level: screen x is the cloud's y, screen y its z. Spans 2 x 1 units, so 4 px per
    unit around (5, 3): B on (1, 1), C on (9, 5), A on (1, 5); single pixels."""
    img = rasterize(CLOUD, COLOURS, frame_cloud(CLOUD, View(0.0, 0.0), (11, 7), pad=1), dot=1)
    assert tuple(img[1, 1]) == GREEN
    assert tuple(img[5, 9]) == BLUE
    assert tuple(img[5, 1]) == RED
    assert int((img != 0x13).any(axis=2).sum()) == 3


def test_same_cloud_same_png_bytes() -> None:
    view = View(30.0, 30.0)
    a = png(rasterize(CLOUD, COLOURS, frame_cloud(CLOUD, view, (64, 48))))
    b = png(rasterize(CLOUD.copy(), COLOURS.copy(), frame_cloud(CLOUD.copy(), view, (64, 48))))
    assert a == b
    assert a[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------- a run dir as export writes it

FRAME_SIZE = (64, 48)
OUTLINES = {
    7: ((10.0, 20.0), (60.5, 20.0), (35.0, 44.25)),
    3: ((1.0, 2.0), (30.0, 2.0), (30.0, 40.0), (1.0, 40.0)),
    9: ((5.0, 5.0), (50.0, 10.0), (20.0, 30.0)),
    5: ((0.0, 0.0), (8.0, 0.0), (8.0, 8.0)),
}
POINTS_7 = "10,20 60.5,20 35,44.25"
POINTS_3 = "1,2 30,2 30,40 1,40"
POINTS_9 = "5,5 50,10 20,30"


def write_run(run: Path) -> None:
    """Export layout (ADR 0022): 5 points on the ground, 4 frames, 3 of them registered on the
    -y side (centres at x = 0, y = -10), frame 5's image missing."""
    web = run / "export/web"
    web.mkdir(parents=True)
    (run / "frames").mkdir()
    xyz = np.array([[-4, -1, 0], [4, -1, 0], [4, 1, 0], [-4, 1, 0], [0, 0, 0]], "<f4")
    xyz.tofile(web / "points.position.f32")
    np.full((5, 3), 200, "u1").tofile(web / "points.color.u8")
    frames = []
    for i, t in [(3, 1.0), (5, 1.5), (7, 2.4), (9, 3.0)]:
        if i != 5:
            Image.new("RGB", FRAME_SIZE, (i * 20, 90, 120)).save(run / f"frames/{i:06d}.png")
        frames.append({
            "i": i, "t": t, "image": f"frames/{i:06d}.png", "registered": i != 5,
            "center": [0.0, -10.0, float(i)],
            "camera": {"width": FRAME_SIZE[0], "height": FRAME_SIZE[1]},
        })  # fmt: skip
    man = {
        "points": {"position": "export/web/points.position.f32",
                   "color": "export/web/points.color.u8"},
        "frames": frames,
    }  # fmt: skip
    (web / "manifest.json").write_text(json.dumps(man))


def card_finding(
    id: int, status: str, frames: tuple[int, ...], points: tuple[int, ...]
) -> FindingInput:
    return FindingInput(
        id=id, class_="corrosion", severity="critical", status=status, area_m2=None,
        scale_source=None, area_uncertainty_pct=None, run_dir="run-a",
        evidence=tuple(Evidence(f, OUTLINES[f]) for f in frames), decisions=(), points=points,
    )  # fmt: skip


TIME = datetime(2026, 10, 26, 9, 32, tzinfo=UTC)
INPUT = ReportInput(
    organization="Caspian Inspection Services", site="Tank T-104", asset=None,
    inspection=InspectionInput(7, "quick_scan", TIME), capture_date=TIME,
    report_number="QV-2026-0007", version=1, engineer=None, report_time=TIME,
    findings=(
        # 4 evidence frames in the API's order: the card shows the first 3, in that order
        card_finding(1, "confirmed", (7, 3, 9, 5), (0, 1, 2, 3)),
        card_finding(2, "rejected", (5,), (4,)),
    ),
    versions=(VersionEntry(1, TIME, None),), verification_id="7F3A-09C2-B4E1-5D68",
    run_dirs=("run-a",),
)  # fmt: skip


@pytest.fixture
def runs(tmp_path: Path) -> Path:
    write_run(tmp_path / "run-a")
    return tmp_path


def test_map_view_looks_from_the_filmed_side(runs: Path) -> None:
    man = json.loads((runs / "run-a/export/web/manifest.json").read_text())
    assert map_view(man) == View(-90.0, 30.0)
    assert map_view({"frames": []}) == View(-90.0, 30.0)
    east = {"frames": [{"registered": True, "center": [5.0, 0.0, 1.0]}]}
    assert map_view(east) == View(0.0, 30.0)


def test_markers_and_crops(runs: Path) -> None:
    """Seen from -y, 30° up, the 8 x 2 ground rectangle is 8 wide and 2 sin 30° = 1 high on
    screen: 1300 x 580 at 24 px pad gives min(1251 / 8, 531 / 1) = 156.375 px per unit around
    (649.5, 289.5). Finding 1's points centre on the origin: its marker is the image centre."""
    man = json.loads((runs / "run-a/export/web/manifest.json").read_text())
    snap = map_snapshot(runs / "run-a", man, INPUT.findings)
    assert (snap.width, snap.height) == (1300, 580)
    assert snap.markers == {1: (649.5, 289.5)}  # the rejected finding 2 has no marker
    crop = snap.crops[1]
    assert (crop.x, crop.y, crop.width, crop.height) == (410, 170, 480, 240)
    assert snap.src.startswith("data:image/png;base64,")
    again = map_snapshot(runs / "run-a", man, INPUT.findings)
    assert again == snap  # byte-identical PNG data, same markers and crops


def card_html(html: str, id: int) -> str:
    start = html.index(f'<div class="card" id="finding-{id}">')
    end = html.find('<div class="card"', start + 1)
    return html[start : end if end >= 0 else html.index("</section>", start)]


def test_card_has_three_outlined_evidence_frames(runs: Path) -> None:
    html = render_html(INPUT, load_strings("en"), "en", load_visuals(INPUT, runs))
    card = card_html(html, 1)
    assert len(re.findall(r"<img\b", card)) == 3
    polygons = re.findall(r'<polygon points="([^"]*)"', card)
    assert polygons == [POINTS_7, POINTS_3, POINTS_9]
    assert len(re.findall(r'<svg viewBox="0 0 64 48"', card)) == 3
    assert len(re.findall(r'<img src="data:image/png;base64,', card)) == 3
    assert "Frame 7 at 2.4 s" in card and "Frame 3 at 1.0 s" in card
    assert "Frame 5 at" not in card  # the 4th frame is not shown
    assert "The frame image was not found." not in card
    assert "number 1 on the map in section 2" in card
    assert '<svg class="loc" viewBox="410 170 480 240">' in card
    rejected = card_html(html, 2)
    assert "rejected, so not drawn on the map" in rejected
    assert '<svg class="loc"' not in rejected
    # frame 5's image is missing on disk: the plate says so instead of an empty picture
    assert rejected.count("The frame image was not found.") == 1
    assert "Frame 5 at 1.5 s" in rejected and not re.findall(r"<img\b", rejected)


def test_card_without_runs_dir_names_the_frames_without_pictures() -> None:
    html = render_html(INPUT, load_strings("en"), "en", load_visuals(INPUT, None))
    card = card_html(html, 1)
    assert not re.findall(r"<img\b", card)
    assert card.count("The frame image was not found.") == 3
    assert "not on the map" in card
    assert "No map: no exported 3D map was found for this inspection." in html


@pytest.mark.parametrize("lang", ["ru", "kk", "en"])
def test_pdf_embeds_the_map_and_frames(runs: Path, lang: str) -> None:
    visuals = load_visuals(INPUT, runs)
    pdf = render_pdf(INPUT, load_strings(lang), lang, visuals)
    assert pdf == render_pdf(INPUT, load_strings(lang), lang, load_visuals(INPUT, runs))
    reader = PdfReader(io.BytesIO(pdf))
    images = [i.image for p in reader.pages for i in p.images]  # pages share one image dict
    assert None not in images
    sizes = sorted({(im.width, im.height) for im in images if im is not None})
    # map 1300 x 580, the finding's crop 480 x 240, its frames 64 x 48
    assert sizes == [(64, 48), (480, 240), (1300, 580)]
    text = " ".join(p.extract_text() for p in reader.pages)
    caption = {"en": "Frame 7 at 2.4 s", "ru": "Кадр 7 на 2,4 с", "kk": "Кадр 7, 2,4 с"}[lang]
    assert caption in re.sub(r"\s+", " ", text)
