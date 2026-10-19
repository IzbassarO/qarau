import { Box3, Matrix4, Quaternion, Vector3 } from 'three';
import type { Bundle } from '../lib/bundle';
import { nearestPointDistance } from './constraints';
import { clampToBounds, damp, type PointSource } from './navigation';

/** Z-up spectator; yaw zero faces +Y, positive yaw turns toward -X. */
export interface ObserverState {
  position: Vector3;
  yaw: number;
  pitch: number;
  velocity: Vector3;
  targetYaw: number;
  targetPitch: number;
}

export interface ObserverInput {
  forward?: boolean;
  backward?: boolean;
  left?: boolean;
  right?: boolean;
  up?: boolean;
  down?: boolean;
  boost?: boolean;
  /** Accumulated mouse/trackpad pixel deltas for this frame. */
  lookX?: number;
  lookY?: number;
}

export interface ObserverContext {
  bounds: Box3;
  points: PointSource;
  /** Speed per unit of nearest-point distance. */
  distanceFactor?: number;
  /** World units/second; defaults are derived from the bounding-box diagonal. */
  minSpeed?: number;
  maxSpeed?: number;
  boostMultiplier?: number;
  /** Exponential rates; zero applies input immediately. */
  damping?: number;
  lookDamping?: number;
  sensitivity?: number;
  /** Only the loose outer box applies; no ground or point collision constraints. */
  boundsMargin?: number;
}

const PITCH_LIMIT = (89 * Math.PI) / 180;
const DEFAULT_DISTANCE_FACTOR = 0.8;
// Six percent of the scene diagonal per second keeps short WASD presses visible.
const MIN_SPEED_DIAGONAL_FRACTION = 0.06;
// Limit normal travel to half a scene per second, and cap very large scenes before boost.
const MAX_SPEED_DIAGONAL_FRACTION = 0.5;
const MAX_DEFAULT_SPEED = 100;
const DEFAULT_BOOST_MULTIPLIER = 4;
const WORLD_UP = new Vector3(0, 0, 1);
const clampPitch = (pitch: number) => Math.max(-PITCH_LIMIT, Math.min(PITCH_LIMIT, pitch));
const finite = (value: number | undefined, fallback: number) =>
  value !== undefined && Number.isFinite(value) ? value : fallback;

export function createObserverState(
  position: Vector3,
  direction: Vector3,
  cameraRight?: Vector3,
): ObserverState {
  const forward = direction.clone().normalize();
  const poleHeading =
    forward.x ** 2 + forward.y ** 2 < 1e-8 &&
    cameraRight &&
    cameraRight.x ** 2 + cameraRight.y ** 2 > 1e-12;
  const yaw = poleHeading
    ? Math.atan2(cameraRight.y, cameraRight.x)
    : Math.atan2(-forward.x, forward.y);
  const pitch = clampPitch(Math.asin(Math.max(-1, Math.min(1, forward.z))));
  return {
    position: position.clone(),
    yaw,
    pitch,
    velocity: new Vector3(),
    targetYaw: yaw,
    targetPitch: pitch,
  };
}

export function observerDirection(state: Pick<ObserverState, 'yaw' | 'pitch'>): Vector3 {
  const horizontal = Math.cos(state.pitch);
  return new Vector3(
    -Math.sin(state.yaw) * horizontal,
    Math.cos(state.yaw) * horizontal,
    Math.sin(state.pitch),
  );
}

/** Three.js cameras look along their local -Z axis. */
export function observerOrientation(state: Pick<ObserverState, 'yaw' | 'pitch'>): Quaternion {
  return new Quaternion().setFromRotationMatrix(
    new Matrix4().lookAt(new Vector3(), observerDirection(state), WORLD_UP),
  );
}

