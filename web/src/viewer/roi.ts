import { Camera, Plane, Ray, Vector3 } from 'three';

export type UpAxis = 'x' | 'y' | 'z';
export type Vec3 = [number, number, number];

export interface RoiBox {
  min: Vec3;
  max: Vec3;
}

/** Versioned, server-persisted shape shared with the reconstruction pipeline. */
export interface Roi extends RoiBox {
  version: 1;
  site_id: string;
  source_run: string;
  frame: string;
  up_axis: UpAxis;
  created_at: string;
  created_by: string;
}

export interface ScreenPoint {
  x: number;
  y: number;
}

export interface Viewport {
  width: number;
  height: number;
}

export type ParseResult = { ok: true; value: Roi } | { ok: false; error: string };

const axisIndex = (axis: UpAxis): number => (axis === 'x' ? 0 : axis === 'y' ? 1 : 2);
const isVec3 = (value: unknown): value is Vec3 =>
  Array.isArray(value) &&
  value.length === 3 &&
  value.every((coordinate) => typeof coordinate === 'number' && Number.isFinite(coordinate));

function validBox(box: RoiBox): boolean {
  return isVec3(box.min) && isVec3(box.max) && box.min.every((min, i) => min <= box.max[i]);
}

/**
 * Unproject all four corners onto the plane whose up-axis coordinate is ground.
 * Input pixels are relative to the CSS viewport, independent of devicePixelRatio.
 * The caller supplies current camera matrices; this function mutates no inputs.
 */
export function screenRectToBox(
  camera: Camera,
  viewport: Viewport,
  start: ScreenPoint,
  end: ScreenPoint,
  upAxis: UpAxis = 'z',
  ground = 0,
): RoiBox | null {
  if (
    ![viewport.width, viewport.height, start.x, start.y, end.x, end.y, ground].every(
      Number.isFinite,
    ) ||
    viewport.width <= 0 ||
    viewport.height <= 0 ||
    start.x === end.x ||
    start.y === end.y
  )
    return null;

  const up = axisIndex(upAxis);
  const normal = new Vector3().setComponent(up, 1);
  const plane = new Plane(normal, -ground);
  const min: Vec3 = [Infinity, Infinity, Infinity];
  const max: Vec3 = [-Infinity, -Infinity, -Infinity];
  for (const [x, y] of [
    [start.x, start.y],
    [end.x, start.y],
    [end.x, end.y],
    [start.x, end.y],
  ]) {
    const nx = (x / viewport.width) * 2 - 1;
    const ny = 1 - (y / viewport.height) * 2;
    const near = new Vector3(nx, ny, -1).unproject(camera);
    const far = new Vector3(nx, ny, 1).unproject(camera);
    const direction = far.sub(near).normalize();
    // A ray lying in the plane has no unique projection either.
    if (Math.abs(normal.dot(direction)) < 1e-12) return null;
    const point = new Ray(near, direction).intersectPlane(plane, new Vector3());
    if (!point || ![point.x, point.y, point.z].every(Number.isFinite)) return null;
    for (let axis = 0; axis < 3; axis++) {
      min[axis] = Math.min(min[axis], point.getComponent(axis));
      max[axis] = Math.max(max[axis], point.getComponent(axis));
    }
  }
  // Remove insignificant floating-point noise along the ground plane.
  min[up] = max[up] = ground;
  if (min.some((value, axis) => axis !== up && value === max[axis])) return null;
  return { min, max };
}

/**
 * Preserve the ground rectangle; include every finite point inside its footprint.
 * Add 5% of the selected height at each end of the up axis. Flat clouds stay flat.
 * No points, an invalid rectangle or an incomplete XYZ buffer returns null.
 */
export function boxFromPointsInRect(
  positions: ArrayLike<number>,
  rect: RoiBox,
  upAxis: UpAxis = 'z',
  margin = 0.05,
): RoiBox | null {
  const up = axisIndex(upAxis);
  const horizontal = [0, 1, 2].filter((axis) => axis !== up);
  if (
    !validBox(rect) ||
    horizontal.some((axis) => rect.min[axis] === rect.max[axis]) ||
    positions.length % 3 !== 0 ||
    !Number.isFinite(margin) ||
    margin < 0
  )
    return null;

  let low = Infinity;
  let high = -Infinity;
  for (let i = 0; i < positions.length; i += 3) {
    if (
      !Number.isFinite(positions[i]) ||
      !Number.isFinite(positions[i + 1]) ||
      !Number.isFinite(positions[i + 2]) ||
      horizontal.some(
        (axis) => positions[i + axis] < rect.min[axis] || positions[i + axis] > rect.max[axis],
      )
    )
      continue;
    low = Math.min(low, positions[i + up]);
    high = Math.max(high, positions[i + up]);
  }
  if (!Number.isFinite(low) || !Number.isFinite(high)) return null;
  const padding = (high - low) * margin;
  const min: Vec3 = [...rect.min];
  const max: Vec3 = [...rect.max];
  min[up] = low - padding;
  max[up] = high + padding;
  return validBox({ min, max }) ? { min, max } : null;
}

