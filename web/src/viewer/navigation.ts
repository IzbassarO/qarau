import { Box3, PerspectiveCamera, Quaternion, Ray, Spherical, Vector3 } from 'three';
import { classifyWheel, wheelStep } from './input';

/** Positions and pivots are world-space; fov is the vertical field of view in degrees. */
export interface NavigationState {
  position: Vector3;
  pivot: Vector3;
  up: Vector3;
  minDistance: number;
  fov: number;
  /** Keeps an off-centre picked pivot from snapping the viewing direction. */
  rotation?: Quaternion;
}

export enum NavigationAction {
  None = 'none',
  Orbit = 'orbit',
  Pan = 'pan',
  Zoom = 'zoom',
}

export interface NavigationInput {
  type: string;
  button?: number;
  shiftKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  metaKey?: boolean;
  deltaMode?: number;
  deltaX?: number;
  deltaY?: number;
}

/** Keep mouse buttons unchanged; delegate wheel hardware inference to input.ts. */
export function mapInput(
  event: NavigationInput,
  knownMouseSteps: readonly number[] = [],
): NavigationAction {
  if (event.type === 'wheel') {
    const action = classifyWheel(event, knownMouseSteps);
    return action === 'trackpad-pan'
      ? NavigationAction.Pan
      : action === 'trackpad-orbit'
        ? NavigationAction.Orbit
        : NavigationAction.Zoom;
  }
  if (event.type.startsWith('pointer') || event.type.startsWith('mouse')) {
    if (event.button === 1) return NavigationAction.Pan;
    if (event.button === 0) return event.shiftKey ? NavigationAction.Pan : NavigationAction.Orbit;
  }
  return NavigationAction.None;
}

/** A scene-relative multiplier shared by orbit, pan and zoom. */
export function speedScale(
  distanceToTarget: number,
  sceneRadius: number,
  { precision = false, sensitivity = 1 }: { precision?: boolean; sensitivity?: number } = {},
): number {
  const distance = Number.isFinite(distanceToTarget) ? Math.max(0, distanceToTarget) : 0;
  const radius = Number.isFinite(sceneRadius) ? Math.max(sceneRadius, 1e-9) : 1;
  const distanceScale = Math.max(0.05, Math.min(1, distance / radius));
  const setting = Number.isFinite(sensitivity) ? Math.max(0.25, Math.min(4, sensitivity)) : 1;
  return distanceScale * setting * (precision ? 0.25 : 1);
}

type VisitPoint = (x: number, y: number, z: number) => void;
export type PointSource = ArrayLike<number> | readonly Vector3[] | PointCloudIndex;
interface PointCell {
  bounds: Box3;
  indices: number[];
}
interface CellNode {
  bounds: Box3;
  cells?: PointCell[];
  children?: [CellNode, CellNode];
}

function groupCells(cells: PointCell[]): CellNode {
  const bounds = new Box3();
  for (const cell of cells) bounds.union(cell.bounds);
  if (cells.length <= 8) return { bounds, cells };
  const size = bounds.getSize(new Vector3());
  const axis = size.x >= size.y && size.x >= size.z ? 0 : size.y >= size.z ? 1 : 2;
  cells.sort(
    (a, b) =>
      a.bounds.min.getComponent(axis) +
      a.bounds.max.getComponent(axis) -
      b.bounds.min.getComponent(axis) -
      b.bounds.max.getComponent(axis),
  );
  const middle = Math.floor(cells.length / 2);
  return {
    bounds,
    children: [groupCells(cells.slice(0, middle)), groupCells(cells.slice(middle))],
  };
}

/**
 * Full-cloud spatial grid with a hierarchy over its cells, built once. Only cells
 * intersecting the swept ray are visited; no nearest obstacle is downsampled away.
 * Cell size uses a bounded percentile sample so a few reconstruction outliers
 * cannot collapse the useful cloud into one cell. Outliers retain their own cells.
 */
export class PointCloudIndex {
  private root: CellNode;

