import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { LANGS, setLang } from '../i18n';
import { readApi, requestApi } from '../lib/api';
import fixture from '../test/fixtures/report-summary.json';
import report from '../test/fixtures/report.json';
import coverage from '../test/fixtures/report-coverage.json';
import { ReportPage } from './ReportPage';

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  readApi: vi.fn(),
  requestApi: vi.fn(),
}));
const pdfJs = vi.hoisted(() => ({
  getDocument: vi.fn(),
  getPage: vi.fn(),
  render: vi.fn(),
  destroy: vi.fn(),
}));
vi.mock('pdfjs-dist', () => ({ GlobalWorkerOptions: {}, getDocument: pdfJs.getDocument }));

const clients: QueryClient[] = [];
const region = () => screen.getByRole('region', { name: 'PDF preview' });
function renderReport() {
  const client = new QueryClient({ defaultOptions: { queries: { gcTime: 0, retry: false } } });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/inspections/37/report']}>
        <Routes>
          <Route path="/inspections/:id/report" element={<ReportPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  setLang('en');
  const routes: Record<string, unknown> = {
    '/api/inspections/37': fixture.inspection,
    '/api/sites': fixture.sites,
    '/api/inspections/37/findings': fixture.findings,
    '/api/findings/71/decisions': fixture.decisions['71'],
    '/api/findings/72/decisions': fixture.decisions['72'],
    '/api/findings/73/decisions': fixture.decisions['73'],
  };
  vi.mocked(readApi).mockImplementation(async (path) => {
    if (!(path in routes)) throw new Error(`Unexpected API path: ${path}`);
    return routes[path];
  });
  vi.mocked(requestApi).mockImplementation(async (path) => {
    if (path === '/api/inspections/37/coverage') return Response.json(coverage);
    if (/^\/api\/inspections\/37\/report.pdf\?lang=(ru|kk|en)$/.test(path))
      return new Response(report.body, { status: report.status, headers: report.headers });
    throw new Error(`Unexpected API path: ${path}`);
  });
  pdfJs.render.mockReturnValue({ promise: Promise.resolve(), cancel: vi.fn() });
  pdfJs.getPage.mockResolvedValue({
    getViewport: () => ({ width: 450, height: 240 }),
    render: pdfJs.render,
  });
  pdfJs.destroy.mockResolvedValue(undefined);
  pdfJs.getDocument.mockImplementation(() => ({
    promise: Promise.resolve({ numPages: 3, getPage: pdfJs.getPage }),
    destroy: pdfJs.destroy,
  }));
  vi.stubGlobal(
    'URL',
    class extends URL {
      static createObjectURL = vi.fn(() => 'blob:report');
      static revokeObjectURL = vi.fn();
    },
  );
});

afterEach(() => {
  clients.splice(0).forEach((client) => client.clear());
  vi.unstubAllGlobals();
});

it('renders the fixture summary, decisions, scale and coverage', async () => {
  const view = renderReport();
  await screen.findByText('Report is ready.');
  const summary = within(screen.getByRole('article', { name: 'Summary' }));
  expect(summary.getAllByText(fixture.sites[0].name)).toHaveLength(2);
  expect(view.container.querySelector('time')?.getAttribute('datetime')).toBe(
    fixture.inspection.created_at,
  );
  expect(view.container.querySelector('time')?.textContent).toContain('21 October 2026');
  expect(summary.getByText(fixture.findings[0].scale)).toBeTruthy();
  expect(summary.getByText('Scale not set')).toBeTruthy();
  const critical = within(summary.getByRole('region', { name: 'Critical' }));
  expect(critical.getByText('Findings: 1')).toBeTruthy();
  expect(critical.getByText(fixture.findings[0].comment)).toBeTruthy();
  expect(critical.getByText('Confirmed')).toBeTruthy();
  expect(critical.getByText('Alan Bekov, confirmed')).toBeTruthy();
  expect(critical.getByText('Dana Sadyk, rejected')).toBeTruthy();
  const watch = within(summary.getByRole('region', { name: 'Watch' }));
  expect(watch.getByText('Findings: 2')).toBeTruthy();
  expect(watch.getByText(fixture.findings[1].comment)).toBeTruthy();
  expect(watch.getByText(fixture.findings[2].comment)).toBeTruthy();
  expect(watch.getByText('No decision yet')).toBeTruthy();
  expect(
    summary.getByText('Inspected and clear: 61.5 %. Findings: 23.1 %. Not seen: 15.4 %.'),
  ).toBeTruthy();
  expect(summary.getByText('Areas not seen: north-wall-upper')).toBeTruthy();
});

