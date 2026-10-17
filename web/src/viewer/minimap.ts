export interface MinimapPoint {
  x: number;
  y: number;
}

export interface MinimapBounds {
  min: MinimapPoint;
  max: MinimapPoint;
}

export interface MinimapTransform {
  scaleX: number;
  scaleY: number;
  worldToPixel: (point: MinimapPoint) => MinimapPoint;
  pixelToWorld: (point: MinimapPoint) => MinimapPoint;
}

/** Z-up top view: +X is right and +Y is up. Coordinates need not be metres. */
export function computeMinimapTransform(bboxXY: MinimapBounds, sizePx: number): MinimapTransform {
  const size = Number.isFinite(sizePx) ? Math.max(0, sizePx) : 0;
  const width = Math.max(0, bboxXY.max.x - bboxXY.min.x);
  const height = Math.max(0, bboxXY.max.y - bboxXY.min.y);
  const scaleX = width > 0 ? size / width : 0;
  const scaleY = height > 0 ? size / height : 0;
  return {
    scaleX,
    scaleY,
    worldToPixel: ({ x, y }) => ({
      x: width > 0 ? (x - bboxXY.min.x) * scaleX : size / 2,
      y: height > 0 ? (bboxXY.max.y - y) * scaleY : size / 2,
    }),
    pixelToWorld: ({ x, y }) => ({
      x: scaleX > 0 ? bboxXY.min.x + x / scaleX : bboxXY.min.x,
      y: scaleY > 0 ? bboxXY.max.y - y / scaleY : bboxXY.min.y,
    }),
  };
}

/**
 * Pixel-space cone, ordered apex/left/right. Yaw and fov are radians; yaw zero
 * faces world +Y (pixel up), and positive yaw faces world -X (pixel left).
 */
export function viewConePolygon(
  pos: MinimapPoint,
  yaw: number,
  fov: number,
  lengthPx: number,
): [MinimapPoint, MinimapPoint, MinimapPoint] {
  const half = Math.max(0, Math.min(Math.PI, fov)) / 2;
  const length = Math.max(0, lengthPx);
  const endpoint = (heading: number): MinimapPoint => ({
    x: pos.x - Math.sin(heading) * length,
    y: pos.y - Math.cos(heading) * length,
  });
  return [{ ...pos }, endpoint(yaw + half), endpoint(yaw - half)];
}
