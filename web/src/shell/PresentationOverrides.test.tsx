import { act, fireEvent, render, screen } from '@testing-library/react';
import { Link, MemoryRouter } from 'react-router';
import { afterEach, expect, it, vi } from 'vitest';
import { setLang, useI18n } from '../i18n';
import { PresentationOverrides } from './PresentationOverrides';

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

function renderPresentation(query: string) {
  const preference = new EventTarget();
  Object.defineProperty(preference, 'matches', { value: false, writable: true });
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => preference),
  );
  setLang('en');
  function Content() {
    const { lang, t } = useI18n();
    return (
      <>
        <p data-testid="language">{lang}</p>
        <h1>{t('report.title')}</h1>
        <Link to="/inspections/demo/report">Clear overrides</Link>
        <Link to="/inspections/demo/report?theme=dark&lang=kk">Change overrides</Link>
      </>
    );
  }
  const view = render(
    <MemoryRouter initialEntries={[`/inspections/demo/report${query}`]}>
      <PresentationOverrides />
      <Content />
    </MemoryRouter>,
  );
  return { ...view, preference };
}

it.each([
  ['light', 'ru', 'Отчёт об инспекции'],
  ['dark', 'ru', 'Отчёт об инспекции'],
  ['light', 'kk', 'Инспекция есебі'],
  ['dark', 'kk', 'Инспекция есебі'],
  ['light', 'en', 'Inspection report'],
  ['dark', 'en', 'Inspection report'],
])('applies URL theme=%s and lang=%s without changing saved preferences', (theme, lang, title) => {
  const { preference } = renderPresentation(`?theme=${theme}&lang=${lang}`);
  expect(document.documentElement.dataset.theme).toBe(theme);
  expect(document.documentElement.lang).toBe(lang);
  expect(screen.getByTestId('language').textContent).toBe(lang);
  expect(screen.getByRole('heading').textContent).toBe(title);
  expect(localStorage.getItem('qarau.lang')).toBe('en');
  act(() => {
    Object.assign(preference, { matches: true });
    preference.dispatchEvent(new Event('change'));
  });
  expect(document.documentElement.dataset.theme).toBe(theme);
});

it('updates overrides on navigation and restores saved language and system theme when removed', () => {
  const { preference } = renderPresentation('?theme=light&lang=ru');
  fireEvent.click(screen.getByRole('link', { name: 'Change overrides' }));
  expect(document.documentElement.dataset.theme).toBe('dark');
  expect(screen.getByRole('heading').textContent).toBe('Инспекция есебі');
  fireEvent.click(screen.getByRole('link', { name: 'Clear overrides' }));
  expect(document.documentElement.dataset.theme).toBe('light');
  expect(document.documentElement.lang).toBe('en');
  expect(screen.getByRole('heading').textContent).toBe('Inspection report');
  expect(localStorage.getItem('qarau.lang')).toBe('en');
  act(() => {
    Object.assign(preference, { matches: true });
    preference.dispatchEvent(new Event('change'));
  });
  expect(document.documentElement.dataset.theme).toBe('dark');
});

it.each(['?theme=invalid&lang=invalid', '?theme=midnight&lang=constructor'])(
  'ignores unsupported URL values: %s',
  (query) => {
    renderPresentation(query);
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(document.documentElement.lang).toBe('en');
    expect(screen.getByRole('heading').textContent).toBe('Inspection report');
    expect(localStorage.getItem('qarau.lang')).toBe('en');
  },
);
