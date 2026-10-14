# 0009 — Segmentation and mask-to-3D projection

- Status: accepted (development); SAM 3.1 API wiring pending (hackathon 2026-10-26)
- Date: 2026-10-14

## Context

M2 puts defects on the map. The segmenter will change (local model now, SAM 3.1 via the
Meta Model API later, maybe on-prem later), so everything after it must not care which one ran.
Contracts for `segmentation/` and `findings/` are in `docs/notes/m2-plan.md` §2-3; the viewer
already reads them.

## Interface

`Segmenter.segment(image_rgb, prompts, frame) -> list[MaskResult(prompt, mask, confidence, bbox)]`
and `info() -> {name, version, licence, dev_only}`. The `segment` stage runs it over the
registered frames (all, or every Nth), writes one PNG mask per instance and
`observations.json` (which also stores `info()` and the prompts with class and severity).
`project` reads only that file; it never imports a model.

## Implementations

| name | what | licence | flag |
| --- | --- | --- | --- |
| `ManualMasks` | masks from files (tests, dataset masks, hand drawn) | n/a | product-safe |
| `GroundedSam2` | Grounding DINO tiny (text to box) + SAM 2.1 tiny (box to mask), transformers, `uv sync --group ml` | Apache-2.0 code and weights | `dev_only: true`, not a product path |
| `SamApiSegmenter` | SAM 3.1 via Meta Model API: request builder, response parser, injected transport | SAM License, commercial terms pending owner decision | default transport raises, no network |

`SamApiSegmenter` wire format is **assumed** (documented in its docstring): request
`{model, image{format,data}, prompts[{text}]}`, response `results[{prompt, instances[{score, box,
mask{png_base64 | rle}}]}]`. Only `build_request` / `parse_response` change when the real API is
known. The interface test runs `ManualMasks` and `SamApiSegmenter` (fake transport) through the
same assertions.

## Projection

- **Visibility.** A point is a candidate in frame f only if f is in its track (CSR
  `frame_points` of the bundle). The reconstruction triangulated it from a keypoint there, so it
  was seen and not occluded. No depth test; works for sparse and for dense bundles.
- Candidates are projected with the frame's camera (all models in the bundle) and tested against the
  mask (mask resized to camera resolution if needed).
- **Voting.** Per prompt, a point is labelled if a mask of that prompt hit it in at least one frame;
  `support` = hits / frames that saw it, reported per finding.
- **Clustering.** Labelled points are grouped by 26-connected occupied voxels (voxel = 1/150 of
  the 2-98 percentile cloud diagonal, `--voxel` to override). Clusters under 3 points are dropped
  (counted in the stage metrics).
- **Finding status.** `confirmed` needs observations in at least 2 frames (each with at least 2
  of the cluster's points) AND a maximum angle between the viewing rays of at least 15 degrees.
  Otherwise `low_evidence` (shown as such, never hidden): a single view cannot place a defect.
- **Measurement and scale.** Area = occupied cells of the cluster projected on its best-fit plane
  (flat-ish surfaces; curved clusters are under-measured, noted in code). With a known scale
  (`--scale`, metres per unit) the size is `m2`; without one it is `rel` (fraction of the squared
  cloud diagonal) and the UI must not print square metres. Scale source is recorded in
  `findings.json`.
- Outputs are idempotent. `export` writes a fresh manifest, so re-exporting drops the `findings`
  key (tested); `segment`/`project` must be run again.

## Results on Raketa (drone, rusty shipwreck, sparse, 128 registered frames, stride 4 = 32 frames)

- Grounded SAM 2, prompt "rust", `box_threshold` 0.3: 58 masks, but Grounding DINO also boxed the
  orange beach (scores 0.2-0.43; hull scores 0.5-0.7). One 18k-point "finding" mixed hull and
  beach. Not plausible.
- **Changed**: `--box-threshold 0.5` (new CLI flag, default stays 0.3). 30 masks, all on the hull.
  `project`: 13 findings, 1 confirmed (hull, 8397 points, 30 frames, 138 degree ray spread,
  support 0.93) and 12 low evidence (3-19 points each, seen from about 4 frames within 5 degrees,
  bits of hull in the sparse cloud). Area is `rel`: Raketa has no scale.
- Viewer check (Playwright, screenshots in `docs/notes/m2/`): orange points sit on the hull
  cluster, timeline ticks show the frames that see finding 1, list shows "confirmed" / "low
  evidence". The threshold is a property of this segmenter and data, not a universal setting.
- Limitations: tiny Grounding DINO is weak on thin or film-like corrosion; whole-hull masks mean
  "rust" here marks the corroded wreck, not individual patches. The sparse cloud has about 38k
  points, so small defects get few points; a dense run will improve this.
