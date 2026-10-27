/** Apply the system theme before rendering, and keep it in sync with system changes. */
export function followSystemTheme(): () => void {
  const preference = window.matchMedia('(prefers-color-scheme: dark)');
  const apply = () => {
    document.documentElement.dataset.theme = preference.matches ? 'dark' : 'light';
  };

  apply();
  preference.addEventListener('change', apply);
  return () => preference.removeEventListener('change', apply);
}
