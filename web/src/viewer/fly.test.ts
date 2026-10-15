import { describe, expect, it } from 'vitest';
import { Box3, Vector3 } from 'three';
import { clampToBounds } from './navigation';
import {
  applyMouseLook,
  createFlyState,
  flyDirection,
  flyOrientation,
  step,
  type FlyOptions,
  type FlyState,
} from './fly';

const v = (x = 0, y = 0, z = 0) => new Vector3(x, y, z);
const expectVector = (actual: Vector3, expected: Vector3, precision = 9) => {
  expect(actual.x).toBeCloseTo(expected.x, precision);
  expect(actual.y).toBeCloseTo(expected.y, precision);
  expect(actual.z).toBeCloseTo(expected.z, precision);
};
const state = (overrides: Partial<FlyState> = {}): FlyState => ({
  position: v(),
  yaw: 0,
  pitch: 0,
  velocity: v(),
  ...overrides,
});
const options: FlyOptions = { baseSpeed: 10, boostMultiplier: 4, damping: 8 };

describe('fly mouse look', () => {
  it('clamps pitch to ±89° and turns right/down with positive mouse deltas', () => {
    const initial = state();
    const rightDown = applyMouseLook(initial, 100, 100, 0.002);
    expect(rightDown.yaw).toBeCloseTo(-0.2);
    expect(rightDown.pitch).toBeCloseTo(-0.2);
    expect(flyDirection(rightDown).x).toBeGreaterThan(0);
    for (const dy of [-1e6, 1e6]) {
      const looked = applyMouseLook(initial, 0, dy, 0.002);
      expect(Math.abs(looked.pitch)).toBeCloseTo((89 * Math.PI) / 180, 12);
      expect(Math.abs(flyDirection(looked).z)).toBeLessThan(1);
    }
    expect(initial.pitch).toBe(0);
    expect(initial.yaw).toBe(0);
  });

  it('adopts the current direction and produces its equivalent Three camera rotation', () => {
    const position = v(5, -7, 2);
    const direction = v(1, -2, 0.8).normalize();
    const initial = createFlyState(position, direction);
    expectVector(initial.position, position);
    expectVector(flyDirection(initial), direction);
    expectVector(v(0, 0, -1).applyQuaternion(flyOrientation(initial)), direction);
    expectVector(initial.velocity, v());
    initial.position.set(0, 0, 0);
    expectVector(position, v(5, -7, 2));
  });

  it.each([-1, 1])('adopts the exact %s Z pole without tilting before mouse look', (sign) => {
    const initial = createFlyState(v(), v(0, 0, sign), v(1, 0, 0));
    expect(initial.pitch).toBeCloseTo((sign * Math.PI) / 2, 12);
    expectVector(flyDirection(initial), v(0, 0, sign));
    expectVector(
      step(initial, { forward: true }, 0.5, options).position.clone().normalize(),
      v(0, 0, sign),
    );
    expect(Math.abs(applyMouseLook(initial, 10, 0, 0.002).pitch)).toBeCloseTo(
      (89 * Math.PI) / 180,
      12,
    );
  });

  it.each([-1, 1])('uses screen-right for a stable heading near the %s Z pole', (sign) => {
    const right = v(1, 0, 0);
    const initial = createFlyState(v(), v(-3e-6, 8e-6, sign), right);
    expect(initial.yaw).toBe(0);
    const looked = applyMouseLook(initial, 10, 0, 0.002);
    expect(looked.yaw).toBeCloseTo(-0.02);
    expect(
      new Vector3(1, 0, 0).applyQuaternion(flyOrientation(looked)).distanceTo(right),
    ).toBeLessThan(0.021);
    expectVector(right, v(1, 0, 0));
  });
});

