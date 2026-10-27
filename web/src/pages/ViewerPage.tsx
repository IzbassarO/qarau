import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import { createDemo } from '../fixtures/demo';
import { useI18n, type Key } from '../i18n';
import { BundleError, FarLayerError, loadBundle, loadManifest, type Bundle } from '../lib/bundle';
import { ViewerScreen } from '../viewer/ViewerScreen';
import { EvidencePanel } from '../viewer/EvidencePanel';
import { findingsForRunOptions } from '../lib/findings';

type State =
  | { s: 'loading'; p: number }
  | { s: 'error'; code: 'noManifest' | 'badVersion' | 'generic'; msg: string }
  | { s: 'ready'; bundle: Bundle };

export function ViewerPage() {
  const { runId = '' } = useParams();
  const { t, lang } = useI18n();
  const [st, setSt] = useState<State>({ s: 'loading', p: 0 });
  const [search, setSearch] = useSearchParams();
  const findings = useQuery({ ...findingsForRunOptions(runId), enabled: runId !== 'demo' });
  const selection = search.get('finding');
  const selectedFindingId =
    selection === 'first'
      ? (findings.data?.[0]?.id ?? null)
      : (findings.data?.find((finding) => String(finding.id) === selection)?.id ?? null);
  const selectFinding = (id: number | null) => {
    setSearch((previous) => {
      const next = new URLSearchParams(previous);
      if (id === null) next.delete('finding');
      else next.set('finding', String(id));
      return next;
    });
  };

  useEffect(() => {
    if (runId === 'demo') return;
    let dead = false;
    setSt({ s: 'loading', p: 0 });
    (async () => {
      try {
        const m = await loadManifest(runId);
        const bundle = await loadBundle(runId, m, (p) => !dead && setSt({ s: 'loading', p }));
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
  let content;
  if (bundle) {
    const value = search.get('point');
    const point = value !== null && /^\d+$/.test(value) ? Number(value) : null;
    const valid =
      point !== null &&
      Number.isSafeInteger(point) &&
      point < bundle.manifest.points.count &&
      point * 3 + 2 < bundle.position.length;
    content = (
      <ViewerScreen
        key={`${runId}:${search.get('point')}:${search.get('mode')}`}
        bundle={bundle}
        apiFindings={findings.data ?? []}
        selectedFindingId={selectedFindingId}
        onFindingSelect={selectFinding}
        initialPoint={valid ? point : null}
        invalidPoint={search.has('point') && !valid}
        initialMode={search.get('mode') === 'observer' ? 'observer' : 'orbit'}
      />
    );
  } else
    content = (
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
  return (
    <div className="qv viewer-page">
      {content}
      <EvidencePanel
        key={runId}
        findings={findings.data}
        selection={selection}
        loading={runId !== 'demo' && findings.isPending}
        error={findings.isError}
        manifest={bundle?.manifest}
        onClose={() => selectFinding(null)}
      />
    </div>
  );
}
