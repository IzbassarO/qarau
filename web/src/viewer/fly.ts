import { Box3, Matrix4, Quaternion, Vector3 } from 'three';
import { clampToBounds } from './navigation';

/** Z-up flight; yaw zero faces +Y, positive pitch looks upward. Angles are radians. */
export interface FlyState {
  position: Vector3;
  yaw: number;
  pitch: number;
  velocity: Vector3;
}

export interface FlyKeys {
  forward?: boolean;
  backward?: boolean;
  left?: boolean;
  right?: boolean;
  boost?: boolean;
}

export interface FlyOptions {
  /** World units per second; the engine scales this to the point-cloud bounds. */
  baseSpeed: number;
  boostMultiplier: number;
  /** Exponential convergence rate in inverse seconds; zero disables smoothing. */
  damping: number;
  bounds?: Box3;
  /** Use the same world-space expansion as orbit navigation. */
  boundsMargin?: number;
}

const PITCH_LIMIT = (89 * Math.PI) / 180;
const worldUp = new Vector3(0, 0, 1);
const clampPitch = (pitch: number) => Math.max(-PITCH_LIMIT, Math.min(PITCH_LIMIT, pitch));
const copyState = (state: FlyState): FlyState => ({
  ...state,
  position: state.position.clone(),
  velocity: state.velocity.clone(),
});

/** Adopt the current direction, including exact poles; only mouse look clamps pitch. */
export function createFlyState(
  position: Vector3,
  direction: Vector3,
  cameraRight?: Vector3,
): FlyState {
  const forward = direction.clone().normalize();
  // Near a pole, tiny residual XY components do not define a useful heading.
  // The existing screen-right axis preserves the stable top/bottom orientation.
  const poleHeading =
    forward.x ** 2 + forward.y ** 2 < 1e-8 &&
    cameraRight &&
    cameraRight.x ** 2 + cameraRight.y ** 2 > 1e-12;
  return {
    position: position.clone(),
    yaw: poleHeading ? Math.atan2(cameraRight.y, cameraRight.x) : Math.atan2(-forward.x, forward.y),
    pitch: Math.asin(Math.max(-1, Math.min(1, forward.z))),
    velocity: new Vector3(),
  };
}

export function flyDirection(state: Pick<FlyState, 'yaw' | 'pitch'>): Vector3 {
  const horizontal = Math.cos(state.pitch);
  return new Vector3(
    -Math.sin(state.yaw) * horizontal,
    Math.cos(state.yaw) * horizontal,
    Math.sin(state.pitch),
  );
}

/** Three.js cameras look along their local -Z axis. */
export function flyOrientation(state: Pick<FlyState, 'yaw' | 'pitch'>): Quaternion {
  const rotation = new Matrix4().lookAt(new Vector3(), flyDirection(state), worldUp);
  return new Quaternion().setFromRotationMatrix(rotation);
}

/** Mouse deltas are pixels; moving right/down turns right/down. */
export function applyMouseLook(
  state: FlyState,
  dx: number,
  dy: number,
  sensitivity: number,
): FlyState {
  const next = copyState(state);
  next.yaw -= dx * sensitivity;
  next.pitch = clampPitch(next.pitch - dy * sensitivity);
  return next;
}

/**
 * Analytically integrate the damped velocity, independent of the frame rate.
 * A resumed tab advances at most one second; invalid/nonpositive dt does not move.
 */
export function step(state: FlyState, keys: FlyKeys, dt: number, options: FlyOptions): FlyState {
  const next = copyState(state);
  const seconds = Number.isFinite(dt) ? Math.max(0, Math.min(1, dt)) : 0;
  if (!seconds) return next;

  const forwardInput = Number(!!keys.forward) - Number(!!keys.backward);
  const strafeInput = Number(!!keys.right) - Number(!!keys.left);
  const right = new Vector3(Math.cos(state.yaw), Math.sin(state.yaw), 0);
  const desired = flyDirection(state)
    .multiplyScalar(forwardInput)
    .addScaledVector(right, strafeInput);
  const speed = Math.max(0, options.baseSpeed) * (keys.boost ? options.boostMultiplier : 1);
  desired.normalize().multiplyScalar(Math.max(0, speed));
  const damping = Math.max(0, options.damping);

  if (damping > 0) {
    const approach = -Math.expm1(-damping * seconds);
    const residual = state.velocity.clone().sub(desired);
    next.position.addScaledVector(desired, seconds).addScaledVector(residual, approach / damping);
    next.velocity
      .copy(state.velocity)
      .addScaledVector(desired.clone().sub(state.velocity), approach);
  } else {
    next.velocity.copy(desired);
    next.position.addScaledVector(desired, seconds);
  }

  if (options.bounds) {
    const bounded = clampToBounds(next.position, options.bounds, options.boundsMargin ?? 0);
    // Do not retain momentum into a boundary after it stops a flight.
    for (let axis = 0; axis < 3; axis++)
      if (bounded.getComponent(axis) !== next.position.getComponent(axis))
        next.velocity.setComponent(axis, 0);
    next.position.copy(bounded);
  }
  if (next.velocity.lengthSq() < 1e-16) next.velocity.set(0, 0, 0);
  return next;
}
