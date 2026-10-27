"""Pictures of the PDF report (RPT1, ADR 0032): the map snapshot and the evidence frames.

Map: the run's viewer bundle (ADR 0003) drawn by a NumPy point rasterizer, orthographic, from a
fixed camera (`map_view`: 30° above the side the video was filmed from), with a z-buffer, on the
v8 `map-ground`, saved as PNG with Pillow. No GPU and no OpenGL: the same bytes on a laptop, in
CI and in the API container. Finding markers are not in the PNG: the template draws them as SVG
over it (crisp, in the report's own font), at the pixel `MapSnapshot.markers` gives.

Evidence: each finding's first 3 observations (the API's order, DEF1-api) as the frame image
and its outline as SVG polygon points in frame pixels.
"""

from __future__ import annotations

import base64
import io
import json
import math
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..layout import manifest_path
from .inputs import FindingInput, ReportInput

MAP_SIZE = (1300, 580)  # px: the 650 x 290 px v8 report figure at 2x
MAP_GROUND = (0x13, 0x13, 0x13)  # v8 `map-ground`
DOT = 3  # px: side of the square drawn per point
PAD = 24  # px kept free around the cloud
ELEVATION = 30.0  # degrees above the horizon
CROP = (480, 240)  # px of the map shown in a finding card, around its marker
N_PLATES = 3


