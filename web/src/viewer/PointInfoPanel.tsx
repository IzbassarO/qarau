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
    <section
      aria-label={t('pointInfo.title')}
      className="flex flex-col gap-3 rounded-xl border border-line-strong bg-raised/40 p-3"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="m-0 text-sm font-semibold">
          {t('pointInfo.title')} <span className="font-mono text-ink-2">#{info.index}</span>
        </h2>
        <button
          type="button"
          onClick={onClose}
          aria-label={t('pointInfo.close')}
          title={t('pointInfo.close')}
          className="flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-md text-ink-2 hover:bg-line"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" stroke="currentColor">
            <path d="m3 3 8 8M11 3l-8 8" />
          </svg>
        </button>
      </div>
      <div>
        <p className="m-0 mb-1 text-xs text-ink-2">
          {t('pointInfo.coordinates')} · {unit}
        </p>
        <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-[13px]">
          {(['x', 'y', 'z'] as const).map((axis) => (
            <div key={axis} className="contents">
              <dt className="text-ink-2">{t(`gizmo.${axis}`)}</dt>
              <dd className="m-0 text-right">
                {info.position[axis].toFixed(3)} {unit}
              </dd>
            </div>
          ))}
        </dl>
      </div>
      <div className="flex items-center justify-between gap-2 text-[13px]">
        <span>{t('pointInfo.views')}</span>
        <span className="flex items-center gap-2">
          {info.visibilitySource === 'estimated' && (
            <span className="rounded border border-line-strong px-1.5 py-0.5 text-xs text-ink-2">
              {t('pointInfo.estimated')}
            </span>
          )}
          <span className="font-mono">{info.views}</span>
        </span>
      </div>
      {info.visibilitySource === 'estimated' && (
        <p className="m-0 text-xs leading-5 text-ink-2">{t('pointInfo.estimatedNote')}</p>
      )}
      <div>
        <p className="m-0 mb-2 text-xs text-ink-2">{t('pointInfo.frames')}</p>
        {info.frames.length === 0 ? (
          <p className="m-0 text-[13px] text-ink-2">{t('pointInfo.noFrames')}</p>
        ) : (
          <div className="flex gap-2 overflow-x-auto pb-1">
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
                className="flex w-28 shrink-0 cursor-pointer flex-col gap-1 overflow-hidden rounded-md border border-line-strong bg-bg p-1 text-ink hover:border-ink-2 disabled:cursor-default"
              >
                <SessionImage
                  src={frame.thumbnailUrl}
                  alt={`${t('pointInfo.frame')} ${frame.frameId}`}
                  className="aspect-video w-full rounded-sm object-contain"
                />
                <span className="text-xs">
                  {frame.timeSec === null
                    ? t('pointInfo.noTimestamp')
                    : `${frame.timeSec.toFixed(3)} ${t('unit.s')}`}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
      <dl className="m-0 flex flex-col gap-2 border-t border-line pt-3 text-xs leading-5">
        {(['height', 'distance', 'confidence'] as const).map((measurement) => {
          const value = info[measurement];
          return (
            <div key={measurement}>
              <dt className="text-ink-2">{t(`pointInfo.${measurement}`)}</dt>
              <dd className="m-0">
                {value.available ? (
                  <span className="font-mono">
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
