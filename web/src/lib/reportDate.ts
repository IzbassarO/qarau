import type { Key, Lang } from '../i18n';

/** Some browser ICU builds render kk months as M01…M12. Keep Kazakh dates explicit. */
export function formatReportDate(date: Date, lang: Lang, t: (key: Key) => string): string {
  if (lang !== 'kk')
    return new Intl.DateTimeFormat(lang === 'en' ? 'en-GB' : 'ru-RU', {
      dateStyle: 'long',
      timeStyle: 'short',
      hour12: false,
    }).format(date);

  const time = `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
  return t('report.datePattern')
    .replace('{year}', String(date.getFullYear()))
    .replace('{day}', String(date.getDate()))
    .replace('{month}', t('report.months').split('|')[date.getMonth()])
    .replace('{time}', time);
}