@dataclass(frozen=True)
class View:
    """A camera direction: it looks at the cloud from `azimuth` (degrees, counter-clockwise
    from +x) and `elevation` (degrees above the xy plane; 90 = straight down)."""

    azimuth: float
    elevation: float

    def axes(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(right, up, forward) unit vectors on screen; forward points into the scene."""
        a, e = math.radians(self.azimuth), math.radians(self.elevation)
        eye = np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])
        right = np.array([-math.sin(a), math.cos(a), 0.0])
        forward = -eye
        return right, np.cross(right, forward), forward


TOP_DOWN = View(-90.0, 90.0)  # +x to the right, +y up


@dataclass(frozen=True)
class Ortho:
    """Orthographic projection of a view, framed to an image: x right, y down, in pixels."""

    view: View
    mid: tuple[float, float]  # screen-plane centre of the framed cloud
    scale: float  # px per map unit
    size: tuple[int, int]  # width, height

    def px(self, xyz: np.ndarray) -> np.ndarray:
        right, up, _ = self.view.axes()
        p = np.asarray(xyz, float).reshape(-1, 3)
        w, h = self.size
        x = (w - 1) / 2 + (p @ right - self.mid[0]) * self.scale
        y = (h - 1) / 2 - (p @ up - self.mid[1]) * self.scale
        return np.stack([x, y], axis=1)


def frame_cloud(xyz: np.ndarray, view: View, size: tuple[int, int], pad: int = PAD) -> Ortho:
    """Frame the cloud: the 0.5-99.5 % range of its points (nearest point, so a small cloud is
    framed by its extremes) fills the image less `pad` on every side."""
    right, up, _ = view.axes()
    p = np.asarray(xyz, float).reshape(-1, 3)
    sx, sy = p @ right, p @ up
    lo = [float(np.percentile(s, 0.5, method="nearest")) for s in (sx, sy)]
    hi = [float(np.percentile(s, 99.5, method="nearest")) for s in (sx, sy)]
    w, h = size
    scale = min((w - 1 - 2 * pad) / max(hi[0] - lo[0], 1e-9),
                (h - 1 - 2 * pad) / max(hi[1] - lo[1], 1e-9))  # fmt: skip
    return Ortho(view, ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2), scale, size)


def rasterize(
    xyz: np.ndarray, rgb: np.ndarray, ortho: Ortho, dot: int = DOT,
    ground: tuple[int, int, int] = MAP_GROUND,
) -> np.ndarray:  # fmt: skip
    """H x W x 3 uint8: every point a `dot` x `dot` square around its pixel; where squares
    overlap the point nearest the camera wins (z-buffer), equal depth: the lower index."""
    w, h = ortho.size
    img = np.empty((h, w, 3), np.uint8)
    img[:] = ground
    p = np.asarray(xyz, float).reshape(-1, 3)
    if not len(p):
        return img
    c = np.floor(ortho.px(p) + 0.5).astype(np.int64)
    depth = p @ ortho.view.axes()[2]
    r = dot // 2
    offs = np.array([(dx, dy) for dy in range(-r, dot - r) for dx in range(-r, dot - r)])
    n = len(p)
    xs = (c[:, 0][:, None] + offs[:, 0]).ravel()
    ys = (c[:, 1][:, None] + offs[:, 1]).ravel()
    idx = np.repeat(np.arange(n), len(offs))
    inside = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
    pix, idx = ys[inside] * w + xs[inside], idx[inside]
    order = np.lexsort((idx, depth[idx], pix))  # per pixel: nearest first, then lower index
    pix, idx = pix[order], idx[order]
    first = np.unique(pix, return_index=True)[1]
    img.reshape(-1, 3)[pix[first]] = np.asarray(rgb, np.uint8).reshape(-1, 3)[idx[first]]
    return img


def png(img: np.ndarray) -> bytes:
    """PNG bytes without metadata: the same pixels give the same bytes."""
    out = io.BytesIO()
    Image.fromarray(img, "RGB").save(out, "PNG")
    return out.getvalue()


def data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


# ---------------------------------------------------------------- the report's pictures


@dataclass(frozen=True)
class Crop:
    """A part of the map around one marker, in map pixels (the card's SVG viewBox)."""

    src: str  # PNG data URI
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class MapSnapshot:
    src: str  # PNG data URI
    width: int
    height: int
    markers: dict[int, tuple[float, float]]  # finding id -> map pixel; rejected: none
    crops: dict[int, Crop]  # finding id -> its card's map location


@dataclass(frozen=True)
class Plate:
    """One evidence frame: its image and the finding's outline in it, in frame pixels."""

    frame_id: int
    src: str | None  # image data URI; None: the image file is missing
    width: int
    height: int
    time_s: float | None  # time in the video
    points: str  # SVG polygon points "u,v u,v ..."; "" without an outline


@dataclass(frozen=True)
class Visuals:
    map: MapSnapshot | None  # None: no exported map for this inspection
    plates: dict[int, tuple[Plate, ...]]  # finding id -> up to 3


def _manifest(run_dir: Path) -> dict[str, Any] | None:
    path = manifest_path(run_dir)
    return json.loads(path.read_text()) if path.is_file() else None


def map_view(man: dict[str, Any]) -> View:
    """The fixed camera of a map: from the side the registered cameras stand on (mean of their
    horizontal directions from the cloud's centre, the origin of a bundle), ELEVATION above it;
    cameras all around (mean shorter than 0.1) or none: from -y, as the top-down map is up."""
    dirs = []
    for f in man.get("frames", []):
        c = f.get("center")
        if f.get("registered") and c is not None and math.hypot(c[0], c[1]) > 0:
            dirs.append(np.array(c[:2], float) / math.hypot(c[0], c[1]))
    mean = np.mean(dirs, axis=0) if dirs else np.zeros(2)
    if float(np.hypot(*mean)) < 0.1:
        return View(-90.0, ELEVATION)
    return View(math.degrees(math.atan2(mean[1], mean[0])), ELEVATION)


def read_points(run_dir: Path, man: dict[str, Any], layer: str) -> tuple[np.ndarray, np.ndarray]:
    lay = man[layer]
    xyz = np.fromfile(run_dir / lay["position"], "<f4").reshape(-1, 3).astype(float)
    rgb = np.fromfile(run_dir / lay["color"], "u1").reshape(-1, 3)
    if len(rgb) != len(xyz):
        raise ValueError(f"{run_dir}: {layer} has {len(xyz)} positions, {len(rgb)} colours")
    return xyz, rgb


def map_snapshot(
    run_dir: Path, man: dict[str, Any], findings: tuple[FindingInput, ...],
    size: tuple[int, int] = MAP_SIZE,
) -> MapSnapshot:  # fmt: skip
    """The map of a run (its dense layer when it has one, as the viewer shows it) and the
    marker of every not rejected finding of the run: the centre of its points."""
    points, _ = read_points(run_dir, man, "points")  # findings index this layer
    xyz, rgb = read_points(run_dir, man, "dense" if "dense" in man else "points")
    ortho = frame_cloud(xyz, map_view(man), size)
    img = rasterize(xyz, rgb, ortho)
    markers: dict[int, tuple[float, float]] = {}
    crops: dict[int, Crop] = {}
    w, h = size
    cw, ch = min(CROP[0], w), min(CROP[1], h)
    for f in findings:
        if f.status == "rejected" or not f.points or max(f.points) >= len(points):
            continue
        mx, my = (float(v) for v in ortho.px(points[list(f.points)].mean(0))[0].round(1))
        markers[f.id] = (mx, my)
        x = int(min(max(round(mx - cw / 2), 0), w - cw))
        y = int(min(max(round(my - ch / 2), 0), h - ch))
        crops[f.id] = Crop(data_uri(png(img[y : y + ch, x : x + cw]), "image/png"), x, y, cw, ch)
    return MapSnapshot(data_uri(png(img), "image/png"), w, h, markers, crops)


def svg_points(outline: tuple[tuple[float, float], ...]) -> str:
    return " ".join(f"{u:g},{v:g}" for u, v in outline)


def plates(run_dir: Path, man: dict[str, Any], f: FindingInput) -> tuple[Plate, ...]:
    frames = {int(e["i"]): e for e in man.get("frames", [])}
    out = []
    for ev in f.evidence[:N_PLATES]:
        e = frames.get(ev.frame_id, {})
        image = run_dir / e["image"] if "image" in e else None
        src, size = None, (0, 0)
        if image is not None and image.is_file():
            data = image.read_bytes()
            with Image.open(io.BytesIO(data)) as im:
                size = im.size
            src = data_uri(data, mimetypes.guess_type(image.name)[0] or "image/jpeg")
        cam = e.get("camera") or {}
        width, height = int(cam.get("width", size[0])), int(cam.get("height", size[1]))
        out.append(Plate(ev.frame_id, src, width, height, e.get("t"), svg_points(ev.outline)))
    return tuple(out)


def load_visuals(inp: ReportInput, runs_dir: Path | None) -> Visuals:
    """The map of the inspection's last exported run (the run findings are marked on, as the
    API picks it) and the evidence plates of every finding. No runs dir or no manifest: no map,
    and plates without images (the report says so)."""
    root = runs_dir or Path()
    dirs = {*inp.run_dirs, *(f.run_dir for f in inp.findings)}
    mans = {d: _manifest(root / d) if runs_dir is not None else None for d in dirs}
    the_map = None
    exported = [d for d in inp.run_dirs if mans[d] is not None]
    if exported:
        d = exported[-1]
        mine = tuple(f for f in inp.findings if f.run_dir == d)
        the_map = map_snapshot(root / d, mans[d] or {}, mine)
    found = {f.id: plates(root / f.run_dir, mans[f.run_dir] or {}, f) for f in inp.findings}
    return Visuals(the_map, found)
