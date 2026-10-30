import { afterEach, expect, it, vi } from 'vitest';
import { LANGS, type Lang } from '../i18n';
import fixture from '../test/fixtures/report-dates.json';
import { formatReportDate } from './reportDate';

afterEach(() => vi.restoreAllMocks());

const { year, month, day, hour, minute } = fixture.local;

it.each<Lang>(['en', 'ru', 'kk'])('formats the local inspection date in %s', (lang) => {
  const date = new Date(year, month - 1, day, hour, minute);
  expect(formatReportDate(date, lang, (key) => LANGS[lang][key])).toBe(fixture.formatted[lang]);
});

it.each(fixture.kazakhMonths)('spells out Kazakh month $month', (entry) => {
  const date = new Date(year, entry.month - 1, day, hour, minute);
  expect(formatReportDate(date, 'kk', (key) => LANGS.kk[key])).toBe(entry.formatted);
});

it('formats Kazakh without depending on browser ICU locale data', () => {
  vi.spyOn(Intl, 'DateTimeFormat').mockImplementation(() => {
    throw new Error('Locale data unavailable');
  });
  const date = new Date(year, month - 1, day, hour, minute);
  expect(formatReportDate(date, 'kk', (key) => LANGS.kk[key])).toBe(fixture.formatted.kk);
});
