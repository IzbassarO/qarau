import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router';
import { useI18n, type Key } from '../i18n';
import { BundleError, loadBundle, loadManifest, type Bundle } from '../lib/bundle';
import { ViewerScreen } from '../viewer/ViewerScreen';

type State =
  | { s: 'loading'; p: number }
  | { s: 'error'; code: 'noManifest' | 'badVersion' | 'generic'; msg: string }
  | { s: 'ready'; bundle: Bundle };

export function ViewerPage() {
  const { runId = '' } = useParams();
  const { t } = useI18n();
  const [st, setSt] = useState<State>({ s: 'loading', p: 0 });

  useEffect(() => {
    let dead = false;
    setSt({ s: 'loading', p: 0 });
    (async () => {
      try {
        const m = await loadManifest(runId);
        const bundle = await loadBundle(runId, m, (p) => !dead && setSt({ s: 'loading', p }));
        if (!dead) setSt({ s: 'ready', bundle });
      } catch (e) {
        if (dead) return;
        const code = e instanceof BundleError ? e.code : 'generic';
        setSt({ s: 'error', code, msg: String(e) });
      }
    })();
    return () => {
      dead = true;
    };
  }, [runId]);

  if (st.s === 'ready') return <ViewerScreen bundle={st.bundle} />;
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 bg-bg p-6 text-center">
      {st.s === 'loading' ? (
        <>
          <p>
            {t('viewer.loading')} · <span className="font-mono">{Math.round(st.p * 100)}%</span>
          </p>
          <div
            className="h-1 w-64 rounded-full bg-line"
            role="progressbar"
            aria-valuenow={Math.round(st.p * 100)}
          >
            <div className="h-1 rounded-full bg-inspected" style={{ width: `${st.p * 100}%` }} />
          </div>
        </>
      ) : (
        <>
          <p className="max-w-md text-watch" role="alert">
            {t(`error.${st.code}` as Key)}
          </p>
          <p className="font-mono text-xs text-ink-2">
            {runId}: {st.msg}
          </p>
          <Link className="text-link hover:underline" to="/">
            {t('error.back')}
          </Link>
        </>
      )}
    </div>
  );
}
