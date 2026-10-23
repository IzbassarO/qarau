import { useQuery } from '@tanstack/react-query';
import { useI18n, type Key } from '../i18n';
import { BundleError, FarLayerError, loadBundle, loadManifest, type Bundle } from '../lib/bundle';
import {
  getInspection,
  inspectionStatus,
  type InspectionSummary,
  type RunSummary,
} from '../lib/inspections';
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
  const detail = useQuery({
    queryKey: ['inspection', inspection.id],
    queryFn: ({ signal }) => getInspection(inspection.id, signal),
    enabled: !demoBundle,
    retry: false,
    refetchInterval: (query) =>
      ['done', 'failed'].includes(inspectionStatus(query.state.data?.status)) ? false : 3000,
  });
  const status = inspectionStatus(detail.data?.status ?? inspection.status);
  const runId = detail.data?.runId ?? inspection.runId;
  const query = useQuery({
    queryKey: ['inspection-bundle', runId],
    queryFn: async () => {
      const manifest = await loadManifest(runId!);
      try {
        return await loadBundle(runId!, manifest, () => {});
      } catch (error) {
        if (error instanceof FarLayerError) return error.bundle;
        throw error;
      }
    },
    enabled: !demoBundle && status === 'done' && !!runId,
    retry: false,
    staleTime: 60_000,
  });
  const bundle = demoBundle ?? query.data;
  const source = inspection.source ?? bundle?.manifest.source.name;
  const steps = processingSteps(demoBundle ? run : detail.data, bundle?.manifest);
  const demoTitle = demoBundle
    ? t('demo.inspectionTitle')
    : inspection.title === inspection.id && /^\d+$/.test(inspection.id)
      ? `${t('shell.inspection')} ${inspection.id}`
      : inspection.title;
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
            {!demoBundle && source && <span className="inspection-source">{source}</span>}
            <span className={`status-label ${status}`}>
              <span className={`status-dot ${status}`} aria-hidden="true" />
              {t(`status.${status}`)}
            </span>
          </p>
        </div>
        {demoBundle && <span className="demo-badge">{t('shell.demo')}</span>}
      </header>
      {demoBundle && <p className="demo-notice">{t('demo.notice')}</p>}
      {detail.isError && !demoBundle && (
        <div className="api-error" role="alert">
          <p>{t('inspection.detailError')}</p>
          <button className="shell-button" onClick={() => void detail.refetch()}>
            {t('shell.retry')}
          </button>
        </div>
      )}
      <ProcessingSteps steps={steps} uploaded={status === 'uploaded'} />
      <section aria-labelledby="inspection-map-title" className="inspection-map-section">
        <div className="section-heading">
          <h2 id="inspection-map-title">{t('inspection.mapAndVideo')}</h2>
          <span className="muted">{t('inspection.framesNote')}</span>
        </div>
        {bundle ? (
          <div className="inspection-viewer">
            <ViewerScreen bundle={bundle} embedded />
          </div>
        ) : status !== 'done' || !runId ? (
          <p className="map-message" role="status">
            {t('processing.mapWaiting')}
          </p>
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
