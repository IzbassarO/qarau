import { useEffect, useRef, useState } from 'react';
import { useI18n, type Key } from '../i18n';

export function NewInspectionDialog({ onClose }: { onClose: () => void }) {
  const { t } = useI18n();
  const dialog = useRef<HTMLDialogElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<Key | null>(null);

  useEffect(() => {
    const element = dialog.current!;
    const previousFocus = document.activeElement;
    element.showModal();
    return () => {
      element.close();
      if (previousFocus instanceof HTMLElement) previousFocus.focus();
    };
  }, []);

  const validate = (selected: File | undefined) => {
    setFile(null);
    if (!selected) return setError(null);
    if (!selected.type.startsWith('video/')) return setError('upload.invalidType');
    if (selected.size === 0) return setError('upload.emptyFile');
    setError(null);
    setFile(selected);
  };

  return (
    <dialog
      ref={dialog}
      className="inspection-dialog"
      aria-labelledby="new-inspection-title"
      aria-describedby="upload-unavailable"
      onCancel={onClose}
    >
      <div className="dialog-heading">
        <h2 id="new-inspection-title">{t('inspection.new')}</h2>
        <button
          type="button"
          className="shell-button icon-button"
          aria-label={t('dialog.close')}
          onClick={onClose}
        >
          <span aria-hidden="true">×</span>
        </button>
      </div>
      <p className="muted">{t('upload.description')}</p>
      <label className="upload-picker" htmlFor="inspection-video">
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
        <span className="muted">{t('upload.formats')}</span>
        <input
          id="inspection-video"
          className="sr-only"
          type="file"
          accept="video/*"
          aria-invalid={!!error}
          aria-describedby={error ? 'upload-error' : 'upload-unavailable'}
          onChange={(event) => validate(event.target.files?.[0])}
        />
      </label>
      {error && (
        <p className="upload-error" role="alert" id="upload-error">
          {t(error)}
        </p>
      )}
      {file && (
        <p className="selected-file" role="status">
          {t('upload.selected')}: {file.name}
        </p>
      )}
      <p className="notice" id="upload-unavailable" role="status">
        {t('upload.unavailable')}
      </p>
      <div className="dialog-actions">
        <button className="shell-button" onClick={onClose}>
          {t('dialog.close')}
        </button>
        <button className="shell-button primary" disabled aria-describedby="upload-unavailable">
          {t('upload.start')}
        </button>
      </div>
    </dialog>
  );
}