  constructor(private positions: ArrayLike<number>) {
    const bounds = new Box3();
    const point = new Vector3();
    const stride = Math.max(1, Math.ceil(positions.length / 3 / 4096));
    for (let axis = 0; axis < 3; axis++) {
      const sample: number[] = [];
      for (let i = axis; i < positions.length; i += stride * 3)
        if (Number.isFinite(positions[i])) sample.push(positions[i]);
      sample.sort((a, b) => a - b);
      if (sample.length) {
        bounds.min.setComponent(axis, sample[Math.floor((sample.length - 1) * 0.02)]);
        bounds.max.setComponent(axis, sample[Math.ceil((sample.length - 1) * 0.98)]);
      }
    }
    const size = bounds.isEmpty() ? new Vector3() : bounds.getSize(new Vector3());
    const cellSize = Math.max(size.x, size.y, size.z, 1e-6) / 32;
    const cells = new Map<number | string, PointCell>();
    for (let i = 0; i + 2 < positions.length; i += 3) {
      const x = positions[i],
        y = positions[i + 1],
        z = positions[i + 2];
      if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) continue;
      const ix = Math.floor((x - bounds.min.x) / cellSize);
      const iy = Math.floor((y - bounds.min.y) / cellSize);
      const iz = Math.floor((z - bounds.min.z) / cellSize);
      // Avoid allocating millions of string keys for the normal cloud. A separate
      // string namespace retains arbitrarily distant outlier cells without aliasing.
      const key =
        ix >= -32 && ix < 64 && iy >= -32 && iy < 64 && iz >= -32 && iz < 64
          ? (ix + 32) * 96 * 96 + (iy + 32) * 96 + iz + 32
          : `${ix},${iy},${iz}`;
      let cell = cells.get(key);
      if (!cell) {
        cell = { bounds: new Box3(), indices: [] };
        cells.set(key, cell);
      }
      cell.indices.push(i);
      cell.bounds.expandByPoint(point.set(x, y, z));
    }
    this.root = groupCells([...cells.values()]);
  }

  /** Exact nearest distance, pruning cells farther away than the best point found. */
  nearestDistance(position: Vector3): number {
    let nearestSq = Infinity;
    const distanceSq = (bounds: Box3) => {
      if (bounds.isEmpty()) return Infinity;
      const x = Math.max(bounds.min.x - position.x, 0, position.x - bounds.max.x);
      const y = Math.max(bounds.min.y - position.y, 0, position.y - bounds.max.y);
      const z = Math.max(bounds.min.z - position.z, 0, position.z - bounds.max.z);
      return x * x + y * y + z * z;
    };
    const traverse = (node: CellNode) => {
      if (distanceSq(node.bounds) >= nearestSq) return;
      if (node.children) {
        const [a, b] = node.children;
        const ordered = distanceSq(a.bounds) <= distanceSq(b.bounds) ? [a, b] : [b, a];
        traverse(ordered[0]);
        traverse(ordered[1]);
      } else if (node.cells) {
        for (const cell of node.cells) {
          if (distanceSq(cell.bounds) >= nearestSq) continue;
          for (const i of cell.indices) {
            const x = this.positions[i] - position.x;
            const y = this.positions[i + 1] - position.y;
            const z = this.positions[i + 2] - position.z;
            nearestSq = Math.min(nearestSq, x * x + y * y + z * z);
          }
        }
      }
    };
    traverse(this.root);
    return Math.sqrt(nearestSq);
  }

  forEachNearRay(ray: Ray, radius: number, distance: number, visit: VisitPoint) {
    const box = new Box3();
    const hit = new Vector3();
    const intersects = (bounds: Box3) => {
      if (bounds.isEmpty()) return false;
      box.copy(bounds).expandByScalar(radius);
      if (!box.containsPoint(ray.origin)) {
        if (!ray.intersectBox(box, hit) || hit.distanceTo(ray.origin) > distance) return false;
      }
      return true;
    };
    const traverse = (node: CellNode) => {
      if (!intersects(node.bounds)) return;
      if (node.children) {
        traverse(node.children[0]);
        traverse(node.children[1]);
      } else if (node.cells)
        for (const cell of node.cells) {
          if (!intersects(cell.bounds)) continue;
          for (const i of cell.indices)
            visit(this.positions[i], this.positions[i + 1], this.positions[i + 2]);
        }
    };
    traverse(this.root);
  }
}