describe('fly motion', () => {
  it('maps WASD relative to yaw in the Z-up world', () => {
    const initial = state({ yaw: -Math.PI / 2 }); // Facing +X.
    const cases = [
      [{ forward: true }, v(1, 0, 0)],
      [{ backward: true }, v(-1, 0, 0)],
      [{ right: true }, v(0, -1, 0)],
      [{ left: true }, v(0, 1, 0)],
    ] as const;
    for (const [keys, expected] of cases) {
      const moved = step(initial, keys, 0.5, options);
      expectVector(moved.position.clone().normalize(), expected);
      expectVector(moved.velocity.clone().normalize(), expected);
    }
    expectVector(initial.position, v());
    expectVector(initial.velocity, v());
  });

  it('flies forward through pitch, while strafe stays horizontal', () => {
    const initial = state({ pitch: Math.PI / 4 });
    const forward = step(initial, { forward: true }, 0.2, options);
    expect(forward.position.z).toBeGreaterThan(0);
    expectVector(forward.position.clone().normalize(), v(0, 1, 1).normalize());
    expect(step(initial, { right: true }, 0.2, options).position.z).toBe(0);
  });

  it('boosts with Shift and normalizes diagonal motion', () => {
    const forward = step(state(), { forward: true }, 0.5, options);
    const boosted = step(state(), { forward: true, boost: true }, 0.5, options);
    const diagonal = step(state(), { forward: true, right: true }, 0.5, options);
    expect(boosted.position.length()).toBeCloseTo(forward.position.length() * 4, 10);
    expect(boosted.velocity.length()).toBeCloseTo(forward.velocity.length() * 4, 10);
    expect(diagonal.position.length()).toBeCloseTo(forward.position.length(), 10);
    expectVector(diagonal.position.clone().normalize(), v(1, 1, 0).normalize());
    expectVector(step(state(), { forward: true, backward: true }, 0.5, options).position, v());
  });

  it('damps released velocity exponentially to rest without a position jump', () => {
    let current = state({ velocity: v(4, -3, 2) });
    const initialVelocity = current.velocity.clone();
    current = step(current, {}, 0.1, options);
    expectVector(current.velocity, initialVelocity.clone().multiplyScalar(Math.exp(-0.8)));
    expectVector(
      current.position,
      initialVelocity.clone().multiplyScalar((1 - Math.exp(-0.8)) / 8),
    );
    for (let i = 0; i < 300; i++) current = step(current, {}, 1 / 60, options);
    expectVector(current.velocity, v());
    const restedPosition = current.position.clone();
    expectVector(step(current, {}, 1, options).position, restedPosition);
  });

  it('integrates the same held input independently of frame rate', () => {
    const initial = state({ velocity: v(-2, 3, 1) });
    const keys = { forward: true, right: true };
    const once = step(initial, keys, 1, options);
    let frames = initial;
    for (let i = 0; i < 120; i++) frames = step(frames, keys, 1 / 120, options);
    expectVector(frames.position, once.position);
    expectVector(frames.velocity, once.velocity);
  });

  it('clamps to the same expanded bounding box as orbit, including flat clouds', () => {
    const bounds = new Box3(v(-1, -2, 0), v(2, 3, 0));
    const initial = state({ position: v(0, 0, 0.3), pitch: Math.PI / 4 });
    const unbounded = step(initial, { forward: true, right: true }, 1, options);
    const bounded = step(initial, { forward: true, right: true }, 1, {
      ...options,
      bounds,
      boundsMargin: 0.5,
    });
    expectVector(bounded.position, clampToBounds(unbounded.position, bounds, 0.5));
    expectVector(bounded.position, v(2.5, 3.5, 0.5));
    expectVector(bounded.velocity, v());
    expectVector(bounds.min, v(-1, -2, 0));
    expectVector(bounds.max, v(2, 3, 0));
  });

  it('ignores invalid/nonpositive time and limits a resumed frame safely', () => {
    const initial = state({ position: v(1, 2, 3), velocity: v(4, 5, 6) });
    for (const dt of [-1, 0, NaN, Infinity]) {
      const result = step(initial, { forward: true }, dt, options);
      expectVector(result.position, initial.position);
      expectVector(result.velocity, initial.velocity);
    }
    expectVector(
      step(initial, { forward: true }, Number.MAX_VALUE, options).position,
      step(initial, { forward: true }, 1, options).position,
    );
  });

  it('supports direct movement when damping is disabled', () => {
    const moved = step(state(), { forward: true }, 0.25, { ...options, damping: 0 });
    expectVector(moved.velocity, v(0, 10, 0));
    expectVector(moved.position, v(0, 2.5, 0));
    expectVector(step(moved, {}, 0.25, { ...options, damping: 0 }).velocity, v());
  });
});
