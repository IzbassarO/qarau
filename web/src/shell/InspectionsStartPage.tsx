import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router';
import { useI18n } from '../i18n';
import { inspectionsFixture } from '../fixtures/inspections';
import { ApiUnavailableError, rerunInspectionStep } from '../lib/api';
import { getInspection, useInspections, type InspectionSummary } from '../lib/inspections';
import { InspectionsList } from './InspectionsList';

export function InspectionsStartPage({
  demo,
  onNewInspection,
}: {
  demo: boolean;
  onNewInspection: () => void;
}) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const client = useQueryClient();
  const query = useInspections({ enabled: !demo });
  const [actionError, setActionError] = useState(false);
  const action = useMutation({
    mutationFn: async ({
      inspection,
      retry,
    }: {
      inspection: InspectionSummary;
      retry: boolean;
    }) => {
      setActionError(false);
      if (!retry && inspection.runId) return inspection.runId;
      const detail = await getInspection(inspection.id);
      const runId = detail.runId ?? detail.run_id;
      if (!runId) throw new Error('Missing inspection run');
      if (!retry) return runId;
      const failedStep = detail.steps?.find((step) => step.status === 'failed');
      if (!failedStep?.name) throw new Error('Missing failed inspection step');
      await rerunInspectionStep(inspection.id, runId, failedStep.name);
    },
    onSuccess: async (runId) => {
      if (runId) navigate(`/viewer/${encodeURIComponent(runId)}`);
      else {
        await Promise.all([
          client.invalidateQueries({ queryKey: ['inspections'] }),
          client.invalidateQueries({ queryKey: ['inspection'] }),
        ]);
      }
    },
    onError: () => setActionError(true),
  });

  return (
    <section className="qv-page__inner inspections-start" aria-labelledby="inspections-title">
      <header className="qv-page__head inspections-heading">
        <div>
          <h1 className="qv-title" id="inspections-title">
            {t('list.title')}
          </h1>
          <p className="qv-caption">{t('shell.emptyDescription')}</p>
        </div>
        <button className="qv-btn" onClick={onNewInspection}>
          {t('list.newInspection')}
        </button>
      </header>
      {!demo && actionError && (
        <div className="qv-notice" role="alert">
          {t('list.actionError')}
        </div>
      )}
      {!demo && query.isError ? (
        <div
          className="qv-notice api-error"
          role={query.error instanceof ApiUnavailableError ? 'status' : 'alert'}
        >
          <div>
            <strong className="qv-strong">{t('shell.connectionError')}</strong>
            <p className="qv-caption">
              {t(query.error.message === '401' ? 'shell.authError' : 'shell.connectionHint')}
            </p>
          </div>
          <button
            className="qv-btn qv-btn--secondary"
            disabled={query.isFetching}
            onClick={() => void client.invalidateQueries({ queryKey: ['inspections'] })}
          >
            {t('shell.retry')}
          </button>
        </div>
      ) : !demo && query.isPending ? (
        <p className="qv-caption" role="status">
          {t('shell.loading')}
        </p>
      ) : (
        <div aria-busy={action.isPending}>
          <InspectionsList
            inspections={demo ? inspectionsFixture : (query.data ?? [])}
            onOpen={(inspection) => {
              if (!demo && !action.isPending) action.mutate({ inspection, retry: false });
            }}
            onProgress={(inspection) => {
              if (!demo) navigate(`/inspections/${encodeURIComponent(inspection.id)}`);
            }}
            onRetry={(inspection) => {
              if (!demo && !action.isPending) action.mutate({ inspection, retry: true });
            }}
          />
        </div>
      )}
    </section>
  );
}