function visitNearRay(
  points: PointSource,
  ray: Ray,
  radius: number,
  distance: number,
  visit: VisitPoint,
) {
  if (points instanceof PointCloudIndex) {
    points.forEachNearRay(ray, radius, distance, visit);
  } else if (points.length && typeof points[0] !== 'number') {
    for (const p of points as readonly Vector3[]) visit(p.x, p.y, p.z);
  } else {
    const positions = points as ArrayLike<number>;
    for (let i = 0; i + 2 < positions.length; i += 3)
      visit(positions[i], positions[i + 1], positions[i + 2]);
  }
}

/** Nearest point in front of the ray within threshold; fallback uses current target depth. */
export function pickPivot(
  ray: Ray,
  points: PointSource,
  threshold: number,
  targetDistance = 1,
): Vector3 {
  let nearest = Infinity;
  const pivot = ray.at(Math.max(targetDistance, 1e-9), new Vector3());
  const offset = new Vector3();
  visitNearRay(points, ray, threshold, Infinity, (x, y, z) => {
    offset.set(x, y, z).sub(ray.origin);
    const depth = offset.dot(ray.direction);
    if (depth <= 0 || depth >= nearest) return;
    const distanceSq = Math.max(0, offset.lengthSq() - depth * depth);
    if (distanceSq <= threshold * threshold) {
      nearest = depth;
      pivot.set(x, y, z);
    }
  });
  return pivot;
}

function copyState(state: NavigationState): NavigationState {
  return {
    ...state,
    position: state.position.clone(),
    pivot: state.pivot.clone(),
    up: state.up.clone(),
    rotation: state.rotation?.clone(),
  };
}

/** dx/dy are radians. World-up orbit, clamped away from both poles. */
export function orbit(state: NavigationState, dx: number, dy: number): NavigationState {
  const next = copyState(state);
  const up = state.up.clone().normalize();
  const toY = new Quaternion().setFromUnitVectors(up, new Vector3(0, 1, 0));
  const offset = state.position.clone().sub(state.pivot);
  const spherical = new Spherical().setFromVector3(offset.clone().applyQuaternion(toY));
  const phi = Math.max(0.01, Math.min(Math.PI - 0.01, spherical.phi - dy));
  const yaw = new Quaternion().setFromAxisAngle(up, -dx);
  const right = new Vector3().crossVectors(up, offset.clone().applyQuaternion(yaw));
  if (right.lengthSq() < 1e-20) right.set(1, 0, 0).applyQuaternion(toY.clone().invert());
  const pitch = new Quaternion().setFromAxisAngle(right.normalize(), phi - spherical.phi);
  const rotation = pitch.multiply(yaw);
  next.position.copy(offset.applyQuaternion(rotation).add(state.pivot));
  if (next.rotation) next.rotation.premultiply(rotation).normalize();
  return next;
}

/**
 * amount is a signed world-space distance (positive = forward). Dolly the pivot
 * when the camera approaches/passes its depth, so even a very large step cannot
 * cross the pivot or leave a zero camera-to-pivot distance. The view does not turn.
 */
export function zoomToward(
  state: NavigationState,
  cursorRay: Ray,
  amount: number,
): NavigationState {
  const next = copyState(state);
  const direction = cursorRay.direction.clone().normalize();
  next.position.addScaledVector(direction, amount);
  const min = Math.max(state.minDistance, 1e-9);
  const depth = state.pivot.clone().sub(next.position).dot(direction);
  if (depth < min) next.pivot.addScaledVector(direction, min - depth);
  return next;
}

/** dx/dy are fractions of viewport height; pan follows the camera's screen plane. */
export function pan(state: NavigationState, dx: number, dy: number): NavigationState {
  const next = copyState(state);
  const forward = state.pivot.clone().sub(state.position).normalize();
  const right = state.rotation
    ? new Vector3(1, 0, 0).applyQuaternion(state.rotation)
    : new Vector3().crossVectors(forward, state.up).normalize();
  const screenUp = state.rotation
    ? new Vector3(0, 1, 0).applyQuaternion(state.rotation)
    : new Vector3().crossVectors(right, forward).normalize();
  const scale =
    2 *
    Math.max(state.position.distanceTo(state.pivot), state.minDistance) *
    Math.tan((state.fov * Math.PI) / 360);
  const move = right.multiplyScalar(-dx * scale).addScaledVector(screenUp, dy * scale);
  next.position.add(move);
  next.pivot.add(move);
  return next;
}

