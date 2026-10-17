import { Box3, Matrix4, Plane, Quaternion, Vector3 } from 'three';
import { PointCloudIndex, type NavigationState, type PointSource } from './navigation';

export type SurfacePoints = ArrayLike<number> | readonly Vector3[];

/** Exact finite-cloud proximity; the shared spatial index avoids scanning every frame. */
export function nearestPointDistance(points: PointSource, position: Vector3): number {
  if (points instanceof PointCloudIndex) return points.nearestDistance(position);
  const vectors = points.length > 0 && typeof points[0] !== 'number';
  const count = vectors ? points.length : Math.floor(points.length / 3);
  let nearestSq = Infinity;
  for (let i = 0; i < count; i++) {
    const candidate = vectors ? (points as readonly Vector3[])[i] : undefined;
    const x = (candidate?.x ?? (points as ArrayLike<number>)[i * 3]) - position.x;
    const y = (candidate?.y ?? (points as ArrayLike<number>)[i * 3 + 1]) - position.y;
    const z = (candidate?.z ?? (points as ArrayLike<number>)[i * 3 + 2]) - position.z;
    const distanceSq = x * x + y * y + z * z;
    if (Number.isFinite(distanceSq)) nearestSq = Math.min(nearestSq, distanceSq);
  }
  return Math.sqrt(nearestSq);
}

const WORLD_UP = new Vector3(0, 0, 1);
const COS_GROUND_TILT = Math.cos((25 * Math.PI) / 180);

function normalizedUp(up: Vector3) {
  return up.lengthSq() > 0 ? up.clone().normalize() : WORLD_UP.clone();
}

/** Bounded, evenly spaced sampling keeps initialization independent of cloud size. */
function samplePoints(points: SurfacePoints, limit: number): Vector3[] {
  const vectors = points.length > 0 && typeof points[0] !== 'number';
  const count = vectors ? points.length : Math.floor(points.length / 3);
  const stride = Math.max(1, Math.ceil(count / limit));
  const result: Vector3[] = [];
  for (let i = 0; i < count; i += stride) {
    const point = vectors
      ? (points as readonly Vector3[])[i].clone()
      : new Vector3(
          (points as ArrayLike<number>)[i * 3],
          (points as ArrayLike<number>)[i * 3 + 1],
          (points as ArrayLike<number>)[i * 3 + 2],
        );
    if (Number.isFinite(point.x) && Number.isFinite(point.y) && Number.isFinite(point.z))
      result.push(point);
  }
  return result;
}

function tangentBasis(up: Vector3): [Vector3, Vector3] {
  const reference = Math.abs(up.z) > 0.9 ? new Vector3(0, 1, 0) : new Vector3(0, 0, -1);
  const x = reference.clone().cross(up).normalize();
  return [x, up.clone().cross(x).normalize()];
}

