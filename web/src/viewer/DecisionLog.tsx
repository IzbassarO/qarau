import { useI18n } from '../i18n';
import type { Decision } from '../lib/findings';

export function DecisionLog({ decisions }: { decisions: readonly Decision[] }) {
  const { t, lang } = useI18n();
  const ordered = [...decisions].sort(
    (a, b) => Date.parse(a.created_at) - Date.parse(b.created_at) || a.id - b.id,
  );
  const date = new Intl.DateTimeFormat(lang === 'en' ? 'en-GB' : lang, {
    day: 'numeric',
    month: 'short',
  });
  const time = new Intl.DateTimeFormat(lang, { hour: '2-digit', minute: '2-digit', hour12: false });
  return (
    <section className="decision-log" aria-label={t('decisions.title')}>
      <h3 className="qv-heading">{t('decisions.title')}</h3>
      {ordered.length === 0 ? (
        <p className="qv-caption qv-muted">{t('decisions.empty')}</p>
      ) : (
        <ol className="qv-log decision-entries">
          {ordered.map((decision) => (
            <li key={decision.id} className="qv-logentry">
              <span className="qv-avatar" aria-hidden="true">
                {decision.user.slice(0, 1)}
              </span>
              <div className="qv-logentry__who">
                <span>{decision.user}</span>
                <span className="qv-logentry__verdict">
                  {t(`decisions.verdict.${decision.verdict}`)}
                </span>
                <time className="qv-logentry__when decision-time" dateTime={decision.created_at}>
                  {date.format(new Date(decision.created_at))},{' '}
                  {time.format(new Date(decision.created_at))}
                </time>
              </div>
              {decision.new_class && (
                <p className="qv-logentry__note">
                  {t('decisions.class')}: {t(`evidence.class.${decision.new_class}`)}
                </p>
              )}
              {decision.new_severity && (
                <p className="qv-logentry__note">
                  {t(`evidence.severity.${decision.new_severity}`)}
                </p>
              )}
              {decision.comment && (
                <p className="qv-logentry__note decision-comment">{decision.comment}</p>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
