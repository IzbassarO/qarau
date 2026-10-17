import {
  Box3,
  BufferAttribute,
  BufferGeometry,
  Line,
  LineBasicMaterial,
  LineDashedMaterial,
  LineSegments,
  PerspectiveCamera,
  Points,
  PointsMaterial,
  Quaternion,
  Scene,
  ShaderMaterial,
  Uint8BufferAttribute,
  Vector3,
  WebGLRenderer,
} from 'three';
import { damp, NavigationControls } from './navigation';
import { applyMouseLook, createFlyState, flyOrientation, step, type FlyState } from './fly';
import { orientationFor, stepAxisView, viewDirectionFor, type Axis } from './gizmo';
import { defaultViewState } from './defaults';
import { hasRgb, type Bundle } from '../lib/bundle';
import {
  colmapPoseToThree,
  csrSlice,
  frameBounds,
  heightRamp,
  median,
  pickNearest,
  quatToMat3,
  type Focus,
  robustBounds,
  verticalFovDeg,
} from '../lib/geometry';

export interface Options {
  buildUp: boolean;
  showCurrent: boolean;
  frustums: boolean;
  findings: boolean;
  pointSize: number;
}

export type ColorMode = 'height' | 'rgb';

const VERT = /* glsl */ `
uniform float uFindings, uSelected;
uniform float uCurrentFrame, uBuildUp, uShowCurrent, uPointSize, uPixelRatio, uRefDist;
attribute float firstSeen;
attribute float visibleNow;
attribute vec3 findingColor; // black = no finding; independent of the base colour mode
attribute float fid;  // finding id, 0 none
varying vec3 vColor;
void main() {
  vec3 c = color;
  float k = 1.0;
  if (firstSeen > 65534.5) c = vec3(0.4196, 0.4471, 0.4902);            // #6B727D never observed
  else if (uBuildUp > 0.5 && firstSeen > uCurrentFrame) c = vec3(0.1647, 0.1961, 0.2510); // #2A3240
  else if (uShowCurrent > 0.5 && visibleNow > 0.5) { c = min(c * 1.25 + 0.12, vec3(1.0)); k = 1.25; }
  if (uFindings > 0.5 && findingColor.r > 0.0 && firstSeen < 65534.5 && !(uBuildUp > 0.5 && firstSeen > uCurrentFrame)) {
    c = findingColor; // #F0565C critical, #F2A33A watch, in both colour modes
    k = fid == uSelected ? 1.9 : 1.35;
    if (uSelected > 0.5 && fid != uSelected) c *= 0.65;
  }
  vColor = c;
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mv;
  gl_PointSize = clamp(uPointSize * k * uPixelRatio * uRefDist / max(-mv.z, 1e-6), 1.5 * uPixelRatio, 6.0 * uPixelRatio); // cap: close-up (through-camera) views turn into blobs above ~6 px
}`;

const FRAG = /* glsl */ `
varying vec3 vColor;
void main() {
  if (length(gl_PointCoord - 0.5) > 0.5) discard;
  gl_FragColor = vec4(vColor, 1.0);
}`;

export interface Callbacks {
  onPick: (point: number | null) => void;
  onFlyModeChange?: (active: boolean) => void;
}

export class ViewerEngine {
  readonly renderer: WebGLRenderer;
  readonly scene = new Scene();
  readonly camera = new PerspectiveCamera(50, 1, 0.01, 100);
  readonly controls: NavigationControls;
  private bounds;
  readonly focus: Focus;
  /** called after every render; the UI uses it to place finding markers */
  onRender?: () => void;
  private rolled = false;
  private mat: ShaderMaterial;
  private pointGeometry: BufferGeometry;
  private heightColors: BufferAttribute;
  private rgbColors?: BufferAttribute;
  private currentColorMode: ColorMode;
  private visibleNow: Uint8Array;
  private visAttr: BufferAttribute;
  private prevFrame = -1;
  private regIdx: number[]; // registered frame indices in time order
  private pathSolid: Line;
  private pathDash: Line;
  private frustumAll: LineSegments;
  private frustumNow: LineSegments;
  private frustumsVisible: boolean;
  private dot: Points;
  private pickMark: Points;
  private navigationBounds: Box3;
  private navigationMargin: number;
  private flyState: FlyState | null = null;
  private flyLookActive = false;
  private flyDistance = 1;
  private heldKeys = new Set<string>();
  private pointerLocked = false;
  private disposed = false;
  private lookDrag: { id: number; x: number; y: number } | null = null;
  private axisAnimation: {
    pivot: Vector3;
    target: ReturnType<typeof viewDirectionFor>;
  } | null = null;
  private dirty = true;
  private raf = 0;
  private tween: null | {
    t0: number;
    p0: Vector3;
    p1: Vector3;
    g0: Vector3;
    g1: Vector3;
    f0: number;
    f1: number;
    q0?: Quaternion;
    q1?: Quaternion;
  } = null;
  private frames = 0;
  private lastFps = performance.now();
  private lastUpdate = performance.now();
  bench = false;
  fps = 0;

