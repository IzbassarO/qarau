# 0003 — Viewer bundle: manifest.json + raw typed arrays

- Status: accepted
- Date: 2026-10-14

## Context

The viewer needs, per point: position, colour, the first frame that observed it, and the
set of frames that observed it; per frame: pose, intrinsics, timecode, and the set of
points it observed. Standard cloud formats (PLY, LAZ, Potree) carry geometry well but not
these cross-references, and parsing them in the browser costs time and code.

## Decision

The pipeline's `export` stage writes `<run>/web/manifest.json` plus little-endian typed
arrays with no header (`points.position.f32`, `points.first_seen.u16`,
`tracks.*_offsets.u32`, `tracks.*.u16/u32`). Frame ↔ point correspondence is stored as two
CSR index structures taken directly from COLMAP tracks. Poses are COLMAP `cam_from_world`
after a rigid alignment that makes +Z up; the applied transform is stored in the manifest.
Frame indices refer to the time-ordered `frames` array, never to COLMAP image ids.
Full field list: `docs/notes/m1-plan.md` §3.

`points.color` is optional for viewer consumers. When present it names an unsigned
8-bit RGB buffer with exactly `3 * points.count` bytes. A missing, unreadable or malformed
colour buffer disables real-colour display while preserving geometry and tracks. No RGB
is inferred or fabricated. Existing v1/v2 bundles with complete RGB remain compatible.

LAZ remains the exchange format for engineers and other tools; Potree octrees are added
only when point counts require LOD (ADR 0005).

## Consequences

- `fetch().arrayBuffer()` → `BufferAttribute`: zero parsing, a few hundred ms for millions
  of points.
- Any later stage (segmentation labels, coverage states) adds one more array and one
  manifest key without changing existing ones; `version` is bumped on breaking changes.
- The bundle is plain files, so it can live in a run directory, in object storage, or in a
  desktop app without conversion.

## Addendum 2026-10-16 (P3b): `far` layer

Manifest key `far = {count, position: "web/far.position.f32", color: "web/far.color.u8",
rule}`: points farther from the nearest registered camera than 2.5 × the median such distance
(ADR 0013). Same encoding and world frame as `points.position` / `points.color`; no tracks,
no first-seen. These points are never in the `points` arrays, the tracks, `world.bounds` or
`cloud.laz`. Additive key, so `version` is unchanged; viewers that do not know `far` ignore
it (the current viewer does; A5 renders it).

## Addendum 2026-10-17: `created_at`

Manifest key `created_at`: ISO 8601 UTC time the export stage wrote the bundle (e.g.
`"2026-10-16T09:30:00+00:00"`). `GET /api/runs` (and the Vite dev stand-in) returns it as the
run date; for runs exported before this key existed it falls back to the manifest file's
modification time. The control center shows runs as "run name · date". Additive key, `version`
unchanged.

## Addendum 2026-10-17 (P4b): `dense` layer, version 2

Export now writes `version: 2` (the viewer reads 1 and 2; projection already wrote 2).
Optional manifest key `dense`, present when export ran with `--dense`:
`{count, position: "web/dense.position.f32", color: "web/dense.color.u8", densifier,
frame_links_source, max_frames_per_point, frame_offsets: {path, dtype: "<u4", length},
frame_ids: {path, dtype: "<u4", length}, linked_points, dropped}`. Layout, cap and the two
link sources: ADR 0017. Every v1 key is unchanged; viewers that do not know `dense` ignore it.
