import { describe, expect, it } from 'vitest';
import { OrthographicCamera, PerspectiveCamera, Vector3 } from 'three';
import {
  boxFromPointsInRect,
  containsPoint,
  filterMask,
  parse,
  screenRectToBox,
  serialize,
  type Roi,
  type RoiBox,
  type UpAxis,
} from './roi';

const viewport = { width: 100, height: 100 };
const start = { x: 25, y: 25 };
const end = { x: 75, y: 75 };
const rectangle: RoiBox = { min: [-1, -1, 0], max: [1, 1, 0] };

function topCamera(up: UpAxis = 'z') {
  const camera = new PerspectiveCamera(90, 1, 0.1, 100);
  camera.position.set(up === 'x' ? 10 : 0, up === 'y' ? 10 : 0, up === 'z' ? 10 : 0);
  camera.up.copy(up === 'z' ? new Vector3(0, 1, 0) : new Vector3(0, 0, 1));
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld();
  return camera;
}

function expectBox(actual: RoiBox | null, expected: RoiBox) {
  expect(actual).not.toBeNull();
  for (let axis = 0; axis < 3; axis++) {
    expect(actual!.min[axis]).toBeCloseTo(expected.min[axis], 10);
    expect(actual!.max[axis]).toBeCloseTo(expected.max[axis], 10);
  }
}

describe('screenRectToBox', () => {
  it('unprojects a top-view rectangle in CSS pixels onto the ground plane', () => {
    expectBox(screenRectToBox(topCamera(), viewport, start, end), {
      min: [-5, -5, 0],
      max: [5, 5, 0],
    });
  });

  it('normalises a reversed drag without changing the camera or input points', () => {
    const camera = topCamera();
    const matrices = [camera.matrixWorld.clone(), camera.projectionMatrix.clone()];
    expect(screenRectToBox(camera, viewport, end, start)).toEqual(
      screenRectToBox(camera, viewport, start, end),
    );
    expect(camera.matrixWorld.equals(matrices[0])).toBe(true);
    expect(camera.projectionMatrix.equals(matrices[1])).toBe(true);
    expect(start).toEqual({ x: 25, y: 25 });
    expect(end).toEqual({ x: 75, y: 75 });
  });

  it.each(['x', 'y', 'z'] as const)('respects the %s up axis and ground height', (up) => {
    const expected: RoiBox = { min: [-2.5, -2.5, -2.5], max: [2.5, 2.5, 2.5] };
    const index = up === 'x' ? 0 : up === 'y' ? 1 : 2;
    expected.min[index] = expected.max[index] = 5;
    expectBox(screenRectToBox(topCamera(up), viewport, start, end, up, 5), expected);
  });

  it('also unprojects an orthographic top view', () => {
    const camera = new OrthographicCamera(-10, 10, 10, -10, 0.1, 100);
    camera.position.set(0, 0, 10);
    camera.updateMatrixWorld();
    expectBox(screenRectToBox(camera, viewport, start, end), {
      min: [-5, -5, 0],
      max: [5, 5, 0],
    });
  });

  it.each([
    { x: 25, y: 25 },
    { x: 25, y: 75 },
    { x: 75, y: 25 },
  ])('rejects a zero-area drag ending at %o', (point) => {
    expect(screenRectToBox(topCamera(), viewport, start, point)).toBeNull();
  });

  it('rejects invalid viewports, coordinates and a plane behind the camera', () => {
    expect(screenRectToBox(topCamera(), { width: 0, height: 100 }, start, end)).toBeNull();
    expect(screenRectToBox(topCamera(), viewport, { x: NaN, y: 25 }, end)).toBeNull();
    expect(screenRectToBox(topCamera(), viewport, start, end, 'z', 20)).toBeNull();
  });

  it('rejects a view with rays parallel to the ground plane', () => {
    const camera = new OrthographicCamera(-10, 10, 10, -10, 0.1, 100);
    camera.position.set(10, 0, 10);
    camera.lookAt(0, 0, 10);
    camera.updateMatrixWorld();
    expect(screenRectToBox(camera, viewport, start, end)).toBeNull();
  });
});

