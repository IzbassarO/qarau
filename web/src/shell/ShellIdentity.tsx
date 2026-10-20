import { LANGS, setLang, useI18n, type Lang } from '../i18n';

export function Wordmark() {
  const { t } = useI18n();
  return <span className="shell-wordmark">{t('shell.product')}</span>;
}

export function LanguageSwitch() {
  const { lang, t } = useI18n();
  return (
    <div className="shell-languages" role="group" aria-label={t('language.label')}>
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
