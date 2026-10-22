import { afterEach, describe, expect, it, vi } from 'vitest';
import { Box3, PerspectiveCamera, Ray, Vector3 } from 'three';
import {
  NavigationAction,
  NavigationControls,
  PointCloudIndex,
  clampToBounds,
  damp,
  limitByNearestPoints,
  mapInput,
  orbit,
  pan,
  pickPivot,
  speedScale,
  zoomToward,
  type NavigationState,
} from './navigation';

const v = (x = 0, y = 0, z = 0) => new Vector3(x, y, z);
const expectVector = (actual: Vector3, expected: Vector3, precision = 8) => {
  expect(actual.x).toBeCloseTo(expected.x, precision);
  expect(actual.y).toBeCloseTo(expected.y, precision);
  expect(actual.z).toBeCloseTo(expected.z, precision);
};
const state = (overrides: Partial<NavigationState> = {}): NavigationState => ({
  position: v(0, 0, 10),
  pivot: v(),
  up: v(0, 1, 0),
  minDistance: 0.1,
  fov: 60,
  ...overrides,
});

describe('pickPivot', () => {
  const ray = new Ray(v(0, 0, 10), v(0, 0, -1));

  it('picks the nearest point in front of the cursor ray, regardless of source order', () => {
    const points = new Float32Array([0, 0, 1, 0.02, 0, 7, 3, 0, 8, 0, 0, 12]);
    expectVector(pickPivot(ray, points, 0.05), v(0.02, 0, 7), 6);
  });

  it('accepts vector points and returns a copy rather than a mutable cloud point', () => {
    const points = [v(0, 0, 2), v(0, 0, 5)];
    const pivot = pickPivot(ray, points, 0.1);
    expectVector(pivot, points[1]);
    pivot.set(100, 100, 100);
    expectVector(points[1], v(0, 0, 5));
  });

  it('falls back to the ray at the current target distance when points miss or are behind', () => {
    expectVector(pickPivot(ray, [v(3, 0, 5), v(0, 0, 11)], 0.1, 4), v(0, 0, 6));
    expectVector(pickPivot(ray, new Float32Array(), 0.1, 4), v(0, 0, 6));
  });

  it('uses the same nearest hit and fallback with an indexed cloud', () => {
    const points = new Float32Array([0, 0, -8, 0.02, 0, 7, 0, 0, 2, 7, 4, 5]);
    const index = new PointCloudIndex(points);
    expectVector(pickPivot(ray, index, 0.05), pickPivot(ray, points, 0.05));
    const miss = new Ray(v(40, 0, 10), v(0, 0, -1));
    expectVector(pickPivot(miss, index, 0.05, 8), v(40, 0, 2));
  });
});

describe('orbit', () => {
  it('moves around the chosen pivot and preserves the camera distance', () => {
    const initial = state({ position: v(4, 3, 12), pivot: v(1, 2, 3) });
    const result = orbit(initial, 0.6, -0.3);
    expect(result.position.distanceTo(result.pivot)).toBeCloseTo(
      initial.position.distanceTo(initial.pivot),
      10,
    );
    expect(result.position.distanceTo(initial.position)).toBeGreaterThan(0.1);
    expectVector(result.pivot, initial.pivot);
    expectVector(initial.position, v(4, 3, 12));
  });

  it('clamps both poles without collapsing or flipping the camera up axis', () => {
    for (const dy of [-100, 100]) {
      const result = orbit(state(), 0.2, dy);
      const offset = result.position.clone().sub(result.pivot);
      expect(offset.length()).toBeCloseTo(10, 8);
      expect(Math.abs(offset.clone().normalize().dot(result.up))).toBeLessThan(1);
      expect(Number.isFinite(result.position.length())).toBe(true);
    }
  });

  it('supports the viewer Z-up coordinate system', () => {
    const initial = state({ position: v(10, 0, 4), up: v(0, 0, 1) });
    const result = orbit(initial, 0.4, 0.2);
    expect(result.position.distanceTo(result.pivot)).toBeCloseTo(
      initial.position.distanceTo(initial.pivot),
      10,
    );
    expectVector(result.up, initial.up);
  });
});