/** margin is world-space, so flat clouds also have room above/below the surface. */
export function clampToBounds(position: Vector3, bbox: Box3, margin: number): Vector3 {
  if (bbox.isEmpty()) return position.clone();
  return position
    .clone()
    .clamp(
      bbox.min.clone().addScalar(-Math.max(0, margin)),
      bbox.max.clone().addScalar(Math.max(0, margin)),
    );
}

/** Swept sphere vs every nearby point; stop before the earliest entry, never tunnel. */
export function limitByNearestPoints(
  from: Vector3,
  to: Vector3,
  points: PointSource,
  radius: number,
): Vector3 {
  const direction = to.clone().sub(from);
  const length = direction.length();
  if (!length || radius <= 0) return to.clone();
  direction.divideScalar(length);
  const ray = new Ray(from, direction);
  let distance = length;
  const offset = new Vector3();
  visitNearRay(points, ray, radius, length + radius, (x, y, z) => {
    offset.set(x, y, z).sub(from);
    const along = offset.dot(direction);
    const squared = offset.lengthSq();
    // Imported camera poses may already be inside a point's exclusion sphere.
    // Permit motion away from it; never trap a camera in the cloud.
    if (squared < radius * radius) {
      if (along > 0) distance = 0;
      return;
    }
    const perpendicularSq = Math.max(0, squared - along * along);
    if (perpendicularSq > radius * radius) return;
    const entry = along - Math.sqrt(radius * radius - perpendicularSq);
    if (entry >= 0 && entry <= distance)
      distance = Math.max(0, entry - Math.max(radius * 1e-4, 1e-12));
  });
  return distance === length ? to.clone() : from.clone().addScaledVector(direction, distance);
}

/** Exponential convergence with dt in seconds, independent of the frame rate. */
export function damp(current: number, target: number, lambda: number, dt: number): number {
  return current + (target - current) * -Math.expm1(-Math.max(0, lambda) * Math.max(0, dt));
}

export interface NavigationOptions {
  points: PointSource;
  bounds: Box3;
  target?: Vector3;
  minDistance?: number;
  collisionRadius?: number;
  boundsMargin?: number;
  sceneRadius?: number;
  sensitivity?: number;
  onChange?: () => void;
  /** Called on valid input even while disabled, so an engine can leave a source-camera pose. */
  onStart?: () => void;
}

/** DOM adapter. No React or renderer; the owner supplies seconds to update(dt). */
export class NavigationControls {
  readonly target: Vector3;
  enabled = true;
  sensitivity = 1;
  readonly points: PointSource;
  private readonly minDistance: number;
  private readonly radius: number;
  private readonly margin: number;
  private readonly sceneRadius: number;
  private wheelHistory: number[] = [];
  private wheelHistoryAt = -Infinity;
  private readonly previousTouchAction: string;
  private disposed = false;
  private drag: { id: number; x: number; y: number; button: number } | null = null;
  private pending = { orbitX: 0, orbitY: 0, panX: 0, panY: 0, zoom: 0 };
  private cursor = { x: 0, y: 0 };

  constructor(
    private camera: PerspectiveCamera,
    private canvas: HTMLCanvasElement,
    private options: NavigationOptions,
  ) {
    this.target = options.target?.clone() ?? new Vector3();
    const diagonal = options.bounds.isEmpty() ? 1 : options.bounds.getSize(new Vector3()).length();
    this.sceneRadius = options.sceneRadius ?? Math.max(diagonal / 2, 1e-6);
    this.sensitivity = options.sensitivity ?? 1;
    this.minDistance = options.minDistance ?? Math.max(diagonal * 0.001, 1e-6);
    this.radius = options.collisionRadius ?? Math.max(diagonal * 0.0015, 1e-6);
    this.margin = options.boundsMargin ?? Math.max(diagonal * 0.5, this.minDistance * 10);
    this.points =
      options.points instanceof PointCloudIndex || Array.isArray(options.points)
        ? options.points
        : new PointCloudIndex(options.points as ArrayLike<number>);
    this.previousTouchAction = canvas.style.touchAction;
    canvas.style.touchAction = 'none';
    canvas.addEventListener('pointerdown', this.onPointerDown);
    canvas.addEventListener('pointermove', this.onPointerMove);
    canvas.addEventListener('pointerup', this.onPointerEnd);
    canvas.addEventListener('pointercancel', this.onPointerEnd);
    canvas.addEventListener('lostpointercapture', this.onPointerEnd);
    canvas.addEventListener('wheel', this.onWheel, { passive: false });
  }

