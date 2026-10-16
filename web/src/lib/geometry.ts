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

export interface Bounds {
  min: number[];
  max: number[];
  center: number[];
  radius: number;
}

/** Point-only bounds at exact, linearly interpolated per-axis percentiles (0–100). */
export function robustBounds(
  pos: ArrayLike<number>,
  { lowPct = 2, highPct = 98 }: { lowPct?: number; highPct?: number } = {},
): Bounds {
  if (
    !Number.isFinite(lowPct) ||
    !Number.isFinite(highPct) ||
    lowPct < 0 ||
    highPct > 100 ||
    lowPct > highPct
  )
    throw new RangeError('Percentiles must satisfy 0 <= lowPct <= highPct <= 100');
  const n = Math.floor(pos.length / 3);
  const min: number[] = [];
  const max: number[] = [];
  for (let k = 0; k < 3; k++) {
    const values = new Float64Array(n);
    let count = 0;
    for (let p = 0; p < n; p++) {
      const at = p * 3;
      if (Number.isFinite(pos[at]) && Number.isFinite(pos[at + 1]) && Number.isFinite(pos[at + 2]))
        values[count++] = pos[at + k];
    }
    const sorted = values.subarray(0, count).sort();
    const percentile = (pct: number) => {
      if (!count) return 0;
      const index = ((count - 1) * pct) / 100;
      const lo = Math.floor(index);
      const hi = Math.ceil(index);
      return sorted[lo] + (sorted[hi] - sorted[lo]) * (index - lo);
    };
    min.push(percentile(lowPct));
    max.push(percentile(highPct));
  }
  const center = min.map((m, k) => (m + max[k]) / 2);
  const radius = Math.max(1e-6, Math.hypot(...min.map((m, k) => max[k] - m)) / 2);
  return { min, max, center, radius };
}

/**
 * Fit all eight box corners with a 10% margin, looking diagonally with world +Z up.
 * fovY is the vertical field of view in degrees, matching PerspectiveCamera.
 */
export function frameBounds(bounds: Pick<Bounds, 'min' | 'max'>, fovY: number, aspect: number) {
  if (!Number.isFinite(fovY) || fovY <= 0 || fovY >= 180 || !Number.isFinite(aspect) || aspect <= 0)
    throw new RangeError('Framing requires 0 < fovY < 180 and a positive aspect ratio');
  const target = bounds.min.map((v, k) => (v + bounds.max[k]) / 2);
  const direction = new Vector3(1, -1.3, 0.8).normalize();
  const right = new Vector3().crossVectors(new Vector3(0, 0, 1), direction).normalize();
  const up = new Vector3().crossVectors(direction, right);
  const tanY = Math.tan((fovY * Math.PI) / 360);
  const tanX = tanY * aspect;
  const diagonal = Math.hypot(...bounds.min.map((value, axis) => bounds.max[axis] - value));
  const minDepth = diagonal * 1e-6;
  // Only point-sized/empty boxes need an absolute fallback; small objects scale normally.
  let distance = diagonal > 0 ? minDepth : 1e-3;
  for (const x of [bounds.min[0], bounds.max[0]])
    for (const y of [bounds.min[1], bounds.max[1]])
      for (const z of [bounds.min[2], bounds.max[2]]) {
        const offset = new Vector3(x - target[0], y - target[1], z - target[2]);
        const depth = offset.dot(direction);
        distance = Math.max(
          distance,
          depth + minDepth,
          depth + (1.1 * Math.abs(offset.dot(right))) / tanX,
          depth + (1.1 * Math.abs(offset.dot(up))) / tanY,
        );
      }
  const position = direction
    .multiplyScalar(distance)
    .add(new Vector3(...target))
    .toArray();
  return { position, target };
}

/** Median of a numeric array (mutates order). */
export function median(a: number[]): number {
  if (!a.length) return NaN;
  a.sort((x, y) => x - y);
  return a[a.length >> 1];
}

export interface Focus {
  center: number[];
  radius: number;
  method: 'converge' | 'density' | 'manifest' | 'bounds';
}

export interface Ray {
  center: number[];
  forward: number[];
}

const det3 = (m: number[]) =>
  m[0] * (m[4] * m[8] - m[5] * m[7]) -
  m[1] * (m[3] * m[8] - m[5] * m[6]) +
  m[2] * (m[3] * m[7] - m[4] * m[6]);

/**
 * Least-squares point closest to all optical axes (trimmed twice against outlier cameras).
 * Returns null when the axes do not converge: near-parallel (forward motion, det ratio ~ 0) or
 * the point is behind most cameras.
 */
