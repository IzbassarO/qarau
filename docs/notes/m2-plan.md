# M2 prep — plan and contracts

Task: `docs/tasks/M2.md`. This file fixes the contracts the parallel parts share, so they
can be built at the same time. Change a contract only through the coordinator.

## 1. Split

| Part | Owner | Scope | Writes |
| --- | --- | --- | --- |
| R | agent | reconstruction eval (loop detection / overlap / global mapper) + default + ADR 0007; PostGIS test + CI | `pipeline/qarau_pipeline/reconstruction.py`, `backend/tests/`, `.github/workflows/ci.yml`, `infra/postgres/` |
| D | agent | densification research + `Densifier` + dense export + ADR 0008 | `pipeline/qarau_pipeline/dense.py`, `export.py` (dense points + `points.source`) |
| S | agent | `Segmenter` + 3 implementations, projection, voting, clustering, measurement, findings files, ADR 0009 | `pipeline/qarau_pipeline/segmentation.py`, `projection.py` |
| V | agent | roll, initial framing, bundle v2 reader, findings UI, e2e | `web/` |
| T | agent | test data search + download + `docs/notes/datasets.md` | `data/samples/`, `docs/notes/datasets.md` |

Everyone: `pipeline/qarau_pipeline/cli.py` gets one new subcommand per part (`dense`,
`segment`, `project`); keep edits to other parts' sections out. Python ML dependencies go
into the `ml` dependency group of `pipeline/pyproject.toml` (not default deps, so CI and
`uv sync` stay light); tests that need them are marked `ml` and skipped when missing.

Data: the M1 runs in `data/runs/{south-building,notre-dame,aegviidu,orono}` are read-only
for agents. New outputs go to new run ids (`data/runs/<run>-dense`, `data/runs/<name>`)
or under `data/eval/`. To derive a run from an existing one, clone with `cp -cR` (APFS
copy-on-write); never hard-link files you will rewrite.

## 2. Viewer bundle v2 (extends ADR 0003, `m1-plan.md` §3)

`manifest.version = 2`. All v1 keys keep their meaning. Additions, all optional:

```jsonc
{
  "version": 2,
  "points": {
    // ... v1 keys ...
    "source": "web/points.source.u8"   // 0 = sparse COLMAP point, 1 = dense; absent = all sparse
  },
  "world": {
    // ... v1 keys ...
    "focus": {"center": [x,y,z], "radius": r, "method": "converge" | "density"}
    // optional hint for the initial view; the viewer computes its own when absent
  },
  "dense": {"method": "...", "frames_used": 120, "points": 1234567},   // present when densified
  "findings": "findings/findings.json"   // present after the projection stage
}
```

Tracks for dense points: `tracks.*` CSR covers **all** points. For a dense point, the
frames in its track are the frames where it passed the visibility test (inside the image,
in front of the camera, depth-consistent with that frame's depth map within tolerance).
`first_seen`, `num_obs`, `frame.n_points` follow from the CSR exactly as in v1. Keep total
CSR size bounded (cap or stride the frames tested per point; record the rule in ADR 0008).

Re-running `export` rewrites the manifest without `findings` (point indices change, so
findings must be re-projected).

### findings/findings.json (written by the `project` stage)

```jsonc
{
  "version": 1,
  "segmenter": {"name": "manual" | "<local model>" | "sam3.1-api", "version": "...",
                "licence": "...", "dev_only": true},
  "prompts": [{"text": "rust", "class": "corrosion", "severity": "watch"}],
  "scale": {"source": "none" | "gps" | "reference", "m_per_unit": null},
  "point_finding": "findings/points.finding.u16",   // per point: 0 = none, k = finding id k
  "params": {"min_frames": 2, "min_angle_deg": 15, "...": "..."},
  "findings": [
    {
      "id": 1,                        // 1-based, == value in points.finding
      "class": "corrosion", "prompt": "rust",
      "severity": "watch",            // from the prompt until risk ranking (M3)
      "status": "confirmed",          // "confirmed" | "low_evidence"
      "confidence": 0.81,             // mean mask confidence over observations
      "n_points": 412,
      "centroid": [x, y, z], "bbox": {"min": [x, y, z], "max": [x, y, z]},
      "size": {"value": 0.42, "unit": "m2"} ,   // or {"value": 0.013, "unit": "rel"}
                                              // rel = area / (cloud bbox diagonal)²
      "frames": [12, 13, 40],         // frame indices where a mask hit this finding, ascending
      "max_angle_deg": 34.0,          // largest angle between viewing rays over those frames
      "observations": [
        {"frame": 12, "mask": "segmentation/masks/000012_00.png", "confidence": 0.84,
         "bbox": [x0, y0, x1, y1], "n_points": 120}
      ]
    }
  ]
}
```

### segmentation/ (written by the `segment` stage)

`segmentation/masks/<frame:06d>_<k:02d>.png` (8-bit, 255 = mask, frame resolution) and
`segmentation/observations.json`:
`{"segmenter": {...}, "prompts": [...], "observations": [{"frame", "prompt", "mask",
"confidence", "bbox"}]}`. `ManualMasks` reads the same layout from any directory, so a
hand-drawn or dataset mask set is a valid "segmenter output".

## 3. Interfaces (pipeline/qarau_pipeline/interfaces.py)

```python
@dataclass
class MaskResult:
    prompt: str
    mask: np.ndarray       # H×W bool, frame resolution
    confidence: float      # 0..1
    bbox: tuple[int, int, int, int]

class Segmenter(Protocol):
    name: str
    def info(self) -> dict[str, Any]: ...        # name, version, licence, dev_only
    def segment(self, image: np.ndarray, prompts: list[str], frame: int) -> list[MaskResult]: ...

class Densifier(Protocol):
    def densify(self, run_dir: Path, out_dir: Path) -> dict[str, Any]: ...   # metrics
```

## 4. Order and checkpoints

1. Coordinator: this plan, `M2.md`, `ml` dependency group → commit to `feat/m2-prep`.
2. R, D, S, V, T start in parallel from that commit (worktrees start at `main`: reset onto
   `feat/m2-prep` first).
3. Coordinator reviews and merges each part as it lands: T and R first, then D, S, V.
4. Integration: the defect sample → reconstruction → dense → segment (local) → project →
   viewer; screenshots; PR.