it('shows loading with aria-busy while the API is pending', () => {
  vi.mocked(readApi).mockReturnValue(new Promise(() => {}));
  renderReport();
  expect(screen.getByRole('status').textContent).toBe('Generating report…');
  expect(region().getAttribute('aria-busy')).toBe('true');
  expect(screen.getByRole('button', { name: 'Download PDF' }).hasAttribute('disabled')).toBe(true);
  expect(pdfJs.getDocument).not.toHaveBeenCalled();
});

it('stays busy until page one finishes rendering, then shows the fixed page count and hash', async () => {
  let finish!: () => void;
  pdfJs.render.mockReturnValue({
    promise: new Promise<void>((resolve) => (finish = resolve)),
    cancel: vi.fn(),
  });
  const view = renderReport();
  await waitFor(() => expect(pdfJs.render).toHaveBeenCalledTimes(1));
  expect(region().getAttribute('aria-busy')).toBe('true');
  expect(screen.queryByText('1 of 3 pages')).toBeNull();
  await act(async () => finish());
  await screen.findByText('Report is ready.');
  expect(region().getAttribute('aria-busy')).toBe('false');
  expect(screen.getByText('1 of 3 pages')).toBeTruthy();
  expect(pdfJs.getPage).toHaveBeenCalledWith(1);
  expect(pdfJs.render.mock.calls[0][0].canvas).toBe(view.container.querySelector('canvas'));
  expect(view.container.querySelector('canvas')?.width).toBe(450);
  expect(screen.getByRole('link', { name: 'Download PDF' }).getAttribute('href')).toBe(
    'blob:report',
  );
  expect(screen.getByTitle(report.headers['X-Report-SHA256']).textContent).toBe(
    'bf98d56a3f72…7263bc26',
  );
  expect(view.container.querySelector('iframe')).toBeNull();
});

it.each(['summary', 'PDF'])(
  'shows failed and clears aria-busy when the %s API rejects',
  async (source) => {
    if (source === 'summary')
      vi.mocked(readApi).mockRejectedValue(new Error(fixture.failure.detail));
    else
      vi.mocked(requestApi).mockImplementation(async (path) => {
        if (path === '/api/inspections/37/coverage') return Response.json(coverage);
        throw new Error(fixture.failure.detail);
      });
    renderReport();
    expect((await screen.findByRole('alert')).textContent).toContain('Could not generate report');
    expect(region().getAttribute('aria-busy')).toBe('false');
    expect(screen.queryByText('Report is ready.')).toBeNull();
  },
);

it('shows failed and clears aria-busy when pdf.js rendering rejects', async () => {
  pdfJs.render.mockImplementation(() => ({
    promise: Promise.reject(new Error('Unable to paint PDF')),
    cancel: vi.fn(),
  }));
  renderReport();
  expect((await screen.findByRole('alert')).textContent).toContain('Could not preview report');
  expect(region().getAttribute('aria-busy')).toBe('false');
  expect(screen.queryByText('1 of 3 pages')).toBeNull();
  expect(screen.getByRole('link', { name: 'Download PDF' })).toBeTruthy();
  expect(pdfJs.destroy).toHaveBeenCalledTimes(1);
});

it.each(['ru', 'kk', 'en'] as const)('selects %s as the report language', async (lang) => {
  renderReport();
  await screen.findByText('Report is ready.');
  fireEvent.click(screen.getByRole('button', { name: LANGS.en[`report.language.${lang}`] }));
  await waitFor(() =>
    expect(requestApi).toHaveBeenCalledWith(`/api/inspections/37/report.pdf?lang=${lang}`, {
      signal: expect.any(AbortSignal),
      responseType: 'response',
    }),
  );
  await screen.findByText('Report is ready.');
  expect(screen.getByRole('link', { name: 'Download PDF' }).getAttribute('download')).toBe(
    `report-37-${lang}.pdf`,
  );
});

it.each([
  ['ru', 'Отчёт готов.', 'Страница 1 из 3'],
  ['kk', 'Есеп дайын.', '3 беттің 1-беті'],
  ['en', 'Report is ready.', '1 of 3 pages'],
] as const)('renders report states in %s', async (lang, ready, count) => {
  setLang(lang);
  renderReport();
  expect(await screen.findByText(ready)).toBeTruthy();
  expect(screen.getByText(count)).toBeTruthy();
});
