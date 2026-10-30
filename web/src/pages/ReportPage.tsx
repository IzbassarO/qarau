import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, useParams } from 'react-router';
import { useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { useI18n, type Lang } from '../i18n';
import {
  fetchReportPdf,
  fetchReportSummary,
  type ReportPdf,
  type ReportSummary,
} from '../lib/report';
import { loadPdf, type PdfPreview } from '../lib/pdfPreview';
import { formatReportDate } from '../lib/reportDate';
import { Wordmark } from '../shell/ShellIdentity';
import './report.css';

export function ReportPage() {
  const { id = '' } = useParams();
  const { lang } = useI18n();
  const queryClient = useQueryClient();
  const [chosenLanguage, setChosenLanguage] = useState<Lang | null>(null);
  const summary = useQuery({
    queryKey: ['report-summary', id],
    queryFn: ({ signal }) => fetchReportSummary(id, signal),
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const pdfLanguage = chosenLanguage ?? lang;
  // Keep the request observer mounted when a language change replaces the preview.
  // Otherwise StrictMode's effect replay aborts and restarts the same PDF fetch.
  const requestKey = `${id}:${pdfLanguage}`;
  const [timedOutRequest, setTimedOutRequest] = useState<string | null>(null);
  const timedOut = timedOutRequest === requestKey;
  const onTimeout = useCallback(() => {
    setTimedOutRequest(requestKey);
    // Disabling a query stops polling but does not abort an existing fetch.
    void queryClient.cancelQueries({ queryKey: ['report-summary', id], exact: true });
    const data = queryClient.getQueryData<ReportSummary>(['report-summary', id]);
    if (data)
      void queryClient.cancelQueries({
        queryKey: ['report-pdf', data.inspectionId, pdfLanguage],
        exact: true,
      });
  }, [id, pdfLanguage, queryClient, requestKey]);
  const onRetry = useCallback(() => setTimedOutRequest(null), []);
  const inspectionId = summary.data?.inspectionId;
  const pdf = useQuery({
    queryKey: ['report-pdf', inspectionId, pdfLanguage],
    queryFn: ({ signal }) => fetchReportPdf(inspectionId!, pdfLanguage, signal),
    enabled: inspectionId !== undefined && !timedOut,
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: false,
    refetchInterval: (query) =>
      !timedOut && query.state.data?.state === 'generating' ? 2000 : false,
  });

  return (
    <ReportContent
      key={requestKey}
      id={id}
      pdfLanguage={pdfLanguage}
      summary={summary}
      pdf={pdf}
      timedOut={timedOut}
      onTimeout={onTimeout}
      onRetry={onRetry}
      onLanguage={setChosenLanguage}
    />
  );
}

function ReportContent({
  id,
  pdfLanguage,
  summary,
  pdf,
  timedOut,
  onTimeout,
  onRetry,
  onLanguage,
}: {
  id: string;
  pdfLanguage: Lang;
  summary: UseQueryResult<ReportSummary, Error>;
  pdf: UseQueryResult<ReportPdf, Error>;
  timedOut: boolean;
  onTimeout: () => void;
  onRetry: () => void;
  onLanguage: (lang: Lang) => void;
}) {
  const { t } = useI18n();
  const [attempt, setAttempt] = useState(0);
  const state: ReportPdf = summary.isError
    ? { state: 'failed', message: summary.error.message }
    : pdf.isError
      ? { state: 'failed', message: pdf.error.message }
      : (pdf.data ?? { state: 'generating' });
  const data = summary.data;
  const blob = state.state === 'ready' ? state.blob : null;
  const [pdfUrl, setPdfUrl] = useState<{ blob: Blob; url: string } | null>(null);
  useEffect(() => {
    if (!blob) {
      setPdfUrl(null);
      return;
    }
    const url = URL.createObjectURL(blob);
    setPdfUrl({ blob, url });
    return () => URL.revokeObjectURL(url);
  }, [blob, id, pdfLanguage]);
  const readyUrl = pdfUrl?.blob === blob ? pdfUrl?.url : undefined;

  const canvas = useRef<HTMLCanvasElement>(null);
  const [preview, setPreview] = useState<{ blob: Blob; pages?: number; failed?: boolean } | null>(
    null,
  );
  const painted = preview?.blob === blob && preview?.pages !== undefined;
  const previewFailed = preview?.blob === blob && preview?.failed;
  const failed = timedOut || state.state === 'failed' || previewFailed;
  const busy =
    !failed &&
    (summary.isFetching || pdf.isFetching || state.state === 'generating' || !painted || !readyUrl);
  const deadline = useRef<number | null>(null);
  useEffect(() => {
    if (!busy) return;
    // Include summary fetch, generation polling, worker startup and painting in one deadline.
    const now = Date.now();
    deadline.current ??= now + 28_000;
    const timeout = window.setTimeout(onTimeout, Math.max(0, deadline.current - now));
    return () => window.clearTimeout(timeout);
  }, [busy, attempt, onTimeout]);
  useEffect(() => {
    if (!blob || timedOut) return;
    let active = true;
    const controller = new AbortController();
    let document: PdfPreview | undefined;
    async function paint() {
      try {
        const bytes = await blob!.arrayBuffer();
        if (!active) return;
        document = await loadPdf(bytes, controller.signal);
        if (!active) return;
        if (!canvas.current) return;
        await document.renderFirstPage(canvas.current);
        const pages = document.numPages;
        if (active) setPreview({ blob: blob!, pages });
        await document.destroy();
      } catch {
        await document?.destroy();
        if (active) setPreview({ blob: blob!, failed: true });
      }
    }
    void paint();
    return () => {
      active = false;
      controller.abort();
      void document?.destroy();
    };
  }, [blob, attempt, timedOut]);

  function retry() {
    deadline.current = null;
    onRetry();
    setPreview(null);
    setAttempt((value) => value + 1);
    if (summary.isError || !summary.data) void summary.refetch();
    else if (state.state !== 'ready') void pdf.refetch();
  }

  return (
    <div className="qv qv-screen report-screen">
      <header className="qv-topbar report-topbar">
        <Link className="qv-btn qv-btn--quiet" to="/">
          {t('shell.emptyTitle')}
        </Link>
        <span className="qv-topbar__title">{t('report.title')}</span>
        <div className="qv-segment report-controls" role="group" aria-label={t('report.language')}>
          {(['ru', 'kk', 'en'] as const).map((locale) => (
            <button
              type="button"
              key={locale}
              aria-pressed={locale === pdfLanguage}
              onClick={() => onLanguage(locale)}
            >
              {t(`report.language.${locale}`)}
            </button>
          ))}
        </div>
        <div className="report-download">
          {readyUrl ? (
            <a className="qv-btn" href={readyUrl} download={`report-${id}-${pdfLanguage}.pdf`}>
              {t('report.download')}
            </a>
          ) : (
            <button type="button" className="qv-btn" disabled>
              {t('report.download')}
            </button>
          )}
          {state.state === 'ready' && (
            <span className="qv-caption qv-muted report-hash">
              <code className="qv-mono" title={state.hash}>
                {state.hash.slice(0, 12)}…{state.hash.slice(-8)}
              </code>
            </span>
          )}
        </div>
      </header>
      <main className="report-main" id="report-main">
        <section className="report-state" aria-label={t('report.pdf')} aria-busy={busy}>
          {busy && <p role="status">{t('report.generating')}</p>}
          {!busy && !failed && <p role="status">{t('report.ready')}</p>}
          {failed && (
            <div role="alert">
              <h2>{t(previewFailed ? 'report.previewFailureTitle' : 'report.failed')}</h2>
              <p>
                {timedOut
                  ? t('report.timeout')
                  : previewFailed
                    ? t('report.previewFailure')
                    : state.state === 'failed'
                      ? state.message || t('report.failureFallback')
                      : ''}
              </p>
              <button type="button" className="qv-btn qv-btn--secondary" onClick={retry}>
                {t('report.retry')}
              </button>
            </div>
          )}
          {blob && (
            <div className="report-preview-box" hidden={Boolean(failed)}>
              <canvas
                ref={canvas}
                className="report-preview"
                role="img"
                aria-label={t('report.pdf')}
              />
              {painted && (
                <p className="qv-caption report-page-count">
                  {t('report.pageCount').replace('{count}', String(preview.pages))}
                </p>
              )}
            </div>
          )}
        </section>
        <article className="qv-doc report-document" aria-label={t('report.summary')}>
          <div className="qv-doc__band">
            <Wordmark />
          </div>
          <h1 className="qv-doc__title">{t('report.title')}</h1>
          {data && <Summary data={data} />}
        </article>
      </main>
    </div>
  );
}

function Summary({ data }: { data: ReportSummary }) {
  const { t, lang } = useI18n();
  return (
    <>
      <p className="qv-doc__subtitle">{data.object ?? t('report.objectUnavailable')}</p>
      <table className="qv-facts">
        <tbody>
          <Fact label={t('report.object')}>{data.object ?? t('report.objectUnavailable')}</Fact>
          <Fact label={t('report.date')}>
            <time dateTime={data.date}>{formatReportDate(new Date(data.date), lang, t)}</time>
          </Fact>
          <Fact label={t('report.scale')}>
            {data.findings.length ? (
              <ul className="report-scales">
                {[...new Set(data.findings.map((finding) => finding.scale))].map((scale) => (
                  <li key={scale}>{scale === 'not set' ? t('report.scaleNotSet') : scale}</li>
                ))}
              </ul>
            ) : (
              t('report.scaleNotSet')
            )}
          </Fact>
        </tbody>
      </table>
      <h2>{t('report.summary')}</h2>
      {(['critical', 'watch'] as const).map((severity) => (
        <FindingGroup
          key={severity}
          severity={severity}
          findings={data.findings.filter((finding) => finding.severity === severity)}
        />
      ))}
      <h2>{t('report.coverage')}</h2>
      {typeof data.coverage === 'string' ? (
        <p>{data.coverage}</p>
      ) : data.coverage?.computed ? (
        <>
          <p>
            {t('report.coverageTotals')
              .replace('{inspected}', String(data.coverage.totals.inspected_ok))
              .replace('{finding}', String(data.coverage.totals.finding))
              .replace('{unseen}', String(data.coverage.totals.not_seen))}
          </p>
          {data.coverage.regions.some((region) => region.state === 'not_seen') && (
            <p>
              {t('report.notSeenRegions')}{' '}
              {data.coverage.regions
                .filter((region) => region.state === 'not_seen')
                .map((region) => region.id)
                .join(', ')}
            </p>
          )}
        </>
      ) : (
        <p>{t('report.coverageNotComputed')}</p>
      )}
    </>
  );
}

function FindingGroup({
  severity,
  findings,
}: {
  severity: 'critical' | 'watch';
  findings: ReportSummary['findings'];
}) {
  const { t } = useI18n();
  return (
    <section aria-label={t(`evidence.severity.${severity}`)}>
      <h3>{t(`evidence.severity.${severity}`)}</h3>
      <p className="qv-caption">
        {t('report.findingCount').replace('{count}', String(findings.length))}
      </p>
      {findings.length > 0 ? (
        <table className="data report-findings">
          <thead>
            <tr>
              <th scope="col">{t('evidence.title')}</th>
              <th scope="col">{t('evidence.status')}</th>
              <th scope="col">{t('report.decidedBy')}</th>
            </tr>
          </thead>
          <tbody>
            {findings.map((finding) => (
              <tr key={finding.id}>
                <td>
                  <span className={`sev ${severity}`}>{t(`evidence.class.${finding.class}`)}</span>
                  <p>{finding.comment}</p>
                </td>
                <td>
                  {finding.status === 'proposed' ||
                  finding.status === 'confirmed' ||
                  finding.status === 'rejected' ||
                  finding.status === 'low_evidence'
                    ? t(`evidence.status.${finding.status}`)
                    : finding.status}
                </td>
                <td>
                  {finding.decisions.length === 0 ? (
                    t('report.noDecision')
                  ) : (
                    <ol className="report-decisions">
                      {finding.decisions.map((decision) => (
                        <li key={decision.id}>
                          {decision.user}, {t(`report.verdict.${decision.verdict}`)}
                        </li>
                      ))}
                    </ol>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p>{t('evidence.empty')}</p>
      )}
    </section>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <tr>
      <td>{label}</td>
      <td>{children}</td>
    </tr>
  );
}
