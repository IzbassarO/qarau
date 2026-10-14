import { describe, expect, it } from 'vitest';
import { Matrix4, PerspectiveCamera, Vector3 } from 'three';
import {
  colmapPoseToThree,
  computeFocus,
  convergePoint,
  csrSlice,
  cumulativeAssembled,
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

it('robustBounds ignores outliers but includes camera centres', () => {
  const pos = new Float32Array(3 * 1000);
  for (let p = 0; p < 1000; p++) pos.set([p / 1000, p / 1000, p / 1000], p * 3);
  pos.set([500, 500, 500], 0);
  const b = robustBounds(pos, [[-3, 0, 0]]);
  expect(b.max[0]).toBeLessThan(1.01);
  expect(b.min[0]).toBe(-3);
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
