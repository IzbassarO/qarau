import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Box3, Line, LineSegments, Points, PointsMaterial, ShaderMaterial, Vector3 } from 'three';
import { createDemo } from '../fixtures/demo';
import type { Bundle } from '../lib/bundle';
import { heightRamp, robustBounds } from '../lib/geometry';
import { ViewerEngine } from './engine';
import { orientationFor, viewDirectionFor } from './gizmo';
import { estimateGround } from './constraints';

// Keep the actual camera, navigation, observer physics and gizmo math; jsdom has no GPU.
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
  const onAlignModeChange = vi.fn();
  const onPick = vi.fn();
  const engine = new ViewerEngine(canvas, bundle, { onFlyModeChange, onPick, onAlignModeChange });
  engines.add(engine);
  canvas.focus();
  return { canvas, engine, onFlyModeChange, onPick, onAlignModeChange };
}

function key(
  target: EventTarget,
  value: string,
  {
    type = 'keydown',
    code,
    repeat = false,
    shiftKey = false,
    ctrlKey = false,
    altKey = false,
  }: {
    type?: string;
    code?: string;
    repeat?: boolean;
    shiftKey?: boolean;
    ctrlKey?: boolean;
    altKey?: boolean;
  } = {},
) {
  target.dispatchEvent(
    new KeyboardEvent(type, {
      key: value,
      code,
      repeat,
      shiftKey,
      ctrlKey,
      altKey,
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

describe('ViewerEngine point info tool', () => {
  it('selects through the existing picker only when active and preserves orbit during drags', () => {
    const { canvas, engine, onPick } = setup(navigationBundle());
    const pixel = engine.project([1, -1, 1]);
    pointer(canvas, 'pointerdown', pixel.x, pixel.y);
    pointer(canvas, 'pointerup', pixel.x, pixel.y);
    expect(onPick).not.toHaveBeenCalled();
    engine.setPointInfoMode(true);
    expect(engine.controls.enabled).toBe(true);
    pointer(canvas, 'pointerdown', pixel.x, pixel.y);
    pointer(canvas, 'pointerup', pixel.x, pixel.y);
    expect(onPick).toHaveBeenCalledOnce();
    expect(onPick.mock.calls[0][0]).toBeTypeOf('number');
    onPick.mockClear();
    const rotation = engine.camera.quaternion.clone();
    pointer(canvas, 'pointerdown', pixel.x, pixel.y);
    pointer(canvas, 'pointermove', pixel.x + 50, pixel.y + 20);
    frame(3);
    pointer(canvas, 'pointerup', pixel.x + 50, pixel.y + 20);
    frame(60);
    expect(onPick).not.toHaveBeenCalled();
    expect(engine.camera.quaternion.angleTo(rotation)).toBeGreaterThan(0.01);
  });

  it('rejects an out-and-back drag, cancelled or unmatched pointers, and double-click events', () => {
    const { canvas, engine, onPick } = setup(navigationBundle());
    engine.setPointInfoMode(true);
    const p = engine.project([1, -1, 1]);
    pointer(canvas, 'pointerdown', p.x, p.y);
    pointer(canvas, 'pointermove', p.x + 50, p.y);
    pointer(canvas, 'pointermove', p.x, p.y);
    pointer(canvas, 'pointerup', p.x, p.y);
    pointer(canvas, 'pointerdown', p.x, p.y);
    pointer(canvas, 'pointercancel', p.x, p.y);
    pointer(canvas, 'pointerup', p.x, p.y);
    pointer(canvas, 'pointerup', p.x, p.y);
    canvas.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, clientX: p.x, clientY: p.y }));
    expect(onPick).not.toHaveBeenCalled();
  });

  it('keeps info, fly and alignment modes exclusive without disabling orbit', () => {
    const { canvas, engine, onPick } = setup(navigationBundle());
    key(canvas, 'f');
    engine.setPointInfoMode(true);
    expect(engine.flyMode).toBe(false);
    expect(engine.controls.enabled).toBe(true);
    engine.beginSurfaceAlignment();
    engine.setPointInfoMode(true);
    expect(engine.controls.enabled).toBe(true);
    key(canvas, 'f');
    key(canvas, 'Escape');
    const p = engine.project([1, -1, 1]);
    pointer(canvas, 'pointerdown', p.x, p.y);
    pointer(canvas, 'pointerup', p.x, p.y);
    expect(onPick).not.toHaveBeenCalled();
  });
});

describe('ViewerEngine far layer', () => {
  const farPoints = (engine: ViewerEngine) =>
    engine.scene.getObjectByName('far') as Points<import('three').BufferGeometry, PointsMaterial>;

  it('renders separate gray points, hidden by default, with an independent visibility toggle', () => {
    const bundle = createDemo('en').bundle;
    const { engine } = setup(bundle);
    const far = farPoints(engine);
    const object = engine.scene.children.find(
      (child) => child instanceof Points && child.material instanceof ShaderMaterial,
    ) as Points;
    expect(engine.hasFarLayer()).toBe(true);
    expect(far).toBeInstanceOf(Points);
    expect(far).not.toBe(object);
    expect(far.geometry.getAttribute('position').array).toBe(bundle.far!.position);
    expect(far.geometry.getAttribute('position').count).toBe(bundle.manifest.far!.count);
    expect(object.geometry.getAttribute('position').array).toBe(bundle.position);
    expect(object.geometry.getAttribute('position').count).toBe(bundle.manifest.points.count);
    expect(far.geometry.getAttribute('color')).toBeUndefined();
    expect(far.material.vertexColors).toBe(false);
    expect(far.material.color.getHexString()).toBe('6b727d');
    expect(far.visible).toBe(false);
    engine.setFarVisible(true);
    expect(far.visible).toBe(true);
    engine.setFrame(5);
    engine.setColorMode('height');
    expect(far.visible).toBe(true);
    expect(far.material.color.getHexString()).toBe('6b727d');
    engine.setFarVisible(false);
    expect(far.visible).toBe(false);
    expect(object.visible).toBe(true);
  });

  it('reads the not-seen CSS token from the canvas', () => {
    const style = document.createElement('style');
    style.textContent = 'canvas { --color-notseen: #123456; }';
    document.body.append(style);
    const { engine } = setup();
    expect(farPoints(engine).material.color.getHexString()).toBe('123456');
  });

  it.each([undefined, { position: new Float32Array(), color: new Uint8Array() }])(
    'has no far object for an absent or empty layer (%s)',
    (far) => {
      const { engine } = setup({ ...createDemo('en').bundle, far });
      expect(engine.hasFarLayer()).toBe(false);
      expect(farPoints(engine)).toBeUndefined();
      expect(() => engine.setFarVisible(true)).not.toThrow();
    },
  );

  it('keeps framing and navigation bounds identical with very distant far points', () => {
    const bundle = navigationBundle();
    const { engine: without } = setup({ ...bundle, far: undefined });
    const { engine: withFar } = setup({
      ...bundle,
      far: { position: new Float32Array([1e6, -1e6, 1e6]), color: new Uint8Array(3) },
    });
    expect(withFar.focus).toEqual(without.focus);
    expect(withFar.camera.position.toArray()).toEqual(without.camera.position.toArray());
    expect(withFar.camera.projectionMatrix.toArray()).toEqual(
      without.camera.projectionMatrix.toArray(),
    );
    expect(withFar.controls.target.toArray()).toEqual(without.controls.target.toArray());
    withFar.setFarVisible(true);
    for (const engine of [without, withFar]) {
      engine.camera.position.set(1e5, -1e5, 1e5);
      engine.controls.sync();
    }
    expect(withFar.camera.position.toArray()).toEqual(without.camera.position.toArray());
    expect(withFar.camera.position.length()).toBeLessThan(10);
    withFar.resetView(false);
    without.resetView(false);
    expect(withFar.camera.position.toArray()).toEqual(without.camera.position.toArray());
  });

  it('disposes and removes the far geometry and material on unload', () => {
    const { engine } = setup();
    const far = farPoints(engine);
    const geometry = vi.spyOn(far.geometry, 'dispose');
    const material = vi.spyOn(far.material, 'dispose');
    engine.dispose();
    engines.delete(engine);
    expect(geometry).toHaveBeenCalledOnce();
    expect(material).toHaveBeenCalledOnce();
    expect(far.parent).toBeNull();
  });
});

describe('ViewerEngine observer input', () => {
  it.each(['control', 'alt'] as const)('uses the configured %s boost key', (boostKey) => {
    const travel = (boost: boolean) => {
      const { canvas, engine } = setup(navigationBundle());
      engine.setBoostKey(boostKey);
      const start = engine.camera.position.clone();
      key(canvas, 'f', { code: 'KeyF' });
      const modifiers = {
        ctrlKey: boost && boostKey === 'control',
        altKey: boost && boostKey === 'alt',
      };
      if (boost) key(canvas, boostKey === 'control' ? 'Control' : 'Alt', modifiers);
      key(canvas, 'w', { code: 'KeyW', ...modifiers });
      frame(10);
      const distance = engine.camera.position.distanceTo(start);
      key(canvas, 'f');
      return distance;
    };
    const normal = travel(false);
    expect(normal).toBeGreaterThan(0);
    // Adaptive speed can change as the two paths diverge from the cloud.
    expect(travel(true)).toBeGreaterThan(normal * 2);
  });

  it('toggles with F, ignores auto-repeat and keeps Observer active after Escape', () => {
    const { canvas, engine, onFlyModeChange } = setup();
    key(canvas, 'f', { code: 'KeyF' });
    expect(engine.flyMode).toBe(true);
    expect(engine.controls.enabled).toBe(false);
    key(canvas, 'f', { code: 'KeyF', repeat: true });
    expect(engine.flyMode).toBe(true);
    expect(onFlyModeChange).toHaveBeenCalledTimes(1);
    key(canvas, 'Escape');
    expect(engine.observerMode).toBe(true);
    expect(engine.controls.enabled).toBe(false);
    key(canvas, 'F', { code: 'KeyF' });
    expect(engine.observerMode).toBe(false);
    expect(engine.controls.enabled).toBe(true);
    key(canvas, 'F', { code: 'KeyF' });
    key(canvas, 'F', { code: 'KeyF' });
    expect(onFlyModeChange.mock.calls).toEqual([[true], [false], [true], [false]]);
  });

  it('ignores shortcuts in editors and keeps Observer active while settings have focus', () => {
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
    key(canvas, 'w', { code: 'KeyW' });
    frame(10);
    const input = document.createElement('select');
    document.body.append(input);
    input.focus();
    key(input, 'f');
    key(input, 'w', { code: 'KeyW' });
    key(canvas, 'w', { code: 'KeyW' });
    frame(120);
    const rested = engine.camera.position.clone();
    frame(30);
    expect(engine.camera.position.distanceTo(rested)).toBeLessThan(1e-7);
    expect(engine.observerMode).toBe(true);
    expect(onFlyModeChange.mock.calls).toEqual([[true]]);
    canvas.focus();
    key(canvas, 'w', { code: 'KeyW' });
    frame(10);
    expect(engine.camera.position.distanceTo(rested)).toBeGreaterThan(0);
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
    // Exiting fly also removes the last sub-pixel residual roll from damping.
    expect(engine.camera.quaternion.angleTo(beforeExit)).toBeLessThan(1e-5);
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

  it('requests pointer lock on a canvas click and keeps Observer active when Esc releases it', () => {
    const { canvas, engine, onFlyModeChange } = setup();
    const request = vi.fn(() => setLock(canvas));
    Object.defineProperty(canvas, 'requestPointerLock', { value: request });
    const orientation = engine.camera.quaternion.clone();
    key(canvas, 'f');
    expect(request).not.toHaveBeenCalled();
    canvas.dispatchEvent(new MouseEvent('click', { button: 0, bubbles: true }));
    expect(request).toHaveBeenCalledOnce();
    expect(document.pointerLockElement).toBe(canvas);
    mouseLook(90, -30);
    frame(20);
    expect(engine.camera.quaternion.angleTo(orientation)).toBeGreaterThan(0.1);
    setLock(null);
    key(canvas, 'Escape');
    expect(engine.observerMode).toBe(true);
    expect(engine.controls.enabled).toBe(false);
    expect(onFlyModeChange.mock.calls).toEqual([[true]]);
    key(canvas, 'f');
    expect(engine.controls.enabled).toBe(true);
    expect(onFlyModeChange.mock.calls).toEqual([[true], [false]]);
  });

  it('keeps drag look available after a rejected pointer-lock request', async () => {
    const { canvas, engine } = setup();
    const request = vi.fn().mockRejectedValue(new Error('Pointer lock denied'));
    Object.defineProperty(canvas, 'requestPointerLock', { value: request });
    const orientation = engine.camera.quaternion.clone();
    key(canvas, 'f');
    canvas.dispatchEvent(new MouseEvent('click', { button: 0, bubbles: true }));
    await Promise.resolve();
    expect(request).toHaveBeenCalledOnce();
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
    forward.z = 0;
    forward.normalize();
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

  it('looks with two-finger wheel input while continuing keyboard movement', () => {
    const { canvas, engine } = setup(navigationBundle());
    key(canvas, 'f');
    key(canvas, 'ArrowUp', { code: 'ArrowUp' });
    frame(10);
    const position = engine.camera.position.clone();
    const orientation = engine.camera.quaternion.clone();
    const wheel = new WheelEvent('wheel', {
      deltaX: 70,
      deltaY: -25,
      bubbles: true,
      cancelable: true,
    });
    canvas.dispatchEvent(wheel);
    expect(wheel.defaultPrevented).toBe(true);
    frame(20);
    expect(engine.camera.quaternion.angleTo(orientation)).toBeGreaterThan(0.05);
    expect(engine.camera.position.distanceTo(position)).toBeGreaterThan(0);
    expect(engine.camera.position.z).toBeCloseTo(position.z, 8);
    expect(engine.observerMode).toBe(true);
  });

  it('uses Shift to descend through a point and below terrain without horizontal movement', () => {
    const bundle = navigationBundle();
    const { canvas, engine } = setup(bundle);
    engine.camera.position.set(1, 1, -0.99);
    engine.camera.lookAt(1, 2, -0.99);
    engine.setObserverMode();
    const start = engine.camera.position.clone();
    key(canvas, 'Shift', { code: 'ShiftLeft', shiftKey: true });
    frame(180);
    key(canvas, 'Shift', { code: 'ShiftLeft', type: 'keyup' });
    expect(engine.camera.position.z).toBeLessThan(-1.01);
    expect(estimateGround(bundle.position).distanceToPoint(engine.camera.position)).toBeLessThan(0);
    expect(engine.camera.position.x).toBeCloseTo(start.x, 8);
    expect(engine.camera.position.y).toBeCloseTo(start.y, 8);
    key(canvas, ' ', { code: 'Space' });
    frame(180);
    expect(engine.camera.position.z).toBeGreaterThan(-1);
    expect(engine.camera.position.x).toBeCloseTo(start.x, 8);
    expect(engine.camera.position.y).toBeCloseTo(start.y, 8);
  });

  it('uses V for a third-person camera and keeps the marker at the moving observer position', () => {
    const { canvas, engine } = setup(navigationBundle());
    key(canvas, 'f');
    const position = engine.camera.position.clone();
    key(canvas, 'v', { code: 'KeyV' });
    frame();
    expect(engine.thirdPerson).toBe(true);
    const marker = engine.scene.getObjectByName('observer-marker')!;
    expect(marker.visible).toBe(true);
    expect(marker.position.distanceTo(position)).toBeLessThan(1e-8);
    expect(engine.camera.position.distanceTo(marker.position)).toBeGreaterThan(0);
    key(canvas, 'v', { code: 'KeyV', repeat: true });
    expect(engine.thirdPerson).toBe(true);
    key(canvas, 'w', { code: 'KeyW' });
    frame(20);
    key(canvas, 'w', { code: 'KeyW', type: 'keyup' });
    frame(90);
    expect(marker.position.distanceTo(position)).toBeGreaterThan(0);
    const moved = marker.position.clone();
    key(canvas, 'v', { code: 'KeyV' });
    frame();
    expect(engine.thirdPerson).toBe(false);
    expect(marker.visible).toBe(false);
    expect(engine.camera.position.distanceTo(moved)).toBeLessThan(1e-7);
    engine.setHumanFigureVisible(true);
    key(canvas, 'v', { code: 'KeyV' });
    frame();
    expect(engine.scene.getObjectByName('observer-human')?.visible).not.toBe(true);
    key(canvas, 'f');
    expect(marker.visible).toBe(false);
  });

  it('shows an optional human only with explicit metric calibration and third-person view', () => {
    const source = navigationBundle();
    const bundle = {
      ...source,
      manifest: {
        ...source.manifest,
        world: { ...source.manifest.world, scale_m_per_unit: 0.5 },
      },
    };
    const { canvas, engine } = setup(bundle);
    engine.setObserverMode();
    key(canvas, 'v', { code: 'KeyV' });
    frame();
    const human = engine.scene.getObjectByName('observer-human')!;
    expect(human.visible).toBe(false);
    engine.setHumanFigureVisible(true);
    frame();
    expect(human.visible).toBe(true);
    human.updateMatrixWorld(true);
    expect(new Box3().setFromObject(human).getSize(new Vector3()).z).toBeCloseTo(3.5, 5);
    key(canvas, 'v', { code: 'KeyV' });
    frame();
    expect(human.visible).toBe(false);
  });

  it('releases lock on blur and disposes keyboard, pointer-lock and animation listeners', () => {
    const { canvas, engine, onFlyModeChange } = setup();
    Object.defineProperty(canvas, 'requestPointerLock', { value: vi.fn(() => setLock(canvas)) });
    key(canvas, 'f');
    canvas.dispatchEvent(new MouseEvent('click', { button: 0, bubbles: true }));
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
    canvas.dispatchEvent(new MouseEvent('click', { button: 0, bubbles: true }));
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

  it('clears held movement on toolbar focus and lets F return to Orbit from that button', () => {
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
    expect(engine.observerMode).toBe(true);
    key(button, 'f');
    expect(engine.observerMode).toBe(false);
  });

  it('releases a pointer lock request that resolves after disposal', async () => {
    const { canvas, engine } = setup();
    let resolveRequest!: () => void;
    const request = new Promise<void>((resolve) => {
      resolveRequest = resolve;
    });
    Object.defineProperty(canvas, 'requestPointerLock', { value: vi.fn(() => request) });
    key(canvas, 'f');
    canvas.dispatchEvent(new MouseEvent('click', { button: 0, bubbles: true }));
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
  const bounds = new Box3().setFromArray(bundle.position);
  for (const frame of bundle.manifest.frames)
    if (frame.registered && frame.center) bounds.expandByPoint(new Vector3(...frame.center));
  return {
    expanded: bounds.clone().expandByScalar(bounds.getSize(new Vector3()).length() / 2),
    collisionRadius: robustBounds(bundle.position).radius * 0.003,
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

  it.each(['z'] as const)(
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
    const bounds = robustBounds(bundle.position);
    expect(pivot.toArray()).toEqual(bounds.center);
    for (const direction of [new Vector3(1, 0, 0), new Vector3(0, 1, 0), new Vector3(0, 0, 1)])
      for (const sign of [-1, 1])
        expect(
          expanded.containsPoint(pivot.clone().addScaledVector(direction, radius * sign)),
        ).toBe(true);
  });
});

describe('ViewerEngine colour modes', () => {
  function cloud(engine: ViewerEngine, bundle: Bundle) {
    return engine.scene.children.find(
      (child) =>
        child instanceof Points &&
        child.geometry.getAttribute('position').array === bundle.position,
    ) as Points;
  }

  it('setColorMode swaps height and RGB buffers without rebuilding geometry or point positions', () => {
    const bundle = navigationBundle();
    bundle.color = Uint8Array.from({ length: bundle.position.length }, (_, i) => (i * 23) % 256);
    const { engine } = setup(bundle);
    const points = cloud(engine, bundle);
    const geometry = points.geometry;
    const positions = geometry.getAttribute('position');
    const rgb = geometry.getAttribute('color');
    expect(engine.colorMode).toBe('rgb');
    expect(Array.from(rgb.array)).toEqual(Array.from(bundle.color));
    expect(rgb.normalized).toBe(true);
    expect((points.material as ShaderMaterial).uniforms.uBuildUp.value).toBe(0);

    engine.setColorMode('height');
    const height = geometry.getAttribute('color');
    expect(engine.colorMode).toBe('height');
    const z = bundle.position[2];
    const [low, high] = bundle.manifest.world.z_range;
    expect(Array.from(height.array.slice(0, 3))).toEqual(heightRamp((z - low) / (high - low)));

    engine.setColorMode('rgb');
    expect(engine.colorMode).toBe('rgb');
    expect(geometry.getAttribute('color')).toBe(rgb);
    expect(rgb).not.toBe(height);
    expect(Array.from(rgb.array)).toEqual(Array.from(bundle.color));
    expect(rgb.normalized).toBe(true);
    engine.setColorMode('height');
    expect(engine.colorMode).toBe('height');
    expect(geometry.getAttribute('color')).toBe(height);
    engine.setColorMode('rgb');
    expect(geometry.getAttribute('color')).toBe(rgb);
    expect(points.geometry).toBe(geometry);
    expect(geometry.getAttribute('position')).toBe(positions);
    expect(positions.array).toBe(bundle.position);
  });

  it('keeps critical finding points red and watch points orange in RGB mode', () => {
    const bundle = navigationBundle();
    bundle.findings = [
      {
        id: 1,
        class: 'corrosion',
        severity: 'critical',
        status: 'confirmed',
        n_points: 1,
        centroid: [-1, -1, -1],
        bbox: { min: [-1, -1, -1], max: [-1, -1, -1] },
        frames: [0],
      },
      {
        id: 2,
        class: 'corrosion',
        severity: 'watch',
        status: 'confirmed',
        n_points: 1,
        centroid: [-1, -1, 1],
        bbox: { min: [-1, -1, 1], max: [-1, -1, 1] },
        frames: [0],
      },
    ];
    bundle.pointFinding = new Uint16Array(bundle.manifest.points.count);
    bundle.pointFinding.set([1, 2]);
    bundle.firstSeen[2] = 65535;
    const { engine } = setup(bundle);
    const points = cloud(engine, bundle);
    const overlay = points.geometry.getAttribute('findingColor');
    const material = points.material as ShaderMaterial;
    engine.setColorMode('rgb');
    expect(engine.colorMode).toBe('rgb');
    expect(Array.from(overlay.array.slice(0, 6))).toEqual([0xf0, 0x56, 0x5c, 0xf2, 0xa3, 0x3a]);
    expect(overlay.normalized).toBe(true);
    expect(points.geometry.getAttribute('findingColor')).toBe(overlay);
    expect(material.uniforms.uFindings.value).toBe(1);
    // Coverage and finding attributes are separate from the swapped base RGB buffer.
    expect(points.geometry.getAttribute('firstSeen').getX(2)).toBe(65535);
    expect(Array.from(points.geometry.getAttribute('fid').array.slice(0, 3))).toEqual([1, 2, 0]);
    engine.setColorMode('height');
    expect(points.geometry.getAttribute('findingColor')).toBe(overlay);
  });

  it.each([undefined, new Uint8Array(1)])(
    'stays in height mode when RGB is unavailable (%s)',
    (color) => {
      const bundle = { ...navigationBundle(), color };
      const { engine } = setup(bundle);
      const points = cloud(engine, bundle);
      const height = points.geometry.getAttribute('color');
      engine.setColorMode('rgb');
      expect(engine.colorMode).toBe('height');
      expect(points.geometry.getAttribute('color')).toBe(height);
    },
  );
});

describe('ViewerEngine opt-in display modes', () => {
  it('uses a thin line and dots at every registered frame, with exactly one reusable frustum', () => {
    const bundle = createDemo('en').bundle;
    const { engine } = setup(bundle);
    const line = engine.scene.getObjectByName('camera-path') as Line;
    const dots = engine.scene.getObjectByName('camera-path-dots') as Points;
    const count = bundle.manifest.frames.filter((f) => f.registered && f.pose).length;
    expect(line).toBeInstanceOf(Line);
    expect(dots).toBeInstanceOf(Points);
    expect(dots.geometry.getAttribute('position').count).toBe(count);
    const frustum = engine.scene.getObjectByName('current-camera-frustum') as LineSegments;
    expect(frustum.geometry.getAttribute('position').count).toBe(16);
    const geometry = frustum.geometry;
    for (const i of [0, 4, 10]) {
      engine.setFrame(i);
      expect(engine.scene.children.filter((child) => child instanceof LineSegments)).toEqual([
        frustum,
      ]);
      expect(frustum.geometry).toBe(geometry);
    }
  });

  it('keeps every frustum hidden across frame changes until enabled, and hides them again', () => {
    const demo = createDemo('en').bundle;
    const bundle = {
      ...demo,
      manifest: { ...demo.manifest, frames: [...demo.manifest.frames] },
    };
    bundle.manifest.frames[1] = { ...bundle.manifest.frames[1], registered: false };
    const { engine } = setup(bundle);
    const frustums = engine.scene.children.filter((child) => child instanceof LineSegments);
    expect(frustums).toHaveLength(1);
    const [currentFrustum] = frustums;
    const cameraDot = engine.scene.children.find(
      (child) =>
        child instanceof Points &&
        child.material instanceof PointsMaterial &&
        child.material.color.getHex() === 0x5b9bff,
    )!;
    const cloud = engine.scene.children.find(
      (child) => child instanceof Points && child.material instanceof ShaderMaterial,
    ) as Points;
    const material = cloud.material as ShaderMaterial;
    const options = {
      buildUp: false,
      frustums: false,
      showCurrent: true,
      findings: true,
      pointSize: 2.5,
    };
    expect(material.uniforms.uBuildUp.value).toBe(0);
    expect([...frustums, cameraDot].every((child) => !child.visible)).toBe(true);
    engine.setFrame(2);
    expect([...frustums, cameraDot].every((child) => !child.visible)).toBe(true);

    engine.setOptions({ ...options, frustums: true, buildUp: true });
    expect(material.uniforms.uBuildUp.value).toBe(1);
    expect([...frustums, cameraDot].every((child) => child.visible)).toBe(true);
    engine.setFrame(1);
    expect(currentFrustum.visible).toBe(false);
    expect(cameraDot.visible).toBe(false);
    engine.setFrame(0);
    expect(currentFrustum.visible).toBe(true);
    expect(cameraDot.visible).toBe(true);

    engine.setOptions(options);
    engine.setFrame(2);
    expect(material.uniforms.uBuildUp.value).toBe(0);
    expect([...frustums, cameraDot].every((child) => !child.visible)).toBe(true);
  });

  it('scales the single current frustum with the scene extent', () => {
    const sizes = (scale: number) => {
      const demo = createDemo('en').bundle;
      const bundle = {
        ...demo,
        position: demo.position.map((value) => value * scale),
        manifest: {
          ...demo.manifest,
          frames: demo.manifest.frames.map((frame) => ({
            ...frame,
            center: frame.center?.map((value) => value * scale),
            camera: frame.camera && { ...frame.camera, K: [500, 500, 320, 180] },
          })),
        },
      };
      const { engine } = setup(bundle);
      engine.setOptions({
        buildUp: false,
        frustums: true,
        showCurrent: true,
        findings: true,
        pointSize: 2.5,
      });
      return engine.scene.children
        .filter((child) => child instanceof LineSegments)
        .map((frustum) => {
          expect(frustum.visible).toBe(true);
          const positions = frustum.geometry.getAttribute('position');
          return new Vector3()
            .fromBufferAttribute(positions, 0)
            .distanceTo(new Vector3().fromBufferAttribute(positions, 1));
        });
    };
    const original = sizes(1);
    const scaled = sizes(10);
    expect(original).toHaveLength(1);
    original.forEach((size, index) => {
      expect(size).toBeGreaterThan(0);
      expect(scaled[index] / size).toBeCloseTo(10, 4);
    });
  });
});

describe('ViewerEngine constrained navigation', () => {
  it('finishes a side preset at the terrain boundary instead of redrawing forever', () => {
    const { engine } = setup(navigationBundle());
    engine.controls.target.set(0, 0, -1);
    engine.camera.position.set(0, 0, 1);
    engine.controls.sync();
    engine.sideView();
    frame(180);
    expect(engine.camera.position.z).toBeGreaterThan(-1);
    expect(engine.camera.position.distanceTo(engine.controls.target)).toBeCloseTo(2, 8);
    expect(engine.camera.position.x).toBeGreaterThan(1.9);
    const render = vi.mocked(engine.renderer.render);
    render.mockClear();
    frame(60);
    expect(render).not.toHaveBeenCalled();
  });

  it('aligns a real point pick to the local surface normal and cancels with Escape', () => {
    const points: number[][] = [];
    for (let x = -12; x <= 12; x += 3) for (let y = -12; y <= 12; y += 3) points.push([x, y, -4]);
    for (let x = -2; x <= 2.01; x += 0.4)
      for (let y = -2; y <= 2.01; y += 0.4) points.push([x, y, 1 + 0.3 * x + 0.2 * y]);
    const { engine, canvas, onPick, onAlignModeChange } = setup(navigationBundle(points));
    engine.controls.target.set(0, 0, 1);
    engine.camera.position.set(5, -6, 5);
    engine.controls.sync();
    engine.beginSurfaceAlignment();
    expect(engine.controls.enabled).toBe(false);
    key(canvas, 'Escape');
    expect(engine.controls.enabled).toBe(true);
    expect(onAlignModeChange.mock.calls).toEqual([[true], [false]]);

    const distance = engine.camera.position.distanceTo(engine.controls.target);
    engine.beginSurfaceAlignment();
    const pixel = engine.project([0, 0, 1]);
    pointer(canvas, 'pointerdown', pixel.x, pixel.y);
    pointer(canvas, 'pointerup', pixel.x, pixel.y);
    frame(180);
    expect(onPick).not.toHaveBeenCalled();
    expect(engine.controls.enabled).toBe(true);
    expect(onAlignModeChange.mock.calls.at(-1)).toEqual([false]);
    expect(engine.controls.target.distanceTo(new Vector3(0, 0, 1))).toBeLessThan(1e-4);
    expect(engine.camera.position.distanceTo(engine.controls.target)).toBeCloseTo(distance, 4);
    const forward = engine.camera.getWorldDirection(new Vector3());
    expect(forward.angleTo(new Vector3(0.3, 0.2, -1).normalize())).toBeLessThan(0.01);
    expect(Math.abs(new Vector3(1, 0, 0).applyQuaternion(engine.camera.quaternion).z)).toBeLessThan(
      1e-8,
    );
  });

  it('clamps orbit and zoom above terrain and inside the scene envelope', () => {
    const bundle = navigationBundle();
    const { engine, canvas } = setup(bundle);
    const ground = estimateGround(bundle.position);
    const { expanded } = navigationEnvelope(bundle);
    const radius = robustBounds(bundle.position).radius;
    for (const init of [
      { ctrlKey: true, deltaY: 5000 },
      { deltaX: 16, deltaY: 2000 },
      { ctrlKey: true, deltaY: -5000 },
      { shiftKey: true, deltaX: -2000, deltaY: 1500 },
    ]) {
      canvas.dispatchEvent(new WheelEvent('wheel', init));
      for (let i = 0; i < 60; i++) {
        frame();
        expect(ground.distanceToPoint(engine.camera.position)).toBeGreaterThan(0);
        expect(expanded.containsPoint(engine.camera.position)).toBe(true);
        const distance = engine.camera.position.distanceTo(engine.controls.target);
        expect(distance).toBeGreaterThanOrEqual(radius * 0.002 - 1e-8);
        expect(distance).toBeLessThanOrEqual(radius * 20 + 1e-8);
        expect(engine.camera.up.toArray()).toEqual([0, 0, 1]);
        expect(
          Math.abs(new Vector3(1, 0, 0).applyQuaternion(engine.camera.quaternion).z),
        ).toBeLessThan(1e-7);
      }
    }
  });
});

describe('ViewerEngine object framing', () => {
  it.each([
    { min: [-1, -1, -1], max: [1, 1, 1], aspect: 1.6 },
    { min: [-50, -50, 0], max: [50, 50, 0], aspect: 1.6 },
    { min: [-1, -1, 0], max: [1, 1, 20], aspect: 0.45 },
    { min: [-40, -3, -2], max: [40, 3, 2], aspect: 3.2 },
  ])(
    'fits all object corners after constraining reset distance ($aspect)',
    ({ min, max, aspect }) => {
      const points = [];
      for (const x of [min[0], max[0]])
        for (const y of [min[1], max[1]]) for (const z of [min[2], max[2]]) points.push([x, y, z]);
      const bundle = navigationBundle();
      // Each face occurs four times, so percentile bounds coincide with the box faces.
      bundle.position = Float32Array.from(points.flat());
      bundle.manifest.points.count = points.length;
      const { engine } = setup(bundle);
      engine.camera.aspect = aspect;
      engine.resetView(false);
      engine.camera.updateMatrixWorld();
      const { expanded } = navigationEnvelope(bundle);
      const centre = new Vector3(...min.map((value, axis) => (value + max[axis]) / 2));
      expect(engine.controls.target.toArray()).toEqual(centre.toArray());
      for (const point of points) {
        const ndc = new Vector3(...point).project(engine.camera);
        expect(Math.abs(ndc.x)).toBeLessThanOrEqual(1 / 1.1 + 1e-6);
        expect(Math.abs(ndc.y)).toBeLessThanOrEqual(1 / 1.1 + 1e-6);
        expect(Math.abs(ndc.z)).toBeLessThan(1);
      }
      const distance = engine.camera.position.distanceTo(centre);
      for (const axis of [new Vector3(1, 0, 0), new Vector3(0, 1, 0), new Vector3(0, 0, 1)])
        for (const sign of [-1, 1])
          expect(
            expanded.containsPoint(centre.clone().addScaledVector(axis, distance * sign)),
          ).toBe(true);
    },
  );

  it('uses object bounds for initial/reset framing and keeps outliers and cameras in navigation limits', () => {
    const extra = Array.from({ length: 300 }, (_, i) => [
      i % 2 ? -1 : 1,
      i % 3 ? -1 : 1,
      i % 5 ? -1 : 1,
    ]);
    const bundle = navigationBundle([...extra, [1000, 1000, 1000]]);
    bundle.manifest.frames[0] = {
      ...bundle.manifest.frames[0],
      registered: true,
      center: [-500, 0, 0],
    };
    const { engine } = setup(bundle);
    const bounds = robustBounds(bundle.position);
    expect(bounds.max).toEqual([1, 1, 1]);
    expect(engine.controls.target.toArray()).toEqual([0, 0, 0]);
    const initial = engine.camera.position.clone();
    expect(initial.length()).toBeLessThan(10);
    engine.camera.updateMatrixWorld();
    for (const x of [-1, 1])
      for (const y of [-1, 1])
        for (const z of [-1, 1]) {
          const ndc = new Vector3(x, y, z).project(engine.camera);
          expect(Math.abs(ndc.x)).toBeLessThan(1);
          expect(Math.abs(ndc.y)).toBeLessThan(1);
        }

    // Neither the discarded outlier nor the recorded camera is cut out of the navigation box.
    engine.camera.position.set(900, 900, 900);
    engine.controls.sync();
    expect(engine.camera.position.toArray()).toEqual([900, 900, 900]);
    engine.camera.position.set(-500, 0, 0);
    engine.controls.sync();
    expect(engine.camera.position.toArray()).toEqual([-500, 0, 0]);
    engine.resetView(false);
    expect(engine.camera.position.distanceTo(initial)).toBeLessThan(1e-9);
    expect(engine.controls.target.toArray()).toEqual(bounds.center);
  });
});