describe('zoomToward', () => {
  it('moves toward the cursor ray, including an off-center cursor', () => {
    const initial = state();
    const direction = v(1, 0, -1).normalize();
    const result = zoomToward(initial, new Ray(initial.position.clone(), direction), 2);
    expectVector(result.position, initial.position.clone().addScaledVector(direction, 2));
    expectVector(initial.position, v(0, 0, 10));
  });

  it('keeps advancing and dollies the pivot forward through repeated close zooms', () => {
    let current = state({ position: v(0, 0, 1), minDistance: 0.2 });
    const originalPivot = current.pivot.clone();
    for (let i = 0; i < 100; i++) {
      const previous = current.position.clone();
      current = zoomToward(current, new Ray(current.position.clone(), v(0, 0, -1)), 0.15);
      expect(current.position.z).toBeLessThan(previous.z);
      expect(current.position.distanceTo(current.pivot)).toBeGreaterThanOrEqual(0.2 - 1e-8);
      expect(Number.isFinite(current.position.distanceTo(current.pivot))).toBe(true);
    }
    expect(current.position.z).toBeCloseTo(-14, 8);
    expect(current.pivot.z).toBeLessThan(originalPivot.z);
  });

  it('handles a single zoom larger than the pivot distance without crossing the pivot', () => {
    const initial = state({ position: v(0, 0, 1), minDistance: 0.2 });
    const result = zoomToward(initial, new Ray(initial.position.clone(), v(0, 0, -1)), 20);
    expectVector(result.position, v(0, 0, -19));
    expect(result.pivot.z).toBeLessThan(result.position.z);
    expect(result.position.distanceTo(result.pivot)).toBeGreaterThanOrEqual(0.2 - 1e-8);
  });

  it('zooms backward for negative amounts and leaves the distant pivot fixed', () => {
    const initial = state();
    const result = zoomToward(initial, new Ray(initial.position.clone(), v(0, 0, -1)), -3);
    expectVector(result.position, v(0, 0, 13));
    expectVector(result.pivot, initial.pivot);
  });
});

describe('pan', () => {
  it('translates camera and pivot equally in the camera screen plane', () => {
    const initial = state();
    const result = pan(initial, 0.1, -0.2);
    const translation = result.position.clone().sub(initial.position);
    expectVector(result.pivot.clone().sub(initial.pivot), translation);
    expect(translation.length()).toBeGreaterThan(0);
    expect(translation.z).toBeCloseTo(0, 10);
    expect(result.position.distanceTo(result.pivot)).toBeCloseTo(10, 10);
    expectVector(initial.pivot, v());
  });

  it('scales pan by distance to the pivot', () => {
    const near = state();
    const far = state({ position: v(0, 0, 20) });
    const nearMove = pan(near, 0.1, 0.1).position.distanceTo(near.position);
    const farMove = pan(far, 0.1, 0.1).position.distanceTo(far.position);
    expect(farMove).toBeCloseTo(2 * nearMove, 10);
  });
});

describe('clampToBounds', () => {
  const bounds = new Box3(v(-1, -2, -3), v(1, 2, 3));

  it('clamps every axis to the bounding box expanded by the world-space margin', () => {
    const input = v(5, -6, 9);
    expectVector(clampToBounds(input, bounds, 0.5), v(1.5, -2.5, 3.5));
    expectVector(input, v(5, -6, 9));
    expectVector(bounds.min, v(-1, -2, -3));
    expectVector(bounds.max, v(1, 2, 3));
  });

  it('keeps valid positions unchanged and handles a flat cloud', () => {
    expectVector(clampToBounds(v(0.5, -1, 2), bounds, 0), v(0.5, -1, 2));
    expectVector(clampToBounds(v(0, 0, 10), new Box3(v(-1, -1, 0), v(1, 1, 0)), 1), v(0, 0, 1));
  });
});

