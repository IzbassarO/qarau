import type { ReactNode } from 'react';
import { LANGS, setLang, useI18n, type Lang } from '../i18n';

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
  children,
}: {
  on: boolean;
  onChange: (v: boolean) => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={() => onChange(!on)}
      className={`flex min-h-9 w-full cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-[13px] text-ink ${on ? 'bg-raised' : 'bg-transparent'}`}
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
