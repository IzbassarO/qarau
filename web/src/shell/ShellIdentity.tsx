import { LANGS, setLang, useI18n, type Lang } from '../i18n';

export function Wordmark({ hero = false }: { hero?: boolean }) {
  const { t } = useI18n();
  return (
    <span className="qv-topbar__brand shell-wordmark">
      <span className={`qv-mark ${hero ? 'qv-mark--hero' : 'qv-mark--tile'}`} aria-hidden="true">
        <svg viewBox="0 0 24 24">
          <g className="q">
            <circle cx="12" cy="3" r="2" />
            <circle cx="18.4" cy="5.6" r="2" />
            <circle cx="21" cy="12" r="2" />
            <circle cx="18.4" cy="18.4" r="2" />
            <circle cx="12" cy="21" r="2" />
            <circle cx="5.6" cy="18.4" r="2" />
            <circle cx="3" cy="12" r="2" />
            <circle cx="5.6" cy="5.6" r="2" />
          </g>
          <circle className="f" cx="22" cy="22" r="2" />
        </svg>
      </span>
      <span>{t('shell.product')}</span>
    </span>
  );
}

export function LanguageSwitch() {
  const { lang, t } = useI18n();
  return (
    <div className="qv-segment shell-languages" role="group" aria-label={t('language.label')}>
      {(Object.keys(LANGS) as Lang[]).map((locale) => (
        <button
          key={locale}
          type="button"
          aria-pressed={locale === lang}
          onClick={() => setLang(locale)}
        >
          {t(`language.${locale}`)}
        </button>
      ))}
    </div>
  );
}
