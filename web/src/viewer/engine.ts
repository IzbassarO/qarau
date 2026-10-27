import {
  Box3,
  BufferAttribute,
  BufferGeometry,
  CapsuleGeometry,
  Color,
  Group,
  Line,
  LineBasicMaterial,
  LineDashedMaterial,
  LineSegments,
  Mesh,
  MeshBasicMaterial,
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
import {
  canShowHumanFigure,
  createObserverState,
  observerOrientation,
  step,
  type ObserverState,
} from './observer';
import { classifyWheel, observerKey, observerMovement, OBSERVER_MOVEMENT_KEYS } from './input';
import { defaultObserverBoostKey, type ObserverBoostKey } from './settings';
import {
  orientationFor,
  stepAxisView,
  threeQuarterDirection,
  viewDirectionFor,
  type Axis,
} from './gizmo';
import { defaultViewState, initialFraming } from './defaults';
import { clampCamera, clampPosition, estimateGround, estimateSurfaceNormal } from './constraints';
import { hasRgb, type Bundle } from '../lib/bundle';
import tokens from '../design/tokens.json' with { type: 'json' };
import {
  colmapPoseToThree,
  csrSlice,
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
export type PointLayer = 'sparse' | 'dense';
type SignalColors = Record<'critical' | 'watch' | 'unseen', string>;

function colorBytes(value: string): [number, number, number] {
  const hex = new Color(value).getHex();
  return [(hex >> 16) & 255, (hex >> 8) & 255, hex & 255];
}

function heightColors(position: Float32Array, [low, high]: [number, number]) {
  const colors = new Uint8Array(position.length);
  for (let p = 0; p < position.length / 3; p++)
    colors.set(heightRamp((position[p * 3 + 2] - low) / (high - low || 1)), p * 3);
  // BufferAttribute preserves the typed array instead of copying it.
  return new BufferAttribute(colors, 3, true);
}

const POINT_PROJECTION = /* glsl */ `
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mv;
  gl_PointSize = clamp(uPointSize * k * uPixelRatio * uRefDist / max(-mv.z, 1e-6), 1.5 * uPixelRatio, 6.0 * uPixelRatio);
`;

const VERT = /* glsl */ `
uniform float uFindings, uSelected;
uniform vec3 uUnseenColor, uLaterColor;
uniform float uCurrentFrame, uBuildUp, uShowCurrent, uPointSize, uPixelRatio, uRefDist;
attribute float firstSeen;
attribute float visibleNow;
attribute vec3 findingColor; // black = no finding; independent of the base colour mode
attribute float fid;  // finding id, 0 none
varying vec3 vColor;
void main() {
  vec3 c = color;
  float k = 1.0;
  if (firstSeen > 65534.5) c = uUnseenColor;
  else if (uBuildUp > 0.5 && firstSeen > uCurrentFrame) c = uLaterColor;
  else if (uShowCurrent > 0.5 && visibleNow > 0.5) { c = min(c * 1.25 + 0.12, vec3(1.0)); k = 1.25; }
  if (uFindings > 0.5 && findingColor.r > 0.0 && firstSeen < 65534.5 && !(uBuildUp > 0.5 && firstSeen > uCurrentFrame)) {
    c = findingColor; // Severity colours remain independent of the base colour mode.
    k = fid == uSelected ? 1.9 : 1.35;
    if (uSelected > 0.5 && fid != uSelected) c *= 0.65;
  }
  vColor = c;
  ${POINT_PROJECTION}
}`;

// Dense exports have point -> frame links, without timeline or finding attributes.
const DENSE_VERT = /* glsl */ `
uniform float uPointSize, uPixelRatio, uRefDist;
varying vec3 vColor;
void main() {
  vColor = color;
  float k = 1.0;
  ${POINT_PROJECTION}
}`;

const FRAG = /* glsl */ `
varying vec3 vColor;
void main() {
  if (length(gl_PointCoord - 0.5) > 0.5) discard;
  gl_FragColor = vec4(vColor, 1.0);
}`;

export interface Callbacks {
  onPick: (point: number | null, layer?: PointLayer) => void;
  onFindingPick?: (findingId: number) => void;
  onFlyModeChange?: (active: boolean) => void;
  onThirdPersonChange?: (active: boolean) => void;
  onAlignModeChange?: (active: boolean) => void;
  onPointInfoModeChange?: (active: boolean) => void;
}

export class ViewerEngine {
  readonly renderer: WebGLRenderer;
  private gl: WebGL2RenderingContext;
  readonly scene = new Scene();
  readonly camera = new PerspectiveCamera(50, 1, 0.01, 100);
  readonly controls: NavigationControls;
  private bounds;
  readonly focus: Focus;
  /** called after every render; the UI uses it to place finding markers */
  onRender?: () => void;
  private sourcePose = false;
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
  private pathDots: Points<BufferGeometry, PointsMaterial>;
  private frustumNow: LineSegments;
  private frustumsVisible: boolean;
  private dot: Points;
  private pickMark: Points;
  private farPoints?: Points<BufferGeometry, PointsMaterial>;
  private densePoints?: Points<BufferGeometry, ShaderMaterial>;
  private denseHeightColors?: BufferAttribute;
  private denseRgbColors?: BufferAttribute;
  private navigationBounds: Box3;
  private navigationMargin: number;
  private ground;
  private aligning = false;
  private pointInfoActive = false;
  private flyState: ObserverState | null = null;
  private flyLookActive = false;
  private lookDelta = { x: 0, y: 0 };
  private boostKey: ObserverBoostKey = defaultObserverBoostKey();
  private thirdPersonActive = false;
  private observerMarker = new Group();
  private humanFigure?: Mesh<CapsuleGeometry, MeshBasicMaterial>;
  private humanFigureVisible = false;
  private flyDistance = 1;
  private heldKeys = new Set<string>();
  private pointerLocked = false;
  private disposed = false;
  private themeObserver: MutationObserver;
  private lookDrag: { id: number; x: number; y: number } | null = null;
  private axisAnimation: {
    pivot: Vector3;
    target: ReturnType<typeof viewDirectionFor>;
  } | null = null;
  private dirty = true;
  private renderFence: WebGLSync | null = null;
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
    const signals = this.readSignalColors();
    this.currentColorMode = defaults.colorMode;
    this.frustumsVisible = defaults.frustums;
    this.renderer = new WebGLRenderer({ canvas, antialias: true });
    // Three r186 requires WebGL2; its type definitions still include WebGL1 here.
    this.gl = this.renderer.getContext() as WebGL2RenderingContext;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(tokens.map.ground);
    this.renderer.autoClear = false;

    const centers = m.frames.filter((f) => f.registered && f.center).map((f) => f.center!);
    this.bounds = robustBounds(b.position);
    const r = this.bounds.radius;
    // Navigation includes every point and recorded camera; trimming is only for framing.
    this.navigationBounds = new Box3().setFromArray(b.position);
    if (b.dense?.position.length)
      this.navigationBounds.union(new Box3().setFromArray(b.dense.position));
    for (const center of centers) this.navigationBounds.expandByPoint(new Vector3(...center));
    if (this.navigationBounds.isEmpty()) this.navigationBounds.set(new Vector3(), new Vector3());
    this.navigationMargin = Math.max(
      1e-6,
      this.navigationBounds.getSize(new Vector3()).length() / 2,
    );
    this.ground = estimateGround(b.position, new Vector3(0, 0, 1));
    this.regIdx = m.frames.filter((f) => f.registered && f.pose).map((f) => f.i);
    this.focus = { center: this.bounds.center, radius: r, method: 'bounds' };

    // ---- points
    const g = new BufferGeometry();
    this.pointGeometry = g;
    g.setAttribute('position', new BufferAttribute(b.position, 3));
    this.heightColors = heightColors(b.position, m.world.z_range);
    if (hasRgb(b)) this.rgbColors = new BufferAttribute(b.color!, 3, true);
    g.setAttribute(
      'color',
      defaults.colorMode === 'rgb' && this.rgbColors ? this.rgbColors : this.heightColors,
    );
    g.setAttribute('firstSeen', new BufferAttribute(b.firstSeen, 1));
    this.visibleNow = new Uint8Array(m.points.count);
    this.visAttr = new BufferAttribute(this.visibleNow, 1);
    this.visAttr.setUsage(35048); // DynamicDrawUsage
    g.setAttribute('visibleNow', this.visAttr);
    // Theme changes refresh severity bytes in the existing attribute; toggles use a uniform.
    g.setAttribute(
      'findingColor',
      new Uint8BufferAttribute(new Uint8Array(m.points.count * 3), 3, true),
    );
    this.updateFindingColors(signals);
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
        uUnseenColor: { value: new Vector3(...colorBytes(signals.unseen)).divideScalar(255) },
        uLaterColor: { value: new Vector3(...colorBytes(tokens.map.grid)).divideScalar(255) },
        uFindings: { value: 1 },
        uSelected: { value: 0 },
      },
    });
    const pts = new Points(g, this.mat);
    pts.frustumCulled = false;
    this.scene.add(pts);

    if (b.dense) {
      const geometry = new BufferGeometry();
      geometry.setAttribute('position', new BufferAttribute(b.dense.position, 3));
      this.denseHeightColors = heightColors(b.dense.position, m.world.z_range);
      this.denseRgbColors = new BufferAttribute(b.dense.color, 3, true);
      geometry.setAttribute(
        'color',
        defaults.colorMode === 'rgb' ? this.denseRgbColors : this.denseHeightColors,
      );
      const { uPointSize, uPixelRatio, uRefDist } = this.mat.uniforms;
      this.densePoints = new Points(
        geometry,
        new ShaderMaterial({
          vertexShader: DENSE_VERT,
          fragmentShader: FRAG,
          vertexColors: true,
          uniforms: { uPointSize, uPixelRatio, uRefDist },
        }),
      );
      this.densePoints.name = 'dense';
      this.densePoints.frustumCulled = false;
      this.scene.add(this.densePoints);
    }

    // ---- far layer: flat not-seen colour, independent of object colours and tracks
    if (b.far && b.far.position.length > 0) {
      const farGeometry = new BufferGeometry();
      farGeometry.setAttribute('position', new BufferAttribute(b.far.position, 3));
      this.farPoints = new Points(
        farGeometry,
        new PointsMaterial({ color: signals.unseen, size: 2.5, sizeAttenuation: false }),
      );
      this.farPoints.name = 'far';
      this.farPoints.visible = defaults.showFarLayer;
      this.scene.add(this.farPoints);
    }

    // ---- camera path: solid up to the current frame, dashed for the rest
    const pathPts = new Float32Array(this.regIdx.length * 3);
    this.regIdx.forEach((fi, k) => pathPts.set(m.frames[fi].center!, k * 3));
    const pg = new BufferGeometry();
    pg.setAttribute('position', new BufferAttribute(pathPts, 3));
    this.pathSolid = new Line(pg, new LineBasicMaterial({ color: tokens.map.text }));
    const dg = pg.clone();
    this.pathDash = new Line(
      dg,
      new LineDashedMaterial({ color: tokens.map.grid, dashSize: r * 0.015, gapSize: r * 0.015 }),
    );
    this.pathDash.computeLineDistances();
    this.pathSolid.frustumCulled = this.pathDash.frustumCulled = false;
    this.pathSolid.name = 'camera-path';
    this.pathDots = new Points(
      pg.clone(),
      new PointsMaterial({ color: tokens.map.text, size: 3, sizeAttenuation: false }),
    );
    this.pathDots.name = 'camera-path-dots';
    this.pathDots.frustumCulled = false;
    this.scene.add(this.pathSolid, this.pathDash, this.pathDots);

    // One frustum buffer, reused for the current frame. Never allocate per-frame pyramids.
    const ng = new BufferGeometry();
    ng.setAttribute('position', new BufferAttribute(new Float32Array(48), 3));
    this.frustumNow = new LineSegments(ng, new LineBasicMaterial({ color: tokens.map.text }));
    this.frustumNow.name = 'current-camera-frustum';
    const dg2 = new BufferGeometry();
    dg2.setAttribute('position', new BufferAttribute(new Float32Array(3), 3));
    this.dot = new Points(
      dg2,
      new PointsMaterial({ color: tokens.map.text, size: 9, sizeAttenuation: false }),
    );
    this.dot.name = 'current-camera-point';
    this.frustumNow.frustumCulled = this.dot.frustumCulled = false;
    this.scene.add(this.frustumNow, this.dot);

    // ---- picked point marker
    const mg = new BufferGeometry();
    mg.setAttribute('position', new BufferAttribute(new Float32Array(3), 3));
    this.pickMark = new Points(
      mg,
      new PointsMaterial({
        color: tokens.map.text,
        size: 10,
        sizeAttenuation: false,
        depthTest: false,
      }),
    );
    this.pickMark.name = 'selected-point';
    this.pickMark.renderOrder = 1;
    this.pickMark.visible = false;
    this.pickMark.frustumCulled = false;
    this.scene.add(this.pickMark);

    // Z-up ring and direction tick. No assumed metres in uncalibrated bundles.
    const markerRadius = Math.max(r * 0.012, 1e-6);
    const ring: Vector3[] = [];
    for (let i = 0; i <= 48; i++) {
      const angle = (i / 48) * Math.PI * 2;
      ring.push(new Vector3(Math.cos(angle) * markerRadius, Math.sin(angle) * markerRadius, 0));
    }
    const markerMaterial = new LineBasicMaterial({ color: tokens.map.text, depthTest: false });
    this.observerMarker.add(
      new Line(new BufferGeometry().setFromPoints(ring), markerMaterial),
      new Line(
        new BufferGeometry().setFromPoints([
          new Vector3(0, markerRadius, 0),
          new Vector3(0, markerRadius * 1.8, 0),
        ]),
        markerMaterial,
      ),
    );
    this.observerMarker.name = 'observer-marker';
    this.observerMarker.visible = false;
    this.scene.add(this.observerMarker);
    if (canShowHumanFigure(b)) {
      const scale = 1 / b.manifest.world.scale_m_per_unit!;
      this.humanFigure = new Mesh(
        new CapsuleGeometry(0.2 * scale, 1.35 * scale, 4, 8),
        new MeshBasicMaterial({ color: tokens.map.text, wireframe: true }),
      );
      this.humanFigure.name = 'observer-human';
      this.humanFigure.rotation.x = Math.PI / 2;
      this.humanFigure.visible = false;
      this.scene.add(this.humanFigure);
    }

    // ---- camera + controls (Blender-like)
    this.camera.up.set(0, 0, 1);
    this.camera.aspect = canvas.clientWidth / Math.max(1, canvas.clientHeight) || 1;
    this.camera.near = r * 0.0005;
    this.camera.far = this.navigationMargin * 60;
    this.controls = new NavigationControls(this.camera, canvas, {
      points: b.position,
      bounds: this.navigationBounds,
      minDistance: r * 0.002,
      sceneRadius: r,
      collisionRadius: r * 0.003,
      boundsMargin: this.navigationMargin,
      onChange: () => (this.dirty = true),
      onStart: () => {
        if (this.flyMode || this.aligning) return;
        this.tween = null;
        this.axisAnimation = null;
        this.exitSourcePose();
      },
    });
    this.resetView(false);

    canvas.tabIndex = 0;
    canvas.addEventListener('pointerdown', this.onDown);
    canvas.addEventListener('click', this.onCanvasClick);
    canvas.addEventListener('wheel', this.onObserverWheel, { passive: false });
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
    this.themeObserver = new MutationObserver(this.updateTheme);
    this.themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    });
    this.setFrame(0);
    // Coalesce setup/resize, and let StrictMode dispose its trial setup before drawing.
    this.raf = requestAnimationFrame(this.loop);
  }

  private readSignalColors(): SignalColors {
    const theme = document.documentElement.dataset.theme;
    const palette =
      theme === 'dark'
        ? tokens.themes.dark
        : theme === 'midnight'
          ? tokens.themes.midnight
          : tokens.themes.light;
    const style = getComputedStyle(this.canvas);
    return {
      critical: style.getPropertyValue('--critical').trim() || palette.critical,
      watch: style.getPropertyValue('--watch').trim() || palette.watch,
      unseen: style.getPropertyValue('--unseen').trim() || palette.unseen,
    };
  }

  private updateFindingColors(signals: SignalColors) {
    const { pointFinding, findings } = this.b;
    if (!pointFinding || !findings) return;
    const palette = new Map(
      findings.map((finding) => [
        finding.id,
        colorBytes(signals[finding.severity === 'critical' ? 'critical' : 'watch']),
      ]),
    );
    const attribute = this.pointGeometry.getAttribute('findingColor') as BufferAttribute;
    const colors = attribute.array as Uint8Array;
    colors.fill(0);
    for (let point = 0; point < this.b.manifest.points.count; point++) {
      const color = palette.get(pointFinding[point]);
      if (color) colors.set(color, point * 3);
    }
    attribute.needsUpdate = true;
  }

  private updateTheme = () => {
    if (this.disposed) return;
    const signals = this.readSignalColors();
    this.farPoints?.material.color.set(signals.unseen);
    this.mat.uniforms.uUnseenColor.value.set(...colorBytes(signals.unseen)).divideScalar(255);
    this.updateFindingColors(signals);
    this.dirty = true;
  };

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
    if (f.registered && f.pose && f.camera && f.center) {
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
    this.frustumNow.visible = this.dot.visible =
      this.frustumsVisible &&
      !!frame?.registered &&
      !!frame.pose &&
      !!frame.camera &&
      !!frame.center;
  }

  setOptions(o: Options) {
    const u = this.mat.uniforms;
    u.uBuildUp.value = o.buildUp ? 1 : 0;
    u.uShowCurrent.value = o.showCurrent ? 1 : 0;
    u.uFindings.value = o.findings ? 1 : 0;
    u.uPointSize.value = o.pointSize;
    if (this.farPoints) this.farPoints.material.size = o.pointSize;
    this.frustumsVisible = o.frustums;
    this.updateFrustumVisibility();
    this.dirty = true;
  }

  hasFarLayer(): boolean {
    return !!this.farPoints;
  }

  setFarVisible(visible: boolean) {
    if (this.farPoints) this.farPoints.visible = visible;
    this.dirty = true;
  }

  hasDenseLayer(): boolean {
    return !!this.densePoints;
  }

  setDenseVisible(visible: boolean) {
    if (this.densePoints) this.densePoints.visible = visible;
    this.dirty = true;
  }

  get colorMode(): ColorMode {
    return this.currentColorMode;
  }

  /** Swap precomputed attributes; positions, tracks and finding colours stay on the GPU. */
  setColorMode(mode: ColorMode) {
    if (mode === 'rgb' && !this.rgbColors && !this.denseRgbColors) return;
    this.currentColorMode = mode;
    this.pointGeometry.setAttribute(
      'color',
      mode === 'rgb' && this.rgbColors ? this.rgbColors : this.heightColors,
    );
    if (this.densePoints)
      this.densePoints.geometry.setAttribute(
        'color',
        mode === 'rgb' ? this.denseRgbColors! : this.denseHeightColors!,
      );
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

  setPicked(p: number | null, layer: PointLayer = 'sparse') {
    if (p === null) this.pickMark.visible = false;
    else {
      const a = this.pickMark.geometry.getAttribute('position') as BufferAttribute;
      const position = layer === 'dense' ? this.b.dense!.position : this.b.position;
      a.set(position.subarray(p * 3, p * 3 + 3));
      a.needsUpdate = true;
      this.pickMark.visible = true;
    }
    this.dirty = true;
  }

  // ------------------------------------------------------------ views
  /** Leave the locked source-camera view and resume world-up orbit. */
  private exitSourcePose = () => {
    if (!this.sourcePose) return;
    this.sourcePose = false;
    this.tween = null;
    this.controls.enabled = true;
    this.controls.sync(); // re-aims at the target with camera.up = +Z
    this.dirty = true;
  };

  /** Source-camera views retain the viewing direction; orbit always levels the horizon. */
  private fly(p1: Vector3, g1: Vector3, fov: number, animate = true, q1?: Quaternion) {
    this.cancelSurfaceAlignment();
    this.exitFlyMode();
    this.axisAnimation = null;
    this.controls.stop();
    this.exitSourcePose();
    const p0 = this.camera.position.clone(),
      g0 = this.controls.target.clone();
    if (q1) {
      this.sourcePose = true;
      this.controls.enabled = false;
    } else this.controls.enabled = true;
    if (!animate) {
      this.camera.position.copy(p1);
      this.controls.target.copy(g1);
      this.controls.sync();
      if (q1) this.camera.quaternion.copy(q1);
      this.enforceCamera();
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
    const framed = initialFraming(
      this.bounds,
      this.navigationBounds,
      this.navigationMargin,
      this.camera.aspect,
    );
    this.fly(framed.position, framed.target, framed.fov, animate);
  }

  topView() {
    this.axisView('z');
  }
  sideView() {
    this.axisView('x');
  }

  frontView() {
    this.axisView('-y');
  }

  threeQuarterView() {
    this.startAxisView(threeQuarterDirection(this.camera.up));
  }

  setSensitivity(value: number) {
    this.controls.sensitivity = value;
  }

  setBoostKey(key: ObserverBoostKey) {
    this.boostKey = key;
  }

  get observerMode() {
    return this.flyMode;
  }

  get observerView() {
    return this.flyState
      ? {
          position: this.flyState.position,
          yaw: this.flyState.yaw,
          fov:
            (Math.atan(Math.tan((this.camera.fov * Math.PI) / 360) * this.camera.aspect) * 360) /
            Math.PI,
        }
      : null;
  }

  get thirdPerson() {
    return this.thirdPersonActive;
  }

  toggleThirdPerson() {
    if (!this.flyMode) return;
    this.thirdPersonActive = !this.thirdPersonActive;
    if (!this.thirdPersonActive && this.flyState) {
      this.camera.position.copy(this.flyState.position);
      this.camera.quaternion.copy(observerOrientation(this.flyState));
    }
    this.cb.onThirdPersonChange?.(this.thirdPersonActive);
    this.dirty = true;
  }

  setHumanFigureVisible(visible: boolean) {
    this.humanFigureVisible = visible && canShowHumanFigure(this.b);
    this.dirty = true;
  }

  setObserverMode() {
    this.enterFlyMode();
  }

  setOrbitMode() {
    this.setPointInfoMode(false);
    this.cancelSurfaceAlignment();
    this.exitFlyMode();
    this.exitSourcePose();
    this.enforceCamera();
  }

  beginSurfaceAlignment() {
    if (this.aligning) {
      this.cancelSurfaceAlignment();
      return;
    }
    this.setOrbitMode();
    this.tween = null;
    this.axisAnimation = null;
    this.controls.stop();
    this.controls.enabled = false;
    this.aligning = true;
    this.cb.onAlignModeChange?.(true);
    this.canvas.focus({ preventScroll: true });
  }

  /** Point inspection shares orbit navigation; only an unmodified click selects. */
  setPointInfoMode(active: boolean) {
    if (active) {
      this.cancelSurfaceAlignment();
      this.exitFlyMode();
    }
    if (this.pointInfoActive === active) return;
    this.pointInfoActive = active;
    this.cb.onPointInfoModeChange?.(active);
  }

  private cancelSurfaceAlignment() {
    if (!this.aligning) return;
    this.aligning = false;
    this.controls.enabled = true;
    this.cb.onAlignModeChange?.(false);
  }

  private alignToPoint(index: number) {
    const point = new Vector3().fromArray(this.b.position, index * 3);
    const normal = estimateSurfaceNormal(this.b.position, point, 30, this.camera.up);
    if (Math.abs(normal.dot(this.camera.up)) >= Math.cos(Math.PI / 18)) normal.copy(this.camera.up);
    else if (normal.dot(this.camera.position.clone().sub(point)) < 0) normal.negate();
    const distance = this.camera.position.distanceTo(this.controls.target);
    this.fly(point.clone().addScaledVector(normal, distance), point, this.camera.fov);
  }

  /** Axis buttons orbit the current pivot at the current distance, with stable pole up. */
  axisView(axis: Axis) {
    // An underside orbit would violate the terrain constraint.
    if (axis === '-z') return;
    this.startAxisView(viewDirectionFor(axis, this.camera.up));
  }

  private startAxisView(target: ReturnType<typeof viewDirectionFor>) {
    this.cancelSurfaceAlignment();
    this.exitFlyMode();
    this.tween = null;
    this.sourcePose = false;
    this.controls.stop();
    this.controls.enabled = true;
    this.axisAnimation = {
      pivot: this.controls.target.clone(),
      target,
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
  zoom(factor: number, precision = false) {
    this.cancelSurfaceAlignment();
    this.exitFlyMode();
    this.axisAnimation = null;
    this.tween = null;
    this.exitSourcePose();
    this.controls.zoom(factor, precision);
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
    this.setPointInfoMode(false);
    this.cancelSurfaceAlignment();
    this.tween = null;
    this.axisAnimation = null;
    this.sourcePose = false;
    this.controls.stop();
    this.controls.enabled = false;
    this.flyDistance = Math.max(
      this.camera.position.distanceTo(this.controls.target),
      this.bounds.radius * 0.002,
    );
    this.flyState = createObserverState(
      this.camera.position,
      this.camera.getWorldDirection(new Vector3()),
      new Vector3(1, 0, 0).applyQuaternion(this.camera.quaternion),
    );
    this.flyLookActive = false;
    this.lookDelta = { x: 0, y: 0 };
    this.heldKeys.clear();
    this.canvas.focus({ preventScroll: true });
    this.cb.onFlyModeChange?.(true);
    this.dirty = true;
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
    // Leave third-person from the observer's own pose, keeping the heading.
    if (this.thirdPersonActive && this.flyState) {
      this.camera.position.copy(this.flyState.position);
      this.camera.quaternion.copy(observerOrientation(this.flyState));
    }
    this.flyState = null;
    this.thirdPersonActive = false;
    this.observerMarker.visible = false;
    if (this.humanFigure) this.humanFigure.visible = false;
    this.cb.onThirdPersonChange?.(false);
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
    if (!locked && this.pointerLocked) {
      this.heldKeys.clear();
      this.endLookDrag();
    }
    this.pointerLocked = locked;
  };

  private look(dx: number, dy: number) {
    if (!this.flyState || (!dx && !dy)) return;
    this.flyLookActive = true;
    this.lookDelta.x += dx;
    this.lookDelta.y += dy;
    this.dirty = true;
  }

  private onMouseLook = (e: MouseEvent) => {
    if (document.pointerLockElement === this.canvas) this.look(e.movementX, e.movementY);
  };

  private onCanvasClick = () => {
    if (this.flyMode && !this.disposed) this.requestPointerLock();
  };

  private onObserverWheel = (e: WheelEvent) => {
    if (!this.flyMode || this.disposed) return;
    e.preventDefault();
    if (classifyWheel(e, [], 'observer') !== 'look') return;
    const scale = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? this.canvas.clientHeight : 1;
    this.look(e.deltaX * scale, e.deltaY * scale);
  };

  private onLookDrag = (e: PointerEvent) => {
    if (
      this.down?.id === e.pointerId &&
      Math.hypot(e.clientX - this.down.x, e.clientY - this.down.y) > 4
    )
      this.down.moved = true;
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
    // Navigation releases capture on pointerup before our bubble listener runs.
    // A release with no held buttons must not erase that pending click.
    if (e.pointerId === this.down?.id && (e.type === 'pointercancel' || e.buttons !== 0))
      this.down = null;
    if (e.pointerId === this.lookDrag?.id) this.endLookDrag();
  };

  private down: { id: number; x: number; y: number; moved: boolean; modified: boolean } | null =
    null;
  private onDown = (e: PointerEvent) => {
    this.down =
      e.button === 0
        ? {
            id: e.pointerId,
            x: e.clientX,
            y: e.clientY,
            moved: false,
            modified: e.shiftKey || e.ctrlKey || e.metaKey || e.altKey,
          }
        : null;
    if (this.flyMode && e.button === 0 && document.pointerLockElement !== this.canvas) {
      this.canvas.focus({ preventScroll: true });
      this.lookDrag = { id: e.pointerId, x: e.clientX, y: e.clientY };
      this.canvas.setPointerCapture?.(e.pointerId);
      e.preventDefault();
    }
  };
  private onUp = (e: PointerEvent) => {
    const down = this.down;
    this.down = null;
    if (e.pointerId === this.lookDrag?.id) this.endLookDrag();
    if (
      !down ||
      down.id !== e.pointerId ||
      down.moved ||
      down.modified ||
      this.flyMode ||
      e.button !== 0 ||
      e.shiftKey ||
      e.ctrlKey ||
      e.metaKey ||
      e.altKey ||
      Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4
    )
      return;
    if (this.aligning) {
      const point = this.pickAt(e);
      if (point !== undefined) this.alignToPoint(point);
      return;
    }
    if (this.pointInfoActive) {
      const point = this.pickInfoAt(e);
      const findingId = point?.layer === 'sparse' ? this.b.pointFinding?.[point.index] : undefined;
      if (findingId) {
        this.cb.onFindingPick?.(findingId);
        return;
      }
      if (point?.layer === 'dense') this.cb.onPick(point.index, 'dense');
      else this.cb.onPick(point?.index ?? null);
    } else {
      const point = this.pickAt(e);
      const findingId = point === undefined ? undefined : this.b.pointFinding?.[point];
      if (findingId) this.cb.onFindingPick?.(findingId);
    }
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
    return observerKey(e);
  }
  private onKey = (e: KeyboardEvent) => {
    const key = this.keyName(e);
    const flyShortcut = this.flyMode && (key === 'f' || key === 'escape');
    const alignExit = this.aligning && key === 'escape';
    const movement = this.flyMode && OBSERVER_MOVEMENT_KEYS.has(key);
    if (
      this.isEditing(e.target) ||
      this.isEditing(document.activeElement) ||
      e.metaKey ||
      ((e.ctrlKey || e.altKey) && !movement) ||
      (document.activeElement !== this.canvas &&
        e.target !== this.canvas &&
        document.pointerLockElement !== this.canvas &&
        !flyShortcut &&
        !alignExit)
    )
      return;
    if (alignExit) this.cancelSurfaceAlignment();
    else if (key === '1') this.setOrbitMode();
    else if (key === '2' || key === '3') return;
    else if (key === 'f') {
      if (!e.repeat) {
        if (this.flyMode) this.setOrbitMode();
        else this.enterFlyMode();
      }
    } else if (this.flyMode) {
      if (key === 'escape') {
        this.heldKeys.clear();
        if (document.pointerLockElement === this.canvas) document.exitPointerLock?.();
      } else if (key === 'v') {
        if (!e.repeat) this.toggleThirdPerson();
      } else if (movement) this.heldKeys.add(key);
      else return;
    } else if (key === 'home') this.resetView();
    else if (key === '7') this.topView();
    else return;
    if (this.flyMode) {
      if (e.shiftKey) this.heldKeys.add('shift');
      if (e.ctrlKey) this.heldKeys.add('control');
      if (e.altKey) this.heldKeys.add('alt');
    }
    e.preventDefault();
  };
  private onKeyUp = (e: KeyboardEvent) => {
    this.heldKeys.delete(this.keyName(e));
  };
  private onFocusIn = (e: FocusEvent) => {
    if (e.target === this.canvas) return;
    this.heldKeys.clear();
    this.endLookDrag();
    if (this.isEditing(e.target) && document.pointerLockElement === this.canvas)
      document.exitPointerLock?.();
  };
  private onBlur = () => this.exitFlyMode();
  private onVisibility = () => {
    if (document.hidden) this.exitFlyMode();
  };

  private updateFly(dt: number) {
    if (!this.flyState) return;
    const previousPosition = this.camera.position.clone();
    const previousRotation = this.camera.quaternion.clone();
    this.flyState = step(
      this.flyState,
      {
        ...observerMovement(this.heldKeys, this.boostKey),
        lookX: this.lookDelta.x,
        lookY: this.lookDelta.y,
      },
      dt,
      {
        points: this.controls.points,
        bounds: this.navigationBounds,
        sensitivity: 0.002 * this.controls.sensitivity,
      },
    );
    this.lookDelta = { x: 0, y: 0 };
    this.camera.position.copy(this.flyState.position);
    if (this.flyLookActive) this.camera.quaternion.copy(observerOrientation(this.flyState));
    if (this.thirdPersonActive) {
      const distance = Math.max(this.bounds.radius * 0.18, 1e-6);
      const offset = new Vector3(
        Math.sin(this.flyState.yaw) * distance,
        -Math.cos(this.flyState.yaw) * distance,
        distance * (0.5 - 0.4 * Math.sin(this.flyState.pitch)),
      );
      this.camera.position.add(offset);
      this.camera.lookAt(this.flyState.position);
    }
    this.observerMarker.visible = this.thirdPersonActive;
    this.observerMarker.position.copy(this.flyState.position);
    this.observerMarker.rotation.z = this.flyState.yaw;
    if (this.humanFigure) {
      this.humanFigure.visible = this.thirdPersonActive && this.humanFigureVisible;
      this.humanFigure.position.copy(this.flyState.position);
      this.humanFigure.position.z += 0.875 / this.b.manifest.world.scale_m_per_unit!;
    }
    this.camera.updateMatrixWorld();
    this.dirty ||=
      !previousPosition.equals(this.camera.position) ||
      !previousRotation.equals(this.camera.quaternion);
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
      const safe = clampPosition(this.controls.constrainMove(this.camera.position, next.position), {
        ground: this.ground,
        box: this.navigationBounds,
        margin: this.navigationMargin,
        epsilon: this.bounds.radius * 0.0001,
      });
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

  private pickInfoAt(e: MouseEvent): { index: number; layer: PointLayer } | undefined {
    const sparse = this.pickAt(e);
    if (!this.densePoints?.visible || !this.b.dense)
      return sparse === undefined ? undefined : { index: sparse, layer: 'sparse' };
    const rc = this.canvas.getBoundingClientRect();
    const vp = this.camera.projectionMatrix.clone().multiply(this.camera.matrixWorldInverse);
    const dense = pickNearest(
      this.b.dense.position,
      vp.elements,
      rc.width,
      rc.height,
      e.clientX - rc.left,
      e.clientY - rc.top,
      8,
    );
    if (dense < 0) return sparse === undefined ? undefined : { index: sparse, layer: 'sparse' };
    if (sparse !== undefined) {
      // Compare the two candidates with pickNearest's pixel-distance + depth score.
      const score = (position: Float32Array, index: number) => {
        const p = new Vector3().fromArray(position, index * 3);
        const depth = -p.clone().applyMatrix4(this.camera.matrixWorldInverse).z;
        p.applyMatrix4(vp);
        return (
          Math.hypot(
            (p.x * 0.5 + 0.5) * rc.width - (e.clientX - rc.left),
            (0.5 - p.y * 0.5) * rc.height - (e.clientY - rc.top),
          ) + Math.log2(Math.max(depth, 1e-6))
        );
      };
      if (score(this.b.position, sparse) < score(this.b.dense.position, dense))
        return { index: sparse, layer: 'sparse' };
    }
    return { index: dense, layer: 'dense' };
  }

  // ------------------------------------------------------------ loop
  /** Final safety pass for every camera producer (input, presets, tween and benchmark). */
  private enforceCamera() {
    const options = {
      ground: this.ground,
      box: this.navigationBounds,
      margin: this.navigationMargin,
      epsilon: this.bounds.radius * 0.0001,
    };
    const before = this.camera.position.clone();
    const rotation = this.camera.quaternion.clone();
    if (this.flyState) return;
    const state = clampCamera(
      {
        position: this.camera.position,
        pivot: this.controls.target,
        up: this.camera.up,
        minDistance: this.bounds.radius * 0.002,
        fov: this.camera.fov,
        rotation: this.camera.quaternion,
      },
      { ...options, minDist: this.bounds.radius * 0.002, maxDist: this.bounds.radius * 20 },
    );
    this.camera.position.copy(state.position);
    this.controls.target.copy(state.pivot);
    this.camera.up.copy(state.up);
    if (state.rotation) this.camera.quaternion.copy(state.rotation);
    this.camera.updateMatrixWorld();
    this.dirty ||=
      !before.equals(this.camera.position) || rotation.angleTo(this.camera.quaternion) > 1e-7;
  }

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
      // Follow the source-camera direction; the final constraint pass levels roll.
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
    if (!this.sourcePose && !this.flyMode && !this.axisAnimation && this.controls.update(dt))
      this.dirty = true;
    this.enforceCamera();
    // fps = frames actually rendered per second; renders are on demand, so idle reads 0
    if (this.dirty && this.renderReady()) {
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

  /** Do not queue full-cloud draws faster than the GPU can finish them. */
  private renderReady() {
    if (!this.renderFence) return true;
    const gl = this.gl;
    // A zero timeout polls completion without blocking input or the compositor.
    if (gl.clientWaitSync(this.renderFence, 0, 0) === gl.TIMEOUT_EXPIRED) return false;
    gl.deleteSync(this.renderFence);
    this.renderFence = null;
    return true;
  }

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
    const shown = [this.frustumNow.visible, this.dot.visible];
    if (atCam) this.frustumNow.visible = this.dot.visible = false;
    r.render(this.scene, this.camera);
    const gl = this.gl;
    this.renderFence = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0);
    gl.flush();
    [this.frustumNow.visible, this.dot.visible] = shown;
    this.onRender?.();
  }

  dispose() {
    this.disposed = true;
    this.themeObserver.disconnect();
    this.exitFlyMode();
    this.axisAnimation = null;
    cancelAnimationFrame(this.raf);
    const c = this.canvas;
    c.removeEventListener('pointerdown', this.onDown);
    c.removeEventListener('click', this.onCanvasClick);
    c.removeEventListener('wheel', this.onObserverWheel);
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
    for (const object of [
      this.pathSolid,
      this.pathDash,
      this.pathDots,
      this.frustumNow,
      this.dot,
      this.pickMark,
    ]) {
      object.geometry.dispose();
      const materials = Array.isArray(object.material) ? object.material : [object.material];
      for (const material of materials) material.dispose();
    }
    this.observerMarker.traverse((object) => {
      if (object instanceof Line) {
        object.geometry.dispose();
        (object.material as LineBasicMaterial).dispose();
      }
    });
    this.humanFigure?.geometry.dispose();
    this.humanFigure?.material.dispose();
    this.farPoints?.geometry.dispose();
    this.farPoints?.material.dispose();
    this.farPoints?.removeFromParent();
    this.pointGeometry.dispose();
    this.mat.dispose();
    this.densePoints?.geometry.dispose();
    this.densePoints?.material.dispose();
    this.densePoints?.removeFromParent();
    if (this.renderFence) {
      this.gl.deleteSync(this.renderFence);
      this.renderFence = null;
    }
    this.renderer.dispose();
  }
}
