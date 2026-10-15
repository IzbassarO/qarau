import { describe, expect, it } from 'vitest';
import { Euler, Quaternion, Vector3 } from 'three';
import {
  orientationFor,
  projectAxisButtons,
  stepAxisView,
  viewDirectionFor,
  type Axis,
} from './gizmo';

const v = (x = 0, y = 0, z = 0) => new Vector3(x, y, z);
const axes: Axis[] = ['x', '-x', 'y', '-y', 'z', '-z'];

function expectVector(actual: Vector3, expected: Vector3, precision = 10) {
  expect(actual.x).toBeCloseTo(expected.x, precision);
  expect(actual.y).toBeCloseTo(expected.y, precision);
  expect(actual.z).toBeCloseTo(expected.z, precision);
}

function expectClickableLayout(orientation: Quaternion) {
  const positions = projectAxisButtons(orientation);
  expect(positions.map(({ axis }) => axis)).toEqual(axes);
  for (const [i, a] of positions.entries()) {
    // A 22px button must fit inside the circular 104px widget, with a 2px gap.
    expect(Math.hypot(a.x, a.y) + 11).toBeLessThanOrEqual(52 + 1e-8);
    for (const b of positions.slice(i + 1))
      expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeGreaterThanOrEqual(24 - 1e-7);
  }
}

describe('projectAxisButtons', () => {
  it('separates different axes that coincide when viewed along a diagonal', () => {
    const orientation = orientationFor(v(1, 1, 0).normalize(), v(0, 0, 1));
    const inverse = orientation.clone().invert();
    const rawX = v(1, 0, 0).applyQuaternion(inverse);
    const rawNegativeY = v(0, -1, 0).applyQuaternion(inverse);
    expect(Math.hypot(rawX.x - rawNegativeY.x, rawX.y - rawNegativeY.y)).toBeLessThan(1e-12);
    expectClickableLayout(orientation);
  });

  it.each(axes)('keeps all six buttons separate at the %s axis pole', (axis) => {
    const target = viewDirectionFor(axis, v(0, 0, 1));
    expectClickableLayout(orientationFor(target.direction, target.up));
  });

  it('keeps every circle in bounds and clickable across rotated and rolled camera views', () => {
    for (let x = 0; x < 8; x++)
      for (let y = 0; y < 8; y++)
        for (let z = 0; z < 8; z++)
          expectClickableLayout(
            new Quaternion().setFromEuler(
              new Euler((x * Math.PI) / 4, (y * Math.PI) / 4, (z * Math.PI) / 4),
            ),
          );
  });

  it('retains the actual camera projection and depth when the endpoints are already separate', () => {
    const orientation = orientationFor(v(1, 1, 1).normalize(), v(0, 0, 1));
    const original = orientation.clone();
    const inverse = orientation.clone().invert();
    for (const position of projectAxisButtons(orientation)) {
      const projected = viewDirectionFor(position.axis, v(0, 0, 1)).direction.applyQuaternion(
        inverse,
      );
      expect(position.x).toBeCloseTo(projected.x * 35, 10);
      expect(position.y).toBeCloseTo(-projected.y * 35, 10);
      expect(position.depth).toBeCloseTo(projected.z, 10);
    }
    expect(orientation.equals(original)).toBe(true);
  });
});

describe('viewDirectionFor', () => {
  it.each<[Axis, Vector3, Vector3]>([
    ['x', v(1, 0, 0), v(0, 0, 1)],
    ['-x', v(-1, 0, 0), v(0, 0, 1)],
    ['y', v(0, 1, 0), v(0, 0, 1)],
    ['-y', v(0, -1, 0), v(0, 0, 1)],
    ['z', v(0, 0, 1), v(0, 1, 0)],
    ['-z', v(0, 0, -1), v(0, -1, 0)],
  ])(
    'returns the correct %s camera direction and up for the engine Z-up convention',
    (axis, direction, up) => {
      const result = viewDirectionFor(axis, v(0, 0, 1));
      expectVector(result.direction, direction);
      expectVector(result.up, up);
    },
  );

  it.each<[Axis, Vector3]>([
    ['x', v(0, 1, 0)],
    ['-x', v(0, 1, 0)],
    ['y', v(0, 0, -1)],
    ['-y', v(0, 0, 1)],
    ['z', v(0, 1, 0)],
    ['-z', v(0, 1, 0)],
  ])('respects Y-up for %s instead of assuming the engine convention', (axis, up) => {
    expectVector(viewDirectionFor(axis, v(0, 1, 0)).up, up);
  });

  it('has a unit, perpendicular screen-up for every axis, including top and bottom', () => {
    for (const up of [v(0, 0, 1), v(0, 1, 0), v(1, 0, 0), v(1, 2, 3)]) {
      const original = up.clone();
      for (const axis of axes) {
        const result = viewDirectionFor(axis, up);
        expect(result.direction.length()).toBeCloseTo(1, 12);
        expect(result.up.length()).toBeCloseTo(1, 12);
        expect(result.direction.dot(result.up)).toBeCloseTo(0, 12);
      }
      expectVector(up, original);
    }
  });

  it('uses the engine convention if the supplied up axis has zero length', () => {
    expectVector(viewDirectionFor('z', v()).up, v(0, 1, 0));
  });
});

