import { useQuery } from '@tanstack/react-query';
import { useI18n, type Key } from '../i18n';
import { BundleError, FarLayerError, loadBundle, loadManifest, type Bundle } from '../lib/bundle';
import {
  getInspection,
  inspectionStatus,
  type SiteInspectionSummary,
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
  inspection: SiteInspectionSummary;
  run?: RunSummary;
  demoBundle?: Bundle;
}) {
  const { t, lang } = useI18n();
  const detail = useQuery({
    queryKey: ['inspection', inspection.id],
    queryFn: ({ signal }) => getInspection(inspection.id, signal),
    enabled: !demoBundle && !inspection.legacyRun,
    retry: false,
    refetchInterval: (query) =>
      ['done', 'failed'].includes(inspectionStatus(query.state.data?.status)) ? false : 3000,
  });
  const currentDetail = demoBundle || inspection.legacyRun ? undefined : detail.data;
  const status = inspectionStatus(currentDetail?.status ?? inspection.status);
  const reportedStatus = currentDetail?.status ?? inspection.reportedStatus;
  const runId = currentDetail?.runId ?? inspection.runId;
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
    enabled: !demoBundle && !!runId && (status === 'done' || !!inspection.legacyRun),
    retry: false,
    staleTime: 60_000,
  });
  const bundle = demoBundle ?? query.data;
  const source = inspection.source ?? bundle?.manifest.source.name;
  const steps = processingSteps(
    demoBundle ? run : (inspection.legacyRun ?? currentDetail ?? { status: inspection.status }),
    bundle?.manifest,
  );
  const demoTitle = demoBundle
    ? t('demo.inspectionTitle')
    : inspection.title === inspection.id && /^\d+$/.test(inspection.id)
      ? `${t('shell.inspection')} ${inspection.id}`
      : inspection.title;
  const errorKey =
    query.error instanceof BundleError ? (`error.${query.error.code}` as Key) : 'error.generic';

  return (
    <article className="qv-page__inner inspection-view">
      <header className="qv-page__head inspection-heading">
        <div>
          <p className="qv-caption">{t('shell.inspection')}</p>
          <h1 className="qv-title">{demoTitle}</h1>
          <p className="qv-caption inspection-meta">
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
            <span className={`qv-status qv-status--muted status-label ${status}`}>
              {status === 'unknown' && reportedStatus ? reportedStatus : t(`status.${status}`)}
            </span>
          </p>
        </div>
        {demoBundle && <span className="qv-caption demo-badge">{t('shell.demo')}</span>}
      </header>
      {demoBundle && <p className="qv-notice demo-notice">{t('demo.notice')}</p>}
      {detail.isError && !demoBundle && !inspection.legacyRun && (
        <div className="qv-notice api-error" role="alert">
          <p>{t('inspection.detailError')}</p>
          <button className="qv-btn qv-btn--secondary" onClick={() => void detail.refetch()}>
            {t('shell.retry')}
          </button>
        </div>
      )}
      <ProcessingSteps steps={steps} uploaded={status === 'uploaded'} />
      <section aria-labelledby="inspection-map-title" className="qv-card inspection-map-section">
        <div className="qv-card__head section-heading">
          <h2 className="qv-heading" id="inspection-map-title">
            {t('inspection.mapAndVideo')}
          </h2>
          <span className="qv-caption">{t('inspection.framesNote')}</span>
        </div>
        {bundle ? (
          <div className="inspection-viewer">
            <ViewerScreen bundle={bundle} embedded />
          </div>
        ) : (!inspection.legacyRun && status !== 'done') || !runId ? (
          <p className="qv-map qv-map__center map-message" role="status">
            {t('processing.mapWaiting')}
          </p>
        ) : query.isError ? (
          <div className="qv-map qv-map__center map-message" role="alert">
            <p>{t(errorKey)}</p>
            <button className="qv-btn qv-btn--secondary" onClick={() => void query.refetch()}>
              {t('shell.retry')}
            </button>
          </div>
        ) : (
          <p className="qv-map qv-map__center map-message" role="status">
            {t('viewer.loading')}
          </p>
        )}
      </section>
    </article>
  );
}
