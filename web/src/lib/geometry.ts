import { Matrix4, Quaternion, Vector3 } from 'three';

/** Rotation matrix (row-major 3x3) of a COLMAP quaternion [w, x, y, z]. */
export function quatToMat3([w, x, y, z]: number[]): number[] {
  return [
    1 - 2 * (y * y + z * z),
    2 * (x * y - z * w),
    2 * (x * z + y * w),
    2 * (x * y + z * w),
    1 - 2 * (x * x + z * z),
    2 * (y * z - x * w),
    2 * (x * z - y * w),
    2 * (y * z + x * w),
    1 - 2 * (x * x + y * y),
  ];
}

/**
 * COLMAP pose (cam_from_world: x_cam = R x_world + t) -> three.js camera pose.
 * COLMAP camera axes: +x right, +y down, +z forward; three.js: +x right, +y up, -z forward,
 * so R_three = Rᵀ · diag(1, -1, -1) and the centre is -Rᵀ t.
 */
export function colmapPoseToThree(qvec: number[], tvec: number[]) {
  const r = quatToMat3(qvec);
  const [tx, ty, tz] = tvec;
  const position = new Vector3(
    -(r[0] * tx + r[3] * ty + r[6] * tz),
    -(r[1] * tx + r[4] * ty + r[7] * tz),
    -(r[2] * tx + r[5] * ty + r[8] * tz),
  );
  // Rᵀ · diag(1,-1,-1): columns of Rᵀ are rows of R, columns 1 and 2 negated
  const m = new Matrix4().set(
    r[0],
    -r[3],
    -r[6],
    0,
    r[1],
    -r[4],
    -r[7],
    0,
    r[2],
    -r[5],
    -r[8],
    0,
    0,
    0,
    0,
    1,
  );
  const quaternion = new Quaternion().setFromRotationMatrix(m);
  const forward = new Vector3(0, 0, -1).applyQuaternion(quaternion);
  return { position, quaternion, forward };
}

/** Vertical field of view in degrees from the image height and focal length fy. */
export const verticalFovDeg = (height: number, fy: number) =>
  (2 * Math.atan(height / (2 * fy)) * 180) / Math.PI;

/** Points that belong to item `i` of a CSR structure (zero-copy view). */
export function csrSlice<T extends Uint16Array | Uint32Array>(
  offsets: Uint32Array,
  data: T,
  i: number,
): T {
  return data.subarray(offsets[i], offsets[i + 1]) as T;
}

/** cumulative[i] = number of points with first_seen <= i (65535 = never observed, never counted). */
export function cumulativeAssembled(firstSeen: Uint16Array, nFrames: number): Uint32Array {
  const c = new Uint32Array(nFrames);
  for (let p = 0; p < firstSeen.length; p++) if (firstSeen[p] < nFrames) c[firstSeen[p]]++;
  for (let i = 1; i < nFrames; i++) c[i] += c[i - 1];
  return c;
}

export const RAMP = ['#0E2A5C', '#2E6FD8', '#9CC3FF', '#EAF2FF'] as const;

/** CPU reference of the shader's height ramp; t in [0, 1] -> [r, g, b] 0..255. */
export function heightRamp(t: number): [number, number, number] {
  const s = Math.min(1, Math.max(0, t)) * (RAMP.length - 1);
  const k = Math.min(RAMP.length - 2, Math.floor(s));
  const f = s - k;
  const a = parseInt(RAMP[k].slice(1), 16);
  const b = parseInt(RAMP[k + 1].slice(1), 16);
  const ch = (sh: number) => Math.round(((a >> sh) & 255) * (1 - f) + ((b >> sh) & 255) * f);
  return [ch(16), ch(8), ch(0)];
}

/**
 * CPU picking: nearest point to pixel (mx, my) within `radius` px; prefers nearer depth among
 * candidates. viewProj is a column-major 4x4 (three.js Matrix4.elements). Returns -1 if none.
 * ponytail: O(N) loop per click (~15 ms per 1 M points); GPU picking or a tree if clouds grow.
 */
export function pickNearest(
  pos: Float32Array,
  viewProj: ArrayLike<number>,
  w: number,
  h: number,
  mx: number,
  my: number,
  radius = 8,
): number {
  const e = viewProj;
  let best = -1;
  let bestScore = Infinity;
  const r2 = radius * radius;
  for (let p = 0, n = pos.length / 3; p < n; p++) {
    const x = pos[p * 3],
      y = pos[p * 3 + 1],
      z = pos[p * 3 + 2];
    const cw = e[3] * x + e[7] * y + e[11] * z + e[15];
    if (cw <= 0) continue;
    const sx = (((e[0] * x + e[4] * y + e[8] * z + e[12]) / cw) * 0.5 + 0.5) * w;
    const sy = (0.5 - ((e[1] * x + e[5] * y + e[9] * z + e[13]) / cw) * 0.5) * h;
    const d2 = (sx - mx) ** 2 + (sy - my) ** 2;
    if (d2 > r2) continue;
    // pixel distance first, depth breaks near-ties
    const rank = Math.sqrt(d2) + depthPenalty(cw);
    if (rank < bestScore) {
      bestScore = rank;
      best = p;
    }
  }
  return best;
}

// nearer points win ties: penalise by log depth (about 1 px per 2x depth)
const depthPenalty = (cw: number) => Math.log2(Math.max(cw, 1e-6));

/**
 * Robust bounds: 2-98 percentile per axis of (a sample of) the points, widened to include the
 * camera centres. Manifest bounds are inflated by outliers, so framing never uses them.
 */
export function robustBounds(pos: Float32Array, centers: number[][], maxSample = 200000) {
  const n = pos.length / 3;
  const step = Math.max(1, Math.floor(n / maxSample));
  const min: number[] = [];
  const max: number[] = [];
  for (let k = 0; k < 3; k++) {
    const v: number[] = [];
    for (let p = 0; p < n; p += step) v.push(pos[p * 3 + k]);
    v.sort((a, b) => a - b);
    min.push(v[Math.floor(v.length * 0.02)] ?? 0);
    max.push(v[Math.min(v.length - 1, Math.floor(v.length * 0.98))] ?? 0);
  }
  for (const c of centers)
    for (let k = 0; k < 3; k++) {
      min[k] = Math.min(min[k], c[k]);
      max[k] = Math.max(max[k], c[k]);
    }
  const center = min.map((m, k) => (m + max[k]) / 2);
  const radius = Math.max(1e-6, Math.hypot(...min.map((m, k) => max[k] - m)) / 2);
  return { min, max, center, radius };
}

/** Median of a numeric array (mutates order). */
export function median(a: number[]): number {
  if (!a.length) return NaN;
  a.sort((x, y) => x - y);
  return a[a.length >> 1];
}
