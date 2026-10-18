import { describe, expect, it } from 'vitest';
import { Box3, Vector3 } from 'three';
import { createDemo } from '../fixtures/demo';
import { nearestPointDistance } from './constraints';
import { PointCloudIndex } from './navigation';
import {
  canShowHumanFigure,
  createObserverState,
  observerDirection,
  observerOrientation,
  step,
  type ObserverContext,
  type ObserverInput,
} from './observer';

const v = (x = 0, y = 0, z = 0) => new Vector3(x, y, z);
const expectVector = (actual: Vector3, expected: Vector3, precision = 9) => {
  expect(actual.x).toBeCloseTo(expected.x, precision);
  expect(actual.y).toBeCloseTo(expected.y, precision);
  expect(actual.z).toBeCloseTo(expected.z, precision);
};
const context: ObserverContext = {
  bounds: new Box3(v(-10, -10, -10), v(10, 10, 10)),
  points: [v(0, 0, -10)],
  minSpeed: 10,
  maxSpeed: 10,
  damping: 0,
};
const initial = (position = v(), yaw = 0, pitch = 0) =>
  createObserverState(position, observerDirection({ yaw, pitch }));

describe('Observer movement', () => {
  it.each([0, Math.PI / 2, -Math.PI / 2, Math.PI])(
    'moves forward, backward and strafes relative to yaw %s in the horizontal plane',
    (yaw) => {
      const forward = v(-Math.sin(yaw), Math.cos(yaw), 0);
      const right = v(Math.cos(yaw), Math.sin(yaw), 0);
      const cases: [ObserverInput, Vector3][] = [
        [{ forward: true }, forward],
        [{ backward: true }, forward.clone().negate()],
        [{ right: true }, right],
        [{ left: true }, right.clone().negate()],
      ];
      for (const [input, direction] of cases) {
        const start = initial(v(), yaw, Math.PI / 3);
        const moved = step(start, input, 0.25, context);
        expectVector(moved.position, direction.clone().multiplyScalar(2.5));
        expectVector(moved.velocity, direction.clone().multiplyScalar(10));
        expectVector(start.position, v());
      }
    },
  );

  it('Space and Shift change only the Z-up coordinate regardless of yaw and pitch', () => {
    const start = initial(v(3, 4, 5), 1.2, -1.3);
    expectVector(step(start, { up: true }, 0.5, context).position, v(3, 4, 10));
    expectVector(step(start, { down: true }, 0.5, context).position, v(3, 4, 0));
    expectVector(step(start, { up: true, down: true }, 0.5, context).position, start.position);
  });

  it('normalizes diagonal movement, cancels opposing controls and supports boost', () => {
    const start = initial();
    const forward = step(start, { forward: true }, 0.5, context);
    const diagonal = step(start, { forward: true, right: true, up: true }, 0.5, context);
    expect(diagonal.position.length()).toBeCloseTo(forward.position.length(), 12);
    expectVector(diagonal.position.clone().normalize(), v(1, 1, 1).normalize());
    expectVector(step(start, { left: true, right: true }, 0.5, context).position, v());
    expect(step(start, { forward: true, boost: true }, 0.5, context).position.length()).toBe(20);
  });

  it('has no gravity and stops drifting once released velocity settles', () => {
    let current = initial(v(3, 4, -5));
    const options = { ...context, damping: 10 };
    expectVector(step(current, {}, 1, options).position, current.position);
    current = step(current, { forward: true, up: true }, 0.25, options);
    expect(current.velocity.z).toBeGreaterThan(0);
    for (let frame = 0; frame < 300; frame++) current = step(current, {}, 1 / 60, options);
    expectVector(current.velocity, v());
    const rested = current.position.clone();
    for (let frame = 0; frame < 100; frame++) current = step(current, {}, 1 / 60, options);
    expectVector(current.position, rested);
  });

  it('smooths velocity without a frame-rate-dependent jump at a fixed speed', () => {
    const options = { ...context, damping: 10 };
    const start = initial();
    const once = step(start, { forward: true }, 1, options);
    let frames = start;
    for (let frame = 0; frame < 120; frame++)
      frames = step(frames, { forward: true }, 1 / 120, options);
    expectVector(frames.position, once.position);
    expectVector(frames.velocity, once.velocity);
    expect(once.velocity.y).toBeLessThan(10);
  });

  it('moves through a point and below terrain without a collision or ground clamp', () => {
    const options = { ...context, points: new PointCloudIndex(new Float32Array([0, 0, 0])) };
    const moved = step(initial(v(0, -1, 0)), { forward: true }, 0.2, options);
    expectVector(moved.position, v(0, 1, 0));
    expectVector(step(initial(), { down: true }, 0.2, options).position, v(0, 0, -2));
  });

  it('keeps only a loose outer bound and clears momentum into it', () => {
    const bounds = new Box3(v(-1, -1, 0), v(1, 1, 0));
    const extent = 10 * bounds.getSize(v()).length();
    const moved = step(initial(), { up: true, boost: true }, 1, { ...context, bounds });
    expect(moved.position.z).toBeCloseTo(extent, 10);
    expectVector(moved.velocity, v());
    expectVector(bounds.max, v(1, 1, 0));
  });

  it('ignores invalid and nonpositive time, and limits a resumed tab to one second', () => {
    const start = initial(v(1, 2, 3));
    for (const dt of [-1, 0, NaN, Infinity])
      expectVector(step(start, { forward: true }, dt, context).position, start.position);
    expectVector(
      step(start, { forward: true }, Number.MAX_VALUE, context).position,
      step(start, { forward: true }, 1, context).position,
    );
  });
});

