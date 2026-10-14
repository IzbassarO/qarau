import {
  AxesHelper,
  BufferAttribute,
  BufferGeometry,
  Color,
  Line,
  LineBasicMaterial,
  LineDashedMaterial,
  LineSegments,
  PerspectiveCamera,
  Points,
  PointsMaterial,
  Scene,
  ShaderMaterial,
  Uint8BufferAttribute,
  Vector2,
  Vector3,
  WebGLRenderer,
} from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import type { Bundle } from '../lib/bundle';
import {
  colmapPoseToThree,
  csrSlice,
  median,
  pickNearest,
  quatToMat3,
  RAMP,
  robustBounds,
  verticalFovDeg,
} from '../lib/geometry';

export interface Options {
  buildUp: boolean;
  showCurrent: boolean;
  frustums: boolean;
  rgb: boolean;
  pointSize: number;
}

const VERT = /* glsl */ `
uniform float uCurrentFrame, uBuildUp, uShowCurrent, uPointSize, uPixelRatio, uRefDist, uColorMode;
uniform vec2 uZRange;
uniform vec3 uRamp0, uRamp1, uRamp2, uRamp3;
attribute float firstSeen;
attribute float visibleNow;
varying vec3 vColor;
vec3 ramp(float t) {
  float s = clamp(t, 0.0, 1.0) * 3.0;
  if (s < 1.0) return mix(uRamp0, uRamp1, s);
  if (s < 2.0) return mix(uRamp1, uRamp2, s - 1.0);
  return mix(uRamp2, uRamp3, s - 2.0);
}
void main() {
  vec3 c = uColorMode > 0.5 ? color : ramp((position.z - uZRange.x) / (uZRange.y - uZRange.x));
  float k = 1.0;
  if (firstSeen > 65534.5) c = vec3(0.4196, 0.4471, 0.4902);            // #6B727D never observed
  else if (uBuildUp > 0.5 && firstSeen > uCurrentFrame) c = vec3(0.1647, 0.1961, 0.2510); // #2A3240
  else if (uShowCurrent > 0.5 && visibleNow > 0.5) { c = min(c * 1.25 + 0.12, vec3(1.0)); k = 1.25; }
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

const raw = (h: string) => {
  const c = new Color(h);
  return new Vector3(c.r, c.g, c.b);
};

export interface Callbacks {
  onPick: (point: number | null) => void;
}

export class ViewerEngine {
  readonly renderer: WebGLRenderer;
  readonly scene = new Scene();
  readonly camera = new PerspectiveCamera(50, 1, 0.01, 100);
  readonly controls: OrbitControls;
  private bounds;
  private mat: ShaderMaterial;
  private visibleNow: Uint8Array;
  private visAttr: BufferAttribute;
  private prevFrame = -1;
  private regIdx: number[]; // registered frame indices in time order
  private pathSolid: Line;
  private pathDash: Line;
  private frustumAll: LineSegments;
  private frustumNow: LineSegments;
  private dot: Points;
  private pickMark: Points;
  private gizmoScene = new Scene();
  private gizmoCam = new PerspectiveCamera(40, 1, 0.1, 10);
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
  } = null;
  private frames = 0;
  private lastFps = performance.now();
  bench = false;
  fps = 0;

  constructor(
    private canvas: HTMLCanvasElement,
    private b: Bundle,
    private cb: Callbacks,
  ) {
    const m = b.manifest;
    this.renderer = new WebGLRenderer({ canvas, antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0x000000);
    this.renderer.autoClear = false;

    const centers = m.frames.filter((f) => f.registered && f.center).map((f) => f.center!);
    this.bounds = robustBounds(b.position, centers);
    const r = this.bounds.radius;
    this.regIdx = m.frames.filter((f) => f.registered && f.pose).map((f) => f.i);

    // ---- points
    const g = new BufferGeometry();
    g.setAttribute('position', new BufferAttribute(b.position, 3));
    g.setAttribute('color', new Uint8BufferAttribute(b.color, 3, true));
    g.setAttribute('firstSeen', new BufferAttribute(b.firstSeen, 1));
    this.visibleNow = new Uint8Array(m.points.count);
    this.visAttr = new BufferAttribute(this.visibleNow, 1);
    this.visAttr.setUsage(35048); // DynamicDrawUsage
    g.setAttribute('visibleNow', this.visAttr);
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
        uBuildUp: { value: 1 },
        uShowCurrent: { value: 1 },
        uPointSize: { value: 2.5 },
        uPixelRatio: { value: this.renderer.getPixelRatio() },
        uRefDist: { value: r * 2 },
        uColorMode: { value: 0 },
        uZRange: { value: [m.world.z_range[0], m.world.z_range[1]] },
        uRamp0: { value: raw(RAMP[0]) },
        uRamp1: { value: raw(RAMP[1]) },
        uRamp2: { value: raw(RAMP[2]) },
        uRamp3: { value: raw(RAMP[3]) },
      },
    });
    this.mat.uniforms.uZRange.value = new Vector2(m.world.z_range[0], m.world.z_range[1]);
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

    // ---- gizmo
    this.gizmoScene.add(new AxesHelper(1));

    // ---- camera + controls (Blender-like)
    this.camera.up.set(0, 0, 1);
    this.camera.near = r * 0.0005;
    this.camera.far = r * 60;
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    this.controls.zoomToCursor = true; // wheel dollies towards the cursor
    this.controls.minDistance = r * 0.002;
    this.controls.maxDistance = r * 8;
    this.controls.screenSpacePanning = true; // right-drag / Shift+left-drag pan (OrbitControls default)
    this.controls.addEventListener('change', () => (this.dirty = true));
    this.resetView(false);

    canvas.tabIndex = 0;
    canvas.addEventListener('dblclick', this.onDbl);
    canvas.addEventListener('pointerdown', this.onDown);
    canvas.addEventListener('pointerup', this.onUp);
    canvas.addEventListener('keydown', this.onKey);
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
      this.frustumNow.visible = this.dot.visible = true;
    } else this.frustumNow.visible = this.dot.visible = false;
    this.dirty = true;
  }

  setOptions(o: Options) {
    const u = this.mat.uniforms;
    u.uBuildUp.value = o.buildUp ? 1 : 0;
    u.uShowCurrent.value = o.showCurrent ? 1 : 0;
    u.uColorMode.value = o.rgb ? 1 : 0;
    u.uPointSize.value = o.pointSize;
    this.frustumAll.visible = o.frustums;
    this.dirty = true;
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
  private fly(p1: Vector3, g1: Vector3, fov: number, animate = true) {
    const p0 = this.camera.position.clone(),
      g0 = this.controls.target.clone();
    if (!animate) {
      this.camera.position.copy(p1);
      this.controls.target.copy(g1);
      this.camera.fov = fov;
      this.camera.updateProjectionMatrix();
      this.controls.update();
      this.dirty = true;
      return;
    }
    this.tween = { t0: performance.now(), p0, p1, g0, g1, f0: this.camera.fov, f1: fov };
  }

  private view(dir: Vector3, animate = true) {
    const c = new Vector3(...this.bounds.center);
    this.fly(c.clone().addScaledVector(dir.normalize(), this.bounds.radius * 2.2), c, 50, animate);
  }
  resetView(animate = true) {
    this.view(new Vector3(1, -1.3, 0.8), animate);
  }
  topView() {
    this.view(new Vector3(0, -1e-4, 1));
  }
  sideView() {
    this.view(new Vector3(0, -1, 0.02));
  }
  zoom(factor: number) {
    const t = this.controls.target;
    this.fly(
      t.clone().add(this.camera.position.clone().sub(t).multiplyScalar(factor)),
      t.clone(),
      this.camera.fov,
    );
  }

  /** Fly into the pose of frame i (position = centre, FOV from fy, looking at median visible depth). */
  viewThroughFrame(i: number, animate = true) {
    const f = this.b.manifest.frames[i];
    if (!f.registered || !f.pose || !f.center || !f.camera) return;
    const { position, forward } = colmapPoseToThree(f.pose.qvec, f.pose.tvec);
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
    );
  }

  // ------------------------------------------------------------ input
  private down = { x: 0, y: 0 };
  private onDown = (e: PointerEvent) => (this.down = { x: e.clientX, y: e.clientY });
  private onUp = (e: PointerEvent) => {
    if (e.button !== 0 || Math.hypot(e.clientX - this.down.x, e.clientY - this.down.y) > 4) return;
    this.cb.onPick(this.pickAt(e) ?? null);
  };
  private onDbl = (e: MouseEvent) => {
    const p = this.pickAt(e);
    if (p === undefined) return;
    this.controls.target.set(
      this.b.position[p * 3],
      this.b.position[p * 3 + 1],
      this.b.position[p * 3 + 2],
    );
    this.dirty = true;
  };
  private noCtx = (e: Event) => e.preventDefault();
  private onKey = (e: KeyboardEvent) => {
    if (e.key === 'Home') this.resetView();
    else if (e.key === 'f' || e.key === 'F') this.resetView();
    else if (e.key === '1') this.sideView();
    else if (e.key === '3') this.view(new Vector3(1, 0, 0.02));
    else if (e.key === '7') this.topView();
  };

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
    if (this.tween) {
      const tw = this.tween;
      const k = Math.min(1, (performance.now() - tw.t0) / 400);
      const s = k * k * (3 - 2 * k);
      this.camera.position.lerpVectors(tw.p0, tw.p1, s);
      this.controls.target.lerpVectors(tw.g0, tw.g1, s);
      this.camera.fov = tw.f0 + (tw.f1 - tw.f0) * s;
      this.camera.updateProjectionMatrix();
      if (k >= 1) this.tween = null;
      this.dirty = true;
    }
    if (this.bench) {
      // steady-state measurement: slowly orbit so every frame is a real redraw
      const t = this.controls.target,
        p = this.camera.position;
      const a = 0.01,
        dx = p.x - t.x,
        dy = p.y - t.y;
      p.x = t.x + dx * Math.cos(a) - dy * Math.sin(a);
      p.y = t.y + dx * Math.sin(a) + dy * Math.cos(a);
      this.dirty = true;
    }
    if (this.controls.update()) this.dirty = true;
    // keep the orbit target near the data
    const t = this.controls.target,
      bd = this.bounds,
      m = bd.radius;
    const cl = new Vector3(
      Math.min(bd.max[0] + m, Math.max(bd.min[0] - m, t.x)),
      Math.min(bd.max[1] + m, Math.max(bd.min[1] - m, t.y)),
      Math.min(bd.max[2] + m, Math.max(bd.min[2] - m, t.z)),
    );
    if (!cl.equals(t)) {
      this.camera.position.add(cl.clone().sub(t));
      t.copy(cl);
    }
    if (!this.dirty) return;
    this.dirty = false;
    this.render();
    this.frames++;
    const now = performance.now();
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
    // axis gizmo, bottom-left, second viewport via scissor
    const s = 96;
    r.setViewport(12, 12, s, s);
    r.setScissor(12, 12, s, s);
    r.setScissorTest(true);
    r.clearDepth();
    this.gizmoCam.quaternion.copy(this.camera.quaternion);
    this.gizmoCam.position.set(0, 0, 3.2).applyQuaternion(this.camera.quaternion);
    r.render(this.gizmoScene, this.gizmoCam);
    r.setScissorTest(false);
  }

  dispose() {
    cancelAnimationFrame(this.raf);
    const c = this.canvas;
    c.removeEventListener('dblclick', this.onDbl);
    c.removeEventListener('pointerdown', this.onDown);
    c.removeEventListener('pointerup', this.onUp);
    c.removeEventListener('keydown', this.onKey);
    c.removeEventListener('contextmenu', this.noCtx);
    this.controls.dispose();
    this.renderer.dispose();
  }
}
