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
import './viewer.css';
import { ViewerEngine, type ColorMode, type Options, type PointLayer } from './engine';
import { defaultViewState } from './defaults';
import { AxisGizmo, type AxisGizmoHandle } from './AxisGizmo';
import {
  H2,
  DenseToggle,
  LangSwitch,
  Logo,
  ModeSwitch,
  NavigationSettings,
  PointInfoTool,
  Row,
  Toggle,
  ToolButton,
} from './ui';
import { buildDensePointInfo, buildPointInfo, frameTime } from './pointInfo';
import { PointInfoPanel } from './PointInfoPanel';
import { Minimap, type MinimapHandle } from './Minimap.tsx';
import { canShowHumanFigure } from './observer';
import { SessionImage } from '../auth/SessionImage';
import { sessionUserId, subscribeToAuth } from '../auth/auth';
import {
  loadSensitivity,
  saveSensitivity,
  loadObserverBoostKey,
  saveObserverBoostKey,
} from './settings';

declare global {
  interface Window {
    __qarau?: {
      pointsLoaded: boolean;
      fps: number;
      engine?: ViewerEngine;
      bench: (on: boolean) => void;
      denseLayer: () => { available: boolean; visible: boolean; count: number };
      densePointScreen: (index: number) => { x: number; y: number } | null;
    };
  }
}

