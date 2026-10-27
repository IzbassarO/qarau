import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useI18n, type Key } from '../i18n';
import type { FrameInfo } from '../lib/bundle';
import { findingOptions, formatArea, type Finding } from '../lib/findings';
import { FrameWithOutline } from './FrameWithOutline';
import './evidence.css';

const statusKeys: Record<string, Key> = {
  proposed: 'evidence.status.proposed',
  confirmed: 'evidence.status.confirmed',
  rejected: 'evidence.status.rejected',
  low_evidence: 'findings.lowEvidence',
};

export function EvidencePanel({
  findingId,
  frames,
  onClose,
  listState,
  onRetry,
}: {
  findingId: number | null;
  frames: readonly FrameInfo[];
  onClose: () => void;
  listState?: 'loading' | 'error';
  onRetry?: () => void;
}) {
  const { t } = useI18n();
  const query = useQuery({
    ...findingOptions(findingId ?? 0),
    enabled: findingId !== null && !listState,
  });
  const finding = query.data;
  return (
    <aside className="qv-detail evidence-panel" aria-label={t('evidence.title')}>
      <header className="qv-detail__head evidence-header">
        <span className="qv-caption">{t('evidence.title')}</span>
        <button
          type="button"
          className="qv-iconbtn"
          aria-label={t('evidence.close')}
          onClick={onClose}
        >
          ×
        </button>
      </header>
      <div className="qv-detail__body">
        {listState === 'loading' ? (
          <p role="status">{t('evidence.loading')}</p>
        ) : listState === 'error' ? (
          <div role="alert">
            <p>{t('evidence.error')}</p>
            <button type="button" className="qv-btn qv-btn--secondary" onClick={onRetry}>
              {t('shell.retry')}
            </button>
          </div>
        ) : findingId === null ? (
          <p role="status">{t('evidence.noFindings')}</p>
        ) : query.isPending ? (
          <p role="status">{t('evidence.loading')}</p>
        ) : query.isError ? (
          <div role="alert" className="evidence-message">
            <p>{t('evidence.error')}</p>
            <button
              type="button"
              className="qv-btn qv-btn--secondary"
              onClick={() => void query.refetch()}
            >
              {t('shell.retry')}
            </button>
          </div>
        ) : finding ? (
          <EvidenceContent key={finding.id} finding={finding} frames={frames} />
        ) : null}
      </div>
    </aside>
  );
}

function EvidenceContent({ finding, frames }: { finding: Finding; frames: readonly FrameInfo[] }) {
  const { t } = useI18n();
  const [active, setActive] = useState(0);
  const activeIndex = Math.min(active, finding.evidence.length - 1);
  const selected = finding.evidence[activeIndex];
  const label = (frameId: number) => `${t('evidence.frame')} ${frameId}`;
  const renderFrame = (index: number) => {
    const evidence = finding.evidence[index];
    return (
      <FrameWithOutline
        key={evidence.frame_id}
        evidence={evidence}
        severity={finding.severity}
        dimensions={frames.find((frame) => frame.i === evidence.frame_id)?.camera}
        label={label(evidence.frame_id)}
      />
    );
  };
  return (
    <>
      <section>
        <span className={`qv-sev ${finding.severity}`}>
          {t(`evidence.severity.${finding.severity}`)}
        </span>
        <h2 className="qv-detail__title">{t(`evidence.class.${finding.class}`)}</h2>
        {finding.comment && <p className="qv-detail__lead">{finding.comment}</p>}
      </section>
      <div className="qv-facts3 evidence-facts">
        <div>
          <span>{t('evidence.area')}</span>
          <b className="qv-mono">{formatArea(finding, t)}</b>
        </div>
        <div>
          <span>{t('evidence.status')}</span>
          <b className={`evidence-status ${finding.severity}`}>
            {statusKeys[finding.status] ? t(statusKeys[finding.status]) : finding.status}
          </b>
        </div>
      </div>
      <p className="qv-caption evidence-scale">
        {t('evidence.scale')}:{' '}
        {finding.scale === 'not set' ? t('evidence.scaleNotSet') : finding.scale}
      </p>
      {selected ? (
        <section aria-label={t('evidence.frames')} className="evidence-media">
          <figure className="qv-thumb qv-thumb--lg evidence-main-frame">
            {renderFrame(activeIndex)}
            <figcaption className="qv-thumb__cap">{label(selected.frame_id)}</figcaption>
          </figure>
          <div className="qv-thumbs evidence-thumbnails">
            {finding.evidence.map((evidence, index) => (
              <button
                type="button"
                key={evidence.frame_id}
                className={`qv-thumb${activeIndex === index ? ' qv-thumb--selected' : ''}`}
                aria-label={label(evidence.frame_id)}
                aria-pressed={activeIndex === index}
                onClick={() => setActive(index)}
              >
                {renderFrame(index)}
                <span className="qv-thumb__cap">{label(evidence.frame_id)}</span>
              </button>
            ))}
          </div>
        </section>
      ) : (
        <p role="status">{t('evidence.empty')}</p>
      )}
    </>
  );
}