/** Small symmetric 3×3 Jacobi eigensolver; returns the least-variance direction. */
function pcaNormal(points: readonly Vector3[], fallback: Vector3): Vector3 {
  if (points.length < 3) return fallback.clone();
  const center = new Vector3();
  for (const point of points) center.add(point);
  center.divideScalar(points.length);
  const a = Array.from({ length: 3 }, () => [0, 0, 0]);
  for (const point of points) {
    const d = point.clone().sub(center).toArray();
    for (let row = 0; row < 3; row++)
      for (let col = row; col < 3; col++) a[row][col] += d[row] * d[col];
  }
  a[1][0] = a[0][1];
  a[2][0] = a[0][2];
  a[2][1] = a[1][2];
  const eigenvectors = [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1],
  ];
  for (let iteration = 0; iteration < 24; iteration++) {
    let p = 0;
    let q = 1;
    for (const [row, col] of [
      [0, 2],
      [1, 2],
    ])
      if (Math.abs(a[row][col]) > Math.abs(a[p][q])) [p, q] = [row, col];
    if (Math.abs(a[p][q]) < Math.max(a[0][0] + a[1][1] + a[2][2], 1e-30) * 1e-12) break;
    const angle = 0.5 * Math.atan2(2 * a[p][q], a[q][q] - a[p][p]);
    const c = Math.cos(angle);
    const s = Math.sin(angle);
    const pp = a[p][p];
    const qq = a[q][q];
    const pq = a[p][q];
    a[p][p] = c * c * pp - 2 * s * c * pq + s * s * qq;
    a[q][q] = s * s * pp + 2 * s * c * pq + c * c * qq;
    a[p][q] = a[q][p] = 0;
    for (let row = 0; row < 3; row++) {
      if (row !== p && row !== q) {
        const rp = a[row][p];
        const rq = a[row][q];
        a[row][p] = a[p][row] = c * rp - s * rq;
        a[row][q] = a[q][row] = s * rp + c * rq;
      }
      const vp = eigenvectors[row][p];
      const vq = eigenvectors[row][q];
      eigenvectors[row][p] = c * vp - s * vq;
      eigenvectors[row][q] = s * vp + c * vq;
    }
  }
  const sorted = [0, 1, 2].sort((left, right) => a[left][left] - a[right][right]);
  // A line or coincident points has no unique surface normal.
  if (a[sorted[1]][sorted[1]] <= Math.max(a[sorted[2]][sorted[2]], 1e-30) * 1e-10)
    return fallback.clone();
  const index = sorted[0];
  return new Vector3(eigenvectors[0][index], eigenvectors[1][index], eigenvectors[2][index])
    .normalize()
    .multiplyScalar(
      eigenvectors[0][index] * fallback.x +
        eigenvectors[1][index] * fallback.y +
        eigenvectors[2][index] * fallback.z <
        0
        ? -1
        : 1,
    );
}

/** PCA over the actual k nearest finite cloud points, not a downsampled neighborhood. */
export function estimateSurfaceNormal(
  points: SurfacePoints,
  point: Vector3,
  k = 30,
  up = WORLD_UP,
): Vector3 {
  const nearest: { distance: number; point: Vector3 }[] = [];
  const vectors = points.length > 0 && typeof points[0] !== 'number';
  const count = vectors ? points.length : Math.floor(points.length / 3);
  const limit = Math.max(3, Math.round(k));
  for (let i = 0; i < count; i++) {
    const candidate = vectors
      ? (points as readonly Vector3[])[i]
      : new Vector3(
          (points as ArrayLike<number>)[i * 3],
          (points as ArrayLike<number>)[i * 3 + 1],
          (points as ArrayLike<number>)[i * 3 + 2],
        );
    const distance = candidate.distanceToSquared(point);
    if (
      !Number.isFinite(distance) ||
      (nearest.length === limit && distance >= nearest.at(-1)!.distance)
    )
      continue;
    let insert = nearest.findIndex((entry) => entry.distance > distance);
    if (insert < 0) insert = nearest.length;
    nearest.splice(insert, 0, { distance, point: candidate.clone() });
    if (nearest.length > limit) nearest.pop();
  }
  return pcaNormal(
    nearest.map((entry) => entry.point),
    normalizedUp(up),
  );
}

/**
 * Estimate outer terrain from the lowest 20% using deterministic seeded RANSAC.
 * Tangential footprint is primary: a dense indoor slab must not beat wider terrain.
 * TODO(P5a): replace assumed world-up and this inferred plane with gravity/terrain.
 */
