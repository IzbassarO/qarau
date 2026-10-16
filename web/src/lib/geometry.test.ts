import { describe, expect, it } from 'vitest';
import { Matrix4, PerspectiveCamera, Vector3 } from 'three';
import {
  colmapPoseToThree,
  computeFocus,
  convergePoint,
  csrSlice,
  cumulativeAssembled,
  frameBounds,
  heightRamp,
  pickNearest,
  robustBounds,
  verticalFovDeg,
} from './geometry';

const close = (a: Vector3, b: number[]) => {
  expect(a.x).toBeCloseTo(b[0], 6);
  expect(a.y).toBeCloseTo(b[1], 6);
  expect(a.z).toBeCloseTo(b[2], 6);
};

describe('colmapPoseToThree', () => {
  it('identity rotation, t = 0: camera at origin looking along world +Z, up = -Y', () => {
    const { position, forward, quaternion } = colmapPoseToThree([1, 0, 0, 0], [0, 0, 0]);
    close(position, [0, 0, 0]);
    close(forward, [0, 0, 1]);
    // COLMAP image-up is -y_cam, so three's camera +Y (up) is world -Y
    close(new Vector3(0, 1, 0).applyQuaternion(quaternion), [0, -1, 0]);
    close(new Vector3(1, 0, 0).applyQuaternion(quaternion), [1, 0, 0]);
  });

  it('centre = -Rᵀ t', () => {
    close(colmapPoseToThree([1, 0, 0, 0], [1, 2, 3]).position, [-1, -2, -3]);
    // 90 deg about world Z: R maps x->y, so Rᵀ maps y->x; C = -Rᵀ t with t = (0, 5, 0) is (-5, 0, 0)
    const s = Math.SQRT1_2;
    close(colmapPoseToThree([s, 0, 0, s], [0, 5, 0]).position, [-5, 0, 0]);
  });

  it('90 deg about world X: camera looks along +Y with +Z up', () => {
    const s = Math.SQRT1_2;
    const { forward, quaternion } = colmapPoseToThree([s, s, 0, 0], [0, 0, 0]);
    close(forward, [0, 1, 0]);
    close(new Vector3(0, 1, 0).applyQuaternion(quaternion), [0, 0, 1]);
  });

  it('keeps roll about the optical axis: image-up in world = -row 1 of R', () => {
    const a = (30 * Math.PI) / 180; // 30 deg roll about the optical axis (COLMAP +z)
    const { forward, quaternion } = colmapPoseToThree(
      [Math.cos(a / 2), 0, 0, Math.sin(a / 2)],
      [0, 0, 0],
    );
    close(forward, [0, 0, 1]);
    // R = Rz(a): row 1 = (sin a, cos a, 0); three's +Y (image up) is world -row1
    close(new Vector3(0, 1, 0).applyQuaternion(quaternion), [-Math.sin(a), -Math.cos(a), 0]);
    close(new Vector3(1, 0, 0).applyQuaternion(quaternion), [Math.cos(a), -Math.sin(a), 0]);
  });

  it('a world point in front of the camera projects inside the view', () => {
    const { position, quaternion } = colmapPoseToThree([1, 0, 0, 0], [0, 0, 0]);
    const cam = new PerspectiveCamera(50, 1, 0.1, 100);
    cam.position.copy(position);
    cam.quaternion.copy(quaternion);
    cam.updateMatrixWorld();
    const ndc = new Vector3(0, 0, 10).project(cam);
    expect(Math.abs(ndc.x)).toBeLessThan(1e-6);
    expect(ndc.z).toBeLessThan(1);
  });
});

it('vertical fov from fy', () => {
  expect(verticalFovDeg(720, 360)).toBeCloseTo(90, 6);
});

it('csrSlice returns the range of item i', () => {
  const off = new Uint32Array([0, 2, 2, 5]);
  const data = new Uint32Array([10, 11, 20, 21, 22]);
  expect(Array.from(csrSlice(off, data, 0))).toEqual([10, 11]);
  expect(Array.from(csrSlice(off, data, 1))).toEqual([]);
  expect(Array.from(csrSlice(off, data, 2))).toEqual([20, 21, 22]);
});

it('cumulative assembled counts first_seen <= i and ignores never-seen', () => {
  const fs = new Uint16Array([0, 0, 2, 2, 2, 65535]);
  expect(Array.from(cumulativeAssembled(fs, 4))).toEqual([2, 2, 5, 5]);
});

