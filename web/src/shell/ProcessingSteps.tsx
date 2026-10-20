import { useEffect, useState } from 'react';
import { useI18n } from '../i18n';
import {
  errorMessage,
  skipBlockedSteps,
  stageLabel,
  stepDuration,
  type ProcessingStep,
} from '../lib/processing';

function StateIcon({ status }: { status: ProcessingStep['status'] }) {
  return (
    <span className={`step-icon ${status}`} aria-hidden="true">
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
        className={status === 'running' ? 'motion-safe:animate-spin' : undefined}
      >
        {status === 'running' ? <path d="M12 3a9 9 0 1 1-9 9" /> : <circle cx="12" cy="12" r="9" />}
        {status === 'done' && <path d="m7.5 12 3 3 6-6" />}
        {status === 'failed' && <path d="m9 9 6 6m0-6-6 6" />}
        {status === 'skipped' && <path d="M8 12h8" />}
        {status === 'unknown' && <path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-1 .4-1 1-1 1.7m0 3h.01" />}
      </svg>
    </span>
  );
}

export function ProcessingSteps({ steps }: { steps: readonly ProcessingStep[] }) {
  const { t, lang } = useI18n();
  const [now, setNow] = useState(Date.now);
  const running = steps.some((step) => step.status === 'running');
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  return (
    <section className="processing-section" aria-labelledby="processing-title">
      <div className="section-heading">
        <h2 id="processing-title">{t('inspection.processing')}</h2>
        <span className="muted">{t('inspection.processingNote')}</span>
      </div>
      {steps.length === 0 ? (
        <p className="muted">{t('inspection.noStages')}</p>
      ) : (
        <ol className="processing-steps" aria-label={t('inspection.processing')} aria-live="polite">
          {skipBlockedSteps(steps).map((step, index) => {
            const error = errorMessage(step.errorCode);
            const label = stageLabel(step.id);
            const duration = stepDuration(step, now);
            return (
              <li
                key={`${step.id}-${index}`}
                className={`processing-step ${step.status}`}
                data-state={step.status}
              >
                <div className="step-row">
                  <StateIcon status={step.status} />
                  <span className="step-name">
                    {t(label)}
                    {label === 'processing.unknownStage' && step.id && (
                      <span className="step-raw">{step.id}</span>
                    )}
                  </span>
                  <span className={`step-state ${step.status}`}>
                    {t(`processing.status.${step.status}`)}
                  </span>
                  <span className="step-time" title={t('processing.duration')}>
                    {duration === undefined ? (
                      t('processing.durationUnavailable')
                    ) : (
                      <time dateTime={`PT${duration}S`}>
                        {new Intl.NumberFormat(lang, { maximumFractionDigits: 1 }).format(duration)}{' '}
                        {t('unit.s')}
                      </time>
                    )}
                  </span>
                </div>
                {step.status === 'unknown' && (
                  <p className="step-unknown">
                    {t('processing.unknownState')}
                    {step.reportedStatus && <span className="step-raw">{step.reportedStatus}</span>}
                  </p>
                )}
                {step.status === 'failed' && (
                  <div className="step-error">
                    <h3>{t(error.title)}</h3>
                    <dl>
                      <div>
                        <dt>{t('processing.whatWentWrong')}</dt>
                        <dd>{t(error.whatWentWrong)}</dd>
                      </div>
                      <div>
                        <dt>{t('processing.whatToDo')}</dt>
                        <dd>{t(error.whatToDo)}</dd>
                      </div>
                    </dl>
                  </div>
                )}
                {(step.detail || step.errorCode) &&
                  (step.status === 'failed' || step.status === 'unknown') && (
                    <details className="step-detail">
                      <summary>{t('processing.details')}</summary>
                      {step.errorCode && (
                        <p>
                          {t('processing.errorCode')} <code>{step.errorCode}</code>
                        </p>
                      )}
                      {step.detail && <p className="step-raw-detail">{step.detail}</p>}
                    </details>
                  )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