describe('Observer distance-adaptive speed', () => {
  const options = {
    bounds: new Box3(v(), v(6, 8, 0)), // Diagonal = 10; default clamps are .6 and 5.
    points: [v()],
    damping: 0,
    distanceFactor: 0.8,
  };

  it('grows with exact nearest-point distance and respects scene-derived clamps', () => {
    const speedAt = (position: Vector3) =>
      step(initial(position), { forward: true }, 0.01, options).velocity.length();
    expect(speedAt(v())).toBeCloseTo(0.6, 12);
    expect(speedAt(v(1, 0, 0))).toBeCloseTo(0.8, 12);
    expect(speedAt(v(2, 0, 0))).toBeCloseTo(1.6, 12);
    expect(speedAt(v(100, 0, 0))).toBeCloseTo(5, 12);
  });

  it.each([0.1, 1, 10])(
    'travels at least 5% of the scene diagonal in one second near geometry (scale %s)',
    (scale) => {
      const bounds = new Box3(v(), v(6 * scale, 8 * scale, 0));
      const start = initial(v(0.001 * scale, 0, 0));
      let current = start;
      for (let frame = 0; frame < 60; frame++)
        current = step(current, { forward: true }, 1 / 60, { ...options, bounds, damping: 10 });
      expect(current.position.distanceTo(start.position)).toBeGreaterThanOrEqual(
        bounds.getSize(v()).length() * 0.05,
      );
    },
  );

  it('moves faster away from geometry but stops increasing at the scene-relative maximum', () => {
    const near = step(initial(), { forward: true }, 1, options);
    const farStart = initial(v(100, 0, 0));
    const far = step(farStart, { forward: true }, 1, options);
    expect(far.velocity.length()).toBeGreaterThan(near.velocity.length());
    expect(far.velocity.length()).toBe(5);
    expect(far.position.distanceTo(farStart.position)).toBe(5);
  });

  it.each([100, 1e6])(
    'caps both default speed bounds in a very large scene (scale %s)',
    (scale) => {
      const bounds = new Box3(v(), v(6 * scale, 8 * scale, 0));
      const ctx = { ...options, bounds };
      for (const position of [v(), v(100 * scale, 0, 0)]) {
        const moved = step(initial(position), { forward: true }, 1, ctx);
        expect(moved.velocity.length()).toBeLessThanOrEqual(100);
        expect(moved.position.distanceTo(position)).toBeLessThanOrEqual(100);
      }
      expect(step(initial(v(100 * scale, 0, 0)), { forward: true }, 1, ctx).velocity.length()).toBe(
        100,
      );
    },
  );

  it.each([0, 2, 100])('retains the boost multiplier at nearest-point distance %s', (distance) => {
    const start = initial(v(distance, 0, 0));
    const normal = step(start, { forward: true }, 1, options);
    const boosted = step(start, { forward: true, boost: true }, 1, options);
    expect(boosted.velocity.length()).toBeCloseTo(normal.velocity.length() * 4, 12);
    expect(boosted.position.distanceTo(start.position)).toBeCloseTo(
      normal.position.distanceTo(start.position) * 4,
      12,
    );
  });

  it('uses the nearest of all points and respects explicit speed clamps', () => {
    const ctx = { ...options, points: [v(0, -100, 0), v(0, -2, 0)], minSpeed: 2, maxSpeed: 3 };
    expect(step(initial(), { forward: true }, 0.1, ctx).velocity.length()).toBe(2);
    expect(step(initial(v(50, 0, 0)), { forward: true }, 0.1, ctx).velocity.length()).toBe(3);
    expect(
      step(initial(), { forward: true }, 0.1, {
        ...ctx,
        points: [],
        distanceFactor: 0,
      }).velocity.length(),
    ).toBe(3);
  });

  it('computes exact proximity consistently for vectors, packed points and indexed outliers', () => {
    const positions: number[] = [];
    for (let i = 0; i < 500; i++) positions.push(Math.sin(i) * 8, Math.cos(i) * 9, i / 50);
    positions.push(10000, -10000, 50000, NaN, 1, 2);
    const packed = new Float32Array(positions);
    const vectors = Array.from({ length: packed.length / 3 }, (_, i) =>
      v(packed[i * 3], packed[i * 3 + 1], packed[i * 3 + 2]),
    );
    const indexed = new PointCloudIndex(packed);
    for (const query of [v(), v(3, 4, 5), v(10001, -10000, 50000), v(-300, 200, 400)]) {
      const distance = nearestPointDistance(vectors, query);
      expect(nearestPointDistance(packed, query)).toBeCloseTo(distance, 12);
      expect(nearestPointDistance(indexed, query)).toBeCloseTo(distance, 12);
    }
    expect(nearestPointDistance(new PointCloudIndex(new Float32Array()), v())).toBe(Infinity);
  });
});