it('height ramp hits the stops and clamps', () => {
  expect(heightRamp(0)).toEqual([0x0e, 0x2a, 0x5c]);
  expect(heightRamp(1)).toEqual([0xea, 0xf2, 0xff]);
  expect(heightRamp(1 / 3)).toEqual([0x2e, 0x6f, 0xd8]);
  expect(heightRamp(-5)).toEqual(heightRamp(0));
  expect(heightRamp(9)).toEqual(heightRamp(1));
});

it('pickNearest picks the point under the cursor, nearer one on ties, none when far', () => {
  const cam = new PerspectiveCamera(60, 1, 0.1, 100);
  cam.position.set(0, 0, 10);
  cam.updateMatrixWorld();
  cam.updateProjectionMatrix();
  const vp = new Matrix4().multiplyMatrices(cam.projectionMatrix, cam.matrixWorldInverse);
  // p0 and p1 on the same ray (centre of screen), p1 nearer; p2 off to the side
  const pos = new Float32Array([0, 0, 0, 0, 0, 5, 3, 0, 0]);
  expect(pickNearest(pos, vp.elements, 400, 400, 200, 200)).toBe(1);
  expect(pickNearest(pos, vp.elements, 400, 400, 5, 5)).toBe(-1);
});

describe('robustBounds', () => {
  it('excludes a few far outliers on every axis without mutating the points', () => {
    const points = Array.from({ length: 101 }, (_, p) => [p, p * 2, -p]).flat();
    points.push(-10000, 50000, -90000, 20000, -60000, 70000);
    const pos = new Float32Array(points);
    const before = pos.slice();
    const b = robustBounds(pos);
    expect(b.min[0]).toBeCloseTo(1.04);
    expect(b.max[0]).toBeCloseTo(98.96);
    expect(b.min[1]).toBeCloseTo(2.08);
    expect(b.max[1]).toBeCloseTo(197.92);
    expect(b.min[2]).toBeCloseTo(-98.96);
    expect(b.max[2]).toBeCloseTo(-1.04);
    expect(b.center).toEqual([50, 100, -50]);
    expect(pos).toEqual(before);
  });

  it('uses exact configurable per-axis percentiles, including the full range', () => {
    const pos = new Float32Array(
      Array.from({ length: 101 }, (_, p) => [p, 200 + p * 2, p - 100]).flat(),
    );
    const b = robustBounds(pos, { lowPct: 25, highPct: 75 });
    expect(b.min).toEqual([25, 250, -75]);
    expect(b.max).toEqual([75, 350, -25]);
    expect(b.center).toEqual([50, 300, -50]);
    expect(b.radius).toBeCloseTo(Math.hypot(50, 100, 50) / 2);
    expect(robustBounds(pos, { lowPct: 0, highPct: 100 }).min).toEqual([0, 200, -100]);
    expect(robustBounds(pos, { lowPct: 0, highPct: 100 }).max).toEqual([100, 400, 0]);
  });

  it('handles empty and coincident points with a finite positive radius', () => {
    expect(robustBounds(new Float32Array())).toEqual({
      min: [0, 0, 0],
      max: [0, 0, 0],
      center: [0, 0, 0],
      radius: 1e-6,
    });
    expect(robustBounds(new Float32Array([4, -2, 9, 4, -2, 9]))).toEqual({
      min: [4, -2, 9],
      max: [4, -2, 9],
      center: [4, -2, 9],
      radius: 1e-6,
    });
  });

  it('ignores invalid vertices and rejects invalid percentile options', () => {
    const b = robustBounds(new Float32Array([1, 2, 3, NaN, 0, 0, 0, Infinity, 0]));
    expect(b.min).toEqual([1, 2, 3]);
    expect(b.max).toEqual([1, 2, 3]);
    for (const options of [
      { lowPct: -1 },
      { highPct: 101 },
      { lowPct: 80, highPct: 20 },
      { lowPct: NaN },
    ])
      expect(() => robustBounds(new Float32Array(), options)).toThrow(RangeError);
  });
});

