import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router';
import { createDemo } from '../fixtures/demo';
import { useI18n, type Key } from '../i18n';
import {
  BundleError,
  FarLayerError,
  loadBundle,
  loadManifest,
  type Bundle,
  type FrameInfo,
} from '../lib/bundle';
import { ViewerScreen } from '../viewer/ViewerScreen';
import { useRunEvidence } from '../viewer/useRunEvidence';
import { EvidencePanel } from '../viewer/EvidencePanel';

type State =
  | { s: 'loading'; p: number; frames?: readonly FrameInfo[] }
  | { s: 'error'; code: 'noManifest' | 'badVersion' | 'generic'; msg: string }
  | { s: 'ready'; bundle: Bundle };

export function ViewerPage() {
  const { runId = '' } = useParams();
  const { t, lang } = useI18n();
  const [st, setSt] = useState<State>({ s: 'loading', p: 0 });
  const [search] = useSearchParams();
  const requestedFinding = search.get('finding');
  const requestKey = `${runId}:${requestedFinding}`;
  const [dismissedFinding, setDismissedFinding] = useState<string | null>(null);
  const initialFinding = dismissedFinding === requestKey ? null : requestedFinding;
  const evidence = useRunEvidence(runId, initialFinding !== null);
  const numericFinding =
    initialFinding !== null &&
    /^\d+$/.test(initialFinding) &&
    Number.isSafeInteger(Number(initialFinding))
      ? Number(initialFinding)
      : null;
  const findingId =
    initialFinding === 'first' ? (evidence.findings[0]?.id ?? null) : numericFinding;

  useEffect(() => {
    if (runId === 'demo') return;
    let dead = false;
    setSt({ s: 'loading', p: 0 });
    (async () => {
      try {
        const m = await loadManifest(runId);
        if (!dead) setSt({ s: 'loading', p: 0, frames: m.frames });
        const bundle = await loadBundle(
          runId,
          m,
          (p) => !dead && setSt({ s: 'loading', p, frames: m.frames }),
        );
        if (!dead) setSt({ s: 'ready', bundle });
      } catch (e) {
        if (dead) return;
        if (e instanceof FarLayerError) {
          setSt({ s: 'ready', bundle: e.bundle });
          return;
        }
        const code = e instanceof BundleError ? e.code : 'generic';
        setSt({ s: 'error', code, msg: String(e) });
      }
    })();
    return () => {
      dead = true;
    };
  }, [runId]);

  // The demo reuses the local bundle and does not need a backend manifest.
  const bundle = runId === 'demo' ? createDemo(lang).bundle : st.s === 'ready' ? st.bundle : null;
  if (bundle) {
    const value = search.get('point');
    const point = value !== null && /^\d+$/.test(value) ? Number(value) : null;
    const valid =
      point !== null &&
      Number.isSafeInteger(point) &&
      point < bundle.manifest.points.count &&
      point * 3 + 2 < bundle.position.length;
    return (
      <ViewerScreen
        key={`${runId}:${search}`}
        bundle={bundle}
        initialPoint={valid ? point : null}
        invalidPoint={search.has('point') && !valid}
        initialMode={search.get('mode') === 'observer' ? 'observer' : 'orbit'}
        initialFinding={initialFinding}
      />
    );
  }
  // Evidence is useful before the map is ready. Do not serialize its API or frames
  // behind binary cloud downloads, ground estimation, or WebGL initialization.
  if (st.s === 'loading' && (initialFinding === 'first' || numericFinding !== null)) {
    return (
      <div className="qv qv-screen viewer-screen">
        <div className="qv-screen__main viewer-workspace">
          <main className="qv-map viewer-main">
            <p role="status">{t('viewer.loading')}</p>
          </main>
          <div className="viewer-overlays">
            <EvidencePanel
              findingId={findingId}
              frames={st.frames ?? []}
              listState={
                initialFinding === 'first'
                  ? evidence.api.isPending
                    ? 'loading'
                    : evidence.api.isError
                      ? 'error'
                      : undefined
                  : undefined
              }
              onRetry={() => void evidence.api.refetch()}
              onClose={() => setDismissedFinding(requestKey)}
            />
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="viewer-page-message">
      {st.s === 'loading' ? (
        <>
          <p>
            {t('viewer.loading')} · <span className="viewer-mono">{Math.round(st.p * 100)}%</span>
          </p>
          <div
            className="viewer-loading-track"
            role="progressbar"
            aria-valuenow={Math.round(st.p * 100)}
          >
            <div className="viewer-loading-progress" style={{ width: `${st.p * 100}%` }} />
          </div>
        </>
      ) : st.s === 'error' ? (
        <>
          <p className="viewer-page-error" role="alert">
            {t(`error.${st.code}` as Key)}
          </p>
          <p className="viewer-mono viewer-caption viewer-muted">
            {runId}: {st.msg}
          </p>
          <Link className="viewer-page-link" to="/">
            {t('error.back')}
          </Link>
        </>
      ) : null}
    </div>
  );
}