export function estimateGround(points: SurfacePoints, up = WORLD_UP): Plane {
  const worldUp = normalizedUp(up);
  const sample = samplePoints(points, 12_000).sort((a, b) => a.dot(worldUp) - b.dot(worldUp));
  if (!sample.length) return new Plane(worldUp, 0);
  const fallback = new Plane(worldUp, -sample[Math.floor((sample.length - 1) * 0.02)].dot(worldUp));
  const low = sample.slice(0, Math.max(3, Math.ceil(sample.length * 0.2)));
  if (low.length < 3) return fallback;
  const bounds = new Box3().setFromPoints(sample);
  const tolerance = Math.max(bounds.getSize(new Vector3()).length() * 0.002, 1e-6);
  const [xAxis, yAxis] = tangentBasis(worldUp);
  let seed = 0x6a09e667;
  const randomIndex = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return Math.floor((seed / 0x1_0000_0000) * low.length);
  };
  let best: { plane: Plane; inliers: Vector3[]; area: number; height: number } | undefined;
  const consider = (plane: Plane) => {
    if (plane.normal.dot(worldUp) < 0) plane.negate();
    if (plane.normal.dot(worldUp) < COS_GROUND_TILT) return;
    const inliers: Vector3[] = [];
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    for (const point of low) {
      if (Math.abs(plane.distanceToPoint(point)) > tolerance) continue;
      inliers.push(point);
      const x = point.dot(xAxis);
      const y = point.dot(yAxis);
      minX = Math.min(minX, x);
      maxX = Math.max(maxX, x);
      minY = Math.min(minY, y);
      maxY = Math.max(maxY, y);
    }
    if (inliers.length < Math.max(3, low.length * 0.03)) return;
    const area = (maxX - minX) * (maxY - minY);
    const height = -plane.constant / plane.normal.dot(worldUp);
    if (
      !best ||
      area > best.area * 1.01 ||
      (area >= best.area * 0.99 &&
        (height < best.height - tolerance ||
          (Math.abs(height - best.height) <= tolerance && inliers.length > best.inliers.length)))
    )
      best = { plane, inliers, area, height };
  };
  // Include horizontal hypotheses so sparsely captured terrain is still considered.
  for (let i = 0; i < low.length; i += Math.max(1, Math.floor(low.length / 32)))
    consider(new Plane().setFromNormalAndCoplanarPoint(worldUp, low[i]));
  for (let iteration = 0; iteration < 160; iteration++) {
    const a = low[randomIndex()];
    const b = low[randomIndex()];
    const c = low[randomIndex()];
    const normal = b.clone().sub(a).cross(c.clone().sub(a));
    if (normal.lengthSq() < 1e-20) continue;
    consider(new Plane().setFromNormalAndCoplanarPoint(normal.normalize(), a));
  }
  if (!best || best.area <= tolerance * tolerance) return fallback;
  const normal = pcaNormal(best.inliers, worldUp);
  if (normal.dot(worldUp) < COS_GROUND_TILT) return best.plane.clone();
  const center = new Vector3();
  for (const point of best.inliers) center.add(point);
  return new Plane().setFromNormalAndCoplanarPoint(
    normal,
    center.divideScalar(best.inliers.length),
  );
}

export interface PositionConstraints {
  ground: Plane;
  box: Box3;
  margin: number;
  up?: Vector3;
  epsilon?: number;
}

export interface CameraConstraints extends PositionConstraints {
  minDist: number;
  maxDist: number;
}

function groundPlane(options: PositionConstraints) {
  const up = normalizedUp(options.up ?? WORLD_UP);
  const plane = options.ground.clone();
  if (plane.normal.lengthSq() === 0) plane.set(up, 0);
  else plane.normalize();
  if (plane.normal.dot(up) < 0) plane.negate();
  return plane;
}

/** Projection onto the expanded scene box intersected with the ground halfspace. */
export function clampPosition(position: Vector3, options: PositionConstraints): Vector3 {
  const plane = groundPlane(options);
  const epsilon = Math.max(0, options.epsilon ?? 1e-4);
  const box = options.box.clone().expandByScalar(Math.max(0, options.margin));
  const result = position.clone();
  if (!box.isEmpty()) box.clampPoint(result, result);
  if (plane.distanceToPoint(result) >= epsilon) return result;
  if (box.isEmpty())
    return result.addScaledVector(plane.normal, epsilon - plane.distanceToPoint(result));
  // clamp(position + lambda * normal) is monotonic along the plane normal.
  const furthest = new Vector3(
    plane.normal.x < 0 ? box.min.x : box.max.x,
    plane.normal.y < 0 ? box.min.y : box.max.y,
    plane.normal.z < 0 ? box.min.z : box.max.z,
  );
  if (plane.distanceToPoint(furthest) < epsilon) return furthest;
  let low = 0;
  let high = Math.max(epsilon - plane.distanceToPoint(result), 1e-6);
  const at = (distance: number) =>
    box.clampPoint(position.clone().addScaledVector(plane.normal, distance), new Vector3());
  while (plane.distanceToPoint(at(high)) < epsilon) high *= 2;
  for (let iteration = 0; iteration < 48; iteration++) {
    const middle = (low + high) / 2;
    if (plane.distanceToPoint(at(middle)) < epsilon) low = middle;
    else high = middle;
  }
  return at(high);
}