const mono = 'viewer-mono';
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
    stroke="currentColor"
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
  initialMode = 'orbit',
}: {
  bundle: Bundle;
  embedded?: boolean;
  initialPoint?: number | null;
  invalidPoint?: boolean;
  initialMode?: 'orbit' | 'observer';
}) {
  const { t } = useI18n();
  const m = bundle.manifest;
  const frames = m.frames;
  const isImages = m.source.kind === 'images';
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<ViewerEngine | null>(null);
  const gizmoRef = useRef<AxisGizmoHandle>(null);
  const minimapRef = useRef<MinimapHandle>(null);
  const [flyMode, setFlyMode] = useState(initialMode === 'observer');
  const [thirdPerson, setThirdPerson] = useState(false);
  const [humanFigure, setHumanFigure] = useState(false);
  const [alignMode, setAlignMode] = useState(false);
  const [pointInfoMode, setPointInfoMode] = useState(
    initialMode !== 'observer' && initialPoint !== null,
  );
  const userId = useSyncExternalStore(subscribeToAuth, sessionUserId, () => null);
  const [sensitivitySetting, setSensitivitySetting] = useState(() => ({
    userId,
    value: loadSensitivity(userId),
  }));
  const sensitivity =
    sensitivitySetting.userId === userId ? sensitivitySetting.value : loadSensitivity(userId);
  const [boostSetting, setBoostSetting] = useState(() => ({
    userId,
    value: loadObserverBoostKey(userId),
  }));
  const boostKey =
    boostSetting.userId === userId ? boostSetting.value : loadObserverBoostKey(userId);
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [picked, setPicked] = useState<{ index: number; layer: PointLayer } | null>(
    initialPoint === null ? null : { index: initialPoint, layer: 'sparse' },
  );
  const [colorMode, setColorMode] = useState(savedColorMode);
  const defaults = defaultViewState(bundle, colorMode);
  const rgbAvailable = hasRgb(bundle) || !!bundle.dense?.color.length;
  const activeColorMode = defaults.colorMode;
  const colorModeHintId = useId();
  const [showFarLayer, setShowFarLayer] = useState(defaults.showFarLayer);
  const farAvailable = !!bundle.far?.position.length;
  const denseAvailable = !!bundle.dense;
  const [showDenseLayer, setShowDenseLayer] = useState(denseAvailable);
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
  const sevColor = (x: Finding) =>
    x.severity === 'critical' ? 'var(--color-finding-critical)' : 'var(--color-finding-watch)';
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
        onPick: (p, layer = 'sparse') => setPicked(p === null ? null : { index: p, layer }),
        onFlyModeChange: setFlyMode,
        onThirdPersonChange: setThirdPerson,
        onAlignModeChange: setAlignMode,
        onPointInfoModeChange: setPointInfoMode,
      });
    } catch {
      setGlError(true);
      return;
    }
    engineRef.current = eng;
    if (initialMode === 'observer') eng.setObserverMode();
    const ro = new ResizeObserver(() => eng.resize());
    ro.observe(canvas);
    eng.resize();
    if (import.meta.env.DEV) {
      window.__qarau = {
        pointsLoaded: true,
        fps: 0,
        engine: eng,
        bench: (on) => (eng.bench = on),
        denseLayer: () => ({
          available: eng.hasDenseLayer(),
          visible: eng.scene.getObjectByName('dense')?.visible ?? false,
          count: bundle.dense?.count ?? 0,
        }),
        densePointScreen: (index) => {
          if (
            !bundle.dense ||
            !Number.isSafeInteger(index) ||
            index < 0 ||
            index >= bundle.dense.count
          )
            return null;
          const p = eng.project(
            Array.from(bundle.dense.position.subarray(index * 3, index * 3 + 3)),
          );
          return p.visible ? { x: p.x, y: p.y } : null;
        },
      };
      const q = setInterval(() => window.__qarau && (window.__qarau.fps = eng.fps), 500);
      return () => {
        clearInterval(q);
        ro.disconnect();
        eng.dispose();
        delete window.__qarau;
      };
    }
    return () => {
      ro.disconnect();
      eng.dispose();
    };
  }, [bundle, initialMode]);

  useEffect(() => engineRef.current?.setFrame(frame), [frame, bundle]);
  useEffect(() => engineRef.current?.setOptions(opts), [opts, bundle]);
  useEffect(() => engineRef.current?.setFarVisible(showFarLayer), [showFarLayer, bundle]);
  useEffect(() => engineRef.current?.setDenseVisible(showDenseLayer), [showDenseLayer, bundle]);
  useEffect(() => engineRef.current?.setColorMode(activeColorMode), [activeColorMode, bundle]);
  useEffect(() => {
    const point = pointInfoMode ? picked : null;
    if (point?.layer === 'dense') engineRef.current?.setPicked(point.index, 'dense');
    else engineRef.current?.setPicked(point?.index ?? null);
  }, [picked, pointInfoMode, bundle]);
  useEffect(() => engineRef.current?.setPointInfoMode(pointInfoMode), [pointInfoMode, bundle]);
  useEffect(() => engineRef.current?.setSelectedFinding(sel ?? 0), [sel, bundle]);
  useEffect(() => engineRef.current?.setSensitivity(sensitivity), [sensitivity, bundle]);
  useEffect(() => engineRef.current?.setBoostKey(boostKey), [boostKey, bundle]);
  useEffect(() => engineRef.current?.setHumanFigureVisible(humanFigure), [humanFigure, bundle]);

  // finding markers are HTML on top of the canvas, re-placed after every render
  useEffect(() => {
    const e = engineRef.current;
    if (!e) return;
    const place = () => {
      gizmoRef.current?.update(e.camera.quaternion);
      const observer = e.observerView;
      if (observer) minimapRef.current?.update(observer);
      for (const x of findings) {
        const el = markerEls.current.get(x.id);
        if (!el) continue;
        const p = e.project(x.centroid);
        el.style.transform = `translate(${p.x}px, ${p.y}px) translate(-50%, -50%)`;
        el.style.visibility = p.visible ? 'visible' : 'hidden';
      }
    };
    e.onRender = place;
    place();
    return () => {
      e.onRender = undefined;
    };
  }, [bundle, findings, frame, opts.buildUp, opts.findings, flyMode]);

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
    () =>
      picked === null
        ? null
        : picked.layer === 'dense'
          ? buildDensePointInfo(bundle, picked.index)
          : buildPointInfo(bundle, picked.index),
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
    <div className={`qv qv-screen viewer-screen${embedded ? ' viewer-embedded' : ''}`}>
      {!embedded && (
        <header className="qv-topbar viewer-header">
          <Link to="/" aria-label="Qarau">
            <Logo />
          </Link>
          <div className="qv-topbar__title viewer-navigation">
            <Link to="/" className="qv-crumb">
              {t('nav.overview')}
            </Link>
            <span className="qv-mono viewer-run-id">{bundle.runId}</span>
            <span className="qv-caption">{t('nav.viewer')}</span>
          </div>
          <LangSwitch />
        </header>
      )}

      {bundle.farError && (
        <p role="alert" className="qv-notice viewer-notice viewer-error">
          {t(bundle.farError === 'missing' ? 'viewer.far.errorMissing' : 'viewer.far.errorBroken')}
        </p>
      )}
      {invalidPoint && (
        <p role="note" className="qv-notice viewer-notice viewer-caption viewer-muted">
          {t('pointInfo.invalidPoint')}
        </p>
      )}

      <div className="qv-screen__main viewer-workspace">
        <details name="viewer-panels" className="viewer-tools-drawer">
          <summary className="qv-pill">{t('toolbar.label')}</summary>
          <div
            role="toolbar"
            aria-label={t('toolbar.label')}
            aria-orientation="vertical"
            className="qv-float viewer-toolbar"
          >
            <ToolButton label={t('toolbar.reset')} onClick={() => eng()?.resetView()}>
              {icon(<path d="M3 9a6 6 0 1 0 2-4.5M3 3v3h3" />)}
            </ToolButton>
            <ToolButton label={t('toolbar.top')} onClick={() => eng()?.topView()}>
              {icon(
                <>
                  <rect x="3" y="3" width="12" height="12" rx="2" />
                  <circle cx="9" cy="9" r="2" fill="currentColor" />
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
            <span className="viewer-toolbar-divider" />
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
        </details>

        <main className="viewer-main">
          <div className="viewer-modebar">
            <ModeSwitch
              orbitActive={!flyMode}
              onOrbit={() => eng()?.setOrbitMode()}
              onObserver={() => eng()?.setObserverMode()}
            />
            {flyMode && (
              <div className="viewer-inline">
                <button
                  type="button"
                  aria-pressed={thirdPerson}
                  onClick={() => {
                    eng()?.toggleThirdPerson();
                    canvasRef.current?.focus({ preventScroll: true });
                  }}
                  className="qv-btn qv-btn--secondary qv-btn--sm viewer-control"
                >
                  {t('viewer.observer.thirdPerson')}
                </button>
                {thirdPerson && canShowHumanFigure(bundle) && (
                  <button
                    type="button"
                    aria-pressed={humanFigure}
                    onClick={() => {
                      setHumanFigure((visible) => !visible);
                      canvasRef.current?.focus({ preventScroll: true });
                    }}
                    className="qv-btn qv-btn--secondary qv-btn--sm viewer-control"
                  >
                    {t('viewer.observer.humanFigure')}
                  </button>
                )}
              </div>
            )}
            <NavigationSettings
              sensitivity={sensitivity}
              onSensitivityChange={(value) => {
                setSensitivitySetting({ userId, value });
                saveSensitivity(userId, value);
              }}
              boostKey={boostKey}
              onBoostKeyChange={(value) => {
                setBoostSetting({ userId, value });
                saveObserverBoostKey(userId, value);
              }}
            />
          </div>
          <div className="qv-map viewer-canvas">
            <canvas
              ref={canvasRef}
              aria-label={t('canvas.label')}
              className="viewer-point-canvas"
            />
            {glError && <p className="viewer-canvas-error">{t('error.webgl')}</p>}
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
                    className={`viewer-finding-marker ${x.id === sel ? 'selected' : ''} ${x.status === 'low_evidence' ? 'low-evidence' : ''}`}
                    style={{ background: sevColor(x) }}
                  >
                    {x.id}
                  </button>
                ))}
              </div>
            )}
            <AxisGizmo
              ref={gizmoRef}
              onSelect={(axis) => {
                eng()?.axisView(axis);
                canvasRef.current?.focus({ preventScroll: true });
              }}
            />
            {flyMode && (
              <Minimap ref={minimapRef} bundle={bundle} label={t('viewer.observer.minimap')} />
            )}
            <div role="status" className={`qv-hud viewer-hint ${flyMode ? 'observer' : 'orbit'}`}>
              {alignMode
                ? t('canvas.alignHint')
                : flyMode
                  ? t('canvas.observerHint')
                  : pointInfoMode
                    ? t('pointInfo.hint')
                    : t('canvas.hint')}
            </div>
          </div>

          <div className="qv-timeline viewer-timeline">
            <div className="viewer-timeline-row">
              <button
                type="button"
                disabled={!canPlay}
                onClick={() =>
                  setPlaying((p) => (!p && frame >= frames.length - 1 ? (setFrame(0), true) : !p))
                }
                aria-label={playing ? t('timeline.pause') : t('timeline.play')}
                className="qv-btn qv-btn--sm viewer-play"
              >
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 14 14"
                  aria-hidden="true"
                  fill="currentColor"
                >
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
              <span className={`${mono} viewer-timecode`}>{tc(frame)}</span>
              <div className="viewer-timeline-track">
                <div aria-hidden="true" className="viewer-timeline-ticks">
                  {/* gray marks = frames not used; coloured = frames where a finding is visible */}
                  {ticks.map((x) => (
                    <span
                      key={x.i}
                      title={t('timeline.notUsed')}
                      className="viewer-timeline-tick unused"
                      style={{ left: `${(x.i / Math.max(1, frames.length - 1)) * 100}%` }}
                    />
                  ))}
                  {tickFindings.flatMap((x) =>
                    x.frames.map((i) => (
                      <span
                        key={`${x.id}-${i}`}
                        data-testid="finding-tick"
                        title={`${t('timeline.findingTick')}: ${t('findings.marker')} ${x.id}`}
                        className="viewer-timeline-tick"
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
                  className="viewer-timeline-range"
                />
              </div>
              <span className={`${mono} viewer-caption viewer-muted`}>
                {frame + 1} / {frames.length}
              </span>
            </div>
            <span className="viewer-caption viewer-muted">{t('timeline.hint')}</span>
          </div>
        </main>

        <details
          name="viewer-panels"
          className="viewer-panel-drawer"
          open={(pointInfoMode && !!pointInfo) || undefined}
        >
          <summary className="qv-pill">{t('viewer.details')}</summary>
          <aside className="qv-float qv-fold__body viewer-panel">
            <div className="qv-float viewer-legend">
              {[
                ['var(--color-viewer-current)', 'legend.now'],
                [`rgb(${rampSwatches[1].join(',')})`, 'legend.seen'],
                ['var(--color-viewer-later)', 'legend.later'],
                ['var(--color-finding-not-seen)', 'legend.never'],
              ].map(([c, k]) => (
                <span key={k} className="viewer-inline">
                  <span className="viewer-legend-swatch" style={{ background: c }} />
                  {t(k as 'legend.now')}
                </span>
              ))}
            </div>

            {pointInfoMode && pointInfo && (
              <PointInfoPanel info={pointInfo} onSeek={seekPointFrame} onClose={closePointInfo} />
            )}
            {pointInfoMode && !pointInfo && (
              <p className="viewer-small viewer-muted">{t('pointInfo.hint')}</p>
            )}
            <section className="viewer-section">
              <div className="viewer-section-heading">
                <h2 className="qv-heading viewer-heading">{t('panel.frame')}</h2>
                <span className={`${mono} viewer-caption viewer-muted`}>{tc(frame)}</span>
              </div>
              <div className="qv-thumb__img viewer-frame">
                <SessionImage
                  src={bundle.assetUrl?.(f.thumb) ?? runUrl(bundle.runId, f.thumb)}
                  alt={t('panel.frame')}
                  className="size-full object-contain"
                />
              </div>
              <p className={`${mono} viewer-caption viewer-muted truncate`}>
                {f.image.split('/').pop()} · {t('panel.blur')}{' '}
                {f.blur === null ? '—' : f.blur.toFixed(1)}
                {f.drop_reason === 'blur' && ` · ${t('panel.droppedBlur')}`}
                {f.drop_reason === 'exposure' && ` · ${t('panel.droppedExposure')}`}
              </p>
              {!f.registered && (
                <p className="viewer-small viewer-error">{t('panel.notRegistered')}</p>
              )}
              <div className="viewer-data">
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
              <p className="viewer-caption viewer-muted">{t('panel.cameraHeightNote')}</p>
            </section>

            <section className="viewer-section" aria-label={t('findings.title')}>
              <H2>{t('findings.title')}</H2>
              {!m.findings ? (
                <p className="viewer-small viewer-muted">{t('findings.notRun')}</p>
              ) : bundle.findingsError ? (
                <p role="alert" className="viewer-small viewer-error">
                  {t(
                    bundle.findingsError === 'missing'
                      ? 'findings.errorMissing'
                      : 'findings.errorBroken',
                  )}
                </p>
              ) : findings.length === 0 ? (
                <p className="viewer-small viewer-muted">{t('findings.empty')}</p>
              ) : (
                <ul className="viewer-findings">
                  {findings.map((x) => (
                    <li key={x.id}>
                      <button
                        type="button"
                        data-testid="finding-item"
                        aria-pressed={x.id === sel}
                        onClick={() => select(x)}
                        className="viewer-finding-item"
                      >
                        <span className="viewer-finding-id" style={{ background: sevColor(x) }}>
                          {x.id}
                        </span>
                        <span className="viewer-finding-label">
                          <span className="truncate">
                            {x.class} ·{' '}
                            {t(x.severity === 'critical' ? 'findings.critical' : 'findings.watch')}
                          </span>
                          <span
                            className={`viewer-caption ${x.status === 'low_evidence' ? 'viewer-error' : 'viewer-muted'}`}
                          >
                            {t(
                              x.status === 'low_evidence'
                                ? 'findings.lowEvidence'
                                : 'findings.confirmed',
                            )}
                          </span>
                        </span>
                        <span className={`${mono} shrink-0 viewer-caption viewer-muted`}>
                          {x.frames.length} {t('findings.frames')}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {selected && (
                <div data-testid="finding-detail" className="qv-card viewer-finding-detail">
                  {selected.status === 'low_evidence' && (
                    <p className="viewer-caption viewer-error">{t('findings.lowEvidenceNote')}</p>
                  )}
                  <span className="viewer-small">{t('findings.seenAt')}</span>
                  <div className="viewer-finding-times">
                    {selected.frames.map((i) => (
                      <button
                        key={i}
                        type="button"
                        data-testid="finding-timecode"
                        onClick={() => jump(i)}
                        className={`${mono} qv-btn qv-btn--secondary qv-btn--sm viewer-control viewer-caption ${i === frame ? 'selected' : ''}`}
                      >
                        {tc(i)}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </section>

            <section className="viewer-section">
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
              {denseAvailable && <DenseToggle on={showDenseLayer} onChange={setShowDenseLayer} />}
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
                  <span className="viewer-label-stack">
                    <span>{t('show.colorMode')}</span>
                    <span className="viewer-caption viewer-muted">
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
              <label className="viewer-slider-row">
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

            <section className="viewer-section">
              <H2>{t('run.title')}</H2>
              <div className="viewer-data">
                <Row k={t('run.used')} v={stats.frames_extracted - stats.frames_dropped} />
                <Row k={t('run.dropped')} v={stats.frames_dropped} />
                <Row k={t('run.registered')} v={stats.frames_registered} />
                <Row k={t('run.points')} v={stats.points.toLocaleString()} />
                {totalS > 0 && <Row k={t('run.time')} v={`${totalS.toFixed(1)} ${t('unit.s')}`} />}
              </div>
              <div className="viewer-height-ramp" aria-hidden="true">
                {RAMP.map((c) => (
                  <span key={c} className="viewer-height-swatch" style={{ background: c }} />
                ))}
              </div>
            </section>
          </aside>
        </details>
      </div>
    </div>
  );
}