describe('frameBounds', () => {
  it.each([
    { min: [-4, -2, 0], max: [4, 2, 20], aspect: 0.45 },
    { min: [10, -3, -2], max: [90, 3, 2], aspect: 3.2 },
    { min: [-2, -40, -1], max: [2, 40, 1], aspect: 1.6 },
    { min: [-1e-9, -1e-9, -1e-9], max: [1e-9, 1e-9, 1e-9], aspect: 1.6 },
  ])('centres and fits every corner of a box at aspect $aspect', ({ min, max, aspect }) => {
    const original = { min: min.slice(), max: max.slice() };
    const { position, target } = frameBounds({ min, max }, 55, aspect);
    expect(target).toEqual(min.map((v, k) => (v + max[k]) / 2));
    expect({ min, max }).toEqual(original);
    const diagonal = Math.hypot(...min.map((value, axis) => max[axis] - value));
    const camera = new PerspectiveCamera(55, aspect, diagonal * 1e-8, diagonal * 100);
    camera.up.set(0, 0, 1);
    camera.position.fromArray(position);
    camera.lookAt(new Vector3(...target));
    camera.updateMatrixWorld();
    let largestProjection = 0;
    for (const x of [min[0], max[0]])
      for (const y of [min[1], max[1]])
        for (const z of [min[2], max[2]]) {
          const corner = new Vector3(x, y, z);
          expect(corner.clone().applyMatrix4(camera.matrixWorldInverse).z).toBeLessThan(0);
          const projected = corner.project(camera);
          expect(Math.abs(projected.x)).toBeLessThanOrEqual(1 / 1.1 + 1e-6);
          expect(Math.abs(projected.y)).toBeLessThanOrEqual(1 / 1.1 + 1e-6);
          expect(Math.abs(projected.z)).toBeLessThan(1);
          largestProjection = Math.max(
            largestProjection,
            Math.abs(projected.x),
            Math.abs(projected.y),
          );
        }
    // The box fills the available space rather than merely fitting somewhere far away.
    expect(largestProjection).toBeCloseTo(1 / 1.1, 6);
  });

  it('gives empty and point-sized boxes a finite, distinct camera position', () => {
    for (const points of [new Float32Array(), new Float32Array([4, -2, 9])]) {
      const bounds = robustBounds(points);
      const { position, target } = frameBounds(bounds, 55, 1);
      expect(target).toEqual(bounds.center);
      expect(position.every(Number.isFinite)).toBe(true);
      expect(new Vector3(...position).distanceTo(new Vector3(...target))).toBeCloseTo(1e-3, 8);
    }
  });

  it('rejects invalid field of view and aspect ratio', () => {
    const bounds = { min: [0, 0, 0], max: [1, 1, 1] };
    for (const fovY of [0, -1, 180, NaN])
      expect(() => frameBounds(bounds, fovY, 1)).toThrow(RangeError);
    for (const aspect of [0, -1, Infinity])
      expect(() => frameBounds(bounds, 55, aspect)).toThrow(RangeError);
  });
});

// ---- initial view focus
function scene(n: number, tower: number[]) {
  // ground plane 100 x 100 plus a dense column at `tower` (x, y), 20 high
  const pos = new Float32Array(n * 3);
  let seed = 7;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  for (let p = 0; p < n; p++) {
    if (p % 2) pos.set([tower[0] + rnd() * 4 - 2, tower[1] + rnd() * 4 - 2, rnd() * 20], p * 3);
    else pos.set([(rnd() - 0.5) * 100, (rnd() - 0.5) * 100, 0], p * 3);
  }
  return pos;
}
const bnd = { min: [-50, -50, 0], max: [50, 50, 20], radius: Math.hypot(100, 100, 20) / 2 };

it('focus: orbit cameras converge on the tower', () => {
  const rays = Array.from({ length: 40 }, (_, k) => {
    const a = (k / 40) * 2 * Math.PI;
    const c = [20 + 30 * Math.cos(a), -10 + 30 * Math.sin(a), 8];
    const d = [20 - c[0], -10 - c[1], 10 - c[2]];
    const l = Math.hypot(...d);
    return { center: c, forward: d.map((v) => v / l) };
  });
  const f = computeFocus(scene(20000, [20, -10]), rays, bnd);
  expect(f.method).toBe('converge');
  expect(Math.hypot(f.center[0] - 20, f.center[1] + 10)).toBeLessThan(1);
  expect(f.radius).toBeLessThan(bnd.radius * 0.6);
});

it('focus: forward-moving cameras (parallel axes) fall back to the density peak', () => {
  const rays = Array.from({ length: 30 }, (_, k) => ({
    center: [-45 + k, -10, 2],
    forward: [1, 0, 0],
  }));
  expect(convergePoint(rays)).toBeNull();
  const f = computeFocus(scene(20000, [20, 15]), rays, bnd);
  expect(f.method).toBe('density');
  expect(Math.hypot(f.center[0] - 20, f.center[1] - 15)).toBeLessThan(5);
});