describe('limitByNearestPoints', () => {
  it('stops before the first point along a move, independent of point ordering', () => {
    const from = v();
    const to = v(10, 0, 0);
    const points = new Float32Array([8, 0, 0, 3, 0, 0, 6, 5, 0]);
    const result = limitByNearestPoints(from, to, points, 0.5);
    expect(result.x).toBeLessThanOrEqual(2.5);
    expect(result.x).toBeGreaterThan(2.49);
    expectVector(v(0, result.y, result.z), v());
    expectVector(from, v());
    expectVector(to, v(10, 0, 0));
  });

  it('detects off-axis swept-sphere collisions rather than testing the destination only', () => {
    const result = limitByNearestPoints(v(), v(10, 0, 0), [v(4, 0.3, 0)], 0.5);
    expect(result.x).toBeGreaterThan(3.59);
    expect(result.x).toBeLessThanOrEqual(3.6);
  });

  it('allows clear moves and points beyond the destination or behind the camera', () => {
    expectVector(
      limitByNearestPoints(v(), v(5, 0, 0), [v(-2, 0, 0), v(8, 0, 0), v(3, 2, 0)], 0.5),
      v(5, 0, 0),
    );
    expectVector(limitByNearestPoints(v(), v(), [v()], 0.5), v());
  });

  it('lets a camera already inside a point radius escape without allowing a deeper move', () => {
    const points = [v()];
    expectVector(limitByNearestPoints(v(0.1, 0, 0), v(2, 0, 0), points, 0.5), v(2, 0, 0));
    expectVector(limitByNearestPoints(v(0.1, 0, 0), v(-2, 0, 0), points, 0.5), v(0.1, 0, 0));
  });

  it('uses every indexed point for collision and agrees with the unindexed cloud', () => {
    const points = new Float32Array([9, 0, 0, 4, 0.3, 0, 2, 3, 0]);
    const index = new PointCloudIndex(points);
    expectVector(
      limitByNearestPoints(v(), v(12, 0, 0), index, 0.5),
      limitByNearestPoints(v(), v(12, 0, 0), points, 0.5),
    );
  });
});

describe('PointCloudIndex', () => {
  it('retains nearest hits and obstacles across a dense cloud and distant outliers', () => {
    const points = new Float32Array(5002 * 3);
    for (let i = 0; i < 5000; i++) {
      points.set(
        [(((i * 17) % 101) - 50) / 10, (((i * 13) % 103) - 51) / 10, ((i * 7) % 107) / 10],
        i * 3,
      );
    }
    points.set([0, 0, 8], 5000 * 3);
    const outlier = v(1e6, -1e6, 1e6);
    points.set(outlier.toArray(), 5001 * 3);
    const index = new PointCloudIndex(points);
    const rays = [
      new Ray(v(0, 0, 20), v(0, 0, -1)),
      new Ray(v(10, 4, 12), v(-1, -0.4, -1).normalize()),
      new Ray(outlier.clone().add(v(0, 0, 10)), v(0, 0, -1)),
    ];
    for (const ray of rays) {
      expectVector(pickPivot(ray, index, 0.2, 10), pickPivot(ray, points, 0.2, 10));
      const destination = ray.at(30, v());
      expectVector(
        limitByNearestPoints(ray.origin, destination, index, 0.2),
        limitByNearestPoints(ray.origin, destination, points, 0.2),
      );
    }
    expectVector(pickPivot(rays[2], index, 0.2), outlier);
  });
});

describe('mapInput', () => {
  it.each([
    [{ type: 'pointerdown', button: 0 }, NavigationAction.Orbit],
    [{ type: 'pointerdown', button: 0, shiftKey: true }, NavigationAction.Pan],
    [{ type: 'pointerdown', button: 1 }, NavigationAction.Pan],
    [{ type: 'pointerdown', button: 2 }, NavigationAction.None],
    [{ type: 'wheel', ctrlKey: true, deltaY: 1.2 }, NavigationAction.Zoom],
    [{ type: 'wheel', ctrlKey: true, shiftKey: true, deltaY: 1.2 }, NavigationAction.Zoom],
    [{ type: 'wheel', shiftKey: true, deltaY: 100 }, NavigationAction.Zoom],
    [{ type: 'wheel', shiftKey: true, deltaY: 3.5 }, NavigationAction.Pan],
    [{ type: 'wheel', shiftKey: true, deltaX: 120, deltaY: 0 }, NavigationAction.Zoom],
    [{ type: 'wheel', deltaMode: 1, deltaY: 3 }, NavigationAction.Zoom],
    [{ type: 'wheel', deltaMode: 2, deltaY: -1 }, NavigationAction.Zoom],
    [{ type: 'wheel', deltaMode: 0, deltaY: 120, deltaX: 0 }, NavigationAction.Zoom],
    [{ type: 'wheel', deltaMode: 0, deltaY: -40, deltaX: 0 }, NavigationAction.Orbit],
    [{ type: 'wheel', deltaMode: 0, deltaY: 12, deltaX: 0 }, NavigationAction.Orbit],
    [{ type: 'wheel', deltaMode: 0, deltaY: 120, deltaX: 2 }, NavigationAction.Orbit],
    [{ type: 'wheel', deltaMode: 0, deltaY: 80.5, deltaX: 0 }, NavigationAction.Orbit],
    [{ type: 'wheel', deltaMode: 0, deltaY: 3.5, deltaX: 2.2 }, NavigationAction.Orbit],
    [{ type: 'keydown' }, NavigationAction.None],
  ])('maps %j to %s', (input, expected) => {
    expect(mapInput(input)).toBe(expected);
  });
});

