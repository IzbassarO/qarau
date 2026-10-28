import { useLayoutEffect } from 'react';
import { useSearchParams } from 'react-router';
import { followSystemTheme } from '../design/theme';
import { setLanguageOverride } from '../i18n';

/** Read-only presentation controls for shared URLs, including sign-in and the viewer. */
export function PresentationOverrides() {
  const [search] = useSearchParams();
  const theme = search.get('theme');
  const lang = search.get('lang');

  useLayoutEffect(
    () => followSystemTheme(theme === 'light' || theme === 'dark' ? theme : undefined),
    [theme],
  );
  useLayoutEffect(() => {
    setLanguageOverride(lang === 'ru' || lang === 'kk' || lang === 'en' ? lang : null);
    return () => setLanguageOverride(null);
  }, [lang]);

  return null;
}
