import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import { useI18n, type Lang } from '../i18n';
import { fetchReportPdf, fetchReportSummary, type ReportPdf } from '../lib/report';
import { formatReportDate } from '../lib/reportDate';
import { Wordmark } from '../shell/ShellIdentity';
import './report.css';

export function ReportPage() {
  const { id = '' } = useParams();
  const { t, lang } = useI18n();
  const [chosenLanguage, setChosenLanguage] = useState<Lang | null>(null);
  const pdfLanguage = chosenLanguage ?? lang;
  const summary = useQuery({
    queryKey: ['report-summary', id],
    queryFn: ({ signal }) => fetchReportSummary(id, signal),
    retry: false,
  });
  const inspectionId = summary.data?.inspectionId;
  const pdf = useQuery({
    queryKey: ['report-pdf', inspectionId, pdfLanguage],
    queryFn: ({ signal }) => fetchReportPdf(inspectionId!, pdfLanguage, signal),
    enabled: inspectionId !== undefined,
    retry: false,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnMount: false,
    refetchInterval: (query) => (query.state.data?.state === 'generating' ? 2000 : false),
  });
  const state: ReportPdf = summary.isError
    ? { state: 'failed', message: summary.error.message }
    : pdf.isError
      ? { state: 'failed', message: '' }
      : (pdf.data ?? { state: 'generating' });
  const data = summary.data;

  return (
    <div className="qv-screen report-screen">
      <header className="qv-topbar report-topbar">
        <Link className="qv-btn qv-btn--quiet" to="/">
          {t('shell.emptyTitle')}
        </Link>
        <span className="qv-topbar__title">{t('report.title')}</span>
      </header>
      <main className="report-main" id="report-main">
        <article
          className="qv-doc report-document"
          data-theme="light"
          aria-label={t('report.summary')}
        >
          <div className="qv-doc__band">
            <Wordmark />
          </div>
          <h1 className="qv-doc__title">{t('report.title')}</h1>
          <section
            className="report-state"
            aria-label={t('report.pdf')}
            aria-busy={state.state === 'generating'}
          >
            <div className="report-controls">
              <span className="qv-caption">{t('report.language')}</span>
              <div className="qv-segment" role="group" aria-label={t('report.language')}>
                {(['ru', 'kk', 'en'] as const).map((locale) => (
                  <button
                    type="button"
                    key={locale}
                    aria-pressed={locale === pdfLanguage}
                    onClick={() => setChosenLanguage(locale)}
                  >
                    {t(`report.language.${locale}`)}
                  </button>
                ))}
              </div>
            </div>
            {state.state === 'generating' && <p role="status">{t('report.generating')}</p>}
            {state.state === 'ready' && <p role="status">{t('report.ready')}</p>}
            {state.state === 'failed' && (
              <div role="alert">
                <h2>{t('report.failed')}</h2>
                <p>{state.message || t('report.failureFallback')}</p>
              </div>
            )}
          </section>
          {data && (
            <>
              <p className="qv-doc__subtitle">{data.object ?? t('report.objectUnavailable')}</p>
              <table className="qv-facts">
                <tbody>
                  <tr>
                    <td>{t('report.object')}</td>
                    <td>{data.object ?? t('report.objectUnavailable')}</td>
                  </tr>
                  <tr>
                    <td>{t('report.date')}</td>
                    <td>
                      <time dateTime={data.date}>
                        {formatReportDate(new Date(data.date), lang, t)}
                      </time>
                    </td>
                  </tr>
                  <tr>
                    <td>{t('report.scale')}</td>
                    <td>
                      {data.findings.length ? (
                        <ul className="report-scales">
                          {[...new Set(data.findings.map((finding) => finding.scale))].map(
                            (scale) => (
                              <li key={scale}>
                                {scale === 'not set' ? t('report.scaleNotSet') : scale}
                              </li>
                            ),
                          )}
                        </ul>
                      ) : (
                        t('report.scaleNotSet')
                      )}
                    </td>
                  </tr>
                </tbody>
              </table>
              <h2>{t('report.summary')}</h2>
              {(['critical', 'watch'] as const).map((severity) => (
                <section key={severity} aria-label={t(`evidence.severity.${severity}`)}>
                  <h3>{t(`evidence.severity.${severity}`)}</h3>
                  {data.findings.some((finding) => finding.severity === severity) ? (
                    <table className="data report-findings">
                      <thead>
                        <tr>
                          <th scope="col">{t('evidence.title')}</th>
                          <th scope="col">{t('evidence.status')}</th>
                          <th scope="col">{t('report.decidedBy')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.findings
                          .filter((finding) => finding.severity === severity)
                          .map((finding) => (
                            <tr key={finding.id}>
                              <td>
                                <span className={`sev ${severity}`}>
                                  {t(`evidence.class.${finding.class}`)}
                                </span>
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
              ))}
              <h2>{t('report.coverage')}</h2>
              <p>{data.coverage ?? t('report.coverageNotComputed')}</p>
            </>
          )}
        </article>
      </main>
    </div>
  );
}
