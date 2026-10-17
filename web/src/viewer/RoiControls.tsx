import { useI18n } from '../i18n';
import { Toggle } from './ui';
import type { useRoi } from './useRoi';

export function RoiControls({
  roi,
  runId,
  onDraw,
  onCancel,
}: {
  roi: ReturnType<typeof useRoi>;
  runId: string;
  onDraw: () => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const button =
    'cursor-pointer rounded-lg border border-line-strong px-3 py-2 text-xs hover:bg-raised disabled:cursor-not-allowed disabled:opacity-45';
  return (
    <section
      aria-label={t('roi.title')}
      className={`absolute left-3 top-3 z-20 w-72 max-w-[calc(100%-1.5rem)] rounded-xl border border-line-strong bg-surface/95 p-3 shadow-lg ${roi.drawing ? 'pointer-events-none' : ''}`}
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="m-0 text-sm font-medium">{t('roi.title')}</h2>
        <button
          className={`${button} pointer-events-auto`}
          onClick={() => {
            onCancel();
            roi.setOpen(false);
          }}
        >
          {t('roi.close')}
        </button>
      </div>
      <div className="flex flex-wrap gap-2">
        {roi.drawing ? (
          <button className={`${button} pointer-events-auto`} onClick={onCancel}>
            {t('roi.cancel')}
          </button>
        ) : (
          <button className={button} disabled={roi.busy} onClick={onDraw}>
            {t('roi.draw')}
          </button>
        )}
        <button className={button} disabled={!roi.canSave} onClick={() => void roi.persist()}>
          {t('roi.save')}
        </button>
        <button
          className={button}
          disabled={!roi.box || roi.busy || roi.drawing}
          onClick={() => void roi.clear()}
        >
          {t('roi.clear')}
        </button>
      </div>
      <div className="mt-2">
        <Toggle on={roi.hidden} onChange={roi.setHidden} disabled={!roi.box}>
          {t('roi.hideOutside')}
        </Toggle>
      </div>
      <p className="mb-0 mt-2 text-xs leading-5 text-ink-2" role="status">
        {t(
          roi.busy
            ? 'roi.working'
            : roi.drawing
              ? 'roi.drawHint'
              : roi.dirty
                ? 'roi.unsaved'
                : roi.saved
                  ? 'roi.saved'
                  : 'roi.hint',
        )}
      </p>
      {roi.demo && <p className="mb-0 mt-2 text-xs text-ink-2">{t('roi.demo')}</p>}
      {roi.saved && roi.saved.source_run !== runId && (
        <p role="note" className="mb-0 mt-2 text-xs leading-5 text-ink-2">
          {t('roi.approximate')} <span className="font-mono">{roi.saved.source_run}</span>
        </p>
      )}
      {roi.error && (
        <div className="mt-2">
          <p role="alert" className="my-2 text-xs leading-5 text-watch">
            {t(roi.error)}
          </p>
          {roi.error === 'roi.loadError' && (
            <button className={button} disabled={roi.busy} onClick={roi.retry}>
              {t('roi.retry')}
            </button>
          )}
        </div>
      )}
    </section>
  );
}
