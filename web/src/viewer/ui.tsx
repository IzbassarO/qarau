import { useId, type MouseEvent, type ReactNode } from 'react';
import { useI18n } from '../i18n';
import { LanguageSwitch, Wordmark } from '../shell/ShellIdentity';
import { MAX_SENSITIVITY, MIN_SENSITIVITY, type ObserverBoostKey } from './settings';

export function DenseToggle({
  on,
  onChange,
}: {
  on: boolean;
  onChange: (visible: boolean) => void;
}) {
  const { t } = useI18n();
  return (
    <Toggle on={on} onChange={onChange}>
      {t('viewer.show.densePoints')}
    </Toggle>
  );
}

export function ToolButton({
  label,
  onClick,
  pressed,
  children,
}: {
  label: string;
  onClick: (event: MouseEvent<HTMLButtonElement>) => void;
  pressed?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      aria-pressed={pressed}
      className="viewer-tool"
    >
      {children}
    </button>
  );
}

export function PointInfoTool({ active, onClick }: { active: boolean; onClick: () => void }) {
  const { t } = useI18n();
  return (
    <ToolButton label={t('pointInfo.tool')} pressed={active} onClick={onClick}>
      <svg
        width="18"
        height="18"
        viewBox="0 0 18 18"
        aria-hidden="true"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
      >
        <circle cx="9" cy="9" r="7" />
        <path d="M9 8v5M8 13h2" />
        <circle cx="9" cy="5" r="0.7" fill="currentColor" />
      </svg>
    </ToolButton>
  );
}

export function ModeSwitch({
  orbitActive = true,
  onOrbit,
  onObserver,
}: {
  orbitActive?: boolean;
  onOrbit: () => void;
  onObserver: () => void;
}) {
  const { t } = useI18n();
  return (
    <div
      role="group"
      aria-label={t('viewer.mode.label')}
      className="viewer-segments"
      onKeyDown={(event) => {
        if (event.ctrlKey || event.metaKey || event.altKey) return;
        if (event.key.toLowerCase() === 'f' || event.code === 'KeyF') {
          if (!event.repeat) (orbitActive ? onObserver : onOrbit)();
          event.preventDefault();
          event.stopPropagation();
          return;
        }
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
        className="viewer-segment"
      >
        {t('viewer.mode.orbit')}
      </button>
      <span title={t('viewer.mode.comingSoon')}>
        <button
          type="button"
          disabled
          aria-pressed={false}
          title={t('viewer.mode.comingSoon')}
          className="viewer-segment"
        >
          {t('viewer.mode.walk')}
        </button>
      </span>
      <button
        type="button"
        aria-pressed={!orbitActive}
        title={t('viewer.mode.observerHint')}
        onClick={onObserver}
        className="viewer-segment"
      >
        {t('viewer.mode.observer')}
      </button>
    </div>
  );
}

export function NavigationSettings({
  sensitivity,
  onSensitivityChange,
  boostKey,
  onBoostKeyChange,
}: {
  sensitivity: number;
  onSensitivityChange: (value: number) => void;
  boostKey?: ObserverBoostKey;
  onBoostKeyChange?: (value: ObserverBoostKey) => void;
}) {
  const { t } = useI18n();
  const sensitivityId = useId();
  return (
    <details className="relative">
      <summary className="viewer-settings-trigger">
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
      <div className="viewer-settings-panel">
        <div className="viewer-settings-field">
          <div className="viewer-settings-label">
            <label htmlFor={sensitivityId}>{t('viewer.settings.sensitivity')}</label>
            <span className="viewer-mono viewer-caption">{sensitivity.toFixed(2)}×</span>
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
        <p className="viewer-caption viewer-muted">{t('viewer.settings.precision')}</p>
        {boostKey && onBoostKeyChange && (
          <label className="viewer-label-stack viewer-caption viewer-muted">
            {t('viewer.settings.observerBoost')}
            <select
              value={boostKey}
              onChange={(event) => onBoostKeyChange(event.target.value as ObserverBoostKey)}
              className="viewer-select"
            >
              <option value="control">{t('viewer.settings.control')}</option>
              <option value="alt">{t('viewer.settings.alt')}</option>
            </select>
          </label>
        )}
      </div>
    </details>
  );
}

export function Logo() {
  return <Wordmark />;
}

export function LangSwitch() {
  return <LanguageSwitch />;
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
      className="viewer-toggle"
    >
      <span className="viewer-toggle-track">
        <span className="viewer-toggle-thumb" />
      </span>
      <span>{children}</span>
    </button>
  );
}

export const H2 = ({ children }: { children: ReactNode }) => (
  <h2 className="viewer-heading">{children}</h2>
);

export const Row = ({ k, v }: { k: ReactNode; v: ReactNode }) => (
  <>
    <span className="viewer-muted">{k}</span>
    <span className="viewer-value">{v}</span>
  </>
);
