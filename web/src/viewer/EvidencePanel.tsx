import { useState } from 'react';
import { SessionImage } from '../auth/SessionImage';
import { useI18n, type Key } from '../i18n';
import type { Manifest } from '../lib/bundle';
import { formatArea, type EvidenceFrame, type Finding } from '../lib/findings';
import { containFit, polygonPoints } from '../lib/outline';

function EvidenceImage({
  evidence,
  manifest,
  severity,
}: {
  evidence: EvidenceFrame;
  manifest?: Manifest;
  severity: Finding['severity'];
}) {
  const { t } = useI18n();
  const [natural, setNatural] = useState<{ width: number; height: number }>();
  const frame = manifest?.frames.find((frame) => frame.i === evidence.frame_id);
  const width = natural?.width ?? frame?.camera?.width ?? manifest?.source.width;
  const height = natural?.height ?? frame?.camera?.height ?? manifest?.source.height;
  // SVG and object-fit use the same centred contain transform, including letterboxing.
  const fit = containFit(width ?? 0, height ?? 0, 400, 250);
  return (
    <figure className="qv-thumb evidence-frame" data-testid="evidence-frame">
      <div className="qv-thumb__img evidence-image">
        <SessionImage
          src={evidence.image_url}
          alt={`${t('pointInfo.frame')} ${evidence.frame_id}`}
          className="evidence-photo"
          onLoad={(event) => {
            const { naturalWidth: width, naturalHeight: height } = event.currentTarget;
            if (width > 0 && height > 0) setNatural({ width, height });
          }}
        />
        <svg viewBox="0 0 400 250" aria-hidden="true">
          <polygon
            className={`qv-mask${severity === 'watch' ? ' qv-mask--watch' : ''}`}
            points={polygonPoints(evidence.outline, fit)}
          />
        </svg>
      </div>
      <figcaption className="qv-thumb__cap">
        {t('pointInfo.frame')} {evidence.frame_id}
        {frame?.t != null && `, ${frame.t.toFixed(1)} ${t('unit.s')}`}
      </figcaption>
    </figure>
  );
}

export function EvidencePanel({
  findings,
  selection,
  loading,
  error,
  manifest,
  onClose,
}: {
  findings: readonly Finding[] | undefined;
  selection: string | null;
  loading: boolean;
  error: boolean;
  manifest?: Manifest;
  onClose: () => void;
}) {
  const { t } = useI18n();
  // Keep terminal states terminal even if a caller starts a background request.
  const [settled, setSettled] = useState(!loading);
  if (!settled && (!loading || error || findings !== undefined)) setSettled(true);
  if (selection === null) return null;
  const finding =
    selection === 'first'
      ? findings?.[0]
      : findings?.find((finding) => String(finding.id) === selection);
  const statusKey = finding ? (`evidence.status.${finding.status}` as Key) : null;
  return (
    <aside className="qv-detail evidence-panel" aria-label={t('evidence.title')}>
      <div className="qv-detail__head evidence-head">
        <span className="qv-caption qv-muted">
          {t('evidence.title')}
          {finding ? ` ${finding.id}` : ''}
        </span>
        <button
          type="button"
          className="qv-iconbtn"
          aria-label={t('evidence.close')}
          onClick={onClose}
        >
          <svg width="14" height="14" viewBox="0 0 14 14" stroke="currentColor" aria-hidden="true">
            <path d="m3 3 8 8M11 3l-8 8" />
          </svg>
        </button>
      </div>
      <div className="qv-detail__body evidence-body">
        {error ? (
          <p role="alert">{t('evidence.error')}</p>
        ) : !settled && loading && findings === undefined ? (
          <p role="status">{t('evidence.loading')}</p>
        ) : !finding ? (
          <p role="status">{t('evidence.empty')}</p>
        ) : (
          <>
            <div>
              <span className={`qv-sev ${finding.severity}`}>
                {t(`evidence.severity.${finding.severity}`)}
              </span>
              <h2 className="qv-detail__title">{t(`evidence.class.${finding.class}`)}</h2>
              {finding.comment && <p className="qv-detail__lead">{finding.comment}</p>}
            </div>
            <dl className="qv-facts3 evidence-facts">
              <div>
                <dt>{t('evidence.area')}</dt>
                <dd className="qv-mono evidence-area">{formatArea(finding, t)}</dd>
                {finding.scale !== 'not set' && (
                  <dd className="qv-caption qv-muted">{finding.scale}</dd>
                )}
              </div>
              <div>
                <dt>{t('evidence.status')}</dt>
                <dd>{statusKey && t(statusKey) !== statusKey ? t(statusKey) : finding.status}</dd>
              </div>
            </dl>
            <div className="evidence-frames" aria-label={t('pointInfo.frames')}>
              {finding.evidence.slice(0, 3).map((evidence) => (
                <EvidenceImage
                  key={`${finding.id}:${evidence.frame_id}:${evidence.image_url}`}
                  evidence={evidence}
                  manifest={manifest}
                  severity={finding.severity}
                />
              ))}
            </div>
          </>
        )}
      </div>
    </aside>
  );
}
