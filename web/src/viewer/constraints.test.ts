import { describe, expect, it } from 'vitest';
import { Box3, Matrix4, Plane, Quaternion, Vector3 } from 'three';
import { clampCamera, clampPosition, estimateGround, estimateSurfaceNormal } from './constraints';
import { orbit, type NavigationState } from './navigation';

const v = (x = 0, y = 0, z = 0) => new Vector3(x, y, z);
const up = v(0, 0, 1);
const ground = new Plane(up.clone(), 0);
const box = new Box3(v(-10, -10, 0), v(10, 10, 20));
const limits = { ground, box, margin: 1, minDist: 0.2, maxDist: 15 };
const state = (overrides: Partial<NavigationState> = {}): NavigationState => ({
  position: v(5, -5, 5),
  pivot: v(),
  up: up.clone(),
  minDistance: 0.2,
  fov: 60,
  ...overrides,
});
const expectVector = (actual: Vector3, expected: Vector3, precision = 8) => {
  expect(actual.x).toBeCloseTo(expected.x, precision);
  expect(actual.y).toBeCloseTo(expected.y, precision);
  expect(actual.z).toBeCloseTo(expected.z, precision);
};
const rotationFor = (forward: Vector3, cameraUp = up) =>
  new Quaternion().setFromRotationMatrix(new Matrix4().lookAt(v(), forward, cameraUp));

describe('estimateGround', () => {
  it('selects wide outer ground over a denser raised building floor slab', () => {
    const points: Vector3[] = [];
    // Outer terrain remains visible around all sides of a much denser slab.
    for (let x = -12; x <= 12; x += 2) for (let y = -12; y <= 12; y += 2) points.push(v(x, y, 0));
    for (let x = -3; x <= 3; x += 0.15) for (let y = -3; y <= 3; y += 0.15) points.push(v(x, y, 3));
    points.push(v(-1, 1, -4)); // A lone reconstruction outlier is not terrain.
    const result = estimateGround(points, up);
    expect(result.normal.angleTo(up)).toBeLessThan((2 * Math.PI) / 180);
    expect(result.distanceToPoint(v())).toBeCloseTo(0, 8);
    expect(result.distanceToPoint(v(0, 0, 3))).toBeGreaterThan(2.9);
    expect(estimateGround(points, up).equals(result)).toBe(true);
  });

  it('fits a tilted outer plane using packed positions and a supplied up axis', () => {
    const points: number[] = [];
    for (let x = -10; x <= 10; x++)
      for (let z = -10; z <= 10; z++) points.push(x, 0.1 * x + 0.05 * z + 2, z);
    const result = estimateGround(new Float32Array(points), v(0, 1, 0));
    const expected = v(-0.1, 1, -0.05).normalize();
    expect(result.normal.angleTo(expected)).toBeLessThan((2 * Math.PI) / 180);
    expect(Math.abs(result.distanceToPoint(v(0, 2, 0)))).toBeLessThan(0.01);
  });

  it('falls back to the second-percentile height for a cloud without plane support', () => {
    const points = Array.from({ length: 100 }, (_, index) => v(0, 0, index));
    expect(estimateGround(points).constant).toBe(-1);
    expect(estimateGround(new Float32Array()).normal.equals(up)).toBe(true);
  });
});