/** Faces, edges and corners belong to the box. Invalid coordinates never do. */
export function containsPoint(box: RoiBox, point: ArrayLike<number>): boolean {
  return (
    point.length >= 3 &&
    [0, 1, 2].every(
      (axis) =>
        Number.isFinite(point[axis]) &&
        point[axis] >= box.min[axis] &&
        point[axis] <= box.max[axis],
    )
  );
}

/** One 0/1 entry per complete XYZ point; the original position buffer is untouched. */
export function filterMask(positions: ArrayLike<number>, box: RoiBox): Uint8Array {
  const mask = new Uint8Array(Math.floor(positions.length / 3));
  for (let p = 0; p < mask.length; p++) {
    const i = p * 3;
    const x = positions[i],
      y = positions[i + 1],
      z = positions[i + 2];
    mask[p] =
      Number.isFinite(x) &&
      Number.isFinite(y) &&
      Number.isFinite(z) &&
      x >= box.min[0] &&
      x <= box.max[0] &&
      y >= box.min[1] &&
      y <= box.max[1] &&
      z >= box.min[2] &&
      z <= box.max[2]
        ? 1
        : 0;
  }
  return mask;
}

const fields = [
  'version',
  'site_id',
  'source_run',
  'frame',
  'up_axis',
  'min',
  'max',
  'created_at',
  'created_by',
] as const;

function validTimestamp(value: string): boolean {
  const match =
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!match || !Number.isFinite(Date.parse(value))) return false;
  const [year, month, day, hour, minute, second] = match.slice(1).map(Number);
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return (
    month >= 1 &&
    month <= 12 &&
    day >= 1 &&
    day <= days[month - 1] &&
    hour <= 23 &&
    minute <= 59 &&
    second <= 59
  );
}

/** Parse either JSON text or a decoded response, reporting every malformed shape. */
export function parse(input: unknown): ParseResult {
  let decoded: unknown = input;
  if (typeof input === 'string') {
    try {
      decoded = JSON.parse(input);
    } catch {
      return { ok: false, error: 'Region must be valid JSON.' };
    }
  }
  if (!decoded || typeof decoded !== 'object' || Array.isArray(decoded))
    return { ok: false, error: 'Region must be an object.' };
  const value = decoded as Record<string, unknown>;
  if (fields.some((field) => !Object.hasOwn(value, field)))
    return { ok: false, error: 'Region is missing required fields.' };
  if (value.version !== 1) return { ok: false, error: 'Region version must be 1.' };
  for (const field of ['site_id', 'source_run', 'frame', 'created_at', 'created_by'] as const)
    if (typeof value[field] !== 'string' || !value[field].trim())
      return { ok: false, error: `Region ${field} must be a non-empty string.` };
  const site = value.site_id as string;
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(site) || site.includes('..'))
    return { ok: false, error: 'Region site_id is not a safe site identifier.' };
  if (value.up_axis !== 'x' && value.up_axis !== 'y' && value.up_axis !== 'z')
    return { ok: false, error: 'Region up_axis must be x, y or z.' };
  if (!isVec3(value.min) || !isVec3(value.max))
    return { ok: false, error: 'Region min and max must each contain three finite numbers.' };
  if (!validBox({ min: value.min, max: value.max }))
    return { ok: false, error: 'Region min must not exceed max.' };
  const createdAt = value.created_at as string;
  if (!validTimestamp(createdAt))
    return { ok: false, error: 'Region created_at must be an ISO 8601 timestamp.' };
  return {
    ok: true,
    value: {
      version: 1,
      site_id: site,
      source_run: value.source_run as string,
      frame: value.frame as string,
      up_axis: value.up_axis,
      min: [...value.min],
      max: [...value.max],
      created_at: createdAt,
      created_by: value.created_by as string,
    },
  };
}

/** Validate before JSON.stringify can turn NaN or Infinity into null. */
export function serialize(roi: Roi): string {
  const result = parse(roi);
  if (!result.ok) throw new Error(result.error);
  return JSON.stringify(result.value);
}
