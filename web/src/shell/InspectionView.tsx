import { useQuery } from '@tanstack/react-query';
import { useI18n, type Key } from '../i18n';
import { BundleError, FarLayerError, loadBundle, loadManifest, type Bundle } from '../lib/bundle';
import type { InspectionSummary, RunSummary } from '../lib/inspections';
import { processingSteps } from '../lib/processing';
import { ProcessingSteps } from './ProcessingSteps';
import { ViewerScreen } from '../viewer/ViewerScreen';

export function InspectionView({
  inspection,
  run,
  demoBundle,
}: {
  inspection: InspectionSummary;
  run?: RunSummary;
  demoBundle?: Bundle;
}) {
  const { t, lang } = useI18n();
  const query = useQuery({
    queryKey: ['inspection-bundle', inspection.id],
    queryFn: async () => {
      const manifest = await loadManifest(inspection.id);
      try {
        return await loadBundle(inspection.id, manifest, () => {});
      } catch (error) {
        if (error instanceof FarLayerError) return error.bundle;
        throw error;
      }
    },
    enabled: !demoBundle,
    retry: false,
    staleTime: 60_000,
  });
  const bundle = demoBundle ?? query.data;
  const steps = processingSteps(run, bundle?.manifest);
  const demoTitle = demoBundle ? t('demo.inspectionTitle') : inspection.title;
  const errorKey =
    query.error instanceof BundleError ? (`error.${query.error.code}` as Key) : 'error.generic';

  return (
    <article className="inspection-view">
      <header className="inspection-heading">
        <div>
          <p className="eyebrow">{t('shell.inspection')}</p>
          <h1>{demoTitle}</h1>
          <p className="inspection-meta">
            {inspection.createdAt ? (
              <time dateTime={inspection.createdAt}>
                {new Intl.DateTimeFormat(lang, { dateStyle: 'long', timeStyle: 'short' }).format(
                  new Date(inspection.createdAt),
                )}
              </time>
            ) : (
              t('inspection.dateUnknown')
            )}
            {!demoBundle && inspection.source && (
              <span className="inspection-source">{inspection.source}</span>
            )}
            <span className={`status-label ${inspection.status}`}>
              <span className={`status-dot ${inspection.status}`} aria-hidden="true" />
              {t(`status.${inspection.status}`)}
            </span>
          </p>
        </div>
        {demoBundle && <span className="demo-badge">{t('shell.demo')}</span>}
      </header>
      {demoBundle && <p className="demo-notice">{t('demo.notice')}</p>}
      <ProcessingSteps steps={steps} />
      <section aria-labelledby="inspection-map-title" className="inspection-map-section">
        <div className="section-heading">
          <h2 id="inspection-map-title">{t('inspection.mapAndVideo')}</h2>
          <span className="muted">{t('inspection.framesNote')}</span>
        </div>
        {bundle ? (
          <div className="inspection-viewer">
            <ViewerScreen key={inspection.id} bundle={bundle} siteId={inspection.siteId} embedded />
          </div>
        ) : query.isError ? (
          <div className="map-message" role="alert">
            <p>{t(errorKey)}</p>
            <button className="shell-button" onClick={() => void query.refetch()}>
              {t('shell.retry')}
            </button>
          </div>
        ) : (
          <p className="map-message" role="status">
            {t('viewer.loading')}
          </p>
        )}
      </section>
    </article>
  );
}
