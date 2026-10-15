import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Box3, Vector3 } from 'three';
import { createDemo } from '../fixtures/demo';
import type { Bundle } from '../lib/bundle';
import { robustBounds } from '../lib/geometry';
import { ViewerEngine } from './engine';
import { orientationFor, viewDirectionFor } from './gizmo';

// Keep the actual camera, navigation, fly physics and gizmo math; jsdom has no GPU.
vi.mock('three', async (importOriginal) => {
  const three = await importOriginal<typeof import('three')>();
  return {
    ...three,
    WebGLRenderer: class {
      autoClear = true;
      setPixelRatio = vi.fn();
      getPixelRatio = () => 1;
      setClearColor = vi.fn();
      setViewport = vi.fn();
      setScissorTest = vi.fn();
      setSize = vi.fn();
      clear = vi.fn();
      render = vi.fn();
      dispose = vi.fn();
    },
  };
});

let now = 1000;
let frameId = 0;
let lockedElement: Element | null = null;
let lockDescriptor: PropertyDescriptor | undefined;
let exitLockDescriptor: PropertyDescriptor | undefined;
const pendingFrames = new Map<number, FrameRequestCallback>();
const engines = new Set<ViewerEngine>();
let exitPointerLock = vi.fn();

beforeEach(() => {
  now = 1000;
  frameId = 0;
  lockedElement = null;
  pendingFrames.clear();
  vi.spyOn(performance, 'now').mockImplementation(() => now);
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    pendingFrames.set(++frameId, callback);
    return frameId;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => pendingFrames.delete(id));
  lockDescriptor = Object.getOwnPropertyDescriptor(document, 'pointerLockElement');
  exitLockDescriptor = Object.getOwnPropertyDescriptor(document, 'exitPointerLock');
  Object.defineProperty(document, 'pointerLockElement', {
    configurable: true,
    get: () => lockedElement,
  });
  exitPointerLock = vi.fn(() => setLock(null));
  Object.defineProperty(document, 'exitPointerLock', {
    configurable: true,
    value: exitPointerLock,
  });
});

