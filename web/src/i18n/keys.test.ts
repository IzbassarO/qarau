import { expect, it } from 'vitest';
import en from './en.json';
import ru from './ru.json';
import kk from './kk.json';

it('has identical translation key sets in English, Russian and Kazakh', () => {
  const keys = Object.keys(en).sort();
  expect(Object.keys(ru).sort()).toEqual(keys);
  expect(Object.keys(kk).sort()).toEqual(keys);
  for (const locale of [en, ru, kk])
    for (const value of Object.values(locale)) expect(value.trim().length).toBeGreaterThan(0);
});