/** Pure spectator integration: smooth input, no gravity, no collision or ground clamp. */
export function step(
  state: ObserverState,
  input: ObserverInput,
  dt: number,
  ctx: ObserverContext,
): ObserverState {
  const next = { ...state, position: state.position.clone(), velocity: state.velocity.clone() };
  const seconds = Number.isFinite(dt) ? Math.max(0, Math.min(1, dt)) : 0;
  if (!seconds) return next;

  const sensitivity = Math.max(0, finite(ctx.sensitivity, 0.002));
  next.targetYaw -= finite(input.lookX, 0) * sensitivity;
  next.targetPitch = clampPitch(next.targetPitch - finite(input.lookY, 0) * sensitivity);
  const lookDamping = Math.max(0, finite(ctx.lookDamping, 16));
  next.yaw = lookDamping ? damp(state.yaw, next.targetYaw, lookDamping, seconds) : next.targetYaw;
  next.pitch = clampPitch(
    lookDamping ? damp(state.pitch, next.targetPitch, lookDamping, seconds) : next.targetPitch,
  );

  const diagonal = ctx.bounds.isEmpty()
    ? 1
    : Math.max(ctx.bounds.getSize(new Vector3()).length(), 1e-6);
  const defaultMaxSpeed = Math.min(diagonal * MAX_SPEED_DIAGONAL_FRACTION, MAX_DEFAULT_SPEED);
  // Nearest-point speed vanishes on geometry; A9's 0.5%-diagonal floor still barely moved.
  // Cap the floor too, so a huge scene cannot force speed above the default maximum.
  const minSpeed = Math.max(
    0,
    finite(ctx.minSpeed, Math.min(diagonal * MIN_SPEED_DIAGONAL_FRACTION, defaultMaxSpeed)),
  );
  const maxSpeed = Math.max(minSpeed, finite(ctx.maxSpeed, defaultMaxSpeed));
  const distance = nearestPointDistance(ctx.points, state.position);
  const factor = Math.max(0, finite(ctx.distanceFactor, DEFAULT_DISTANCE_FACTOR));
  const adaptiveSpeed = Math.min(
    maxSpeed,
    Math.max(minSpeed, Number.isFinite(distance) ? distance * factor : maxSpeed),
  );
  const speed =
    adaptiveSpeed *
    (input.boost ? Math.max(1, finite(ctx.boostMultiplier, DEFAULT_BOOST_MULTIPLIER)) : 1);
  const forwardInput = Number(!!input.forward) - Number(!!input.backward);
  const strafeInput = Number(!!input.right) - Number(!!input.left);
  const verticalInput = Number(!!input.up) - Number(!!input.down);
  // Horizontal travel intentionally ignores pitch, including a straight-down look.
  const desired = new Vector3(
    -Math.sin(next.yaw) * forwardInput + Math.cos(next.yaw) * strafeInput,
    Math.cos(next.yaw) * forwardInput + Math.sin(next.yaw) * strafeInput,
    verticalInput,
  )
    .normalize()
    .multiplyScalar(speed);

  // Integrate exponential velocity analytically to keep damping frame-rate independent.
  const damping = Math.max(0, finite(ctx.damping, 10));
  if (damping) {
    const approach = -Math.expm1(-damping * seconds);
    const residual = state.velocity.clone().sub(desired);
    next.position.addScaledVector(desired, seconds).addScaledVector(residual, approach / damping);
    next.velocity.set(
      damp(state.velocity.x, desired.x, damping, seconds),
      damp(state.velocity.y, desired.y, damping, seconds),
      damp(state.velocity.z, desired.z, damping, seconds),
    );
  } else {
    next.velocity.copy(desired);
    next.position.addScaledVector(desired, seconds);
  }

  const bounded = clampToBounds(
    next.position,
    ctx.bounds,
    Math.max(0, finite(ctx.boundsMargin, diagonal * 10)),
  );
  for (let axis = 0; axis < 3; axis++)
    if (bounded.getComponent(axis) !== next.position.getComponent(axis))
      next.velocity.setComponent(axis, 0);
  next.position.copy(bounded);
  if (next.velocity.lengthSq() < 1e-16) next.velocity.set(0, 0, 0);
  return next;
}

/** A human is meaningful only after the bundle explicitly declares metric calibration. */
export function canShowHumanFigure(bundle: Pick<Bundle, 'manifest'>): boolean {
  const scale = bundle.manifest.world.scale_m_per_unit;
  return typeof scale === 'number' && Number.isFinite(scale) && scale > 0;
}