  private state(): NavigationState {
    return {
      position: this.camera.position.clone(),
      pivot: this.target.clone(),
      up: this.camera.up.clone(),
      minDistance: this.minDistance,
      fov: this.camera.fov,
      rotation: this.camera.quaternion.clone(),
    };
  }

  private rayAt(x: number, y: number): Ray {
    const rect = this.canvas.getBoundingClientRect();
    const nx = ((x - rect.left) / Math.max(1, rect.width)) * 2 - 1;
    const ny = 1 - ((y - rect.top) / Math.max(1, rect.height)) * 2;
    this.camera.updateMatrixWorld();
    return new Ray(
      this.camera.position.clone(),
      new Vector3(nx, ny, 0.5).unproject(this.camera).sub(this.camera.position).normalize(),
    );
  }

  /** Adopt a flight/reset; supply its previous position to sweep-test the flight. */
  sync(from?: Vector3) {
    this.clearPending();
    this.camera.position.copy(
      from
        ? this.constrainMove(from, this.camera.position)
        : clampToBounds(this.camera.position, this.options.bounds, this.margin),
    );
    if (this.camera.position.distanceToSquared(this.target) < this.minDistance ** 2)
      this.target
        .copy(this.camera.position)
        .addScaledVector(this.camera.getWorldDirection(new Vector3()), this.minDistance);
    this.camera.lookAt(this.target);
    this.camera.updateMatrixWorld();
  }

  private clearPending() {
    this.pending = { orbitX: 0, orbitY: 0, panX: 0, panY: 0, zoom: 0 };
  }

  /** Cancel residual input/capture before another camera mode takes ownership. */
  stop() {
    this.clearPending();
    this.wheelHistory = [];
    const drag = this.drag;
    this.drag = null;
    if (drag && this.canvas.hasPointerCapture?.(drag.id))
      this.canvas.releasePointerCapture(drag.id);
  }

  /** Toolbar zoom follows the same damped, bounded, collision-tested path as pinch. */
  zoom(factor: number, precision = false) {
    if (this.disposed || !this.enabled || !Number.isFinite(factor) || factor <= 0) return;
    const rect = this.canvas.getBoundingClientRect();
    this.cursor = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    const distance = Math.max(this.camera.position.distanceTo(this.target), this.minDistance * 10);
    this.pending.zoom += distance * (1 - factor) * this.speed(precision);
  }

  private speed(precision: boolean): number {
    return speedScale(this.camera.position.distanceTo(this.target), this.sceneRadius, {
      precision,
      sensitivity: this.sensitivity,
    });
  }

  private onPointerDown = (event: PointerEvent) => {
    const action = mapInput(event);
    if (this.disposed || this.drag || action === NavigationAction.None) return;
    // onStart lets the engine leave an exact video-camera pose before navigation.
    this.options.onStart?.();
    if (!this.enabled) return;
    this.clearPending();
    this.canvas.focus({ preventScroll: true });
    this.drag = { id: event.pointerId, x: event.clientX, y: event.clientY, button: event.button };
    this.canvas.setPointerCapture?.(event.pointerId);
    if (action === NavigationAction.Orbit) {
      const distance = Math.max(this.minDistance, this.camera.position.distanceTo(this.target));
      const height = Math.max(1, this.canvas.getBoundingClientRect().height);
      const threshold = Math.max(
        this.radius,
        (distance * Math.tan((this.camera.fov * Math.PI) / 360) * 16) / height,
      );
      this.target.copy(
        pickPivot(this.rayAt(event.clientX, event.clientY), this.points, threshold, distance),
      );
    }
    event.preventDefault();
  };