/**
 * Orbit keeps world-up and a level horizon, including upward views of tall surfaces.
 * Only recovery from an underground input prevents an upward, underside-facing view.
 * Box/ground are convex, so interpolation between safe points keeps distance clamps safe.
 * Scene-derived distance limits must fit within the expanded scene box.
 */
export function clampCamera(state: NavigationState, options: CameraConstraints): NavigationState {
  const up = normalizedUp(options.up ?? WORLD_UP);
  const recoveredUnderground = groundPlane(options).distanceToPoint(state.position) < 0;
  const pivot = clampPosition(state.pivot, { ...options, epsilon: 0 });
  const project = (point: Vector3) => {
    let safe = clampPosition(point, options);
    if (!recoveredUnderground) return safe;
    for (let iteration = 0; iteration < 12; iteration++) {
      const below = safe.clone().sub(pivot).dot(up);
      if (below >= -1e-10) return safe;
      safe = clampPosition(safe.addScaledVector(up, -below), options);
    }
    return pivot.clone();
  };
  let position = project(state.position);
  const anchor = project(pivot);
  const min = Math.max(1e-9, options.minDist);
  const max = Math.max(min, options.maxDist);
  const atDistance = (endpoint: Vector3, radius: number) => {
    const delta = endpoint.clone().sub(anchor);
    const offset = anchor.clone().sub(pivot);
    const a = delta.lengthSq();
    if (a < 1e-30) return anchor.clone();
    const b = 2 * offset.dot(delta);
    const c = offset.lengthSq() - radius * radius;
    const t = (-b + Math.sqrt(Math.max(0, b * b - 4 * a * c))) / (2 * a);
    return anchor.clone().addScaledVector(delta, Math.max(0, Math.min(1, t)));
  };
  let distance = position.distanceTo(pivot);
  if (distance < min) {
    const offset = position.clone().sub(pivot);
    const extended =
      state.position.distanceToSquared(state.pivot) > 1e-20 && offset.lengthSq() > 1e-20
        ? project(pivot.clone().addScaledVector(offset, min / offset.length()))
        : undefined;
    if (extended && extended.distanceTo(pivot) >= min - 1e-12) {
      // A close zoom should back out along its existing ray, without a heading jump.
      position = atDistance(extended, min);
    } else {
      const box = options.box.clone().expandByScalar(Math.max(0, options.margin));
      let farthest = position;
      let extent = distance;
      const candidates: Vector3[] = [];
      if (!box.isEmpty()) {
        for (const x of [box.min.x, box.max.x])
          for (const y of [box.min.y, box.max.y])
            for (const z of [box.min.z, box.max.z]) candidates.push(new Vector3(x, y, z));
      } else candidates.push(pivot.clone().addScaledVector(tangentBasis(up)[0], min));
      for (const candidate of candidates) {
        const safe = project(candidate);
        const radius = safe.distanceTo(pivot);
        if (radius > extent) {
          farthest = safe;
          extent = radius;
        }
      }
      position = extent >= min ? atDistance(farthest, min) : farthest.clone();
    }
    distance = position.distanceTo(pivot);
  }
  if (distance > max) position = atDistance(position, max);

  const forward = state.rotation
    ? new Vector3(0, 0, -1).applyQuaternion(state.rotation).normalize()
    : pivot.clone().sub(position).normalize();
  // Preserve off-centre pivots and valid upward looks. An invalid underground
  // pose must not retain the direction looking up through terrain after recovery.
  const upward = forward.dot(up);
  if (recoveredUnderground && upward > 0) forward.addScaledVector(up, -upward);
  if (forward.lengthSq() < 1e-12) forward.copy(pivot).sub(position);
  if (forward.lengthSq() < 1e-12) forward.copy(up).negate();
  forward.normalize();
  const screenUp = Math.abs(forward.dot(up)) > 1 - 1e-10 ? tangentBasis(up)[1] : up;
  const rotation = new Quaternion().setFromRotationMatrix(
    new Matrix4().lookAt(new Vector3(), forward, screenUp),
  );
  return {
    ...state,
    position,
    pivot,
    up,
    rotation,
    minDistance: min,
  };
}
