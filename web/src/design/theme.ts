/** Follow the system theme unless the URL supplies a temporary presentation override. */
export function followSystemTheme(override?: 'light' | 'dark'): () => void {
  const preference = window.matchMedia('(prefers-color-scheme: dark)');
  const apply = () => {
    document.documentElement.dataset.theme = override ?? (preference.matches ? 'dark' : 'light');
  };

  apply();
  preference.addEventListener('change', apply);
  return () => preference.removeEventListener('change', apply);
}