afterEach(() => {
  for (const engine of engines) engine.dispose();
  engines.clear();
  document.body.replaceChildren();
  if (lockDescriptor) Object.defineProperty(document, 'pointerLockElement', lockDescriptor);
  else Reflect.deleteProperty(document, 'pointerLockElement');
  if (exitLockDescriptor) Object.defineProperty(document, 'exitPointerLock', exitLockDescriptor);
  else Reflect.deleteProperty(document, 'exitPointerLock');
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function frame(count = 1) {
  for (let i = 0; i < count; i++) {
    now += 1000 / 60;
    const callbacks = [...pendingFrames.values()];
    pendingFrames.clear();
    for (const callback of callbacks) callback(now);
  }
}

function setLock(element: Element | null) {
  lockedElement = element;
  document.dispatchEvent(new Event('pointerlockchange'));
}

function setup(bundle = createDemo('en').bundle) {
  const canvas = document.createElement('canvas');
  document.body.append(canvas);
  Object.defineProperties(canvas, {
    clientWidth: { value: 800 },
    clientHeight: { value: 600 },
  });
  vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({
    left: 0,
    top: 0,
    right: 800,
    bottom: 600,
    width: 800,
    height: 600,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  });
  const onFlyModeChange = vi.fn();
  const onPick = vi.fn();
  const engine = new ViewerEngine(canvas, bundle, { onFlyModeChange, onPick });
  engines.add(engine);
  canvas.focus();
  return { canvas, engine, onFlyModeChange, onPick };
}

function key(
  target: EventTarget,
  value: string,
  {
    type = 'keydown',
    code,
    repeat = false,
    shiftKey = false,
  }: { type?: string; code?: string; repeat?: boolean; shiftKey?: boolean } = {},
) {
  target.dispatchEvent(
    new KeyboardEvent(type, {
      key: value,
      code,
      repeat,
      shiftKey,
      bubbles: true,
      cancelable: true,
    }),
  );
}

function pointer(canvas: HTMLCanvasElement, type: string, x: number, y: number) {
  const event = new MouseEvent(type, {
    button: 0,
    clientX: x,
    clientY: y,
    bubbles: true,
    cancelable: true,
  });
  Object.defineProperty(event, 'pointerId', { value: 1 });
  canvas.dispatchEvent(event);
}

function mouseLook(dx: number, dy: number) {
  const event = new MouseEvent('mousemove', { bubbles: true });
  Object.defineProperties(event, {
    movementX: { value: dx },
    movementY: { value: dy },
  });
  document.dispatchEvent(event);
}

describe('ViewerEngine fly input', () => {
  it('boosts when Shift is already held before fly mode starts', () => {
    const travel = (shiftKey: boolean) => {
      const { canvas, engine } = setup(navigationBundle());
      const start = engine.camera.position.clone();
      key(canvas, 'f', { code: 'KeyF', shiftKey });
      key(canvas, 'w', { code: 'KeyW', shiftKey });
      frame(10);
      const distance = engine.camera.position.distanceTo(start);
      key(canvas, 'f');
      return distance;
    };
    const normal = travel(false);
    expect(normal).toBeGreaterThan(0);
    expect(travel(true)).toBeCloseTo(normal * 4, 8);
  });

  it('toggles with F, ignores auto-repeat, and exits with Escape', () => {
    const { canvas, engine, onFlyModeChange } = setup();
    key(canvas, 'f', { code: 'KeyF' });
    expect(engine.flyMode).toBe(true);
    expect(engine.controls.enabled).toBe(false);
    key(canvas, 'f', { code: 'KeyF', repeat: true });
    expect(engine.flyMode).toBe(true);
    expect(onFlyModeChange).toHaveBeenCalledTimes(1);
    key(canvas, 'Escape');
    expect(engine.flyMode).toBe(false);
    expect(engine.controls.enabled).toBe(true);
    key(canvas, 'F', { code: 'KeyF' });
    key(canvas, 'F', { code: 'KeyF' });
    expect(onFlyModeChange.mock.calls).toEqual([[true], [false], [true], [false]]);
  });

  it('ignores shortcuts in text controls and editable descendants, and exits when editing starts', () => {
    const { canvas, engine, onFlyModeChange } = setup();
    for (const tag of ['input', 'textarea', 'select', 'div']) {
      const editor = document.createElement(tag);
      editor.tabIndex = 0;
      let target: Element = editor;
      if (tag === 'div') {
        editor.setAttribute('contenteditable', 'true');
        target = document.createElement('span');
        editor.append(target);
      }
      document.body.append(editor);
      editor.focus();
      key(target, 'f', { code: 'KeyF' });
      // A synthetic canvas event must also respect the currently focused editor.
      key(canvas, 'f', { code: 'KeyF' });
      expect(engine.flyMode).toBe(false);
      editor.remove();
    }
    expect(onFlyModeChange).not.toHaveBeenCalled();
    canvas.focus();
    key(canvas, 'f');
    const input = document.createElement('input');
    document.body.append(input);
    input.focus();
    expect(engine.flyMode).toBe(false);
    expect(onFlyModeChange.mock.calls).toEqual([[true], [false]]);
  });

  it('uses drag look without pointer lock and resumes orbit at the same camera pose', () => {
    const { canvas, engine, onPick } = setup();
    const position = engine.camera.position.clone();
    const orientation = engine.camera.quaternion.clone();
    key(canvas, 'f');
    pointer(canvas, 'pointerdown', 100, 100);
    pointer(canvas, 'pointermove', 180, 130);
    pointer(canvas, 'pointerup', 180, 130);
    frame(30);
    expect(engine.camera.position.distanceTo(position)).toBeLessThan(1e-10);
    expect(engine.camera.quaternion.angleTo(orientation)).toBeGreaterThan(0.1);
    expect(onPick).not.toHaveBeenCalled();
    const beforeExit = engine.camera.quaternion.clone();
    const forward = engine.camera.getWorldDirection(new Vector3());
    const distance = position.distanceTo(engine.controls.target);
    key(canvas, 'f');
    expect(engine.controls.target.distanceTo(position)).toBeCloseTo(distance, 8);
    expect(
      engine.controls.target.clone().sub(position).normalize().distanceTo(forward),
    ).toBeLessThan(1e-9);
    frame(30);
    expect(engine.camera.position.distanceTo(position)).toBeLessThan(1e-10);
    expect(engine.camera.quaternion.angleTo(beforeExit)).toBeLessThan(1e-6);
  });

  it.each(['z', '-z'] as const)(
    'keeps the %s axis pose until look and preserves its stable heading on first look',
    (axis) => {
      const { canvas, engine } = setup(navigationBundle());
      engine.axisView(axis);
      frame(150);
      const position = engine.camera.position.clone();
      const orientation = engine.camera.quaternion.clone();
      key(canvas, 'f');
      frame(60);
      expect(engine.camera.position.equals(position)).toBe(true);
      expect(engine.camera.quaternion.angleTo(orientation)).toBeLessThan(1e-7);
      pointer(canvas, 'pointerdown', 100, 100);
      pointer(canvas, 'pointermove', 120, 100);
      pointer(canvas, 'pointerup', 120, 100);
      frame(60);
      // The small look should not turn a residual pole heading into a large roll.
      expect(engine.camera.quaternion.angleTo(orientation)).toBeGreaterThan(0.02);
      expect(engine.camera.quaternion.angleTo(orientation)).toBeLessThan(0.05);
      const afterLook = engine.camera.quaternion.clone();
      key(canvas, 'f');
      key(canvas, 'f');
      frame(60);
      expect(engine.camera.quaternion.angleTo(afterLook)).toBeLessThan(1e-7);
    },
  );

  it('preserves a rolled source-camera pose until nonzero mouse look starts', () => {
    const { canvas, engine } = setup();
    engine.viewThroughFrame(0, false);
    engine.camera.rotateZ(Math.PI / 3);
    const position = engine.camera.position.clone();
    const orientation = engine.camera.quaternion.clone();
    key(canvas, 'f');
    frame(60);
    pointer(canvas, 'pointerdown', 100, 100);
    pointer(canvas, 'pointermove', 100, 100);
    frame(30);
    expect(engine.camera.position.equals(position)).toBe(true);
    expect(engine.camera.quaternion.angleTo(orientation)).toBeLessThan(1e-7);
    pointer(canvas, 'pointermove', 140, 100);
    pointer(canvas, 'pointerup', 140, 100);
    frame(60);
    expect(engine.camera.quaternion.angleTo(orientation)).toBeGreaterThan(0.05);
    expect(engine.camera.position.equals(position)).toBe(true);
  });

  it('uses pointer-lock mouse deltas and exits when the browser releases the lock', () => {
    const { canvas, engine, onFlyModeChange } = setup();
    const request = vi.fn(() => setLock(canvas));
    Object.defineProperty(canvas, 'requestPointerLock', { value: request });
    const orientation = engine.camera.quaternion.clone();
    key(canvas, 'f');
    expect(request).toHaveBeenCalledOnce();
    expect(document.pointerLockElement).toBe(canvas);
    mouseLook(90, -30);
    frame(20);
    expect(engine.camera.quaternion.angleTo(orientation)).toBeGreaterThan(0.1);
    setLock(null);
    expect(engine.flyMode).toBe(false);
    expect(engine.controls.enabled).toBe(true);
    expect(onFlyModeChange.mock.calls).toEqual([[true], [false]]);
  });

  it('keeps drag look available after a rejected pointer-lock request', async () => {
    const { canvas, engine } = setup();
    const request = vi.fn().mockRejectedValue(new Error('Pointer lock denied'));
    Object.defineProperty(canvas, 'requestPointerLock', { value: request });
    const orientation = engine.camera.quaternion.clone();
    key(canvas, 'f');
    await Promise.resolve();
    expect(engine.flyMode).toBe(true);
    expect(document.pointerLockElement).toBeNull();
    pointer(canvas, 'pointerdown', 40, 40);
    pointer(canvas, 'pointermove', 120, 20);
    pointer(canvas, 'pointerup', 120, 20);
    frame(20);
    expect(engine.camera.quaternion.angleTo(orientation)).toBeGreaterThan(0.1);
  });

  it('moves with physical WASD across keyboard layouts and damps after release', () => {
    const { canvas, engine } = setup();
    const start = engine.camera.position.clone();
    const forward = engine.camera.getWorldDirection(new Vector3());
    key(canvas, 'f');
    key(canvas, 'ц', { code: 'KeyW' });
    frame(20);
    const traveled = engine.camera.position.clone().sub(start);
    expect(traveled.length()).toBeGreaterThan(0.5);
    expect(traveled.normalize().dot(forward)).toBeCloseTo(1, 8);
    const beforeRelease = engine.camera.position.clone();
    key(canvas, 'ц', { code: 'KeyW', type: 'keyup' });
    frame();
    const firstCoast = engine.camera.position.distanceTo(beforeRelease);
    expect(firstCoast).toBeGreaterThan(0);
    frame(90);
    const rested = engine.camera.position.clone();
    frame();
    expect(engine.camera.position.distanceTo(rested)).toBeLessThan(firstCoast * 0.001);
  });

  it('releases lock on blur and disposes keyboard, pointer-lock and animation listeners', () => {
    const { canvas, engine, onFlyModeChange } = setup();
    Object.defineProperty(canvas, 'requestPointerLock', { value: vi.fn(() => setLock(canvas)) });
    key(canvas, 'f');
    key(canvas, 'w', { code: 'KeyW' });
    frame(5);
    window.dispatchEvent(new Event('blur'));
    expect(engine.flyMode).toBe(false);
    expect(document.pointerLockElement).toBeNull();
    expect(exitPointerLock).toHaveBeenCalledOnce();
    const stopped = engine.camera.position.clone();
    frame(30);
    expect(engine.camera.position.distanceTo(stopped)).toBeLessThan(1e-10);
    key(canvas, 'f');
    expect(engine.flyMode).toBe(true);
    engine.dispose();
    engines.delete(engine);
    expect(document.pointerLockElement).toBeNull();
    expect(exitPointerLock).toHaveBeenCalledTimes(2);
    expect(engine.renderer.dispose).toHaveBeenCalledOnce();
    expect(pendingFrames.size).toBe(0);
    key(canvas, 'f');
    setLock(canvas);
    mouseLook(200, 200);
    window.dispatchEvent(new Event('blur'));
    frame(10);
    expect(engine.flyMode).toBe(false);
    expect(onFlyModeChange.mock.calls).toEqual([[true], [false], [true], [false]]);
    expect(engine.camera.position.distanceTo(stopped)).toBeLessThan(1e-10);
  });

  it('clears held movement on toolbar focus and lets F/Escape exit fly from that button', () => {
    const { canvas, engine } = setup(navigationBundle());
    const button = document.createElement('button');
    document.body.append(button);
    button.focus();
    key(button, 'f');
    expect(engine.flyMode).toBe(false);
    canvas.focus();
    key(canvas, 'f');
    key(canvas, 'w', { code: 'KeyW' });
    frame(10);
    button.focus();
    frame(90);
    const rested = engine.camera.position.clone();
    frame();
    expect(engine.camera.position.distanceTo(rested)).toBeLessThan(1e-7);
    key(button, 'f');
    expect(engine.flyMode).toBe(false);
    canvas.focus();
    key(canvas, 'f');
    button.focus();
    key(button, 'Escape');
    expect(engine.flyMode).toBe(false);
  });

  it('releases a pointer lock request that resolves after disposal', async () => {
    const { canvas, engine } = setup();
    let resolveRequest!: () => void;
    const request = new Promise<void>((resolve) => {
      resolveRequest = resolve;
    });
    Object.defineProperty(canvas, 'requestPointerLock', { value: vi.fn(() => request) });
    key(canvas, 'f');
    engine.dispose();
    engines.delete(engine);
    setLock(canvas);
    expect(document.pointerLockElement).toBe(canvas);
    resolveRequest();
    await request;
    expect(exitPointerLock).toHaveBeenCalledOnce();
    expect(document.pointerLockElement).toBeNull();
    expect(pendingFrames.size).toBe(0);
  });
});

/** A small cube leaves clear space for radius-two camera arcs; extra points are obstacles. */
function navigationBundle(extra: number[][] = []): Bundle {
  const demo = createDemo('en').bundle;
  const points: number[] = [];
  for (const x of [-1, 1]) for (const y of [-1, 1]) for (const z of [-1, 1]) points.push(x, y, z);
  for (const point of extra) points.push(...point);
  const count = points.length / 3;
  return {
    ...demo,
    manifest: {
      ...demo.manifest,
      world: {
        ...demo.manifest.world,
        focus: { center: [0, 0, 0], radius: 1 },
      },
      points: { ...demo.manifest.points, count },
      frames: [{ ...demo.manifest.frames[0], registered: false }],
    },
    position: new Float32Array(points),
    color: new Uint8Array(count * 3).fill(180),
    firstSeen: new Uint16Array(count),
    frameOffsets: new Uint32Array([0, count]),
    frameInPoints: Uint32Array.from({ length: count }, (_, i) => i),
    pointOffsets: Uint32Array.from({ length: count + 1 }, (_, i) => i),
    pointFrames: new Uint16Array(count),
    assembled: new Uint32Array([count]),
  };
}

function navigationEnvelope(bundle: Bundle) {
  const bounds = robustBounds(
    bundle.position,
    bundle.manifest.frames.filter((f) => f.registered && f.center).map((f) => f.center!),
  );
  return {
    expanded: new Box3(new Vector3(...bounds.min), new Vector3(...bounds.max)).expandByScalar(
      bounds.radius,
    ),
    collisionRadius: bounds.radius * 0.003,
  };
}

describe('ViewerEngine axis flights', () => {
  it('finishes radial movement when an off-centre pivot starts with the target orientation', () => {
    const { engine } = setup(navigationBundle());
    const pivot = new Vector3(0.1, -0.2, 0.05);
    engine.controls.target.copy(pivot);
    engine.camera.position.copy(pivot).add(new Vector3(0, 0, 2));
    const target = viewDirectionFor('x', engine.camera.up);
    engine.camera.quaternion.copy(orientationFor(target.direction, target.up));
    const start = engine.camera.position.clone();
    engine.axisView('x');
    expect(engine.camera.position.equals(start)).toBe(true);
    frame();
    expect(engine.camera.position.distanceTo(start)).toBeGreaterThan(0.05);
    const firstStep = engine.camera.position.clone();
    frame(150);
    expect(engine.camera.position.distanceTo(firstStep)).toBeGreaterThan(0.1);
    expect(engine.camera.position.distanceTo(pivot.clone().add(new Vector3(2, 0, 0)))).toBeLessThan(
      1e-4,
    );
    expect(engine.camera.position.distanceTo(pivot)).toBeCloseTo(2, 9);
    expect(engine.controls.target.equals(pivot)).toBe(true);
    expect(
      engine.camera.quaternion.angleTo(orientationFor(target.direction, target.up)),
    ).toBeLessThan(1e-6);
  });

  it.each(['z', '-z'] as const)(
    'reaches the %s pole with stable screen-up and unchanged pivot/radius',
    (axis) => {
      const { engine } = setup(navigationBundle());
      const pivot = new Vector3(0.1, -0.2, 0.05);
      engine.controls.target.copy(pivot);
      engine.camera.position.copy(pivot).add(new Vector3(2, 0, 0));
      engine.camera.lookAt(pivot);
      const start = engine.camera.position.clone();
      engine.axisView(axis);
      frame();
      expect(engine.camera.position.distanceTo(start)).toBeGreaterThan(0);
      const target = viewDirectionFor(axis, engine.camera.up);
      for (let i = 0; i < 150; i++) {
        frame();
        expect(engine.camera.position.distanceTo(pivot)).toBeCloseTo(2, 9);
        expect(engine.controls.target.equals(pivot)).toBe(true);
      }
      expect(
        engine.camera.position.distanceTo(pivot.clone().addScaledVector(target.direction, 2)),
      ).toBeLessThan(1e-4);
      expect(
        new Vector3(0, 1, 0).applyQuaternion(engine.camera.quaternion).distanceTo(target.up),
      ).toBeLessThan(1e-4);
      expect(
        engine.camera
          .getWorldDirection(new Vector3())
          .distanceTo(target.direction.clone().negate()),
      ).toBeLessThan(1e-4);
      expect(engine.camera.up.equals(new Vector3(0, 0, 1))).toBe(true);
    },
  );

  it('cancels a running axis flight when a new orbit starts', () => {
    const { canvas, engine } = setup(navigationBundle());
    engine.axisView('z');
    frame(4);
    const interruptedPosition = engine.camera.position.clone();
    const interruptedOrientation = engine.camera.quaternion.clone();
    pointer(canvas, 'pointerdown', 400, 300);
    pointer(canvas, 'pointerup', 400, 300);
    frame(150);
    expect(engine.camera.position.distanceTo(interruptedPosition)).toBeLessThan(1e-10);
    expect(engine.camera.quaternion.angleTo(interruptedOrientation)).toBeLessThan(1e-6);
    expect(
      new Vector3(0, 0, 1)
        .applyQuaternion(engine.camera.quaternion)
        .distanceTo(new Vector3(0, 0, 1)),
    ).toBeGreaterThan(0.1);
  });

  it('stops a spherical flight at navigation bounds while preserving its radius', () => {
    const bundle = navigationBundle();
    const { expanded } = navigationEnvelope(bundle);
    const { engine } = setup(bundle);
    const pivot = new Vector3(2, 0, 0);
    engine.controls.target.copy(pivot);
    engine.camera.position.set(2, 0, 2);
    engine.camera.quaternion.copy(orientationFor(new Vector3(0, 0, 1), new Vector3(0, 1, 0)));
    engine.axisView('x');
    for (let i = 0; i < 150; i++) {
      frame();
      expect(expanded.containsPoint(engine.camera.position)).toBe(true);
      expect(engine.camera.position.distanceTo(pivot)).toBeCloseTo(2, 9);
      expect(engine.controls.target.equals(pivot)).toBe(true);
    }
    expect(engine.camera.position.x).toBeGreaterThan(2.5);
    expect(engine.camera.position.x).toBeLessThanOrEqual(expanded.max.x);
    const stopped = engine.camera.position.clone();
    frame(30);
    expect(engine.camera.position.equals(stopped)).toBe(true);
  });

  it('sweeps the axis arc and stops before a cloud obstacle without shortening the radius', () => {
    const obstacle = new Vector3(Math.SQRT2, Math.SQRT2, 0);
    const bundle = navigationBundle([obstacle.toArray()]);
    const { expanded, collisionRadius } = navigationEnvelope(bundle);
    const { engine } = setup(bundle);
    engine.controls.target.set(0, 0, 0);
    engine.camera.position.set(2, 0, 0);
    engine.camera.lookAt(engine.controls.target);
    engine.axisView('y');
    for (let i = 0; i < 150; i++) {
      frame();
      expect(expanded.containsPoint(engine.camera.position)).toBe(true);
      expect(engine.camera.position.length()).toBeCloseTo(2, 9);
      expect(engine.camera.position.distanceTo(obstacle)).toBeGreaterThanOrEqual(
        collisionRadius - 1e-6,
      );
    }
    expect(engine.camera.position.x).toBeGreaterThan(0.5);
    expect(engine.camera.position.y).toBeGreaterThan(0.5);
    expect(engine.camera.position.distanceTo(obstacle)).toBeLessThan(0.06);
    const stopped = engine.camera.position.clone();
    frame(30);
    expect(engine.camera.position.equals(stopped)).toBe(true);
  });

  it('resets within the navigation room needed for all six demo axis directions', () => {
    const bundle = createDemo('en').bundle;
    const { expanded } = navigationEnvelope(bundle);
    const { engine } = setup(bundle);
    const pivot = engine.controls.target.clone();
    const radius = engine.camera.position.distanceTo(pivot);
    expect(Math.tan((engine.camera.fov * Math.PI) / 360) * radius).toBeCloseTo(
      Math.tan((25 * Math.PI) / 180) * engine.focus.radius * 2.4,
      8,
    );
    for (const direction of [new Vector3(1, 0, 0), new Vector3(0, 1, 0), new Vector3(0, 0, 1)])
      for (const sign of [-1, 1])
        expect(
          expanded.containsPoint(pivot.clone().addScaledVector(direction, radius * sign)),
        ).toBe(true);
  });
});