describe('boxFromPointsInRect', () => {
  it('fills the full selected height plus 5% at each end, excluding horizontal outliers', () => {
    const positions = new Float32Array([0, 0, -2, 1, 1, 8, -1, -1, 2, 5, 5, -100]);
    const original = positions.slice();
    expect(boxFromPointsInRect(positions, rectangle)).toEqual({
      min: [-1, -1, -2.5],
      max: [1, 1, 8.5],
    });
    expect(positions).toEqual(original);
    expect(rectangle).toEqual({ min: [-1, -1, 0], max: [1, 1, 0] });
  });

  it('uses the configured up axis and leaves flat clouds finite', () => {
    const rect: RoiBox = { min: [-1, 0, -1], max: [1, 0, 1] };
    expect(boxFromPointsInRect([0, -2, 0, 0, 8, 0, 5, -100, 5], rect, 'y')).toEqual({
      min: [-1, -2.5, -1],
      max: [1, 8.5, 1],
    });
    expect(boxFromPointsInRect([0, 0, 7], rectangle)).toEqual({
      min: [-1, -1, 7],
      max: [1, 1, 7],
    });
  });

  it('rejects an empty selection and ignores non-finite points', () => {
    expect(boxFromPointsInRect([], rectangle)).toBeNull();
    expect(boxFromPointsInRect([5, 5, 2], rectangle)).toBeNull();
    expect(boxFromPointsInRect([0, 0, NaN, Infinity, 0, 2], rectangle)).toBeNull();
    expect(boxFromPointsInRect([0, 0], rectangle)).toBeNull();
  });

  it('rejects zero-area or reversed rectangles and invalid margins', () => {
    expect(boxFromPointsInRect([0, 0, 0], { min: [0, 0, 0], max: [0, 1, 0] })).toBeNull();
    expect(boxFromPointsInRect([0, 0, 0], { min: [1, 0, 0], max: [0, 1, 0] })).toBeNull();
    expect(boxFromPointsInRect([0, 0, 0], rectangle, 'z', -0.05)).toBeNull();
  });
});

describe('containsPoint and filterMask', () => {
  const box: RoiBox = { min: [-1, -2, -3], max: [1, 2, 3] };

  it('includes interior points and the box faces, edges and corners', () => {
    expect(containsPoint(box, [0, 0, 0])).toBe(true);
    expect(containsPoint(box, [-1, 0, 0])).toBe(true);
    expect(containsPoint(box, [1, 2, 0])).toBe(true);
    expect(containsPoint(box, [-1, -2, -3])).toBe(true);
    expect(containsPoint(box, [1, 2, 3])).toBe(true);
    expect(containsPoint(box, [1.001, 0, 0])).toBe(false);
    expect(containsPoint(box, [0, NaN, 0])).toBe(false);
    expect(containsPoint(box, [0, 0])).toBe(false);
  });

  it('returns one mask byte per point without mutating the source buffer', () => {
    const positions = new Float32Array([0, 0, 0, 2, 0, 0, -1, -2, -3, 0, Infinity, 0]);
    const original = positions.slice();
    const mask = filterMask(positions, box);
    expect(mask).toBeInstanceOf(Uint8Array);
    expect([...mask]).toEqual([1, 0, 1, 0]);
    expect(positions).toEqual(original);
    expect([...filterMask([], box)]).toEqual([]);
  });
});

const roi: Roi = {
  version: 1,
  site_id: 'notre-dame',
  source_run: 'notre-dame',
  frame: 'reconstruction',
  up_axis: 'z',
  min: [-1, -2, -3],
  max: [1, 2, 3],
  created_at: '2026-10-17T16:25:00.000Z',
  created_by: 'engineer@example.com',
};

describe('serialize and parse', () => {
  it('round-trips every pipeline field through JSON and decoded responses', () => {
    expect(parse(serialize(roi))).toEqual({ ok: true, value: roi });
    const result = parse(roi);
    expect(result).toEqual({ ok: true, value: roi });
    if (result.ok) {
      result.value.min[0] = -99;
      expect(roi.min[0]).toBe(-1);
    }
  });

  it.each([
    'not json',
    '{',
    null,
    [],
    {},
    { ...roi, version: 2 },
    { ...roi, site_id: '../private' },
    { ...roi, site_id: '..' },
    { ...roi, source_run: '' },
    { ...roi, frame: ' ' },
    { ...roi, created_by: 7 },
    { ...roi, up_axis: 'w' },
    { ...roi, min: [NaN, -2, -3] },
    { ...roi, max: [1, Infinity, 3] },
    { ...roi, min: [2, -2, -3] },
    { ...roi, min: [-1, -2] },
    { ...roi, max: ['1', 2, 3] },
    { ...roi, created_at: 'yesterday' },
    { ...roi, created_at: '2026-99-99T00:00:00Z' },
    { ...roi, created_at: '2026-02-29T00:00:00Z' },
    { ...roi, created_at: '2026-02-31T00:00:00Z' },
    { ...roi, created_at: '2026-10-17T24:00:00Z' },
  ])('returns an error for malformed input %#', (value) => {
    const result = parse(value);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error.length).toBeGreaterThan(0);
  });

  it.each(Object.keys(roi))('rejects the missing required field %s', (key) => {
    const incomplete: Record<string, unknown> = { ...roi };
    delete incomplete[key];
    expect(parse(incomplete).ok).toBe(false);
  });

  it('refuses to serialize NaN instead of silently converting it to null', () => {
    expect(() => serialize({ ...roi, min: [NaN, -2, -3] })).toThrow();
  });

  it('accepts leap days and explicit timezone offsets', () => {
    expect(parse({ ...roi, created_at: '2028-02-29T16:25:00+05:00' }).ok).toBe(true);
  });
});