describe('speedScale', () => {
  it('slows navigation near its target relative to scene size', () => {
    expect(speedScale(0.1, 10)).toBeLessThan(speedScale(2, 10));
    expect(speedScale(2, 10)).toBeLessThan(speedScale(10, 10));
    expect(speedScale(10, 10)).toBe(speedScale(100, 10));
    expect(speedScale(0, 10)).toBeGreaterThan(0);
  });

  it('makes Alt/Option precision exactly four times slower', () => {
    for (const distance of [0.1, 2, 10, 100]) {
      expect(speedScale(distance, 10, { precision: true })).toBe(speedScale(distance, 10) * 0.25);
    }
  });

  it('scales sensitivity within 0.25–4 and handles invalid values', () => {
    expect(speedScale(2, 10, { sensitivity: 2 })).toBe(speedScale(2, 10) * 2);
    expect(speedScale(2, 10, { sensitivity: 0 })).toBe(speedScale(2, 10) * 0.25);
    expect(speedScale(2, 10, { sensitivity: 10 })).toBe(speedScale(2, 10) * 4);
    expect(speedScale(2, 10, { sensitivity: NaN })).toBe(speedScale(2, 10));
    expect(Number.isFinite(speedScale(0, 0))).toBe(true);
  });
});

describe('damp', () => {
  it('converges monotonically without overshooting', () => {
    let current = 0;
    for (let i = 0; i < 120; i++) {
      const next = damp(current, 10, 8, 1 / 60);
      expect(next).toBeGreaterThanOrEqual(current);
      expect(next).toBeLessThanOrEqual(10);
      current = next;
    }
    expect(current).toBeCloseTo(10, 5);
    expect(damp(3, 10, 8, 0)).toBe(3);
  });

  it('produces the same result after 60 × 1/60 s and 30 × 1/30 s', () => {
    const advance = (steps: number) => {
      let value = -5;
      for (let i = 0; i < steps; i++) value = damp(value, 12, 4, 1 / steps);
      return value;
    };
    expect(advance(60)).toBeCloseTo(advance(30), 12);
    expect(damp(damp(0, 10, 4, 1 / 60), 10, 4, 1 / 60)).toBeCloseTo(damp(0, 10, 4, 1 / 30), 12);
  });
});

const controlsToDispose: NavigationControls[] = [];
afterEach(() => {
  for (const controls of controlsToDispose.splice(0)) controls.dispose();
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

function controlsFixture(
  points = new Float32Array([0, 0, 0]),
  target = v(),
  bounds = new Box3(v(-100, -100, -100), v(100, 100, 100)),
) {
  const canvas = document.createElement('canvas');
  document.body.appendChild(canvas);
  vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({
    left: 0,
    top: 0,
    right: 800,
    bottom: 600,
    width: 800,
    height: 600,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  });
  canvas.setPointerCapture = vi.fn();
  canvas.releasePointerCapture = vi.fn();
  canvas.hasPointerCapture = vi.fn(() => true);
  const camera = new PerspectiveCamera(60, 4 / 3, 0.01, 1000);
  camera.position.set(0, 0, 10);
  camera.lookAt(v());
  camera.updateMatrixWorld();
  const onChange = vi.fn();
  const onStart = vi.fn();
  const controls = new NavigationControls(camera, canvas, {
    points,
    bounds,
    target,
    minDistance: 0.1,
    collisionRadius: 0.01,
    boundsMargin: 0,
    // Keep the A2 geometric fixtures at full speed; distance scaling is tested separately.
    sceneRadius: 0.01,
    onChange,
    onStart,
  });
  controlsToDispose.push(controls);
  return { canvas, camera, controls, onChange, onStart };
}

function pointer(canvas: HTMLCanvasElement, type: string, init: MouseEventInit = {}) {
  const event = new MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    clientX: 400,
    clientY: 300,
    button: 0,
    ...init,
  });
  Object.defineProperty(event, 'pointerId', { value: 1 });
  Object.defineProperty(event, 'pointerType', { value: 'mouse' });
  canvas.dispatchEvent(event);
  return event;
}

