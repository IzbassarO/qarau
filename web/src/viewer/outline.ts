import type { Polygon } from '../lib/findings';

export interface ContainFit {
  scale: number;
  offsetX: number;
  offsetY: number;
}

/** Centre the unmodified source image, using the same transform as object-fit: contain. */
export function fitContain(frameW: number, frameH: number, boxW: number, boxH: number): ContainFit {
  if (![frameW, frameH, boxW, boxH].every((n) => Number.isFinite(n) && n > 0))
    return { scale: 0, offsetX: 0, offsetY: 0 };
  const scale = Math.min(boxW / frameW, boxH / frameH);
  return { scale, offsetX: (boxW - frameW * scale) / 2, offsetY: (boxH - frameH * scale) / 2 };
}

export function polygonToSvgPoints(polygon: Polygon, fit: ContainFit): string {
  return polygon
    .map(([u, v]) => `${u * fit.scale + fit.offsetX},${v * fit.scale + fit.offsetY}`)
    .join(' ');
}
