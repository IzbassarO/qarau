import type { Key } from '../i18n';
import type { Finding } from '../lib/findings';

export function formatArea(
  finding: Pick<Finding, 'area_m2' | 'area_units' | 'scale'>,
  t: (key: Key) => string,
): string {
  if (finding.area_m2 !== null && finding.scale !== 'not set')
    return `${finding.area_m2.toFixed(1)} ${t('evidence.squareMetres')}`;
  return `${finding.area_units.toFixed(1)} ${t('evidence.unscaledUnits')}`;
}

/** Adapt API evidence metadata to the existing bundle renderer, keeping API IDs intact. */
export function withApiFindings(
  bundle: import('../lib/bundle').Bundle,
  findings: readonly Finding[],
): import('../lib/bundle').Bundle {
  if (!findings.length) return bundle;
  const pointFinding = bundle.pointFinding
    ? new Uint32Array(bundle.pointFinding)
    : new Uint32Array(bundle.manifest.points.count);
  const markers = findings.map((finding): import('../lib/bundle').Finding => {
    for (const index of finding.point_indices) {
      if (index >= 0 && index < pointFinding.length) pointFinding[index] = finding.id;
    }
    const min = [Infinity, Infinity, Infinity];
    const max = [-Infinity, -Infinity, -Infinity];
    for (const point of finding.points)
      for (let axis = 0; axis < 3; axis++) {
        min[axis] = Math.min(min[axis], point[axis]);
        max[axis] = Math.max(max[axis], point[axis]);
      }
    if (!finding.points.length) {
      min.fill(0);
      max.fill(0);
    }
    return {
      id: finding.id,
      class: finding.class,
      severity: finding.severity,
      status: finding.status,
      n_points: finding.point_indices.length,
      centroid: min.map((value, axis) => (value + max[axis]) / 2),
      bbox: { min, max },
      frames: finding.evidence
        .map((evidence) =>
          bundle.manifest.frames.findIndex((frame) => frame.i === evidence.frame_id),
        )
        .filter((index) => index >= 0)
        .sort((a, b) => a - b),
    };
  });
  const ids = new Set(findings.map((finding) => finding.id));
  return {
    ...bundle,
    pointFinding,
    findings: [...(bundle.findings ?? []).filter((finding) => !ids.has(finding.id)), ...markers],
  };
}