function settle(controls: NavigationControls) {
  for (let i = 0; i < 180; i++) controls.update(1 / 60);
}

function wheelAt(canvas: HTMLCanvasElement, init: WheelEventInit, timeStamp: number) {
  const event = new WheelEvent('wheel', { clientX: 400, clientY: 300, ...init });
  Object.defineProperty(event, 'timeStamp', { value: timeStamp });
  canvas.dispatchEvent(event);
}

describe('NavigationControls DOM lifecycle', () => {
  it.each([false, true])(
    'never turns repeated small integer trackpad scrolling into zoom (Shift: %s)',
    (shiftKey) => {
      const { canvas, camera, controls } = controlsFixture(new Float32Array());
      for (let i = 0; i < 20; i++) {
        wheelAt(canvas, { deltaY: 12, shiftKey }, i * 16);
        controls.update(1);
        expect(camera.position.distanceTo(controls.target)).toBeCloseTo(10, 8);
      }
    },
  );

  it('learns smaller pixel notches only from confirmed mouse events and expires their history', () => {
    const { canvas, camera, controls } = controlsFixture(new Float32Array());
    for (let i = 0; i < 3; i++) {
      wheelAt(canvas, { deltaMode: 1, deltaY: 3 }, i * 20);
      controls.update(1);
    }
    const afterMouse = camera.position.distanceTo(controls.target);
    // Three line ticks establish 3 × 16 = 48 pixels as a known mouse step.
    wheelAt(canvas, { deltaY: 48 }, 60);
    settle(controls);
    const afterPixelNotch = camera.position.distanceTo(controls.target);
    expect(afterPixelNotch).toBeGreaterThan(afterMouse);
    // The same ambiguous pixel event returns to orbit after a gesture gap.
    wheelAt(canvas, { deltaY: 48 }, 400);
    settle(controls);
    expect(camera.position.distanceTo(controls.target)).toBeCloseTo(afterPixelNotch, 8);
  });

  it('keeps confirmed mouse history separate for every control instance', () => {
    const first = controlsFixture(new Float32Array());
    const second = controlsFixture(new Float32Array());
    for (let i = 0; i < 3; i++) {
      wheelAt(first.canvas, { deltaMode: 1, deltaY: 3 }, i * 20);
      first.controls.update(1);
    }
    wheelAt(second.canvas, { deltaY: 48 }, 60);
    settle(second.controls);
    expect(second.camera.position.distanceTo(second.controls.target)).toBeCloseTo(10, 8);
  });

  it.each(['orbit', 'pan', 'zoom'] as const)(
    'applies Alt precision and live sensitivity to trackpad %s',
    (action) => {
      const move = (altKey: boolean, sensitivity = 1) => {
        const { canvas, camera, controls } = controlsFixture(new Float32Array());
        controls.sensitivity = sensitivity;
        const previous = camera.position.clone();
        canvas.dispatchEvent(
          new WheelEvent('wheel', {
            deltaX: action === 'zoom' ? 0 : 8,
            deltaY: action === 'zoom' ? -4 : 0,
            shiftKey: action === 'pan',
            ctrlKey: action === 'zoom',
            altKey,
            clientX: 400,
            clientY: 300,
          }),
        );
        settle(controls);
        return action === 'orbit'
          ? previous.angleTo(camera.position)
          : camera.position.distanceTo(previous);
      };
      const normal = move(false);
      expect(normal).toBeGreaterThan(0);
      expect(move(true)).toBeCloseTo(normal * 0.25, 6);
      expect(move(false, 2)).toBeCloseTo(normal * 2, 6);
    },
  );

  it('keeps Shift mouse-wheel zoom working when the browser swaps its delta to X', () => {
    const { canvas, camera, controls } = controlsFixture(new Float32Array());
    canvas.dispatchEvent(
      new WheelEvent('wheel', {
        deltaX: 120,
        shiftKey: true,
        clientX: 400,
        clientY: 300,
      }),
    );
    settle(controls);
    expect(camera.position.z).toBeGreaterThan(10);
    expect(camera.position.x).toBeCloseTo(0, 8);
    expectVector(controls.target, v());
  });

  it('picks a point on pointerdown and keeps that pivot for the rest of the orbit drag', () => {
    const { canvas, camera, controls, onStart, onChange } = controlsFixture(
      new Float32Array([0, 0, 0]),
      v(2, 0, 0),
    );
    pointer(canvas, 'pointerdown');
    expectVector(controls.target, v());
    expect(onStart).toHaveBeenCalled();
    pointer(canvas, 'pointermove', { clientX: 510, clientY: 330 });
    pointer(canvas, 'pointerup', { clientX: 510, clientY: 330 });
    settle(controls);
    expectVector(controls.target, v());
    expect(camera.position.distanceTo(v(0, 0, 10))).toBeGreaterThan(0.1);
    expect(camera.position.distanceTo(controls.target)).toBeCloseTo(10, 5);
    expect(onChange).toHaveBeenCalled();
  });

  it('does not repick a point during trackpad orbit or pinch zoom', () => {
    const { canvas, camera, controls } = controlsFixture(new Float32Array([0, 0, 5]), v(1, 0, 0));
    const original = controls.target.clone();
    const event = new WheelEvent('wheel', { deltaX: 3, deltaY: 5, cancelable: true });
    canvas.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    settle(controls);
    expectVector(controls.target, original);
    const beforeZoom = camera.position.clone();
    canvas.dispatchEvent(
      new WheelEvent('wheel', {
        ctrlKey: true,
        deltaY: -4,
        clientX: 400,
        clientY: 300,
        cancelable: true,
      }),
    );
    settle(controls);
    expect(camera.position.distanceTo(beforeZoom)).toBeGreaterThan(0);
    expectVector(controls.target, original);
  });

  it('does not snap the viewing direction when an off-center pivot is picked', () => {
    const pivot = v(2, 0, 0);
    const { canvas, camera, controls } = controlsFixture(new Float32Array(pivot.toArray()));
    const rotation = camera.quaternion.clone();
    const projected = pivot.clone().project(camera);
    const clientX = (projected.x + 1) * 400;
    const clientY = (1 - projected.y) * 300;
    pointer(canvas, 'pointerdown', { clientX, clientY });
    expectVector(controls.target, pivot);
    expect(camera.quaternion.angleTo(rotation)).toBeCloseTo(0, 10);
    expect(controls.update(1 / 60)).toBe(false);
    expect(camera.quaternion.angleTo(rotation)).toBeCloseTo(0, 10);
    const originalAngle = camera
      .getWorldDirection(v())
      .angleTo(controls.target.clone().sub(camera.position));
    pointer(canvas, 'pointermove', { clientX: clientX + 100, clientY });
    pointer(canvas, 'pointerup', { clientX: clientX + 100, clientY });
    settle(controls);
    expect(camera.quaternion.angleTo(rotation)).toBeGreaterThan(0.1);
    expect(
      camera.getWorldDirection(v()).angleTo(controls.target.clone().sub(camera.position)),
    ).toBeCloseTo(originalAngle, 8);
  });

  it('keeps the camera within its bounds through large zoom and pan inputs', () => {
    const bounds = new Box3(v(-10, -10, -10), v(10, 10, 10));
    const { canvas, camera, controls } = controlsFixture(new Float32Array(), v(), bounds);
    for (const deltaY of [-2000, 2000, -2000]) {
      canvas.dispatchEvent(
        new WheelEvent('wheel', {
          ctrlKey: true,
          deltaY,
          clientX: 400,
          clientY: 300,
        }),
      );
      for (let frame = 0; frame < 60; frame++) {
        controls.update(1 / 60);
        expect(bounds.containsPoint(camera.position)).toBe(true);
      }
    }
    pointer(canvas, 'pointerdown', { button: 1 });
    pointer(canvas, 'pointermove', { button: 1, clientX: 50000, clientY: -50000 });
    pointer(canvas, 'pointerup', { button: 1, clientX: 50000, clientY: -50000 });
    for (let frame = 0; frame < 60; frame++) {
      controls.update(1 / 60);
      expect(bounds.containsPoint(camera.position)).toBe(true);
    }
    expect(Math.abs(camera.position.x)).toBeGreaterThan(0.1);
    expect(Math.abs(camera.position.y)).toBeGreaterThan(0.1);
  });

  it('continues damping a clear pan after the first frame', () => {
    const { canvas, camera, controls } = controlsFixture(new Float32Array());
    camera.position.set(1.1, -2.2, 9.3);
    camera.lookAt(controls.target);
    camera.updateMatrixWorld();
    const expected = pan(
      state({ position: camera.position.clone(), rotation: camera.quaternion.clone() }),
      25 / 600,
      60 / 600,
    );
    canvas.dispatchEvent(new WheelEvent('wheel', { shiftKey: true, deltaX: 25, deltaY: 60 }));
    expect(controls.update(1 / 60)).toBe(true);
    const firstPosition = camera.position.clone();
    expect(controls.update(1 / 60)).toBe(true);
    expect(camera.position.y).toBeGreaterThan(firstPosition.y);
    settle(controls);
    expectVector(camera.position, expected.position, 6);
    expectVector(controls.target, expected.pivot, 6);
  });

  it('preserves the view offset when a diagonal pan reaches the first bounds plane', () => {
    const { canvas, camera, controls } = controlsFixture(
      new Float32Array(),
      v(),
      new Box3(v(-1, -2, -10), v(1, 2, 10)),
    );
    const originalOffset = camera.position.clone().sub(controls.target);
    canvas.dispatchEvent(new WheelEvent('wheel', { shiftKey: true, deltaX: -600, deltaY: 300 }));
    settle(controls);
    expectVector(camera.position, v(1, 0.5, 10));
    expectVector(camera.position.clone().sub(controls.target), originalOffset);
  });

  it('stops an orbit at a bounds plane without shortening its radius or changing its viewing angle', () => {
    const { canvas, camera, controls } = controlsFixture(
      new Float32Array(),
      v(),
      new Box3(v(-1, -100, -100), v(1, 100, 100)),
    );
    pointer(canvas, 'pointerdown');
    const radius = camera.position.distanceTo(controls.target);
    const viewingAngle = camera
      .getWorldDirection(v())
      .angleTo(controls.target.clone().sub(camera.position));
    pointer(canvas, 'pointermove', { clientX: 500 });
    controls.update(10);
    pointer(canvas, 'pointerup', { clientX: 500 });
    expect(Math.abs(camera.position.x)).toBeCloseTo(1, 5);
    expect(camera.position.distanceTo(controls.target)).toBeCloseTo(radius, 8);
    expect(
      camera.getWorldDirection(v()).angleTo(controls.target.clone().sub(camera.position)),
    ).toBeCloseTo(viewingAngle, 6);
    expectVector(controls.target, v());
  });

  it.each([1 / 60, 10])(
    'stops an orbit before a cloud point while preserving its radius with %ss steps',
    (dt) => {
      const obstacle = v(-10 * Math.sin(0.1), 0, 10 * Math.cos(0.1));
      const { canvas, camera, controls } = controlsFixture(new Float32Array(obstacle.toArray()));
      pointer(canvas, 'pointerdown');
      pointer(canvas, 'pointermove', { clientX: 500 });
      pointer(canvas, 'pointerup', { clientX: 500 });
      for (let i = 0; i < 180; i++) controls.update(dt);
      expect(camera.position.distanceTo(controls.target)).toBeCloseTo(10, 8);
      expect(camera.position.distanceTo(obstacle)).toBeGreaterThanOrEqual(0.01 - 1e-6);
      const angle = Math.atan2(-camera.position.x, camera.position.z);
      expect(angle).toBeGreaterThan(0.098);
      expect(angle).toBeLessThan(0.1);
      expectVector(
        camera.getWorldDirection(v()),
        controls.target.clone().sub(camera.position).normalize(),
      );
    },
  );

  it('does not tunnel through a cloud point during a large damped zoom', () => {
    const { canvas, camera, controls } = controlsFixture(new Float32Array([0, 0, 5]));
    canvas.dispatchEvent(
      new WheelEvent('wheel', {
        ctrlKey: true,
        deltaY: -2000,
        clientX: 400,
        clientY: 300,
      }),
    );
    settle(controls);
    expect(camera.position.z).toBeGreaterThan(5.01);
    expect(camera.position.z).toBeLessThan(5.02);
  });

  it('stops an external camera flight before the first obstacle when given its previous position', () => {
    const { camera, controls } = controlsFixture(new Float32Array([0, 0, 5, 0, 0, 8]));
    const previous = camera.position.clone();
    camera.position.set(0, 0, -10);
    controls.sync(previous);
    expect(camera.position.z).toBeGreaterThan(8.01);
    expect(camera.position.z).toBeLessThan(8.02);
    expectVector(previous, v(0, 0, 10));
    expectVector(camera.getWorldDirection(v()), v(0, 0, -1));
  });

  it('clamps and reaims an adopted camera pose while clearing old navigation inertia', () => {
    const { canvas, camera, controls } = controlsFixture(
      new Float32Array(),
      v(),
      new Box3(v(-5, -5, -5), v(5, 5, 5)),
    );
    canvas.dispatchEvent(new WheelEvent('wheel', { shiftKey: true, deltaX: 30, deltaY: 60 }));
    camera.position.set(20, -20, 20);
    controls.sync();
    expectVector(camera.position, v(5, -5, 5));
    expectVector(
      camera.getWorldDirection(v()),
      controls.target.clone().sub(camera.position).normalize(),
    );
    expect(controls.update(1 / 60)).toBe(false);
    expectVector(camera.position, v(5, -5, 5));
  });

  it('applies point collision limits to toolbar zoom', () => {
    const { camera, controls } = controlsFixture(new Float32Array([0, 0, 5]));
    for (let i = 0; i < 3; i++) {
      controls.zoom(0.1);
      settle(controls);
      expect(camera.position.z).toBeGreaterThan(5.01);
      expect(camera.position.z).toBeLessThan(5.02);
    }
  });

  it('keeps toolbar zoom advancing through close dolly inputs without collapsing the pivot', () => {
    const { camera, controls } = controlsFixture(new Float32Array());
    for (let i = 0; i < 40; i++) {
      const previousZ = camera.position.z;
      controls.zoom(0.5);
      settle(controls);
      expect(camera.position.z).toBeLessThan(previousZ);
      expect(camera.position.distanceTo(controls.target)).toBeGreaterThanOrEqual(0.1 - 1e-8);
    }
    expect(camera.position.z).toBeLessThan(-5);
    expect(controls.target.z).toBeLessThan(camera.position.z);
  });

  it('ignores input while disabled and removes listeners on dispose', () => {
    const { canvas, camera, controls, onStart } = controlsFixture();
    const initialPosition = camera.position.clone();
    controls.enabled = false;
    pointer(canvas, 'pointerdown');
    pointer(canvas, 'pointermove', { clientX: 550 });
    const disabledPinch = new WheelEvent('wheel', {
      deltaY: -12,
      ctrlKey: true,
      cancelable: true,
    });
    canvas.dispatchEvent(disabledPinch);
    expect(disabledPinch.defaultPrevented).toBe(true);
    settle(controls);
    expectVector(camera.position, initialPosition);
    // The engine may use onStart to enable controls when leaving a video pose.
    onStart.mockClear();
    controls.enabled = true;
    controls.dispose();
    pointer(canvas, 'pointerdown');
    pointer(canvas, 'pointermove', { clientX: 550 });
    const wheel = new WheelEvent('wheel', { deltaY: -120, cancelable: true });
    canvas.dispatchEvent(wheel);
    settle(controls);
    expect(wheel.defaultPrevented).toBe(false);
    expectVector(camera.position, initialPosition);
    expect(onStart).not.toHaveBeenCalled();
  });

  it('releases capture on pointercancel and accepts a new drag afterward', () => {
    const { canvas, camera, controls } = controlsFixture();
    pointer(canvas, 'pointerdown');
    expect(canvas.setPointerCapture).toHaveBeenCalledWith(1);
    pointer(canvas, 'pointercancel');
    expect(canvas.releasePointerCapture).toHaveBeenCalledWith(1);
    pointer(canvas, 'pointermove', { clientX: 550 });
    settle(controls);
    expectVector(camera.position, v(0, 0, 10));
    pointer(canvas, 'pointerdown');
    pointer(canvas, 'pointermove', { clientX: 550 });
    pointer(canvas, 'pointerup');
    settle(controls);
    expect(camera.position.distanceTo(v(0, 0, 10))).toBeGreaterThan(0.1);
    expect(canvas.setPointerCapture).toHaveBeenCalledTimes(2);
  });

  it('registers no double-click listener and preserves existing touch-action on dispose', () => {
    const canvas = document.createElement('canvas');
    canvas.style.touchAction = 'pan-x';
    const add = vi.spyOn(canvas, 'addEventListener');
    const remove = vi.spyOn(canvas, 'removeEventListener');
    const controls = new NavigationControls(new PerspectiveCamera(), canvas, {
      points: new Float32Array(),
      bounds: new Box3(v(-10, -10, -10), v(10, 10, 10)),
    });
    controlsToDispose.push(controls);
    expect(add.mock.calls.some(([name]) => name === 'dblclick')).toBe(false);
    controls.dispose();
    for (const [name, listener] of add.mock.calls) {
      expect(remove).toHaveBeenCalledWith(name, listener);
    }
    expect(canvas.style.touchAction).toBe('pan-x');
  });
});