  private onPointerMove = (event: PointerEvent) => {
    if (!this.enabled || !this.drag || this.drag.id !== event.pointerId) return;
    const height = Math.max(1, this.canvas.getBoundingClientRect().height);
    const speed = this.speed(event.altKey);
    const dx = ((event.clientX - this.drag.x) / height) * speed;
    const dy = ((event.clientY - this.drag.y) / height) * speed;
    const action = mapInput({
      type: 'pointermove',
      button: this.drag.button,
      shiftKey: event.shiftKey,
    });
    if (action === NavigationAction.Pan) {
      this.pending.panX += dx;
      this.pending.panY += dy;
    } else {
      this.pending.orbitX += dx * Math.PI;
      this.pending.orbitY += dy * Math.PI;
    }
    this.drag.x = event.clientX;
    this.drag.y = event.clientY;
    event.preventDefault();
  };

  private onPointerEnd = (event: PointerEvent) => {
    if (this.drag?.id !== event.pointerId) return;
    this.drag = null;
    if (this.canvas.hasPointerCapture?.(event.pointerId))
      this.canvas.releasePointerCapture(event.pointerId);
  };

  private onWheel = (event: WheelEvent) => {
    if (this.disposed) return;
    // Block document scrolling/browser pinch zoom even while a source pose owns the camera.
    event.preventDefault();
    this.options.onStart?.();
    if (!this.enabled) return;
    if (event.timeStamp - this.wheelHistoryAt > 250 || event.timeStamp < this.wheelHistoryAt)
      this.wheelHistory = [];
    this.wheelHistoryAt = event.timeStamp;
    const action = mapInput(event, this.wheelHistory);
    const height = Math.max(1, this.canvas.getBoundingClientRect().height);
    const scale = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? height : 1;
    const tick = wheelStep(event);
    // Only confirmed mouse ticks train the heuristic. Repetition alone must
    // never turn plain small pixel trackpad scrolling into zoom.
    this.wheelHistory =
      action === NavigationAction.Zoom && !event.ctrlKey && tick !== undefined
        ? [...this.wheelHistory.slice(-5), tick * scale]
        : [];
    const dx = event.deltaX * scale;
    const dy = event.deltaY * scale;
    const speed = this.speed(event.altKey);
    if (action === NavigationAction.Zoom) {
      const distance = Math.max(
        this.camera.position.distanceTo(this.target),
        this.minDistance * 10,
      );
      const wheelDelta = dy || (event.shiftKey ? dx : 0);
      this.pending.zoom +=
        Math.expm1(-Math.min(2, Math.max(-2, wheelDelta * 0.002))) * distance * speed;
      this.cursor = { x: event.clientX, y: event.clientY };
    } else if (action === NavigationAction.Pan) {
      // Some browsers remap Shift+vertical-wheel to deltaX; retain both axes.
      this.pending.panX += (dx / height) * speed;
      this.pending.panY += (dy / height) * speed;
    } else {
      this.pending.orbitX += (dx / height) * Math.PI * speed;
      this.pending.orbitY += (dy / height) * Math.PI * speed;
    }
  };

  /** Shared swept collision/bounds constraint for orbit, fly and view animations. */
  constrainMove(from: Vector3, to: Vector3): Vector3 {
    let bounded = clampToBounds(to, this.options.bounds, this.margin);
    const expanded = this.options.bounds.clone().expandByScalar(this.margin);
    if (!bounded.equals(to) && expanded.containsPoint(from)) {
      // Clip the whole segment, preserving the pan/dolly direction and pivot offset.
      const delta = to.clone().sub(from);
      let fraction = 1;
      for (let axis = 0; axis < 3; axis++) {
        const value = delta.getComponent(axis);
        if (value !== 0) {
          const edge = value > 0 ? expanded.max : expanded.min;
          fraction = Math.min(
            fraction,
            (edge.getComponent(axis) - from.getComponent(axis)) / value,
          );
        }
      }
      bounded = from.clone().addScaledVector(delta, Math.max(0, fraction));
      // Remove floating point overshoot at an exactly reached face.
      bounded = clampToBounds(bounded, this.options.bounds, this.margin);
    }
    return limitByNearestPoints(from, bounded, this.points, this.radius);
  }