describe('Observer look', () => {
  it('adopts direction and builds the equivalent Three.js camera orientation', () => {
    const direction = v(1, -2, 0.8).normalize();
    const position = v(3, -5, 2);
    const state = createObserverState(position, direction);
    expectVector(state.position, position);
    expectVector(observerDirection(state), direction);
    expectVector(v(0, 0, -1).applyQuaternion(observerOrientation(state)), direction);
    expectVector(state.velocity, v());
    state.position.set(0, 0, 0);
    expectVector(position, v(3, -5, 2));
  });

  it('smooths pixel deltas toward the intended right/down direction', () => {
    const start = initial();
    const looked = step(start, { lookX: 100, lookY: 100 }, 1 / 60, context);
    expect(looked.yaw).toBeLessThan(0);
    expect(looked.yaw).toBeGreaterThan(-0.2);
    expect(looked.pitch).toBeLessThan(0);
    expect(looked.pitch).toBeGreaterThan(-0.2);
    expect(looked.targetYaw).toBeCloseTo(-0.2, 12);
    expect(looked.targetPitch).toBeCloseTo(-0.2, 12);
    let settled = looked;
    for (let frame = 0; frame < 120; frame++) settled = step(settled, {}, 1 / 60, context);
    expect(settled.yaw).toBeCloseTo(-0.2, 9);
    expect(settled.pitch).toBeCloseTo(-0.2, 9);
    expect(start.yaw).toBe(0);
    expect(start.pitch).toBe(0);
  });

  it.each([-1, 1])(
    'clamps pitch to ±89° for look direction %s, including initial poles',
    (sign) => {
      const pole = createObserverState(v(), v(0, 0, sign), v(0, 1, 0));
      expect(pole.yaw).toBeCloseTo(Math.PI / 2, 12);
      expect(pole.pitch).toBeCloseTo((sign * 89 * Math.PI) / 180, 12);
      const looked = step(initial(), { lookY: -sign * 1e6 }, 1, { ...context, lookDamping: 0 });
      expect(looked.pitch).toBeCloseTo((sign * 89 * Math.PI) / 180, 12);
      expect(looked.targetPitch).toBe(looked.pitch);
      expect(Math.abs(observerDirection(looked).z)).toBeLessThan(1);
    },
  );
});

describe('Observer human scale', () => {
  it('keeps the figure hidden without explicit metric calibration', () => {
    const bundle = createDemo('en').bundle;
    expect(bundle.manifest.world.scale_m_per_unit).toBeUndefined();
    expect(canShowHumanFigure(bundle)).toBe(false);
    for (const invalid of [0, -1, NaN, Infinity]) {
      bundle.manifest.world.scale_m_per_unit = invalid;
      expect(canShowHumanFigure(bundle)).toBe(false);
    }
  });

  it('allows the figure only with a finite positive metres-per-unit scale', () => {
    const bundle = createDemo('en').bundle;
    for (const metricScale of [1, 0.01, 2.5]) {
      bundle.manifest.world.scale_m_per_unit = metricScale;
      expect(canShowHumanFigure(bundle)).toBe(true);
    }
  });
});
