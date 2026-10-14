import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router';
import { useI18n } from '../i18n';
import { runUrl, type Bundle } from '../lib/bundle';
import { csrSlice, heightRamp, RAMP } from '../lib/geometry';
import { ViewerEngine, type Options } from './engine';
import { H2, LangSwitch, Logo, Row, Toggle } from './ui';

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
const fmtTime = (s: number) => {
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, '0')}:${(s - m * 60).toFixed(1).padStart(4, '0')}`;
};

function ToolButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className="flex size-9 cursor-pointer items-center justify-center rounded-lg border-0 bg-transparent text-xl text-[#c9ced6] hover:bg-raised"
    >
      {children}
    </button>
  );
}

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

export function ViewerScreen({ bundle }: { bundle: Bundle }) {
  const { t } = useI18n();
  const m = bundle.manifest;
  const frames = m.frames;
  const isImages = m.source.kind === 'images';
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<ViewerEngine | null>(null);
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [picked, setPicked] = useState<number | null>(null);
  const [fps, setFps] = useState(0);
  const [opts, setOpts] = useState<Options>({
    buildUp: true,
    showCurrent: true,
    frustums: true,
    rgb: false,
    pointSize: 2.5,
  });
  const [glError, setGlError] = useState(false);

  const f = frames[frame];
  const tc = (i: number) => (isImages ? `#${i}` : fmtTime(frames[i].t));

  // engine lifecycle
  useEffect(() => {
    const canvas = canvasRef.current!;
    let eng: ViewerEngine;
    try {
      eng = new ViewerEngine(canvas, bundle, { onPick: (p) => setPicked(p) });
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
  }, [bundle]);

  useEffect(() => engineRef.current?.setFrame(frame), [frame, bundle]);
  useEffect(() => engineRef.current?.setOptions(opts), [opts, bundle]);
  useEffect(() => engineRef.current?.setPicked(picked), [picked, bundle]);

  // play: step in real time by frames[i].t (photo sets: 4 frames per second)
  useEffect(() => {
    if (!playing) return;
    const rate = isImages ? 4 : 1;
    const start = performance.now();
    const t0 = isImages ? frame : frames[frame].t;
    let raf = 0;
    const tick = () => {
      const el = ((performance.now() - start) / 1000) * rate + t0;
      let i = frame;
      while (i + 1 < frames.length && (isImages ? i + 1 : frames[i + 1].t) <= el) i++;
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

  const observed = useMemo(
    () =>
      picked === null
        ? null
        : Array.from(csrSlice(bundle.pointOffsets, bundle.pointFrames, picked)),
    [picked, bundle],
  );

  const eng = () => engineRef.current;
  const ticks = frames.filter((x) => !x.registered || !x.kept);
  const stats = m.stats;
  const totalS = Object.values(stats.processing_s ?? {}).reduce((a, b) => a + b, 0);
  const rampSwatches = [0, 1 / 3, 2 / 3, 1].map((v) => heightRamp(v));

  return (
    <div className="flex h-full min-h-[640px] flex-col overflow-hidden bg-bg">
      <header className="flex flex-wrap items-center gap-4 border-b border-line bg-surface px-4 py-2.5">
        <Link to="/" aria-label="Qarau">
          <Logo />
        </Link>
        <span className="text-ink-2">{m.run_id}</span>
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
          {import.meta.env.DEV && ` · ${fps} fps`}
        </span>
        <LangSwitch />
      </header>

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
          <ToolButton label={t('toolbar.camera')} onClick={() => eng()?.viewThroughFrame(frame)}>
            {icon(
              <>
                <rect x="2" y="5" width="10" height="8" rx="1.5" />
                <path d="M12 8l4-2v6l-4-2" />
              </>,
            )}
          </ToolButton>
          <span className="my-1 h-px w-6 bg-line" />
          <ToolButton label={t('toolbar.zoomIn')} onClick={() => eng()?.zoom(0.8)}>
            +
          </ToolButton>
          <ToolButton label={t('toolbar.zoomOut')} onClick={() => eng()?.zoom(1.25)}>
            −
          </ToolButton>
        </div>

        <main className="flex min-w-0 flex-1 flex-col">
          <div className="relative min-h-0 grow bg-black">
            <canvas
              ref={canvasRef}
              aria-label={t('canvas.label')}
              className="absolute inset-0 size-full outline-none"
            />
            {glError && (
              <p className="absolute inset-0 grid place-items-center text-watch">
                {t('error.webgl')}
              </p>
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
            <div className="absolute bottom-3 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-md bg-bg/80 px-2.5 py-1.5 text-xs text-ink-2">
              {t('canvas.hint')}
            </div>
          </div>

          <div className="flex flex-col gap-2 border-t border-line bg-surface px-4 pb-3.5 pt-2.5">
            <div className="flex items-center gap-3">
              <button
                type="button"
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
                  {/* ponytail: finding ticks arrive in M2; gray marks = frames not used */}
                  {ticks.map((x) => (
                    <span
                      key={x.i}
                      title={t('timeline.notUsed')}
                      className="absolute top-0 h-2 w-0.5 rounded-sm bg-notseen"
                      style={{ left: `${(x.i / Math.max(1, frames.length - 1)) * 100}%` }}
                    />
                  ))}
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
          <section className="flex flex-col gap-2">
            <div className="flex items-baseline justify-between">
              <h2 className="m-0 text-sm font-semibold">{t('panel.frame')}</h2>
              <span className={`${mono} text-xs text-ink-2`}>{tc(frame)}</span>
            </div>
            <div className="relative aspect-video overflow-hidden rounded-lg border border-line bg-[#0d1014]">
              <img
                src={runUrl(bundle.runId, f.thumb)}
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
            <Toggle on={opts.rgb} onChange={(v) => setOpts({ ...opts, rgb: v })}>
              {t('show.rgb')}
            </Toggle>
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

          <section className="flex flex-col gap-2">
            <H2>{t('point.title')}</H2>
            {observed === null ? (
              <p className="m-0 text-[13px] text-ink-2">{t('point.hint')}</p>
            ) : (
              <div className="flex flex-col gap-2 rounded-[10px] border border-line-strong p-3">
                <span className="text-[13px]">
                  {observed.length ? t('point.observed') : t('point.none')}
                </span>
                <div className="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto">
                  {observed.map((i) => (
                    <button
                      key={i}
                      type="button"
                      onClick={() => jump(i)}
                      className={`${mono} h-7 cursor-pointer rounded-md border border-line-strong bg-transparent px-2 text-xs text-ink ${i === frame ? 'bg-raised' : ''}`}
                    >
                      {tc(i)}
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  onClick={() => setPicked(null)}
                  className="h-7 cursor-pointer self-start rounded-md border border-line-strong bg-transparent px-2 text-xs text-ink-2"
                >
                  {t('point.clear')}
                </button>
              </div>
            )}
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
