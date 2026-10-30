import { useEffect, useRef, useState } from 'react';
import { useI18n, type Key } from '../i18n';
import { listSites, type SiteOption } from '../lib/sites';
import { ApiUnavailableError, startProcessing } from '../lib/api';
import {
  createInspection,
  uploadFile,
  validateVideo,
  UploadError,
  VIDEO_ACCEPT,
  type UploadProgress,
  type UploadSession,
} from '../lib/upload';

const ERROR_KEYS = {
  unsupported_type: 'upload.invalidType',
  too_large: 'upload.tooLarge',
  network: 'upload.network',
  cancelled: 'upload.cancelled',
  server: 'upload.server',
} as const satisfies Record<string, Key>;

export function NewInspectionDialog({
  onClose,
  onUploaded,
}: {
  onClose: () => void;
  onUploaded?: (inspectionId: number) => void;
}) {
  const { t, lang } = useI18n();
  const dialog = useRef<HTMLDialogElement>(null);
  const controller = useRef<AbortController | null>(null);
  const inspectionId = useRef<number | null>(null);
  const session = useRef<UploadSession>({});
  const [file, setFile] = useState<File | null>(null);
  const [siteId, setSiteId] = useState('');
  const [sites, setSites] = useState<SiteOption[]>([]);
  const [siteState, setSiteState] = useState<'loading' | 'ready' | 'unavailable' | 'error'>(
    'loading',
  );
  const [siteAttempt, setSiteAttempt] = useState(0);
  const [error, setError] = useState<Key | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<UploadProgress | null>(null);

  useEffect(() => {
    const element = dialog.current!;
    const previousFocus = document.activeElement;
    element.showModal();
    return () => {
      controller.current?.abort();
      controller.current = null;
      element.close();
      if (previousFocus instanceof HTMLElement) previousFocus.focus();
    };
  }, []);

  useEffect(() => {
    const request = new AbortController();
    void listSites(request.signal).then(
      (result) => {
        if (request.signal.aborted) return;
        setSites(result);
        setSiteState('ready');
      },
      (error: unknown) => {
        if (!request.signal.aborted)
          setSiteState(error instanceof ApiUnavailableError ? 'unavailable' : 'error');
      },
    );
    return () => request.abort();
  }, [siteAttempt]);

  const resetSession = () => {
    inspectionId.current = null;
    session.current = {};
    setProgress(null);
    setError(null);
  };
  const validate = (selected: File | undefined) => {
    resetSession();
    setFile(null);
    if (!selected) return;
    if (selected.size === 0 && selected.type.startsWith('video/'))
      return setError('upload.emptyFile');
    try {
      validateVideo(selected);
      setFile(selected);
    } catch (error) {
      setError(ERROR_KEYS[error instanceof UploadError ? error.code : 'server']);
    }
  };
  const close = () => {
    controller.current?.abort();
    onClose();
  };
  const cancel = () => {
    controller.current?.abort();
    controller.current = null;
    setBusy(false);
    setError('upload.cancelled');
  };
  const start = async () => {
    if (!file || !siteId || controller.current) return;
    const request = new AbortController();
    controller.current = request;
    setBusy(true);
    setError(null);
    try {
      if (inspectionId.current === null) {
        const inspection = await createInspection(
          { siteId: Number(siteId), filename: file.name, size: file.size, contentType: file.type },
          request.signal,
        );
        if (request.signal.aborted) return;
        inspectionId.current = inspection.id;
      }
      const { runId } = await uploadFile(inspectionId.current, file, {
        signal: request.signal,
        session: session.current,
        onProgress: (value) => {
          if (!request.signal.aborted) setProgress(value);
        },
      });
      if (request.signal.aborted) return;
      // Processing starts by itself; if this fails the status screen offers "Start processing".
      await startProcessing(inspectionId.current, runId).catch(() => {});
      if (request.signal.aborted) return;
      onClose();
      onUploaded?.(inspectionId.current);
    } catch (error) {
      if (controller.current === request)
        setError(ERROR_KEYS[error instanceof UploadError ? error.code : 'server']);
    } finally {
      if (controller.current === request) {
        controller.current = null;
        setBusy(false);
      }
    }
  };
  const number = new Intl.NumberFormat(lang, {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });
  const mb = (bytes: number) => number.format(bytes / 1024 ** 2);

  return (
    <dialog
      ref={dialog}
      className="qv qv-dialog inspection-dialog"
      aria-labelledby="new-inspection-title"
      aria-describedby="upload-description"
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
    >
      <div className="qv-dialog__head dialog-heading">
        <h2 className="qv-heading" id="new-inspection-title">
          {t('inspection.new')}
        </h2>
        <button
          type="button"
          className="qv-iconbtn icon-button"
          aria-label={t('dialog.close')}
          onClick={close}
        >
          <span aria-hidden="true">×</span>
        </button>
      </div>
      <div className="qv-dialog__body qv-dialog__body--one">
        <p className="qv-caption" id="upload-description">
          {t('upload.description')}
        </p>
        <div className="qv-field upload-site">
          <label htmlFor="inspection-site">{t('upload.site')}</label>
          <select
            className="qv-select"
            id="inspection-site"
            value={siteId}
            disabled={busy || siteState !== 'ready' || sites.length === 0}
            aria-describedby={
              siteState !== 'ready' || sites.length === 0 ? 'upload-sites-state' : undefined
            }
            onChange={(event) => {
              resetSession();
              setSiteId(event.target.value);
            }}
          >
            <option value="">
              {t(
                siteState === 'loading'
                  ? 'upload.sitesLoading'
                  : siteState === 'unavailable'
                    ? 'upload.sitesUnavailable'
                    : 'upload.chooseSite',
              )}
            </option>
            {sites.map((site) => (
              <option key={site.id} value={site.id}>
                {site.name}
              </option>
            ))}
          </select>
          {siteState === 'loading' && (
            <p id="upload-sites-state" className="qv-caption" role="status">
              {t('upload.sitesLoading')}
            </p>
          )}
          {siteState === 'ready' && sites.length === 0 && (
            <p id="upload-sites-state" className="qv-notice" role="status">
              {t('upload.sitesEmpty')}
            </p>
          )}
          {siteState === 'unavailable' && (
            <p id="upload-sites-state" className="qv-notice" role="status">
              {t('upload.catalogOnly')}
            </p>
          )}
          {siteState === 'error' && (
            <div id="upload-sites-state" className="upload-sites-error">
              <p className="qv-notice upload-error" role="alert">
                {t('upload.sitesError')}
              </p>
              <button
                className="qv-btn qv-btn--secondary"
                onClick={() => {
                  setSiteState('loading');
                  setSiteAttempt((value) => value + 1);
                }}
              >
                {t('shell.retry')}
              </button>
            </div>
          )}
        </div>
        <label className="qv-drop upload-picker" htmlFor="inspection-video">
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          >
            <path d="M12 16V4m-5 5 5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" />
          </svg>
          <span>{t('upload.choose')}</span>
          <span className="qv-caption">{t('upload.formats')}</span>
          <input
            id="inspection-video"
            className="sr-only"
            type="file"
            accept={VIDEO_ACCEPT}
            disabled={busy}
            aria-invalid={!!error && error !== 'upload.cancelled' && error !== 'upload.network'}
            aria-describedby={error ? 'upload-error' : 'upload-description'}
            onChange={(event) => validate(event.target.files?.[0])}
          />
        </label>
        {file && (
          <p className="qv-caption selected-file">
            {t('upload.selected')}: {file.name}
          </p>
        )}
        {(busy || progress) && file && (
          <div className="qv-upbar upload-progress" aria-live="polite">
            <label htmlFor="video-progress">{t('upload.progress')}</label>
            <progress
              className="qv-pbar"
              id="video-progress"
              max={100}
              value={progress?.percent ?? 0}
            />
            <div className="qv-caption upload-metrics">
              <span>
                {mb(progress?.sentBytes ?? 0)} {t('upload.unitMB')} / {mb(file.size)}{' '}
                {t('upload.unitMB')}
              </span>
              <span>
                {Math.round(progress?.percent ?? 0)} {t('upload.unitPercent')}
              </span>
              <span>
                {mb(progress?.bytesPerSecond ?? 0)} {t('upload.unitMBs')}
              </span>
            </div>
            {busy && (
              <p className="qv-caption" role="status">
                {t(
                  progress?.percent === 100
                    ? 'upload.finishing'
                    : progress
                      ? 'upload.sending'
                      : 'upload.preparing',
                )}
              </p>
            )}
          </div>
        )}
        {error && (
          <p className="qv-notice upload-error" role="alert" id="upload-error">
            {t(error)}
          </p>
        )}
      </div>
      <div className="qv-dialog__foot dialog-actions">
        {busy ? (
          <button className="qv-btn qv-btn--secondary" onClick={cancel}>
            {t('upload.cancel')}
          </button>
        ) : (
          <>
            <button className="qv-btn qv-btn--secondary" onClick={close}>
              {t('dialog.close')}
            </button>
            <button
              className="qv-btn"
              disabled={!file || !siteId || siteState !== 'ready'}
              onClick={() => void start()}
            >
              {t(error === 'upload.network' ? 'upload.retry' : 'upload.start')}
            </button>
          </>
        )}
      </div>
    </dialog>
  );
}