  constructor(
    private canvas: HTMLCanvasElement,
    private b: Bundle,
    private cb: Callbacks,
  ) {
    const m = b.manifest;
    const defaults = defaultViewState(b);
    this.currentColorMode = defaults.colorMode;
    this.frustumsVisible = defaults.frustums;
    this.renderer = new WebGLRenderer({ canvas, antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0x000000);
    this.renderer.autoClear = false;

    const centers = m.frames.filter((f) => f.registered && f.center).map((f) => f.center!);
    this.bounds = robustBounds(b.position);
    const r = this.bounds.radius;
    // Navigation includes every point and recorded camera; trimming is only for framing.
    this.navigationBounds = new Box3().setFromArray(b.position);
    for (const center of centers) this.navigationBounds.expandByPoint(new Vector3(...center));
    if (this.navigationBounds.isEmpty()) this.navigationBounds.set(new Vector3(), new Vector3());
    this.navigationMargin = Math.max(
      1e-6,
      this.navigationBounds.getSize(new Vector3()).length() / 2,
    );
    this.regIdx = m.frames.filter((f) => f.registered && f.pose).map((f) => f.i);
    this.focus = { center: this.bounds.center, radius: r, method: 'bounds' };

    // ---- points
    const g = new BufferGeometry();
    this.pointGeometry = g;
    g.setAttribute('position', new BufferAttribute(b.position, 3));
    const heightColors = new Uint8Array(b.position.length);
    const [low, high] = m.world.z_range;
    for (let p = 0; p < m.points.count; p++)
      heightColors.set(heightRamp((b.position[p * 3 + 2] - low) / (high - low || 1)), p * 3);
    this.heightColors = new Uint8BufferAttribute(heightColors, 3, true);
    if (hasRgb(b)) this.rgbColors = new Uint8BufferAttribute(b.color!, 3, true);
    g.setAttribute('color', this.rgbColors ?? this.heightColors);
    g.setAttribute('firstSeen', new BufferAttribute(b.firstSeen, 1));
    this.visibleNow = new Uint8Array(m.points.count);
    this.visAttr = new BufferAttribute(this.visibleNow, 1);
    this.visAttr.setUsage(35048); // DynamicDrawUsage
    g.setAttribute('visibleNow', this.visAttr);
    // findings (v2): per-point severity + id, uploaded once; toggling is a uniform
    const findingColors = new Uint8Array(m.points.count * 3);
    if (b.pointFinding && b.findings) {
      const palette = new Map(
        b.findings.map((x) => [
          x.id,
          x.severity === 'critical' ? [0xf0, 0x56, 0x5c] : [0xf2, 0xa3, 0x3a],
        ]),
      );
      for (let p = 0; p < m.points.count; p++) {
        const color = palette.get(b.pointFinding[p]);
        if (color) findingColors.set(color, p * 3);
      }
    }
    g.setAttribute('findingColor', new Uint8BufferAttribute(findingColors, 3, true));
    g.setAttribute(
      'fid',
      new BufferAttribute(b.pointFinding ?? new Uint16Array(m.points.count), 1),
    );
    // bounding sphere once; robust (outliers must not inflate it) and never recomputed
    g.boundingSphere = null;
    g.computeBoundingSphere();
    g.boundingSphere!.set(new Vector3(...this.bounds.center), r * 4);
    this.mat = new ShaderMaterial({
      vertexShader: VERT,
      fragmentShader: FRAG,
      vertexColors: true,
      uniforms: {
        uCurrentFrame: { value: 0 },
        uBuildUp: { value: defaults.buildUp ? 1 : 0 },
        uShowCurrent: { value: 1 },
        uPointSize: { value: 2.5 },
        uPixelRatio: { value: this.renderer.getPixelRatio() },
        uRefDist: { value: r * 2 },
        uFindings: { value: 1 },
        uSelected: { value: 0 },
      },
    });
    const pts = new Points(g, this.mat);
    pts.frustumCulled = false;
    this.scene.add(pts);

    // ---- camera path: solid up to the current frame, dashed for the rest
    const pathPts = new Float32Array(this.regIdx.length * 3);
    this.regIdx.forEach((fi, k) => pathPts.set(m.frames[fi].center!, k * 3));
    const pg = new BufferGeometry();
    pg.setAttribute('position', new BufferAttribute(pathPts, 3));
    this.pathSolid = new Line(pg, new LineBasicMaterial({ color: 0xcfd6e2 }));
    const dg = pg.clone();
    this.pathDash = new Line(
      dg,
      new LineDashedMaterial({ color: 0x4a5160, dashSize: r * 0.015, gapSize: r * 0.015 }),
    );
    this.pathDash.computeLineDistances();
    this.pathSolid.frustumCulled = this.pathDash.frustumCulled = false;
    this.scene.add(this.pathSolid, this.pathDash);

    // ---- frustums: every N-th registered frame (<= ~40), current one larger
    const every = Math.max(1, Math.ceil(this.regIdx.length / 40));
    const sel = this.regIdx.filter((_, k) => k % every === 0);
    const fa = new Float32Array(sel.length * 16 * 3);
    sel.forEach((fi, k) => this.writeFrustum(fa, k * 48, fi, r * 0.04));
    const fg = new BufferGeometry();
    fg.setAttribute('position', new BufferAttribute(fa, 3));
    this.frustumAll = new LineSegments(fg, new LineBasicMaterial({ color: 0x7d8696 }));
    const ng = new BufferGeometry();
    ng.setAttribute('position', new BufferAttribute(new Float32Array(48), 3));
    this.frustumNow = new LineSegments(ng, new LineBasicMaterial({ color: 0x5b9bff }));
    const dg2 = new BufferGeometry();
    dg2.setAttribute('position', new BufferAttribute(new Float32Array(3), 3));
    this.dot = new Points(
      dg2,
      new PointsMaterial({ color: 0x5b9bff, size: 9, sizeAttenuation: false }),
    );
    this.frustumAll.frustumCulled = this.frustumNow.frustumCulled = this.dot.frustumCulled = false;
    this.scene.add(this.frustumAll, this.frustumNow, this.dot);

    // ---- picked point marker
    const mg = new BufferGeometry();
    mg.setAttribute('position', new BufferAttribute(new Float32Array(3), 3));
    this.pickMark = new Points(
      mg,
      new PointsMaterial({ color: 0xffffff, size: 16, sizeAttenuation: false }),
    );
    this.pickMark.visible = false;
    this.pickMark.frustumCulled = false;
    this.scene.add(this.pickMark);

    // ---- camera + controls (Blender-like)
    this.camera.up.set(0, 0, 1);
    this.camera.aspect = canvas.clientWidth / Math.max(1, canvas.clientHeight) || 1;
    this.camera.near = r * 0.0005;
    this.camera.far = this.navigationMargin * 60;
    this.controls = new NavigationControls(this.camera, canvas, {
      points: b.position,
      bounds: this.navigationBounds,
      minDistance: r * 0.002,
      collisionRadius: r * 0.003,
      boundsMargin: this.navigationMargin,
      onChange: () => (this.dirty = true),
      onStart: () => {
        if (this.flyMode) return;
        this.tween = null;
        this.axisAnimation = null;
        this.exitRoll();
      },
    });
    this.resetView(false);

    canvas.tabIndex = 0;
    canvas.addEventListener('pointerdown', this.onDown);
    canvas.addEventListener('pointerup', this.onUp);
    canvas.addEventListener('pointermove', this.onLookDrag);
    canvas.addEventListener('pointercancel', this.onLookEnd);
    canvas.addEventListener('lostpointercapture', this.onLookEnd);
    document.addEventListener('keydown', this.onKey);
    document.addEventListener('keyup', this.onKeyUp);
    document.addEventListener('mousemove', this.onMouseLook);
    document.addEventListener('pointerlockchange', this.onPointerLockChange);
    document.addEventListener('focusin', this.onFocusIn);
    document.addEventListener('visibilitychange', this.onVisibility);
    window.addEventListener('blur', this.onBlur);
    canvas.addEventListener('contextmenu', this.noCtx);
    this.setFrame(0);
    this.loop();
  }

  // ------------------------------------------------------------ frames / options
  /** Fill 5 vertices*... pyramid (apex + 4 corners, 8 segments) for frame fi at depth d. */
  private writeFrustum(out: Float32Array, o: number, fi: number, d: number) {
    const f = this.b.manifest.frames[fi];
    const R = quatToMat3(f.pose!.qvec);
    const [fx, fy, cx, cy] = f.camera!.K;
    const c = f.center!;
    const corner = (u: number, v: number) => {
      const x = ((u - cx) / fx) * d,
        y = ((v - cy) / fy) * d,
        z = d;
      // world = C + Rᵀ (x, y, z)
      return [
        c[0] + R[0] * x + R[3] * y + R[6] * z,
        c[1] + R[1] * x + R[4] * y + R[7] * z,
        c[2] + R[2] * x + R[5] * y + R[8] * z,
      ];
    };
    const W = f.camera!.width,
      H = f.camera!.height;
    const q = [corner(0, 0), corner(W, 0), corner(W, H), corner(0, H)];
    const seg: number[][] = [];
    for (let k = 0; k < 4; k++) seg.push(c, q[k]);
    for (let k = 0; k < 4; k++) seg.push(q[k], q[(k + 1) % 4]);
    seg.forEach((v, k) => out.set(v, o + k * 3));
  }

  setFrame(i: number) {
    const b = this.b;
    const prev = this.prevFrame;
    if (prev >= 0)
      for (const p of csrSlice(b.frameOffsets, b.frameInPoints, prev)) this.visibleNow[p] = 0;
    for (const p of csrSlice(b.frameOffsets, b.frameInPoints, i)) this.visibleNow[p] = 1;
    this.visAttr.needsUpdate = true; // the only per-frame upload
    this.prevFrame = i;
    this.mat.uniforms.uCurrentFrame.value = i;

    const f = b.manifest.frames[i];
    // travelled path: registered frames up to i
    let n = 0;
    while (n < this.regIdx.length && this.regIdx[n] <= i) n++;
    this.pathSolid.geometry.setDrawRange(0, n);
    this.pathDash.geometry.setDrawRange(Math.max(0, n - 1), this.regIdx.length);
    if (f.registered && f.pose) {
      const a = this.frustumNow.geometry.getAttribute('position') as BufferAttribute;
      this.writeFrustum(a.array as Float32Array, 0, i, this.bounds.radius * 0.12);
      a.needsUpdate = true;
      (this.dot.geometry.getAttribute('position') as BufferAttribute).set(f.center!);
      (this.dot.geometry.getAttribute('position') as BufferAttribute).needsUpdate = true;
    }
    this.updateFrustumVisibility();
    this.dirty = true;
  }

  private updateFrustumVisibility() {
    const frame = this.b.manifest.frames[this.prevFrame];
    this.frustumAll.visible = this.frustumsVisible;
    this.frustumNow.visible = this.dot.visible =
      this.frustumsVisible && !!frame?.registered && !!frame.pose;
  }

  setOptions(o: Options) {
    const u = this.mat.uniforms;
    u.uBuildUp.value = o.buildUp ? 1 : 0;
    u.uShowCurrent.value = o.showCurrent ? 1 : 0;
    u.uFindings.value = o.findings ? 1 : 0;
    u.uPointSize.value = o.pointSize;
    this.frustumsVisible = o.frustums;
    this.updateFrustumVisibility();
    this.dirty = true;
  }

  get colorMode(): ColorMode {
    return this.currentColorMode;
  }

  /** Swap precomputed attributes; positions, tracks and finding colours stay on the GPU. */
  setColorMode(mode: ColorMode) {
    if (mode === 'rgb' && !this.rgbColors) return;
    this.currentColorMode = mode;
    this.pointGeometry.setAttribute('color', mode === 'rgb' ? this.rgbColors! : this.heightColors);
    this.dirty = true;
  }

  setSelectedFinding(id: number) {
    this.mat.uniforms.uSelected.value = id;
    this.dirty = true;
  }

  /** Project a world point to canvas pixels (after the last render). */
  project(p: number[]) {
    const v = new Vector3(p[0], p[1], p[2]).project(this.camera);
    return {
      x: (v.x * 0.5 + 0.5) * this.canvas.clientWidth,
      y: (0.5 - v.y * 0.5) * this.canvas.clientHeight,
      visible: v.z > -1 && v.z < 1,
    };
  }

  setPicked(p: number | null) {
    if (p === null) this.pickMark.visible = false;
    else {
      const a = this.pickMark.geometry.getAttribute('position') as BufferAttribute;
      a.set(this.b.position.subarray(p * 3, p * 3 + 3));
      a.needsUpdate = true;
      this.pickMark.visible = true;
    }
    this.dirty = true;
  }

  // ------------------------------------------------------------ views
  /** Leave the through-camera view: back to the world-up orbit (drops the roll). */
  private exitRoll = () => {
    if (!this.rolled) return;
    this.rolled = false;
    this.tween = null;
    this.controls.enabled = true;
    this.controls.sync(); // re-aims at the target with camera.up = +Z
    this.dirty = true;
  };

  /** q1 = exact camera rotation (through-camera view, roll included); else normal orbit view. */
  private fly(p1: Vector3, g1: Vector3, fov: number, animate = true, q1?: Quaternion) {
    this.exitFlyMode();
    this.axisAnimation = null;
    this.controls.stop();
    this.exitRoll();
    const p0 = this.camera.position.clone(),
      g0 = this.controls.target.clone();
    if (q1) {
      this.rolled = true;
      this.controls.enabled = false;
    } else this.controls.enabled = true;
    if (!animate) {
      this.camera.position.copy(p1);
      this.controls.target.copy(g1);
      this.controls.sync();
      if (q1) this.camera.quaternion.copy(q1);
      this.camera.fov = fov;
      this.camera.updateProjectionMatrix();
      this.dirty = true;
      return;
    }
    this.tween = {
      t0: performance.now(),
      p0,
      p1,
      g0,
      g1,
      f0: this.camera.fov,
      f1: fov,
      q0: q1 && this.camera.quaternion.clone(),
      q1,
    };
  }

  resetView(animate = true) {
    const fovY = 50;
    const framed = frameBounds(this.bounds, fovY, this.camera.aspect);
    const c = new Vector3(...framed.target);
    const offset = new Vector3(...framed.position).sub(c);
    const expanded = this.navigationBounds.clone().expandByScalar(this.navigationMargin);
    const framingDistance = offset.length();
    let distance = framingDistance;
    // A wide/flat cloud's framing radius can exceed its vertical navigation room.
    // Reset within a sphere that fits all six axis views at the same distance.
    if (expanded.containsPoint(c))
      for (let axis = 0; axis < 3; axis++)
        distance = Math.min(
          distance,
          c.getComponent(axis) - expanded.min.getComponent(axis),
          expanded.max.getComponent(axis) - c.getComponent(axis),
        );
    distance = Math.max(this.bounds.radius * 0.002, distance * 0.999);
    const direction = offset.normalize();
    const right = new Vector3().crossVectors(this.camera.up, direction).normalize();
    const up = new Vector3().crossVectors(direction, right);
    let tanHalfFov = Math.tan((fovY * Math.PI) / 360);
    // Shortening the distance changes near-corner perspective more than centre-plane scale.
    // Fit those corners again at the constrained distance, preserving the framing margin.
    for (const x of [this.bounds.min[0], this.bounds.max[0]])
      for (const y of [this.bounds.min[1], this.bounds.max[1]])
        for (const z of [this.bounds.min[2], this.bounds.max[2]]) {
          const corner = new Vector3(x, y, z).sub(c);
          const depth = Math.max(1e-12, distance - corner.dot(direction));
          tanHalfFov = Math.max(
            tanHalfFov,
            (1.1 * Math.abs(corner.dot(up))) / depth,
            (1.1 * Math.abs(corner.dot(right))) / (depth * this.camera.aspect),
          );
        }
    const fov = (Math.atan(tanHalfFov) * 360) / Math.PI;
    this.fly(c.clone().addScaledVector(direction, distance), c, fov, animate);
  }
  topView() {
    this.axisView('z');
  }
  sideView() {
    this.axisView('x');
  }

  /** Axis buttons orbit the current pivot at the current distance, with stable pole up. */
  axisView(axis: Axis) {
    this.exitFlyMode();
    this.tween = null;
    this.rolled = false;
    this.controls.stop();
    this.controls.enabled = true;
    this.axisAnimation = {
      pivot: this.controls.target.clone(),
      target: viewDirectionFor(axis, this.camera.up),
    };
    this.dirty = true;
  }
  /** Keep the viewing direction, centre on a finding. */
  flyToFinding(centroid: number[], diag: number) {
    const c = new Vector3(...centroid);
    const dir = this.camera.position.clone().sub(this.controls.target);
    if (dir.lengthSq() < 1e-12) dir.set(1, -1.3, 0.8);
    const dist = Math.min(this.bounds.radius * 2, Math.max(diag * 3, this.bounds.radius * 0.1));
    this.fly(c.clone().addScaledVector(dir.normalize(), dist), c, 50);
  }
  zoom(factor: number) {
    this.exitFlyMode();
    this.axisAnimation = null;
    this.tween = null;
    this.exitRoll();
    this.controls.zoom(factor);
  }

  /** Fly into the pose of frame i (position = centre, FOV from fy, looking at median visible depth). */
  viewThroughFrame(i: number, animate = true) {
    const f = this.b.manifest.frames[i];
    if (!f.registered || !f.pose || !f.center || !f.camera) return;
    const { position, forward, quaternion } = colmapPoseToThree(f.pose.qvec, f.pose.tvec);
    const sl = csrSlice(this.b.frameOffsets, this.b.frameInPoints, i);
    const step = Math.max(1, Math.floor(sl.length / 5000));
    const depths: number[] = [];
    const pos = this.b.position;
    for (let k = 0; k < sl.length; k += step) {
      const p = sl[k] * 3;
      const d =
        (pos[p] - position.x) * forward.x +
        (pos[p + 1] - position.y) * forward.y +
        (pos[p + 2] - position.z) * forward.z;
      if (d > 0) depths.push(d);
    }
    const d = median(depths);
    const dist = Number.isFinite(d) ? d : position.distanceTo(new Vector3(...this.bounds.center));
    this.fly(
      position,
      position.clone().addScaledVector(forward, dist),
      verticalFovDeg(f.camera.height, f.camera.K[1]),
      animate,
      quaternion,
    );
  }

  // ------------------------------------------------------------ input
  get flyMode() {
    return this.flyState !== null;
  }

  private enterFlyMode() {
    if (this.flyMode || this.disposed) return;
    this.tween = null;
    this.axisAnimation = null;
    this.rolled = false;
    this.controls.stop();
    this.controls.enabled = false;
    this.flyDistance = Math.max(
      this.camera.position.distanceTo(this.controls.target),
      this.bounds.radius * 0.002,
    );
    this.flyState = createFlyState(
      this.camera.position,
      this.camera.getWorldDirection(new Vector3()),
      new Vector3(1, 0, 0).applyQuaternion(this.camera.quaternion),
    );
    this.flyLookActive = false;
    this.heldKeys.clear();
    this.canvas.focus({ preventScroll: true });
    this.cb.onFlyModeChange?.(true);
    this.requestPointerLock();
  }

  private requestPointerLock() {
    if (!this.canvas.requestPointerLock || document.pointerLockElement) return;
    try {
      // Older browsers return void; rejected requests leave drag-to-look available.
      const request = this.canvas.requestPointerLock();
      void Promise.resolve(request)
        .then(() => {
          if ((!this.flyMode || this.disposed) && document.pointerLockElement === this.canvas)
            document.exitPointerLock?.();
        })
        .catch(() => undefined);
    } catch {
      // Unsupported/denied pointer lock uses the same drag-to-look fallback.
    }
  }

  private exitFlyMode() {
    if (!this.flyMode) return;
    this.flyState = null;
    this.heldKeys.clear();
    this.endLookDrag();
    this.controls.target
      .copy(this.camera.position)
      .addScaledVector(this.camera.getWorldDirection(new Vector3()), this.flyDistance);
    this.controls.stop();
    this.controls.enabled = true;
    this.pointerLocked = false;
    if (document.pointerLockElement === this.canvas) document.exitPointerLock?.();
    this.cb.onFlyModeChange?.(false);
    this.dirty = true;
  }

  private onPointerLockChange = () => {
    const locked = document.pointerLockElement === this.canvas;
    if (locked && (!this.flyMode || this.disposed)) {
      document.exitPointerLock?.();
      return;
    }
    if (!locked && this.pointerLocked) this.exitFlyMode();
    this.pointerLocked = locked;
  };

  private look(dx: number, dy: number) {
    if (!this.flyState || (!dx && !dy)) return;
    this.flyLookActive = true;
    this.flyState = applyMouseLook(this.flyState, dx, dy, 0.002);
    this.dirty = true;
  }

  private onMouseLook = (e: MouseEvent) => {
    if (document.pointerLockElement === this.canvas) this.look(e.movementX, e.movementY);
  };

  private onLookDrag = (e: PointerEvent) => {
    if (!this.flyMode || !this.lookDrag || document.pointerLockElement === this.canvas) return;
    if (e.pointerId !== this.lookDrag.id) return;
    this.look(e.clientX - this.lookDrag.x, e.clientY - this.lookDrag.y);
    this.lookDrag.x = e.clientX;
    this.lookDrag.y = e.clientY;
    e.preventDefault();
  };

  private endLookDrag() {
    const drag = this.lookDrag;
    this.lookDrag = null;
    if (drag && this.canvas.hasPointerCapture?.(drag.id))
      this.canvas.releasePointerCapture(drag.id);
  }
  private onLookEnd = (e: PointerEvent) => {
    if (e.pointerId === this.lookDrag?.id) this.endLookDrag();
  };

  private down = { x: 0, y: 0 };
  private onDown = (e: PointerEvent) => {
    this.down = { x: e.clientX, y: e.clientY };
    if (this.flyMode && e.button === 0 && document.pointerLockElement !== this.canvas) {
      this.canvas.focus({ preventScroll: true });
      this.lookDrag = { id: e.pointerId, x: e.clientX, y: e.clientY };
      this.canvas.setPointerCapture?.(e.pointerId);
      e.preventDefault();
    }
  };
  private onUp = (e: PointerEvent) => {
    this.onLookEnd(e);
    if (
      this.flyMode ||
      e.button !== 0 ||
      e.shiftKey ||
      Math.hypot(e.clientX - this.down.x, e.clientY - this.down.y) > 4
    )
      return;
    this.cb.onPick(this.pickAt(e) ?? null);
  };
  private noCtx = (e: Event) => e.preventDefault();

  private isEditing(target: EventTarget | null) {
    return (
      target instanceof Element &&
      !!target.closest(
        'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"]',
      )
    );
  }
  private keyName(e: KeyboardEvent) {
    // Physical WASD also works with Russian/Kazakh keyboard layouts.
    return /^Key[WASD]$/.test(e.code) ? e.code.slice(3).toLowerCase() : e.key.toLowerCase();
  }
  private onKey = (e: KeyboardEvent) => {
    const key = this.keyName(e);
    const flyExit = this.flyMode && (key === 'f' || e.code === 'KeyF' || key === 'escape');
    if (
      this.isEditing(e.target) ||
      this.isEditing(document.activeElement) ||
      e.ctrlKey ||
      e.metaKey ||
      e.altKey ||
      (document.activeElement !== this.canvas &&
        e.target !== this.canvas &&
        document.pointerLockElement !== this.canvas &&
        !flyExit)
    )
      return;
    if (key === 'f' || e.code === 'KeyF') {
      if (!e.repeat) {
        if (this.flyMode) this.exitFlyMode();
        else this.enterFlyMode();
      }
    } else if (this.flyMode) {
      if (key === 'escape') this.exitFlyMode();
      else if (['w', 'a', 's', 'd', 'shift'].includes(key)) this.heldKeys.add(key);
      else return;
    } else if (key === 'home') this.resetView();
    else if (key === '1') this.axisView('-y');
    else if (key === '3') this.sideView();
    else if (key === '7') this.topView();
    else return;
    if (this.flyMode && e.shiftKey) this.heldKeys.add('shift');
    e.preventDefault();
  };
  private onKeyUp = (e: KeyboardEvent) => {
    this.heldKeys.delete(this.keyName(e));
  };
  private onFocusIn = (e: FocusEvent) => {
    if (this.isEditing(e.target)) this.exitFlyMode();
    else if (e.target !== this.canvas) this.heldKeys.clear();
  };
  private onBlur = () => this.exitFlyMode();
  private onVisibility = () => {
    if (document.hidden) this.exitFlyMode();
  };

  private updateFly(dt: number) {
    if (!this.flyState) return;
    const previousPosition = this.camera.position.clone();
    const previousRotation = this.camera.quaternion.clone();
    const diagonal = this.navigationBounds.getSize(new Vector3()).length();
    this.flyState = step(
      this.flyState,
      {
        forward: this.heldKeys.has('w'),
        backward: this.heldKeys.has('s'),
        left: this.heldKeys.has('a'),
        right: this.heldKeys.has('d'),
        boost: this.heldKeys.has('shift'),
      },
      dt,
      {
        baseSpeed: Math.max(diagonal * 0.15, 1e-6),
        boostMultiplier: 4,
        damping: 10,
        bounds: this.navigationBounds,
        boundsMargin: this.navigationMargin,
      },
    );
    const safe = this.controls.constrainMove(previousPosition, this.flyState.position);
    if (!safe.equals(this.flyState.position)) this.flyState.velocity.set(0, 0, 0);
    this.flyState.position.copy(safe);
    this.camera.position.copy(safe);
    if (this.flyLookActive) {
      const target = flyOrientation(this.flyState);
      this.camera.quaternion.slerp(target, damp(0, 1, 16, dt));
      if (this.camera.quaternion.angleTo(target) < 1e-6) this.camera.quaternion.copy(target);
    }
    this.camera.updateMatrixWorld();
    this.dirty ||=
      !previousPosition.equals(safe) || !previousRotation.equals(this.camera.quaternion);
  }

  private updateAxisView(dt: number) {
    const animation = this.axisAnimation;
    if (!animation) return;
    const target = orientationFor(animation.target.direction, animation.target.up);
    // Sweep short chords of the spherical flight, keeping radius when obstructed.
    const radialAngle = this.camera.position
      .clone()
      .sub(animation.pivot)
      .angleTo(animation.target.direction);
    const angle =
      Math.max(this.camera.quaternion.angleTo(target), radialAngle) * damp(0, 1, 10, dt);
    const radius = this.camera.position.distanceTo(animation.pivot);
    const maxAngle = Math.min(
      0.025,
      Math.sqrt((this.bounds.radius * 0.003) / Math.max(radius, 1e-9)),
    );
    const steps = Math.max(1, Math.ceil(angle / maxAngle));
    for (let i = 0; i < steps; i++) {
      const next = stepAxisView(
        {
          position: this.camera.position,
          quaternion: this.camera.quaternion,
        },
        animation.pivot,
        animation.target,
        dt / steps,
      );
      const safe = this.controls.constrainMove(this.camera.position, next.position);
      if (!safe.equals(next.position)) {
        this.axisAnimation = null;
        break;
      }
      this.camera.position.copy(next.position);
      this.camera.quaternion.copy(next.quaternion);
    }
    if (
      this.camera.quaternion.angleTo(target) < 1e-5 &&
      this.camera.position.clone().sub(animation.pivot).angleTo(animation.target.direction) < 1e-5
    )
      this.axisAnimation = null;
    this.camera.updateMatrixWorld();
    this.dirty = true;
  }

  private pickAt(e: MouseEvent): number | undefined {
    const rc = this.canvas.getBoundingClientRect();
    this.camera.updateMatrixWorld();
    const vp = this.camera.projectionMatrix.clone().multiply(this.camera.matrixWorldInverse);
    const p = pickNearest(
      this.b.position,
      vp.elements,
      rc.width,
      rc.height,
      e.clientX - rc.left,
      e.clientY - rc.top,
      8,
    );
    return p < 0 ? undefined : p;
  }

  // ------------------------------------------------------------ loop
  resize() {
    const w = this.canvas.clientWidth,
      h = this.canvas.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.dirty = true;
  }

  private loop = () => {
    this.raf = requestAnimationFrame(this.loop);
    const now = performance.now();
    const dt = Math.min(0.05, Math.max(0, (now - this.lastUpdate) / 1000));
    this.lastUpdate = now;
    if (this.tween) {
      const tw = this.tween;
      const k = Math.min(1, (performance.now() - tw.t0) / 400);
      const s = k * k * (3 - 2 * k);
      const previousPosition = this.camera.position.clone();
      this.camera.position.lerpVectors(tw.p0, tw.p1, s);
      this.controls.target.lerpVectors(tw.g0, tw.g1, s);
      // Source-camera views reproduce the recorded pose exactly, roll included.
      this.controls.sync(tw.q1 ? undefined : previousPosition);
      if (tw.q0 && tw.q1) this.camera.quaternion.slerpQuaternions(tw.q0, tw.q1, s);
      this.camera.fov = tw.f0 + (tw.f1 - tw.f0) * s;
      this.camera.updateProjectionMatrix();
      if (k >= 1) this.tween = null;
      this.dirty = true;
    }
    this.updateFly(dt);
    this.updateAxisView(dt);
    if (this.bench && !this.flyMode && !this.axisAnimation) {
      // steady-state measurement: slowly orbit so every frame is a real redraw
      const t = this.controls.target,
        p = this.camera.position;
      const a = 0.01,
        dx = p.x - t.x,
        dy = p.y - t.y;
      p.x = t.x + dx * Math.cos(a) - dy * Math.sin(a);
      p.y = t.y + dx * Math.sin(a) + dy * Math.cos(a);
      this.controls.sync();
      this.dirty = true;
    }
    if (!this.rolled && !this.flyMode && !this.axisAnimation && this.controls.update(dt))
      this.dirty = true;
    // fps = frames actually rendered per second; renders are on demand, so idle reads 0
    if (this.dirty) {
      this.dirty = false;
      this.render();
      this.frames++;
    }
    if (now - this.lastFps > 1000) {
      this.fps = (this.frames * 1000) / (now - this.lastFps);
      this.frames = 0;
      this.lastFps = now;
    }
  };

  private render() {
    const r = this.renderer;
    const w = this.canvas.clientWidth,
      h = this.canvas.clientHeight;
    r.setViewport(0, 0, w, h);
    r.setScissorTest(false);
    r.clear();
    // Looking through the video camera: frustum lines would cut across the view, hide them.
    const c = (this.dot.geometry.getAttribute('position') as BufferAttribute).array;
    const atCam =
      this.dot.visible &&
      this.camera.position.distanceTo(new Vector3(c[0], c[1], c[2])) < this.bounds.radius * 0.02;
    const shown = [this.frustumAll.visible, this.frustumNow.visible, this.dot.visible];
    if (atCam) this.frustumAll.visible = this.frustumNow.visible = this.dot.visible = false;
    r.render(this.scene, this.camera);
    [this.frustumAll.visible, this.frustumNow.visible, this.dot.visible] = shown;
    this.onRender?.();
  }

  dispose() {
    this.disposed = true;
    this.exitFlyMode();
    this.axisAnimation = null;
    cancelAnimationFrame(this.raf);
    const c = this.canvas;
    c.removeEventListener('pointerdown', this.onDown);
    c.removeEventListener('pointerup', this.onUp);
    c.removeEventListener('pointermove', this.onLookDrag);
    c.removeEventListener('pointercancel', this.onLookEnd);
    c.removeEventListener('lostpointercapture', this.onLookEnd);
    document.removeEventListener('keydown', this.onKey);
    document.removeEventListener('keyup', this.onKeyUp);
    document.removeEventListener('mousemove', this.onMouseLook);
    document.removeEventListener('pointerlockchange', this.onPointerLockChange);
    document.removeEventListener('focusin', this.onFocusIn);
    document.removeEventListener('visibilitychange', this.onVisibility);
    window.removeEventListener('blur', this.onBlur);
    c.removeEventListener('contextmenu', this.noCtx);
    this.controls.dispose();
    this.renderer.dispose();
  }
}