  private orbitWithinLimits(state: NavigationState, dx: number, dy: number): NavigationState {
    const distance = Math.max(state.position.distanceTo(state.pivot), this.minDistance);
    // Sweep short chords along the arc so a large input/frame cannot tunnel past
    // an obstacle on the curve. Keep the chord sagitta smaller than the point radius.
    const maxAngle = this.radius > 0 ? Math.min(0.05, Math.sqrt(this.radius / distance)) : 0.05;
    const steps = Math.max(1, Math.ceil(Math.max(Math.abs(dx), Math.abs(dy)) / maxAngle));
    for (let i = 0; i < steps; i++) {
      const proposed = orbit(state, dx / steps, dy / steps);
      if (this.constrainMove(state.position, proposed.position).equals(proposed.position)) {
        state = proposed;
        continue;
      }
      // A clipped chord would shorten the orbit radius. Find the last allowed
      // angular fraction instead, preserving the pivot's screen position and radius.
      let low = 0;
      let high = 1;
      let accepted = state;
      for (let search = 0; search < 24; search++) {
        const fraction = (low + high) / 2;
        const candidate = orbit(state, (dx / steps) * fraction, (dy / steps) * fraction);
        if (this.constrainMove(state.position, candidate.position).equals(candidate.position)) {
          low = fraction;
          accepted = candidate;
        } else high = fraction;
      }
      this.clearPending();
      return accepted;
    }
    return state;
  }

  update(dt = 1 / 60): boolean {
    if (this.disposed || !this.enabled) {
      this.clearPending();
      return false;
    }
    const fraction = damp(0, 1, 12, dt);
    if (!fraction) return false;
    const step = { ...this.pending };
    let active = false;
    for (const key of Object.keys(step) as (keyof typeof step)[]) {
      step[key] *= fraction;
      this.pending[key] -= step[key];
      if (Math.abs(this.pending[key]) < 1e-8) this.pending[key] = 0;
      active ||= step[key] !== 0;
    }
    if (!active) return false;
    let state = this.state();
    const pureOrbit =
      (step.orbitX !== 0 || step.orbitY !== 0) && !step.panX && !step.panY && !step.zoom;
    if (step.orbitX || step.orbitY)
      state = pureOrbit
        ? this.orbitWithinLimits(state, step.orbitX, step.orbitY)
        : orbit(state, step.orbitX, step.orbitY);
    if (step.panX || step.panY) state = pan(state, step.panX, step.panY);
    if (step.zoom) state = zoomToward(state, this.rayAt(this.cursor.x, this.cursor.y), step.zoom);
    const safe = pureOrbit
      ? state.position
      : this.constrainMove(this.camera.position, state.position);
    const proposed = state.position.clone().sub(this.camera.position);
    const actual = safe.clone().sub(this.camera.position);
    const ratio = proposed.lengthSq() ? Math.min(1, actual.length() / proposed.length()) : 1;
    // Keep the same fraction of pan/dolly/rotation when a boundary blocks a move.
    this.target.lerp(state.pivot, ratio);
    if (state.rotation) this.camera.quaternion.slerp(state.rotation, ratio);
    this.camera.position.copy(safe);
    if (!safe.equals(state.position)) this.clearPending();
    this.camera.updateMatrixWorld();
    this.options.onChange?.();
    return true;
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.clearPending();
    if (this.drag && this.canvas.hasPointerCapture?.(this.drag.id))
      this.canvas.releasePointerCapture(this.drag.id);
    this.drag = null;
    this.canvas.removeEventListener('pointerdown', this.onPointerDown);
    this.canvas.removeEventListener('pointermove', this.onPointerMove);
    this.canvas.removeEventListener('pointerup', this.onPointerEnd);
    this.canvas.removeEventListener('pointercancel', this.onPointerEnd);
    this.canvas.removeEventListener('lostpointercapture', this.onPointerEnd);
    this.canvas.removeEventListener('wheel', this.onWheel);
    this.canvas.style.touchAction = this.previousTouchAction;
  }
}
