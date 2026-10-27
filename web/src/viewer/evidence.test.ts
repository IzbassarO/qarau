import { expect, it } from 'vitest';
import { createDemo } from '../fixtures/demo';
import fixture from '../test/fixtures/finding.json';
import type { Finding } from '../lib/findings';
import { withApiFindings } from './evidence';

it('preserves large finding IDs and replaces matching bundle markers with API data', () => {
  const bundle = createDemo('en').bundle;
  const finding = {
    ...fixture.finding,
    id: 70001,
    point_indices: [0, 2],
    evidence: [{ ...fixture.finding.evidence[0], frame_id: bundle.manifest.frames[0].i }],
  } as Finding;
  const result = withApiFindings(bundle, [finding]);
  expect(result.pointFinding?.[0]).toBe(70001);
  expect(result.pointFinding?.[2]).toBe(70001);
  expect(result.findings?.find((item) => item.id === 70001)).toMatchObject({
    id: 70001,
    class: 'corrosion',
    severity: 'critical',
    frames: [0],
    bbox: { min: [0, 0, 0], max: [0.1, 0.1, 0] },
  });
  expect(result.position).toBe(bundle.position);
  expect(bundle.pointFinding?.[0]).not.toBe(70001);
});
