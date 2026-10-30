import { expect, it } from 'vitest';
import en from './en.json';
import ru from './ru.json';
import kk from './kk.json';

it('translates report copy into Russian and Kazakh and preserves placeholders', () => {
  const keys = (Object.keys(en) as (keyof typeof en)[]).filter((key) => key.startsWith('report.'));
  expect(keys).toContain('report.pageCount');
  expect(keys).toContain('report.previewFailure');
  for (const key of keys) {
    for (const locale of [ru, kk]) {
      // Language choices always use the language's native name in every UI locale.
      if (!key.startsWith('report.language.')) expect(locale[key]).not.toBe(en[key]);
      expect(locale[key].match(/\{\w+\}/g)?.sort() ?? []).toEqual(
        en[key].match(/\{\w+\}/g)?.sort() ?? [],
      );
    }
  }
});
