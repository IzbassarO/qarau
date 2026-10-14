# 0002 — Two modes (Quick Scan, Monitoring) and explicit coverage

- Status: accepted
- Date: 2026-10-13

## Context

Oil & gas customers inspect the same assets repeatedly and care most about defects that
grow. Construction, residential and one-off inspections need a fast answer from a single
video. Engineers distrust AI output they cannot verify, and a map with no red points can
falsely suggest "all clear" when areas were simply not filmed.

## Decision

1. One pipeline, two modes. **Quick Scan** (single video → map, findings, ranked list) is
   built first. **Monitoring** adds a baseline map per site and registers every later
   video to it to report change. A Quick Scan can be promoted to a site's baseline.
2. Every map region is in exactly one coverage state: `inspected_ok`, `finding`
   (`critical` / `watch`), or `not_seen`. Not-seen areas are drawn gray and listed in
   every report.
3. Every finding links to its evidence frames and masks; an engineer confirms or rejects
   it; decisions are append-only.

## Consequences

- More work in M3 (coverage, evidence panel) before the product looks "smart" — accepted:
  trust is the product.
- The decision log becomes training data for later fine-tuning.
