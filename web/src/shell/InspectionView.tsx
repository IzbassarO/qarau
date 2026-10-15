import { useQuery } from '@tanstack/react-query';
import { useI18n, type Key } from '../i18n';
import { BundleError, loadBundle, loadManifest, type Bundle } from '../lib/bundle';
import { processingSteps, type InspectionSummary, type RunSummary } from '../lib/inspections';
import { ViewerScreen } from '../viewer/ViewerScreen';

const stageKeys: Record<string, Key> = {
  ingest: 'stage.ingest',
  frames: 'stage.frames',
  reconstruction: 'stage.reconstruction',
  segmentation: 'stage.segmentation',
  projection: 'stage.projection',
  measurement: 'stage.measurement',
  registration: 'stage.registration',
  change_detection: 'stage.changeDetection',
  coverage: 'stage.coverage',
  risk_ranking: 'stage.riskRanking',
  report: 'stage.report',
  export: 'stage.export',
};

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
      return loadBundle(inspection.id, manifest, () => {});
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
            <span className={`status-label ${inspection.status}`}>
              <span className={`status-dot ${inspection.status}`} aria-hidden="true" />
              {t(`status.${inspection.status}`)}
            </span>
          </p>
        </div>
        {demoBundle && <span className="demo-badge">{t('shell.demo')}</span>}
      </header>
      {demoBundle && <p className="demo-notice">{t('demo.notice')}</p>}
      <section aria-labelledby="inspection-map-title" className="inspection-map-section">
        <div className="section-heading">
          <h2 id="inspection-map-title">{t('inspection.mapAndVideo')}</h2>
          <span className="muted">{t('inspection.framesNote')}</span>
        </div>
        {bundle ? (
          <div className="inspection-viewer">
            <ViewerScreen bundle={bundle} embedded />
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
      <section className="processing-section" aria-labelledby="processing-title">
        <div className="section-heading">
          <h2 id="processing-title">{t('inspection.processing')}</h2>
          <span className="muted">{t('inspection.processingNote')}</span>
        </div>
        {steps.length === 0 ? (
          <p className="muted">{t('inspection.noStages')}</p>
        ) : (
          <ol className="processing-steps">
            {steps.map((step) => (
              <li key={step.name}>
                <span className={`status-dot ${step.status}`} aria-hidden="true" />
                <span className="step-name">
                  {stageKeys[step.name] ? t(stageKeys[step.name]) : step.name}
                </span>
                <span className={`status-label ${step.status}`}>{t(`status.${step.status}`)}</span>
                {Number.isFinite(step.seconds) && (
                  <span className="step-time">
                    {new Intl.NumberFormat(lang, { maximumFractionDigits: 1 }).format(step.seconds)}{' '}
                    {t('unit.s')}
                  </span>
                )}
              </li>
            ))}
          </ol>
        )}
      </section>
    </article>
  );
}
