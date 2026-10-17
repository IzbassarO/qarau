import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import { Link } from 'react-router';
import { useI18n } from '../i18n';
import { hasRgb, runUrl, type Bundle, type Finding } from '../lib/bundle';
import { heightRamp, RAMP } from '../lib/geometry';
import { ViewerEngine, type ColorMode, type Options } from './engine';
import { defaultViewState } from './defaults';
import { AxisGizmo, type AxisGizmoHandle } from './AxisGizmo';
import {
  H2,
  LangSwitch,
  Logo,
  ModeSwitch,
  NavigationSettings,
  PointInfoTool,
  Row,
  Toggle,
  ToolButton,
} from './ui';
import { buildPointInfo, frameTime } from './pointInfo';
import { PointInfoPanel } from './PointInfoPanel';
import { SessionImage } from '../auth/SessionImage';
import { sessionUserId, subscribeToAuth } from '../auth/auth';
import { loadSensitivity, saveSensitivity } from './settings';
import { useRoi } from './useRoi';
import { RoiControls } from './RoiControls';
import { containsPoint } from './roi';

declare global {
  interface Window {
    __qarau?: {
      pointsLoaded: boolean;
      fps: number;
      engine?: ViewerEngine;
      bench: (on: boolean) => void;
    };
  }
}

const mono = 'font-mono';
const colorModeStorageKey = 'qarau.viewer.colorMode.v2';
function savedColorMode(): ColorMode | undefined {
  try {
    const saved = localStorage.getItem(colorModeStorageKey);
    return saved === 'rgb' || saved === 'height' ? saved : undefined;
  } catch {
    return undefined;
  }
}