describe('orientationFor', () => {
  it.each(axes)('orients local -Z towards the pivot and local +Y upright for %s', (axis) => {
    const target = viewDirectionFor(axis, v(0, 0, 1));
    const quaternion = orientationFor(target.direction, target.up);
    expectVector(v(0, 0, -1).applyQuaternion(quaternion), target.direction.clone().negate());
    expectVector(v(0, 1, 0).applyQuaternion(quaternion), target.up);
    expect(quaternion.length()).toBeCloseTo(1, 12);
  });
});

describe('stepAxisView', () => {
  const pivot = v(2, -3, 4);
  const startDirection = v(1, -2, 1).normalize();
  const initial = () => ({
    position: startDirection.clone().multiplyScalar(12).add(pivot),
    quaternion: orientationFor(startDirection, v(0, 0, 1)),
  });

  it.each(axes)('converges smoothly to %s while preserving pivot distance', (axis) => {
    const target = viewDirectionFor(axis, v(0, 0, 1));
    const targetQuaternion = orientationFor(target.direction, target.up);
    let state = initial();
    let lastAngle = state.quaternion.angleTo(targetQuaternion);
    const first = stepAxisView(state, pivot, target, 1 / 60);
    expect(first.quaternion.angleTo(targetQuaternion)).toBeGreaterThan(0);
    expect(first.quaternion.angleTo(state.quaternion)).toBeGreaterThan(0);
    for (let i = 0; i < 180; i++) {
      state = stepAxisView(state, pivot, target, 1 / 60);
      const angle = state.quaternion.angleTo(targetQuaternion);
      expect(angle).toBeLessThanOrEqual(lastAngle + 1e-7);
      expect(state.position.distanceTo(pivot)).toBeCloseTo(12, 9);
      expectVector(
        v(0, 0, -1).applyQuaternion(state.quaternion),
        pivot.clone().sub(state.position).normalize(),
        8,
      );
      lastAngle = angle;
    }
    expectVector(state.position, target.direction.clone().multiplyScalar(12).add(pivot), 8);
    expect(state.quaternion.angleTo(targetQuaternion)).toBeLessThan(1e-6);
  });

  it('gives the same flight after equal elapsed time at different frame rates', () => {
    const target = viewDirectionFor('-z', v(0, 0, 1));
    const advance = (frames: number) => {
      let state = initial();
      for (let i = 0; i < frames; i++) state = stepAxisView(state, pivot, target, 1 / frames, 4);
      return state;
    };
    const at30 = advance(30);
    const at60 = advance(60);
    // Three.js uses normalized lerp below 0.9995 quaternion dot, so very small
    // angles have a sub-micrometre numerical difference at this 12-unit radius.
    expectVector(at30.position, at60.position, 5);
    expect(at30.quaternion.angleTo(at60.quaternion)).toBeLessThan(1e-6);
  });

  it('handles an exact opposite view without a gimbal flip or a change in radius', () => {
    let state = {
      position: pivot.clone().add(v(0, 0, 12)),
      quaternion: orientationFor(v(0, 0, 1), v(0, 1, 0)),
    };
    const target = viewDirectionFor('-z', v(0, 0, 1));
    for (let i = 0; i < 180; i++) {
      state = stepAxisView(state, pivot, target, 1 / 60);
      expect(state.quaternion.length()).toBeCloseTo(1, 12);
      expect(state.position.distanceTo(pivot)).toBeCloseTo(12, 9);
    }
    expectVector(state.position, pivot.clone().add(v(0, 0, -12)), 8);
    expectVector(v(0, 1, 0).applyQuaternion(state.quaternion), v(0, -1, 0), 8);
  });

  it('does not jump at zero dt when a picked pivot starts off the camera viewing ray', () => {
    const state = { position: v(5, 2, 3), quaternion: new Quaternion() };
    const target = viewDirectionFor('x', v(0, 0, 1));
    const result = stepAxisView(state, pivot, target, 0);
    expectVector(result.position, state.position);
    expect(result.quaternion.angleTo(state.quaternion)).toBe(0);
    expect(result.position).not.toBe(state.position);
    expect(result.quaternion).not.toBe(state.quaternion);
  });

  it('keeps its inputs immutable and permits a final exact snap with a large dt', () => {
    const state = initial();
    const originalPosition = state.position.clone();
    const originalQuaternion = state.quaternion.clone();
    const target = viewDirectionFor('y', v(0, 0, 1));
    const result = stepAxisView(state, pivot, target, 10);
    expectVector(result.position, pivot.clone().add(v(0, 12, 0)));
    expectVector(state.position, originalPosition);
    expect(state.quaternion.equals(originalQuaternion)).toBe(true);
    expectVector(pivot, v(2, -3, 4));
    expectVector(target.direction, v(0, 1, 0));
    expectVector(target.up, v(0, 0, 1));
  });

  it('handles zero radius without introducing an invalid orientation', () => {
    const result = stepAxisView(
      { position: pivot.clone(), quaternion: new Quaternion() },
      pivot,
      viewDirectionFor('x', v(0, 0, 1)),
      1 / 60,
    );
    expectVector(result.position, pivot);
    expect(result.quaternion.length()).toBeCloseTo(1, 12);
  });
});
