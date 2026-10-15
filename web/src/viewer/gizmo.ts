import { Matrix4, Quaternion, Vector3 } from 'three';
import { damp } from './navigation';

export type Axis = 'x' | '-x' | 'y' | '-y' | 'z' | '-z';

export interface AxisViewTarget {
  /** Camera-to-pivot offset direction, opposite to the camera's viewing direction. */
  direction: Vector3;
  /** The stable screen-up direction at the target view. */
  up: Vector3;
}

export interface AxisViewState {
  position: Vector3;
  quaternion: Quaternion;
}

/**
 * Positive buttons look from the positive world axis towards the pivot. At a pole,
 * world-up cannot also be screen-up: Z-up uses +Y for top and -Y for bottom. The
 * equivalent Y-up convention uses -Z/+Z, keeping both views upright and deterministic.
 */
export function viewDirectionFor(axis: Axis, upAxis: Vector3): AxisViewTarget {
  const direction = new Vector3();
  const component = axis.endsWith('x') ? 0 : axis.endsWith('y') ? 1 : 2;
  direction.setComponent(component, axis.startsWith('-') ? -1 : 1);
  const worldUp = upAxis.lengthSq() > 0 ? upAxis.clone().normalize() : new Vector3(0, 0, 1);
  const up = worldUp.clone().addScaledVector(direction, -worldUp.dot(direction));
  if (up.lengthSq() < 1e-12) {
    up.copy(Math.abs(worldUp.z) > 0.9 ? new Vector3(0, 1, 0) : new Vector3(0, 0, -1));
    up.addScaledVector(worldUp, -up.dot(worldUp));
    up.multiplyScalar(direction.dot(worldUp) < 0 ? -1 : 1);
  }
  return { direction, up: up.normalize() };
}

/** Three.js cameras look down local -Z; the supplied direction is local +Z. */
export function orientationFor(direction: Vector3, up: Vector3): Quaternion {
  return new Quaternion().setFromRotationMatrix(new Matrix4().lookAt(direction, new Vector3(), up));
}

/**
 * Frame-rate-independent spherical flight around a fixed pivot, without shortening
 * its radius. Camera orientation uses quaternion slerp, including at the poles.
 * A picked, off-centre pivot may not initially be on the camera's viewing ray; a
 * separate radial orientation preserves that initial position without a snap.
 */
export function stepAxisView(
  state: AxisViewState,
  pivot: Vector3,
  target: AxisViewTarget,
  dt: number,
  damping = 10,
): AxisViewState {
  const alpha = damp(0, 1, damping, dt);
  const destination = orientationFor(target.direction, target.up);
  const quaternion = state.quaternion.clone().slerp(destination, alpha).normalize();
  const offset = state.position.clone().sub(pivot);
  const radius = offset.length();
  if (radius === 0) return { position: state.position.clone(), quaternion };
  const cameraBack = new Vector3(0, 0, 1).applyQuaternion(state.quaternion);
  const correction = new Quaternion().setFromUnitVectors(cameraBack, offset.divideScalar(radius));
  const radialOrientation = correction.multiply(state.quaternion).slerp(destination, alpha);
  const position = new Vector3(0, 0, radius).applyQuaternion(radialOrientation).add(pivot);
  return { position, quaternion };
}