const fmtTime = (s: number) => {
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, '0')}:${(s - m * 60).toFixed(1).padStart(4, '0')}`;
};

const icon = (d: ReactNode) => (
  <svg
    width="18"
    height="18"
    viewBox="0 0 18 18"
    fill="none"
    stroke="#C9CED6"
    strokeWidth="1.5"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    {d}
  </svg>
);

export function ViewerScreen({
  bundle,
  embedded = false,
  initialPoint = null,
  invalidPoint = false,
  siteId,
}: {
  bundle: Bundle;
  embedded?: boolean;
  initialPoint?: number | null;
  invalidPoint?: boolean;
  siteId?: string;
}) {
  const { t } = useI18n();
  const m = bundle.manifest;
  const frames = m.frames;
  const isImages = m.source.kind === 'images';
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<ViewerEngine | null>(null);
  const gizmoRef = useRef<AxisGizmoHandle>(null);
  const [flyMode, setFlyMode] = useState(false);
  const [alignMode, setAlignMode] = useState(false);
  const [pointInfoMode, setPointInfoMode] = useState(initialPoint !== null);
  const userId = useSyncExternalStore(subscribeToAuth, sessionUserId, () => null);
  const roi = useRoi(bundle, siteId, userId, engineRef);
  const [sensitivitySetting, setSensitivitySetting] = useState(() => ({
    userId,
    value: loadSensitivity(userId),
  }));
  const sensitivity =
    sensitivitySetting.userId === userId ? sensitivitySetting.value : loadSensitivity(userId);
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [picked, setPicked] = useState<number | null>(initialPoint);
  const [fps, setFps] = useState(0);
  const [colorMode, setColorMode] = useState(savedColorMode);
  const defaults = defaultViewState(bundle, colorMode);
  const rgbAvailable = hasRgb(bundle);
  const activeColorMode = defaults.colorMode;
  const colorModeHintId = useId();
  const [showFarLayer, setShowFarLayer] = useState(defaults.showFarLayer);
  const farAvailable = !!bundle.far?.position.length;
  const [opts, setOpts] = useState<Options>({
    buildUp: defaults.buildUp,
    showCurrent: true,
    frustums: defaults.frustums,
    findings: true,
    pointSize: 2.5,
  });
  const [sel, setSel] = useState<number | null>(null);
  const markerEls = useRef(new Map<number, HTMLButtonElement>());
  const [glError, setGlError] = useState(false);

  const f = frames[frame];
  const findings = useMemo(() => bundle.findings ?? [], [bundle]);
  const selected = findings.find((x) => x.id === sel) ?? null;
  const sevColor = (x: Finding) => (x.severity === 'critical' ? '#F0565C' : '#F2A33A');
  // a finding exists on the map once its first frame has played (always, with build-up off)
  const seen = (x: Finding) => !opts.buildUp || !x.frames.length || x.frames[0] <= frame;
  const tc = (i: number) => {
    const time = frameTime(frames[i]);
    return isImages ? `#${i}` : time === null ? t('pointInfo.noTimestamp') : fmtTime(time);
  };
  const canPlay = isImages || frames.every((frame) => frameTime(frame) !== null);

  // engine lifecycle
  useEffect(() => {
    const canvas = canvasRef.current!;
    let eng: ViewerEngine;
    try {
      eng = new ViewerEngine(canvas, bundle, {
        onPick: (p) => setPicked(p),
        onFlyModeChange: setFlyMode,
        onAlignModeChange: setAlignMode,
        onPointInfoModeChange: setPointInfoMode,
        onRoiDrawingChange: roi.setDrawing,
        onRoiRectChange: roi.setRect,
        onRoiBoxChange: roi.drawn,
        onRoiDrawError: roi.drawError,
      });
    } catch {
      setGlError(true);
      return;
    }
    engineRef.current = eng;
    const ro = new ResizeObserver(() => eng.resize());
    ro.observe(canvas);
    eng.resize();
    const timer = setInterval(() => setFps(Math.round(eng.fps)), 1000);
    if (import.meta.env.DEV) {
      window.__qarau = {
        pointsLoaded: true,
        fps: 0,
        engine: eng,
        bench: (on) => (eng.bench = on),
      };
      const q = setInterval(() => window.__qarau && (window.__qarau.fps = eng.fps), 500);
      return () => {
        clearInterval(q);
        clearInterval(timer);
        ro.disconnect();
        eng.dispose();
        delete window.__qarau;
      };
    }
    return () => {
      clearInterval(timer);
      ro.disconnect();
      eng.dispose();
    };
    // The ROI callbacks write state and do not capture the current box or site.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bundle]);

  useEffect(() => engineRef.current?.setFrame(frame), [frame, bundle]);
  useEffect(() => engineRef.current?.setOptions(opts), [opts, bundle]);
  useEffect(() => engineRef.current?.setFarVisible(showFarLayer), [showFarLayer, bundle]);
  useEffect(() => engineRef.current?.setColorMode(activeColorMode), [activeColorMode, bundle]);
  useEffect(
    () => engineRef.current?.setPicked(pointInfoMode ? picked : null),
    [picked, pointInfoMode, bundle],
  );
  useEffect(() => engineRef.current?.setPointInfoMode(pointInfoMode), [pointInfoMode, bundle]);
  useEffect(() => engineRef.current?.setSelectedFinding(sel ?? 0), [sel, bundle]);
  useEffect(() => engineRef.current?.setSensitivity(sensitivity), [sensitivity, bundle]);

  // finding markers are HTML on top of the canvas, re-placed after every render
  useEffect(() => {
    const e = engineRef.current;
    if (!e) return;
    const place = () => {
      gizmoRef.current?.update(e.camera.quaternion);
      for (const x of findings) {
        const el = markerEls.current.get(x.id);
        if (!el) continue;
        const p = e.project(x.centroid);
        el.style.transform = `translate(${p.x}px, ${p.y}px) translate(-50%, -50%)`;
        const inRegion = !roi.hidden || !roi.box || containsPoint(roi.box, x.centroid);
        el.style.visibility = p.visible && inRegion && !roi.drawing ? 'visible' : 'hidden';
      }
    };
    e.onRender = place;
    place();
    return () => {
      e.onRender = undefined;
    };
  }, [bundle, findings, frame, opts.buildUp, opts.findings, roi.hidden, roi.box, roi.drawing]);

  // play: step in real time by frames[i].t (photo sets: 4 frames per second)
  useEffect(() => {
    if (!playing || !canPlay) return;
    const rate = isImages ? 4 : 1;
    const start = performance.now();
    const t0 = isImages ? frame : frameTime(frames[frame])!;
    let raf = 0;
    const tick = () => {
      const el = ((performance.now() - start) / 1000) * rate + t0;
      let i = frame;
      while (i + 1 < frames.length && (isImages ? i + 1 : frameTime(frames[i + 1])!) <= el) i++;
      setFrame(i);
      if (i >= frames.length - 1) setPlaying(false);
      else raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- restart only when play is toggled
  }, [playing]);

  const jump = (i: number) => {
    setFrame(i);
    engineRef.current?.viewThroughFrame(i);
  };

  const select = (x: Finding) => {
    if (sel === x.id) return setSel(null);
    setSel(x.id);
    const d = x.bbox.max.map((v, k) => v - x.bbox.min[k]);
    engineRef.current?.flyToFinding(x.centroid, Math.hypot(d[0], d[1], d[2]));
  };

  const pointInfo = useMemo(
    () => (picked === null ? null : buildPointInfo(bundle, picked)),
    [picked, bundle],
  );
  const closePointInfo = () => {
    setPicked(null);
    setPointInfoMode(false);
  };
  const seekPointFrame = (timeSec: number, frameId: number) => {
    const index = frames.findIndex((frame) => frame.i === frameId && frameTime(frame) === timeSec);
    if (index < 0) return;
    setPlaying(false);
    jump(index);
  };

  const eng = () => engineRef.current;
  const changeColorMode = (mode: ColorMode) => {
    setColorMode(mode);
    try {
      localStorage.setItem(colorModeStorageKey, mode);
    } catch {
      // The current view still works when browser storage is unavailable.
    }
  };
  const tickFindings = !opts.findings ? [] : selected ? [selected] : findings;
  const ticks = frames.filter((x) => !x.registered || !x.kept);
  const stats = m.stats;
  const totalS = Object.values(stats.processing_s ?? {}).reduce((a, b) => a + b, 0);
  const rampSwatches = [0, 1 / 3, 2 / 3, 1].map((v) => heightRamp(v));

  return (
    <div className="flex h-full min-h-[640px] flex-col overflow-hidden bg-bg">
      {!embedded && (
        <header className="flex flex-wrap items-center gap-4 border-b border-line bg-surface px-4 py-2.5">
          <Link to="/" aria-label="Qarau">
            <Logo />
          </Link>
          <span className="text-ink-2">{bundle.runId}</span>
          <div
            role="tablist"
            className="flex gap-0.5 rounded-[9px] border border-line-strong p-[3px]"
          >
            <span
              role="tab"
              aria-selected="false"
              className="inline-flex h-7 items-center rounded-md px-3 text-[13px] text-ink-2"
            >
              {t('nav.overview')}
            </span>
            <span
              role="tab"
              aria-selected="true"
              className="inline-flex h-7 items-center rounded-md bg-line px-3 text-[13px]"
            >
              {t('nav.viewer')}
            </span>
          </div>
          <div className="grow" />
          <span className={`${mono} text-xs text-ink-2`}>
            {stats.points.toLocaleString()}
            {import.meta.env.DEV && fps > 0 && ` · ${fps} fps`}
          </span>
          <LangSwitch />
        </header>
      )}

      {bundle.farError && (
        <p role="alert" className="m-0 border-b border-line bg-surface px-4 py-2 text-watch">
          {t(bundle.farError === 'missing' ? 'viewer.far.errorMissing' : 'viewer.far.errorBroken')}
        </p>
      )}
      {invalidPoint && (
        <p role="note" className="m-0 border-b border-line bg-surface px-4 py-2 text-xs text-ink-2">
          {t('pointInfo.invalidPoint')}
        </p>
      )}

      <div className="flex min-h-0 grow">
        <div
          role="toolbar"
          aria-label={t('toolbar.label')}
          aria-orientation="vertical"
          className="flex w-[52px] shrink-0 flex-col items-center gap-1.5 border-r border-line bg-surface py-2.5"
        >
          <ToolButton label={t('toolbar.reset')} onClick={() => eng()?.resetView()}>
            {icon(<path d="M3 9a6 6 0 1 0 2-4.5M3 3v3h3" />)}
          </ToolButton>
          <ToolButton label={t('toolbar.top')} onClick={() => eng()?.topView()}>
            {icon(
              <>
                <rect x="3" y="3" width="12" height="12" rx="2" />
                <circle cx="9" cy="9" r="2" fill="#C9CED6" />
              </>,
            )}
          </ToolButton>
          <ToolButton label={t('toolbar.side')} onClick={() => eng()?.sideView()}>
            {icon(<path d="M2 14h14M5 14V7h8v7" />)}
          </ToolButton>
          <ToolButton label={t('toolbar.front')} onClick={() => eng()?.frontView()}>
            {icon(<path d="M3 14V4h12v10M6 14V9h6v5M2 14h14" />)}
          </ToolButton>
          <ToolButton label={t('toolbar.threeQuarter')} onClick={() => eng()?.threeQuarterView()}>
            {icon(<path d="m9 2 6 3.5v7L9 16l-6-3.5v-7L9 2Zm0 7L3 5.5M9 9l6-3.5M9 9v7" />)}
          </ToolButton>
          <ToolButton
            label={t('toolbar.alignSurface')}
            pressed={alignMode}
            onClick={() => eng()?.beginSurfaceAlignment()}
          >
            {icon(<path d="m2 12 7 4 7-4-7-4-7 4ZM9 10V2M6 5l3-3 3 3" />)}
          </ToolButton>
          <ToolButton label={t('toolbar.camera')} onClick={() => eng()?.viewThroughFrame(frame)}>
            {icon(
              <>
                <rect x="2" y="5" width="10" height="8" rx="1.5" />
                <path d="M12 8l4-2v6l-4-2" />
              </>,
            )}
          </ToolButton>
          <PointInfoTool
            active={pointInfoMode}
            onClick={() => {
              if (pointInfoMode) closePointInfo();
              else setPointInfoMode(true);
            }}
          />
          <ToolButton
            label={t('roi.title')}
            pressed={roi.open}
            onClick={() => {
              if (roi.open) eng()?.cancelRoiDrawing();
              roi.setOpen(!roi.open);
            }}
          >
            {icon(
              <>
                <path d="M2 6V2h4M12 2h4v4M16 12v4h-4M6 16H2v-4" />
                <rect x="5" y="5" width="8" height="8" strokeDasharray="2 2" />
              </>,
            )}
          </ToolButton>
          <span className="my-1 h-px w-6 bg-line" />
          <ToolButton
            label={t('toolbar.zoomIn')}
            onClick={(event) => eng()?.zoom(0.8, event.altKey)}
          >
            +
          </ToolButton>
          <ToolButton
            label={t('toolbar.zoomOut')}
            onClick={(event) => eng()?.zoom(1.25, event.altKey)}
          >
            −
          </ToolButton>
        </div>

        <main className="flex min-w-0 flex-1 flex-col">
          <div className="relative z-20 flex flex-wrap items-center justify-between gap-2 border-b border-line bg-surface px-3 py-2">
            <ModeSwitch orbitActive={!flyMode} onOrbit={() => eng()?.setOrbitMode()} />
            <NavigationSettings
              sensitivity={sensitivity}
              onSensitivityChange={(value) => {
                setSensitivitySetting({ userId, value });
                saveSensitivity(userId, value);
              }}
            />
          </div>
          <div className="relative min-h-0 grow bg-black">
            <canvas
              ref={canvasRef}
              aria-label={t('canvas.label')}
              className={`absolute inset-0 size-full outline-none ${roi.drawing ? 'cursor-crosshair' : ''}`}
            />
            {roi.rect && (
              <div
                data-testid="roi-drag-rectangle"
                className="pointer-events-none absolute border border-[#ddd6bf] bg-[#ddd6bf]/10"
                style={{
                  left: roi.rect.x,
                  top: roi.rect.y,
                  width: roi.rect.width,
                  height: roi.rect.height,
                }}
              />
            )}
            {roi.open && (
              <RoiControls
                roi={roi}
                runId={bundle.runId}
                onDraw={() => {
                  setPlaying(false);
                  eng()?.beginRoiDrawing();
                }}
                onCancel={() => eng()?.cancelRoiDrawing()}
              />
            )}
            {glError && (
              <p className="absolute inset-0 grid place-items-center text-watch">
                {t('error.webgl')}
              </p>
            )}
            {opts.findings && (
              <div className="pointer-events-none absolute inset-0 overflow-hidden">
                {findings.filter(seen).map((x) => (
                  <button
                    key={x.id}
                    type="button"
                    ref={(el) => {
                      if (el) markerEls.current.set(x.id, el);
                      else markerEls.current.delete(x.id);
                    }}
                    data-testid="finding-marker"
                    aria-label={`${t('findings.marker')} ${x.id}`}
                    onClick={() => select(x)}
                    className={`pointer-events-auto absolute left-0 top-0 flex size-6 cursor-pointer items-center justify-center rounded-full border-2 p-0 font-mono text-xs font-semibold text-bg ${x.id === sel ? 'border-white' : 'border-bg'} ${x.status === 'low_evidence' ? 'opacity-80 outline-dashed outline-1 outline-offset-2 outline-ink-2' : ''}`}
                    style={{ background: sevColor(x) }}
                  >
                    {x.id}
                  </button>
                ))}
              </div>
            )}
            <div className="absolute right-3 top-3 flex flex-col gap-1.5 rounded-lg bg-bg/85 px-3 py-2.5 text-xs leading-4">
              {[
                ['#EAF2FF', 'legend.now'],
                [`rgb(${rampSwatches[1].join(',')})`, 'legend.seen'],
                ['#2A3240', 'legend.later'],
                ['#6B727D', 'legend.never'],
              ].map(([c, k]) => (
                <span key={k} className="flex items-center gap-2">
                  <span className="size-2 rounded-full" style={{ background: c }} />
                  {t(k as 'legend.now')}
                </span>
              ))}
            </div>
            <AxisGizmo
              ref={gizmoRef}
              onSelect={(axis) => {
                eng()?.axisView(axis);
                canvasRef.current?.focus({ preventScroll: true });
              }}
            />
            <div
              role="status"
              className="pointer-events-none absolute bottom-3 left-1/2 w-max max-w-[calc(100%-15rem)] -translate-x-1/2 rounded-md bg-bg/80 px-2.5 py-1.5 text-center text-xs leading-5 text-ink-2"
            >
              {roi.drawing
                ? t('roi.drawHint')
                : alignMode
                  ? t('canvas.alignHint')
                  : flyMode
                    ? t('canvas.flyHint')
                    : pointInfoMode
                      ? t('pointInfo.hint')
                      : t('canvas.hint')}
            </div>
          </div>

          <div className="flex flex-col gap-2 border-t border-line bg-surface px-4 pb-3.5 pt-2.5">
            <div className="flex items-center gap-3">
              <button
                type="button"
                disabled={!canPlay}
                onClick={() =>
                  setPlaying((p) => (!p && frame >= frames.length - 1 ? (setFrame(0), true) : !p))
                }
                aria-label={playing ? t('timeline.pause') : t('timeline.play')}
                className="flex size-9 shrink-0 cursor-pointer items-center justify-center rounded-full border-0 bg-ink"
              >
                <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" fill="#0B0C0E">
                  {playing ? (
                    <>
                      <rect x="2" y="1" width="3.5" height="12" rx="1" />
                      <rect x="8.5" y="1" width="3.5" height="12" rx="1" />
                    </>
                  ) : (
                    <path d="M3 1.5v11l9-5.5z" />
                  )}
                </svg>
              </button>
              <span className={`${mono} min-w-24 text-[13px]`}>{tc(frame)}</span>
              <div className="relative flex h-9 grow items-center">
                <div aria-hidden="true" className="absolute inset-x-0 top-0.5 h-2">
                  {/* gray marks = frames not used; coloured = frames where a finding is visible */}
                  {ticks.map((x) => (
                    <span
                      key={x.i}
                      title={t('timeline.notUsed')}
                      className="absolute top-0 h-2 w-0.5 rounded-sm bg-notseen"
                      style={{ left: `${(x.i / Math.max(1, frames.length - 1)) * 100}%` }}
                    />
                  ))}
                  {tickFindings.flatMap((x) =>
                    x.frames.map((i) => (
                      <span
                        key={`${x.id}-${i}`}
                        data-testid="finding-tick"
                        title={`${t('timeline.findingTick')}: ${t('findings.marker')} ${x.id}`}
                        className="absolute top-0 h-2 w-0.5 rounded-sm"
                        style={{
                          left: `${(i / Math.max(1, frames.length - 1)) * 100}%`,
                          background: sevColor(x),
                        }}
                      />
                    )),
                  )}
                </div>
                <input
                  type="range"
                  aria-label={t('timeline.position')}
                  min={0}
                  max={frames.length - 1}
                  step={1}
                  value={frame}
                  onChange={(e) => setFrame(Number(e.target.value))}
                  className="mt-3.5 w-full"
                />
              </div>
              <span className={`${mono} text-xs text-ink-2`}>
                {frame + 1} / {frames.length}
              </span>
            </div>
            <span className="text-xs text-ink-2">{t('timeline.hint')}</span>
          </div>
        </main>

        <aside className="flex w-[340px] shrink-0 flex-col gap-4 overflow-y-auto border-l border-line bg-surface p-4">
          {pointInfoMode && pointInfo && (
            <PointInfoPanel info={pointInfo} onSeek={seekPointFrame} onClose={closePointInfo} />
          )}
          {pointInfoMode && !pointInfo && (
            <p className="m-0 text-[13px] text-ink-2">{t('pointInfo.hint')}</p>
          )}
          <section className="flex flex-col gap-2">
            <div className="flex items-baseline justify-between">
              <h2 className="m-0 text-sm font-semibold">{t('panel.frame')}</h2>
              <span className={`${mono} text-xs text-ink-2`}>{tc(frame)}</span>
            </div>
            <div className="relative aspect-video overflow-hidden rounded-lg border border-line bg-[#0d1014]">
              <SessionImage
                src={bundle.assetUrl?.(f.thumb) ?? runUrl(bundle.runId, f.thumb)}
                alt={t('panel.frame')}
                className="size-full object-contain"
              />
            </div>
            <p className={`${mono} m-0 truncate text-xs text-ink-2`}>
              {f.image.split('/').pop()} · {t('panel.blur')}{' '}
              {f.blur === null ? '—' : f.blur.toFixed(1)}
              {f.drop_reason === 'blur' && ` · ${t('panel.droppedBlur')}`}
              {f.drop_reason === 'exposure' && ` · ${t('panel.droppedExposure')}`}
            </p>
            {!f.registered && (
              <p className="m-0 text-[13px] text-watch">{t('panel.notRegistered')}</p>
            )}
            <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1 text-[13px]">
              <Row k={t('panel.cameraHeight')} v={f.center ? f.center[2].toFixed(2) : '—'} />
              <Row
                k={t('panel.visible')}
                v={(bundle.frameOffsets[frame + 1] - bundle.frameOffsets[frame]).toLocaleString()}
              />
              <Row
                k={t('panel.assembled')}
                v={`${Math.round((bundle.assembled[frame] / m.points.count) * 100)}%`}
              />
            </div>
            <p className="m-0 text-xs text-ink-2">{t('panel.cameraHeightNote')}</p>
          </section>

          <section className="flex flex-col gap-2" aria-label={t('findings.title')}>
            <H2>{t('findings.title')}</H2>
            {!m.findings ? (
              <p className="m-0 text-[13px] text-ink-2">{t('findings.notRun')}</p>
            ) : bundle.findingsError ? (
              <p role="alert" className="m-0 text-[13px] text-watch">
                {t(
                  bundle.findingsError === 'missing'
                    ? 'findings.errorMissing'
                    : 'findings.errorBroken',
                )}
              </p>
            ) : findings.length === 0 ? (
              <p className="m-0 text-[13px] text-ink-2">{t('findings.empty')}</p>
            ) : (
              <ul className="m-0 flex list-none flex-col gap-1 p-0">
                {findings.map((x) => (
                  <li key={x.id}>
                    <button
                      type="button"
                      data-testid="finding-item"
                      aria-pressed={x.id === sel}
                      onClick={() => select(x)}
                      className={`flex w-full cursor-pointer items-center gap-2.5 rounded-lg border px-2.5 py-1.5 text-left text-[13px] text-ink ${x.id === sel ? 'border-line-strong bg-raised' : 'border-transparent bg-transparent'}`}
                    >
                      <span
                        className="flex size-6 shrink-0 items-center justify-center rounded-full font-mono text-xs font-semibold text-bg"
                        style={{ background: sevColor(x) }}
                      >
                        {x.id}
                      </span>
                      <span className="flex min-w-0 grow flex-col">
                        <span className="truncate">
                          {x.class} ·{' '}
                          {t(x.severity === 'critical' ? 'findings.critical' : 'findings.watch')}
                        </span>
                        <span
                          className={`text-xs ${x.status === 'low_evidence' ? 'text-watch' : 'text-ink-2'}`}
                        >
                          {t(
                            x.status === 'low_evidence'
                              ? 'findings.lowEvidence'
                              : 'findings.confirmed',
                          )}
                        </span>
                      </span>
                      <span className={`${mono} shrink-0 text-xs text-ink-2`}>
                        {x.frames.length} {t('findings.frames')}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {selected && (
              <div
                data-testid="finding-detail"
                className="flex flex-col gap-2 rounded-[10px] border border-line-strong p-3"
              >
                {selected.status === 'low_evidence' && (
                  <p className="m-0 text-xs text-watch">{t('findings.lowEvidenceNote')}</p>
                )}
                <span className="text-[13px]">{t('findings.seenAt')}</span>
                <div className="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto">
                  {selected.frames.map((i) => (
                    <button
                      key={i}
                      type="button"
                      data-testid="finding-timecode"
                      onClick={() => jump(i)}
                      className={`${mono} h-7 cursor-pointer rounded-md border border-line-strong bg-transparent px-2 text-xs text-ink ${i === frame ? 'bg-raised' : ''}`}
                    >
                      {tc(i)}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </section>

          <section className="flex flex-col gap-1.5">
            <H2>{t('show.title')}</H2>
            <Toggle on={opts.buildUp} onChange={(v) => setOpts({ ...opts, buildUp: v })}>
              {t('show.buildUp')}
            </Toggle>
            <Toggle on={opts.showCurrent} onChange={(v) => setOpts({ ...opts, showCurrent: v })}>
              {t('show.current')}
            </Toggle>
            <Toggle on={opts.frustums} onChange={(v) => setOpts({ ...opts, frustums: v })}>
              {t('show.frustums')}
            </Toggle>
            {farAvailable && (
              <Toggle on={showFarLayer} onChange={setShowFarLayer}>
                {t('viewer.show.farPoints')}
              </Toggle>
            )}
            {findings.length > 0 && (
              <Toggle on={opts.findings} onChange={(v) => setOpts({ ...opts, findings: v })}>
                {t('show.findings')}
              </Toggle>
            )}
            <div title={!rgbAvailable ? t('show.rgbUnavailable') : undefined}>
              <Toggle
                on={activeColorMode === 'rgb'}
                onChange={(on) => changeColorMode(on ? 'rgb' : 'height')}
                disabled={!rgbAvailable}
                label={t('show.colorMode')}
                title={!rgbAvailable ? t('show.rgbUnavailable') : undefined}
                describedBy={!rgbAvailable ? colorModeHintId : undefined}
              >
                <span className="flex flex-col gap-0.5">
                  <span>{t('show.colorMode')}</span>
                  <span className="text-xs text-ink-2">
                    {t(activeColorMode === 'rgb' ? 'show.rgb' : 'show.heightBlue')}
                  </span>
                </span>
              </Toggle>
              {!rgbAvailable && (
                <span id={colorModeHintId} role="tooltip" className="sr-only">
                  {t('show.rgbUnavailable')}
                </span>
              )}
            </div>
            <label className="flex items-center gap-3 px-2.5 text-[13px]">
              <span className="shrink-0">{t('show.pointSize')}</span>
              <input
                type="range"
                min={0.5}
                max={8}
                step={0.25}
                value={opts.pointSize}
                onChange={(e) => setOpts({ ...opts, pointSize: Number(e.target.value) })}
                className="w-full"
              />
            </label>
          </section>

          <section className="flex flex-col gap-1.5">
            <H2>{t('run.title')}</H2>
            <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1 text-[13px]">
              <Row k={t('run.used')} v={stats.frames_extracted - stats.frames_dropped} />
              <Row k={t('run.dropped')} v={stats.frames_dropped} />
              <Row k={t('run.registered')} v={stats.frames_registered} />
              <Row k={t('run.points')} v={stats.points.toLocaleString()} />
              {totalS > 0 && <Row k={t('run.time')} v={`${totalS.toFixed(1)} ${t('unit.s')}`} />}
            </div>
            <div className="mt-1 flex gap-1" aria-hidden="true">
              {RAMP.map((c) => (
                <span key={c} className="h-1.5 flex-1 rounded-full" style={{ background: c }} />
              ))}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}
