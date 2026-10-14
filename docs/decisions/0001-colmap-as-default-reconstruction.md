# 0001 — COLMAP is the default 3D reconstruction engine

- Status: accepted
- Date: 2026-10-13

## Context

We need camera poses and a point cloud from customer video. Two candidates:
COLMAP (classical structure-from-motion, BSD licence, mature, slow) and VGGT / VGGT-Ω
(Meta + Oxford feed-forward transformer, seconds per scene, more robust on hard footage).
VGGT weights are released under CC-BY-NC — non-commercial use only.

## Decision

COLMAP is the product path. VGGT is used only for research and comparison, behind the
same `Reconstructor` interface, until Meta grants a commercial licence or a commercially
licensed equivalent exists.

## Consequences

- Processing is slower (minutes, not seconds); acceptable because results are viewed
  after processing, not live.
- We can sell the product without licence risk.
- Switching later is a config change, not a rewrite.
