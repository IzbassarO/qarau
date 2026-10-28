import type { Polygon } from './findings';

export interface ContainFit {
  scale: number;
  offsetX: number;
  offsetY: number;
}

/** Centre the unmodified source image, using the same transform as object-fit: contain. */
export function containFit(frameW: number, frameH: number, boxW: number, boxH: number): ContainFit {
  if (![frameW, frameH, boxW, boxH].every((n) => Number.isFinite(n) && n > 0))
    return { scale: 0, offsetX: 0, offsetY: 0 };
  const scale = Math.min(boxW / frameW, boxH / frameH);
  return { scale, offsetX: (boxW - frameW * scale) / 2, offsetY: (boxH - frameH * scale) / 2 };
}

export function mapPoint([u, v]: readonly number[], fit: ContainFit): [number, number] {
  return [u * fit.scale + fit.offsetX, v * fit.scale + fit.offsetY];
}

export function polygonPoints(polygon: Polygon, fit: ContainFit): string {
  return polygon.map((point) => mapPoint(point, fit).join(',')).join(' ');
}
