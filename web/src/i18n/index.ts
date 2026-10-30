import { useSyncExternalStore } from 'react';
import en from './en.json';
import kk from './kk.json';
import ru from './ru.json';

export const LANGS = { ru, kk, en } as const;
export type Lang = keyof typeof LANGS;
export type Key = keyof typeof ru;

const KEY = 'qarau.lang';
const read = (): Lang => {
  try {
    const v = localStorage.getItem(KEY);
    if (v && v in LANGS) return v as Lang;
  } catch {
    /* storage unavailable */
  }
  return 'ru';
};

let lang: Lang = read();
const subs = new Set<() => void>();

export function setLang(l: Lang) {
  lang = l;
  try {
    localStorage.setItem(KEY, l);
  } catch {
    /* ignore */
  }
  document.documentElement.lang = l;
  subs.forEach((f) => f());
}

/** Tiny translation hook: const { t, lang } = useI18n(); t('nav.viewer'). */
export function useI18n() {
  const l = useSyncExternalStore(
    (f) => (subs.add(f), () => subs.delete(f)),
    () => lang,
  );
  return { lang: l, t: (k: Key) => LANGS[l][k] ?? en[k] ?? k };
}