describe('clampCamera', () => {
  it('keeps the camera above ground and inside the expanded box, and the target at ground or above', () => {
    const initial = state({ position: v(100, -100, -50), pivot: v(2, 2, -4) });
    const result = clampCamera(initial, limits);
    expect(result.position.z).toBeGreaterThanOrEqual(1e-4 - 1e-10);
    expect(result.pivot.z).toBeGreaterThanOrEqual(0);
    expect(box.clone().expandByScalar(1).containsPoint(result.position)).toBe(true);
    expectVector(initial.position, v(100, -100, -50));
    expectVector(initial.pivot, v(2, 2, -4));
    expectVector(box.min, v(-10, -10, 0));
    expectVector(ground.normal, up);
  });

  it.each([v(0, 0, 0), v(0.01, 0, 0.01), v(1_000, -1_000, 1_000)])(
    'keeps the distance inside zoom limits for %j',
    (position) => {
      const result = clampCamera(state({ position }), limits);
      const distance = result.position.distanceTo(result.pivot);
      expect(distance).toBeGreaterThanOrEqual(limits.minDist - 1e-9);
      expect(distance).toBeLessThanOrEqual(limits.maxDist + 1e-9);
      expect(ground.distanceToPoint(result.position)).toBeGreaterThanOrEqual(1e-4 - 1e-10);
    },
  );

  it('enforces a maximum distance even when the target lies exactly on the ground', () => {
    const result = clampCamera(state({ position: v(8, 8, 0), pivot: v() }), {
      ...limits,
      maxDist: 1,
    });
    expectVector(result.pivot, v());
    expect(result.position.distanceTo(result.pivot)).toBeCloseTo(1, 10);
    expect(ground.distanceToPoint(result.position)).toBeGreaterThanOrEqual(1e-4 - 1e-10);
  });

  it('keeps world-up and removes roll after arbitrary orbit inputs', () => {
    const rolled = rotationFor(v(-1, 1, -1)).multiply(
      new Quaternion().setFromAxisAngle(v(0, 0, 1), 1.4),
    );
    let current = state({ rotation: rolled, up: v(0.2, 0.5, 0.3).normalize() });
    for (const [dx, dy] of [
      [1.2, 4],
      [-8, 0.2],
      [0.8, -9],
      [100, 100],
    ]) {
      current = clampCamera(orbit(current, dx, dy), limits);
      expectVector(current.up, up);
      const right = v(1, 0, 0).applyQuaternion(current.rotation!);
      expect(right.dot(up)).toBeCloseTo(0, 8);
    }
    expect(rolled.equals(current.rotation!)).toBe(false);
  });

  it('prevents an under-ground camera from looking upward through the ground', () => {
    const result = clampCamera(
      state({ position: v(0, 0, -10), rotation: rotationFor(v(0.2, 0, 1).normalize()) }),
      limits,
    );
    expect(result.position.z).toBeGreaterThanOrEqual(1e-4 - 1e-10);
    expect(result.position.clone().sub(result.pivot).dot(up)).toBeGreaterThanOrEqual(0);
    const forward = v(0, 0, -1).applyQuaternion(result.rotation!);
    expect(forward.dot(up)).toBeLessThanOrEqual(1e-10);
  });

  it('preserves a downward forward direction when the orbit pivot is off-center', () => {
    const forward = v(-0.2, 0.4, -1).normalize();
    const initial = state({ pivot: v(2, 1, 0), rotation: rotationFor(forward) });
    const result = clampCamera(initial, limits);
    expectVector(v(0, 0, -1).applyQuaternion(result.rotation!), forward);
    expect(result.rotation).not.toBe(initial.rotation);
    expectVector(result.pivot, initial.pivot);
  });

  it('permits looking upward at a tall surface while safely above the ground', () => {
    const position = v(5, -5, 2);
    const pivot = v(0, 0, 10);
    const forward = pivot.clone().sub(position).normalize();
    const initial = state({ position, pivot, rotation: rotationFor(forward) });
    const result = clampCamera(initial, limits);
    expectVector(result.position, position);
    expectVector(result.pivot, pivot);
    expectVector(v(0, 0, -1).applyQuaternion(result.rotation!), forward);
    expect(forward.dot(up)).toBeGreaterThan(0);
    expect(v(1, 0, 0).applyQuaternion(result.rotation!).dot(up)).toBeCloseTo(0, 8);
  });

  it('places a collapsed view exactly at the minimum distance with camera ground clearance', () => {
    const result = clampCamera(state({ position: v(), pivot: v() }), limits);
    expect(result.position.distanceTo(result.pivot)).toBeCloseTo(limits.minDist, 10);
    expect(ground.distanceToPoint(result.position)).toBeGreaterThanOrEqual(1e-4 - 1e-10);
    expectVector(result.pivot, v());
  });

  it('preserves a close view ray when enforcing the minimum distance', () => {
    const position = v(0.01, 0, 0.01);
    const initial = state({ position, pivot: v() });
    const result = clampCamera(initial, limits);
    const expected = position.clone().normalize();
    expect(result.position.distanceTo(result.pivot)).toBeCloseTo(limits.minDist, 10);
    expect(result.position.clone().sub(result.pivot).normalize().angleTo(expected)).toBeLessThan(
      1e-7,
    );
    expectVector(result.position, expected.multiplyScalar(limits.minDist));
  });

  it('supports Y-up constraints and a tilted ground without escaping the box', () => {
    const worldUp = v(0, 1, 0);
    const tilted = new Plane(v(0.1, 1, 0.05).normalize(), -1);
    const bounds = new Box3(v(-5, -5, -5), v(5, 5, 5));
    const result = clampCamera(
      state({ position: v(-30, -20, 40), pivot: v(0, -3, 0), up: worldUp }),
      { ground: tilted, box: bounds, margin: 0, minDist: 0.1, maxDist: 5, up: worldUp },
    );
    expect(bounds.containsPoint(result.position)).toBe(true);
    expect(tilted.distanceToPoint(result.position)).toBeGreaterThanOrEqual(1e-4 - 1e-10);
    expect(tilted.distanceToPoint(result.pivot)).toBeGreaterThanOrEqual(-1e-10);
    expect(result.position.distanceTo(result.pivot)).toBeLessThanOrEqual(5 + 1e-10);
    expectVector(result.up, worldUp);
  });
});

describe('clampPosition', () => {
  it('projects onto tilted ground inside the box for fly mode without needing orientation', () => {
    const tilted = new Plane(v(0.2, 0, 1).normalize(), 0);
    const result = clampPosition(v(-20, 30, -40), { ...limits, ground: tilted });
    expect(box.clone().expandByScalar(1).containsPoint(result)).toBe(true);
    expect(tilted.distanceToPoint(result)).toBeGreaterThanOrEqual(1e-4 - 1e-10);
  });
});

describe('estimateSurfaceNormal', () => {
  it('finds a tilted surface normal within two degrees using the k nearest points', () => {
    const points: number[] = [];
    for (let x = -5; x <= 5; x++)
      for (let y = -5; y <= 5; y++) points.push(x, y, 0.3 * x - 0.2 * y + 4);
    // A distant wall must not influence the local normal.
    for (let y = -5; y <= 5; y++) for (let z = 0; z <= 10; z++) points.push(30, y, z);
    const actual = estimateSurfaceNormal(new Float32Array(points), v(0.1, 0.1, 4.01), 30);
    expect(actual.angleTo(v(-0.3, 0.2, 1).normalize())).toBeLessThan((2 * Math.PI) / 180);
  });

  it('returns a deterministic up normal for a degenerate or empty neighborhood', () => {
    expectVector(estimateSurfaceNormal([v(), v(1, 0, 0), v(2, 0, 0)], v()), up);
    expectVector(estimateSurfaceNormal([], v(), 30, v(0, 1, 0)), v(0, 1, 0));
  });
});
