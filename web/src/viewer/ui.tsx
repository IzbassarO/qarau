import { useId, type ReactNode } from 'react';
import { LANGS, setLang, useI18n, type Lang } from '../i18n';
import { MAX_SENSITIVITY, MIN_SENSITIVITY } from './settings';

export function ModeSwitch({
  orbitActive = true,
  onOrbit,
}: {
  orbitActive?: boolean;
  onOrbit: () => void;
}) {
  const { t } = useI18n();
  return (
    <div
      role="group"
      aria-label={t('viewer.mode.label')}
      className="flex gap-0.5 rounded-lg border border-line-strong p-0.5"
      onKeyDown={(event) => {
        if (event.ctrlKey || event.metaKey || event.altKey) return;
        if (!['1', '2', '3'].includes(event.key)) return;
        if (event.key === '1' && !event.repeat) onOrbit();
        event.preventDefault();
        event.stopPropagation();
      }}
    >
      <button
        type="button"
        aria-pressed={orbitActive}
        title={t('viewer.mode.orbitHint')}
        onClick={onOrbit}
        className={`h-8 cursor-pointer rounded-md px-3 text-xs ${orbitActive ? 'bg-line text-ink' : 'text-ink-2'}`}
      >
        {t('viewer.mode.orbit')}
      </button>
      {(['walk', 'observer'] as const).map((mode) => (
        <span key={mode} title={t('viewer.mode.comingSoon')}>
          <button
            type="button"
            disabled
            aria-pressed={false}
            title={t('viewer.mode.comingSoon')}
            className="h-8 cursor-not-allowed rounded-md px-3 text-xs text-ink-2 opacity-45"
          >
            {t(`viewer.mode.${mode}`)}
          </button>
        </span>
      ))}
    </div>
  );
}

export function NavigationSettings({
  sensitivity,
  onSensitivityChange,
}: {
  sensitivity: number;
  onSensitivityChange: (value: number) => void;
}) {
  const { t } = useI18n();
  const sensitivityId = useId();
  return (
    <details className="relative">
      <summary className="flex h-9 cursor-pointer list-none items-center gap-2 rounded-lg border border-line-strong px-3 text-xs text-ink-2 hover:bg-raised [&::-webkit-details-marker]:hidden">
        <svg
          width="15"
          height="15"
          viewBox="0 0 18 18"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          aria-hidden="true"
        >
          <path d="M3 4h12M3 9h12M3 14h12M6 2v4M12 7v4M7 12v4" />
        </svg>
        {t('viewer.settings.title')}
      </summary>
      <div className="absolute right-0 top-11 z-30 flex w-64 flex-col gap-3 rounded-xl border border-line-strong bg-surface p-4 shadow-lg">
        <div className="flex flex-col gap-3 text-[13px]">
          <div className="flex items-center justify-between gap-4">
            <label htmlFor={sensitivityId}>{t('viewer.settings.sensitivity')}</label>
            <span className="font-mono text-xs">{sensitivity.toFixed(2)}×</span>
          </div>
          <input
            id={sensitivityId}
            type="range"
            min={MIN_SENSITIVITY}
            max={MAX_SENSITIVITY}
            step={0.05}
            value={sensitivity}
            onChange={(event) => onSensitivityChange(Number(event.target.value))}
            className="w-full"
          />
        </div>
        <p className="m-0 text-xs leading-5 text-ink-2">{t('viewer.settings.precision')}</p>
      </div>
    </details>
  );
}

export function Logo() {
  return (
    <span className="flex items-center gap-2.5 text-ink">
      <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
        <circle cx="11" cy="11" r="9" fill="none" stroke="currentColor" strokeWidth="1.6" />
        <circle cx="11" cy="11" r="3.2" fill="currentColor" />
      </svg>
      <span className="text-base font-semibold tracking-tight">qarau</span>
    </span>
  );
}

export function LangSwitch() {
  const { lang, t } = useI18n();
  return (
    <div
      className="flex gap-0.5 rounded-lg border border-line-strong p-0.5"
      role="group"
      aria-label={t('language.label')}
    >
      {(Object.keys(LANGS) as Lang[]).map((l) => (
        <button
          key={l}
          type="button"
          aria-pressed={l === lang}
          onClick={() => setLang(l)}
          className={`h-6 cursor-pointer rounded-md px-2 text-xs uppercase ${l === lang ? 'bg-line text-ink' : 'text-ink-2'}`}
        >
          {t(`language.${l}`)}
        </button>
      ))}
    </div>
  );
}

export function Toggle({
  on,
  onChange,
  disabled = false,
  label,
  title,
  describedBy,
  children,
}: {
  on: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  label?: string;
  title?: string;
  describedBy?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      aria-label={label}
      aria-describedby={describedBy}
      aria-pressed={on}
      title={title}
      onClick={() => onChange(!on)}
      className={`flex min-h-9 w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-[13px] text-ink disabled:cursor-not-allowed disabled:opacity-50 ${on ? 'bg-raised' : 'bg-transparent'}`}
    >
      <span
        className={`relative h-4 w-7 shrink-0 rounded-full ${on ? 'bg-inspected' : 'bg-line-strong'}`}
      >
        <span
          className={`absolute top-0.5 h-3 w-3 rounded-full bg-ink ${on ? 'left-3.5' : 'left-0.5'}`}
        />
      </span>
      <span>{children}</span>
    </button>
  );
}

export const H2 = ({ children }: { children: ReactNode }) => (
  <h2 className="m-0 mb-0.5 text-xs font-medium uppercase tracking-[0.06em] text-ink-2">
    {children}
  </h2>
);

export const Row = ({ k, v }: { k: ReactNode; v: ReactNode }) => (
  <>
    <span className="text-ink-2">{k}</span>
    <span className="text-right font-mono">{v}</span>
  </>
);
