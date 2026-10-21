import { useI18n } from '../i18n';
import { SessionImage } from '../auth/SessionImage';
import type { PointInfo } from './pointInfo';

export function PointInfoPanel({
  info,
  onSeek,
  onClose,
}: {
  info: PointInfo;
  onSeek: (timeSec: number, frameId: number) => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const unit = t(info.unit === 'm' ? 'pointInfo.metres' : 'pointInfo.modelUnits');
  return (
    <section aria-label={t('pointInfo.title')} className="point-info-panel">
      <div className="viewer-section-heading">
        <h2 className="viewer-heading">
          {t('pointInfo.title')} <span className="viewer-mono viewer-muted">#{info.index}</span>
        </h2>
        <button
          type="button"
          onClick={onClose}
          aria-label={t('pointInfo.close')}
          title={t('pointInfo.close')}
          className="viewer-tool"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" stroke="currentColor">
            <path d="m3 3 8 8M11 3l-8 8" />
          </svg>
        </button>
      </div>
      <div>
        <p className="point-info-label">
          {t('pointInfo.coordinates')} · {unit}
        </p>
        <dl className="viewer-data viewer-mono">
          {(['x', 'y', 'z'] as const).map((axis) => (
            <div key={axis} className="contents">
              <dt className="viewer-muted">{t(`gizmo.${axis}`)}</dt>
              <dd className="viewer-value">
                {info.position[axis].toFixed(3)} {unit}
              </dd>
            </div>
          ))}
        </dl>
      </div>
      <div className="point-info-row">
        <span>{t('pointInfo.views')}</span>
        <span className="viewer-inline">
          {info.visibilitySource === 'estimated' && (
            <span className="viewer-badge">{t('pointInfo.estimated')}</span>
          )}
          <span className="viewer-mono">{info.views}</span>
        </span>
      </div>
      {info.visibilitySource === 'estimated' && (
        <p className="viewer-caption viewer-muted">{t('pointInfo.estimatedNote')}</p>
      )}
      <div>
        <p className="point-info-label">{t('pointInfo.frames')}</p>
        {info.frames.length === 0 ? (
          <p className="viewer-small viewer-muted">{t('pointInfo.noFrames')}</p>
        ) : (
          <div className="point-info-frames">
            {info.frames.map((frame) => (
              <button
                key={frame.frameId}
                type="button"
                disabled={frame.timeSec === null}
                aria-label={`${t('pointInfo.seekFrame')} ${frame.frameId}`}
                title={
                  frame.timeSec === null ? t('pointInfo.noTimestamp') : t('pointInfo.seekFrame')
                }
                onClick={() => {
                  if (frame.timeSec !== null) onSeek(frame.timeSec, frame.frameId);
                }}
                className="point-info-frame"
              >
                <SessionImage
                  src={frame.thumbnailUrl}
                  alt={`${t('pointInfo.frame')} ${frame.frameId}`}
                  className="point-info-thumbnail"
                />
                <span className="viewer-caption">
                  {frame.timeSec === null
                    ? t('pointInfo.noTimestamp')
                    : `${frame.timeSec.toFixed(3)} ${t('unit.s')}`}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
      <dl className="point-info-measurements">
        {(['height', 'distance', 'confidence'] as const).map((measurement) => {
          const value = info[measurement];
          return (
            <div key={measurement}>
              <dt className="viewer-muted">{t(`pointInfo.${measurement}`)}</dt>
              <dd className="m-0">
                {value.available ? (
                  <span className="viewer-mono">
                    {value.value.toFixed(3)}{' '}
                    {measurement === 'confidence' ? '' : t('pointInfo.metres')}
                  </span>
                ) : (
                  t(
                    measurement === 'confidence'
                      ? 'pointInfo.needsConfidence'
                      : 'pointInfo.needsScale',
                  )
                )}
              </dd>
            </div>
          );
        })}
      </dl>
    </section>
  );
}