export function convergePoint(rays: Ray[]): number[] | null {
  let use = rays;
  let x: number[] | null = null;
  for (let it = 0; it < 3 && use.length >= 3; it++) {
    const A = new Array<number>(9).fill(0);
    const b = [0, 0, 0];
    for (const { center: o, forward: d } of use)
      for (let r = 0; r < 3; r++)
        for (let c = 0; c < 3; c++) {
          const a = (r === c ? 1 : 0) - d[r] * d[c];
          A[r * 3 + c] += a;
          b[r] += a * o[c];
        }
    const det = det3(A);
    const mean = (A[0] + A[4] + A[8]) / 3;
    if (it === 0 && det / mean ** 3 < 0.1) return null; // axes (nearly) parallel
    if (!(Math.abs(det) > 1e-12)) return null;
    const sol = [0, 1, 2].map((k) => {
      const m = A.slice();
      for (let r = 0; r < 3; r++) m[r * 3 + k] = b[r];
      return det3(m) / det;
    });
    x = sol;
    const dist = use.map(({ center: o, forward: d }) => {
      const v = [sol[0] - o[0], sol[1] - o[1], sol[2] - o[2]];
      const t = v[0] * d[0] + v[1] * d[1] + v[2] * d[2];
      return Math.hypot(v[0] - t * d[0], v[1] - t * d[1], v[2] - t * d[2]);
    });
    const cut = [...dist].sort((a, c) => a - c)[Math.floor(dist.length * 0.8)];
    use = use.filter((_, k) => dist[k] <= cut);
  }
  if (!x) return null;
  const c = x;
  const front = rays.filter(
    ({ center: o, forward: d }) =>
      (c[0] - o[0]) * d[0] + (c[1] - o[1]) * d[1] + (c[2] - o[2]) * d[2] > 0,
  ).length;
  return front >= rays.length * 0.7 ? c : null;
}

/**
 * Where the inspected object is: the converge point of the camera axes when they converge inside
 * the cloud (orbits, image sets around a building), else the voxel-density peak (points weighted
 * by how many frames saw them, so well-observed structure beats sparse terrain).
 * radius: 1.6 x the 30th percentile of point distances from the centre, clamped to the cloud.
 * ponytail: density ignores where the cameras look; add a look-at prior if a scene frames wrong.
 */
export function computeFocus(
  pos: Float32Array,
  rays: Ray[],
  bounds: { min: number[]; max: number[]; radius: number },
  pointOffsets?: Uint32Array,
  maxSample = 200000,
): Focus {
  const n = pos.length / 3;
  const step = Math.max(1, Math.floor(n / maxSample));
  const R = bounds.radius;
  const dists = (c: number[]) => {
    const d: number[] = [];
    for (let p = 0; p < n; p += step)
      d.push(Math.hypot(pos[p * 3] - c[0], pos[p * 3 + 1] - c[1], pos[p * 3 + 2] - c[2]));
    return d.sort((a, b) => a - b);
  };
  const radiusAt = (d: number[]) =>
    Math.min(R, Math.max(0.03 * R, 1.6 * (d[Math.floor(d.length * 0.3)] ?? R)));
  const conv = convergePoint(rays);
  if (conv) {
    const d = dists(conv);
    const inside = d.filter((v) => v < 0.2 * R).length;
    if (inside >= Math.max(30, 0.002 * d.length))
      return { center: conv, radius: radiusAt(d), method: 'converge' };
  }

  const cell = R / 24;
  const dim = [0, 1, 2].map((k) => Math.max(1, Math.ceil((bounds.max[k] - bounds.min[k]) / cell)));
  const grid = new Float32Array(dim[0] * dim[1] * dim[2]);
  const idx = (i: number, j: number, k: number) => (k * dim[1] + j) * dim[0] + i;
  for (let p = 0; p < n; p += step) {
    const c = [0, 1, 2].map((k) => Math.floor((pos[p * 3 + k] - bounds.min[k]) / cell));
    if (c.some((v, k) => v < 0 || v >= dim[k])) continue;
    grid[idx(c[0], c[1], c[2])] += pointOffsets ? pointOffsets[p + 1] - pointOffsets[p] : 1;
  }
  let best = -1;
  let at = [dim[0] >> 1, dim[1] >> 1, dim[2] >> 1];
  for (let k = 0; k < dim[2]; k++)
    for (let j = 0; j < dim[1]; j++)
      for (let i = 0; i < dim[0]; i++) {
        let s = 0; // 3x3x3 box sum
        for (let dk = -1; dk <= 1; dk++)
          for (let dj = -1; dj <= 1; dj++)
            for (let di = -1; di <= 1; di++) {
              const a = i + di,
                b = j + dj,
                c = k + dk;
              if (a >= 0 && b >= 0 && c >= 0 && a < dim[0] && b < dim[1] && c < dim[2])
                s += grid[idx(a, b, c)];
            }
        if (s > best) {
          best = s;
          at = [i, j, k];
        }
      }
  const center = at.map((v, k) => bounds.min[k] + (v + 0.5) * cell);
  return { center, radius: radiusAt(dists(center)), method: 'density' };
}
