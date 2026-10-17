import { hasRgb, type Bundle } from '../lib/bundle';
import type { ColorMode } from './engine';
import { Box3, Vector3 } from 'three';
import { frameBounds, type Bounds } from '../lib/geometry';

interface DefaultViewState {
  mode: 'orbit';
  colorMode: ColorMode;
  buildUp: boolean;
  frustums: boolean;
  showFarLayer: boolean;
}

/** Only explicit user choices should be passed as saved; missing storage is no preference. */
export function defaultViewState(bundle: Bundle, saved?: ColorMode): DefaultViewState {
  return {
    mode: 'orbit',
    colorMode: hasRgb(bundle) ? (saved ?? 'rgb') : 'height',
    buildUp: false,
    frustums: false,
    showFarLayer: false,
  };
}

/** Shared initial/reset framing, fitted within navigation room without clipping corners. */
export function initialFraming(bounds: Bounds, box: Box3, margin: number, aspect: number) {
  const framed = frameBounds(bounds, 50, aspect);
  const target = new Vector3(...framed.target);
  const offset = new Vector3(...framed.position).sub(target);
  const expanded = box.clone().expandByScalar(margin);
  let distance = offset.length();
  if (expanded.containsPoint(target))
    for (let axis = 0; axis < 3; axis++)
      distance = Math.min(
        distance,
        target.getComponent(axis) - expanded.min.getComponent(axis),
        expanded.max.getComponent(axis) - target.getComponent(axis),
      );
  distance = Math.max(bounds.radius * 0.002, distance * 0.999);
  const direction = offset.normalize();
  const right = new Vector3().crossVectors(new Vector3(0, 0, 1), direction).normalize();
  const up = new Vector3().crossVectors(direction, right);
  let tanHalfFov = Math.tan((50 * Math.PI) / 360);
  for (const x of [bounds.min[0], bounds.max[0]])
    for (const y of [bounds.min[1], bounds.max[1]])
      for (const z of [bounds.min[2], bounds.max[2]]) {
        const corner = new Vector3(x, y, z).sub(target);
        const depth = Math.max(1e-12, distance - corner.dot(direction));
        tanHalfFov = Math.max(
          tanHalfFov,
          (1.1 * Math.abs(corner.dot(up))) / depth,
          (1.1 * Math.abs(corner.dot(right))) / (depth * aspect),
        );
      }
  return {
    position: target.clone().addScaledVector(direction, distance),
    target,
    fov: (Math.atan(tanHalfFov) * 360) / Math.PI,
  };
}
