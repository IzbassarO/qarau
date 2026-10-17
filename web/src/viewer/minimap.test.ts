import { describe, expect, it } from 'vitest';
import { computeMinimapTransform, viewConePolygon } from './minimap';

describe('computeMinimapTransform', () => {
  const bounds = { min: { x: -10, y: 20 }, max: { x: 30, y: 100 } };

  it('maps all bounding-box corners to the matching top-view corners', () => {
    const { worldToPixel } = computeMinimapTransform(bounds, 180);
    expect(worldToPixel({ x: -10, y: 100 })).toEqual({ x: 0, y: 0 });
    expect(worldToPixel({ x: 30, y: 100 })).toEqual({ x: 180, y: 0 });
    expect(worldToPixel({ x: -10, y: 20 })).toEqual({ x: 0, y: 180 });
    expect(worldToPixel({ x: 30, y: 20 })).toEqual({ x: 180, y: 180 });
  });

  it('preserves interior and outside positions in both directions', () => {
    const transform = computeMinimapTransform(bounds, 180);
    for (const point of [
      { x: 10, y: 60 },
      { x: -50, y: 180 },
    ]) {
      const result = transform.pixelToWorld(transform.worldToPixel(point));
      expect(result.x).toBeCloseTo(point.x);
      expect(result.y).toBeCloseTo(point.y);
    }
  });

  it('centres a flat dimension without producing invalid coordinates', () => {
    const transform = computeMinimapTransform({ min: { x: 5, y: 20 }, max: { x: 5, y: 100 } }, 180);
    expect(transform.worldToPixel({ x: 5, y: 60 })).toEqual({ x: 90, y: 90 });
    expect(transform.pixelToWorld({ x: 90, y: 90 })).toEqual({ x: 5, y: 60 });
  });
});

describe('viewConePolygon', () => {
  it('anchors its apex at the observer and points along the yaw heading', () => {
    const pos = { x: 73, y: 91 };
    for (const yaw of [0, Math.PI / 2, Math.PI, -Math.PI / 2]) {
      const [apex, left, right] = viewConePolygon(pos, yaw, Math.PI / 3, 30);
      expect(apex).toEqual(pos);
      expect(apex).not.toBe(pos);
      const middle = { x: (left.x + right.x) / 2, y: (left.y + right.y) / 2 };
      expect(middle.x - pos.x).toBeCloseTo(-Math.sin(yaw) * 30 * Math.cos(Math.PI / 6));
      expect(middle.y - pos.y).toBeCloseTo(-Math.cos(yaw) * 30 * Math.cos(Math.PI / 6));
    }
  });

  it('uses the requested field of view and pixel length', () => {
    const [apex, left, right] = viewConePolygon({ x: 90, y: 90 }, 0, Math.PI / 2, 40);
    expect(left.x).toBeLessThan(apex.x);
    expect(right.x).toBeGreaterThan(apex.x);
    expect(left.y).toBeCloseTo(right.y);
    expect(Math.hypot(left.x - apex.x, left.y - apex.y)).toBeCloseTo(40);
    expect(Math.hypot(right.x - apex.x, right.y - apex.y)).toBeCloseTo(40);
    const a = { x: left.x - apex.x, y: left.y - apex.y };
    const b = { x: right.x - apex.x, y: right.y - apex.y };
    expect(a.x * b.x + a.y * b.y).toBeCloseTo(0);
  });
});
