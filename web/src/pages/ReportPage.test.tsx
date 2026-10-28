import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AuthProvider } from '../auth/AuthProvider';
import { RequireAuth } from '../auth/RequireAuth';
import { setLang, type Lang } from '../i18n';
import * as reportApi from '../lib/report';
import { AppShell } from '../shell/AppShell';
import fixture from '../test/fixtures/report-summary.json';
import { fixturePdf, mockReportApi } from '../test/reportApi';
import { ReportPage } from './ReportPage';

const clients: QueryClient[] = [];
function renderReport(id = '37', signedIn = true) {
  if (signedIn) sessionStorage.setItem('qarau.auth.session', 'Bearer test-report-session');
  const client = new QueryClient({ defaultOptions: { queries: { gcTime: 0 } } });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <MemoryRouter initialEntries={[`/inspections/${id}/report`]}>
          <Routes>
            <Route element={<RequireAuth />}>
              <Route
                path="/inspections/:id/report"
                element={
                  <AppShell>
                    <ReportPage />
                  </AppShell>
                }
              />
            </Route>
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  setLang('en');
});
afterEach(() => {
  clients.splice(0).forEach((client) => client.clear());
  sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it('renders fixture object, date, scale, severity groups, API statuses and decision names', async () => {
  mockReportApi();
  const view = renderReport();
  const document = await screen.findByRole('article', { name: 'Summary' });
  await screen.findByText(fixture.findings[0].comment);
  expect(within(document).getAllByText(fixture.sites[0].name)).toHaveLength(2);
  expect(view.container.querySelector('time')?.getAttribute('datetime')).toBe(
    fixture.inspection.created_at,
  );
  expect(screen.getByText(fixture.findings[0].scale)).toBeTruthy();
  expect(screen.getByText(/Scale not set/)).toBeTruthy();
  const critical = screen.getByRole('region', { name: 'Critical' });
  expect(within(critical).getByText(fixture.findings[0].comment)).toBeTruthy();
  expect(within(critical).getByText('Confirmed')).toBeTruthy();
  expect(within(critical).getByText('Alan Bekov, confirmed')).toBeTruthy();
  expect(within(critical).getByText('Dana Sadyk, rejected')).toBeTruthy();
  const watch = screen.getByRole('region', { name: 'Watch' });
  expect(within(watch).getByText(fixture.findings[1].comment)).toBeTruthy();
  expect(within(watch).getByText('Rejected')).toBeTruthy();
  expect(within(watch).getByText('Dana Sadyk, rejected')).toBeTruthy();
  expect(within(watch).getByText(fixture.findings[2].comment)).toBeTruthy();
  expect(within(watch).getByText('Proposed')).toBeTruthy();
  expect(within(watch).getByText('No decision yet')).toBeTruthy();
  expect(screen.queryByText(/confidence/i)).toBeNull();
});

it('shows coverage not computed for the fixture’s null coverage', async () => {
  expect(fixture.coverage).toBeNull();
  mockReportApi();
  renderReport();
  expect(await screen.findByText('Coverage not computed')).toBeTruthy();
});

it('renders an available coverage line verbatim without calculating coverage', async () => {
  mockReportApi();
  vi.spyOn(reportApi, 'fetchReportSummary').mockResolvedValue({
    inspectionId: 37,
    object: fixture.sites[0].name,
    date: fixture.inspection.created_at,
    findings: [],
    coverage: fixture.coverageLine,
  });
  renderReport();
  expect(await screen.findByText(fixture.coverageLine)).toBeTruthy();
  expect(screen.queryByText('Coverage not computed')).toBeNull();
});

it('shows generating while the synchronous PDF request is pending, then ready', async () => {
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  const fetch = mockReportApi(() => pending);
  renderReport();
  expect(screen.getByRole('status').textContent).toBe('Generating report…');
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith('/api/inspections/37/report.pdf?lang=en', expect.anything()),
  );
  expect(screen.getByRole('status').textContent).toBe('Generating report…');
  expect(screen.getByRole('region', { name: 'PDF preview' }).getAttribute('aria-busy')).toBe(
    'true',
  );
  await act(async () => finish(fixturePdf()));
  expect(await screen.findByText('Report is ready. PDF preview will appear here.')).toBeTruthy();
  expect(screen.getByRole('region', { name: 'PDF preview' }).getAttribute('aria-busy')).toBe(
    'false',
  );
  expect(screen.queryByRole('alert')).toBeNull();
});

it('shows generating for an accepted response without displaying a ready placeholder', async () => {
  const fetch = mockReportApi(() => new Response(null, { status: 202 }));
  renderReport();
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith('/api/inspections/37/report.pdf?lang=en', expect.anything()),
  );
  expect(screen.getByRole('status').textContent).toBe('Generating report…');
  expect(screen.queryByText('Report is ready. PDF preview will appear here.')).toBeNull();
});

it('shows the failed PDF state with the API’s words while keeping the summary', async () => {
  mockReportApi(() => Response.json(fixture.failure, { status: 500 }));
  renderReport();
  const alert = await screen.findByRole('alert');
  expect(within(alert).getByRole('heading').textContent).toBe('Could not generate report');
  expect(within(alert).getByText(fixture.failure.detail)).toBeTruthy();
  expect(screen.getByText(fixture.findings[0].comment)).toBeTruthy();
});

it('requests report.pdf?lang=kk when Қазақша is chosen and keeps the UI language', async () => {
  const fetch = mockReportApi();
  renderReport();
  await screen.findByText('Report is ready. PDF preview will appear here.');
  const languages = screen.getByRole('group', { name: 'PDF language' });
  expect(
    within(languages).getByRole('button', { name: 'English' }).getAttribute('aria-pressed'),
  ).toBe('true');
  fireEvent.click(within(languages).getByRole('button', { name: 'Қазақша' }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      '/api/inspections/37/report.pdf?lang=kk',
      expect.objectContaining({
        headers: { Authorization: 'Bearer test-report-session' },
      }),
    ),
  );
  expect(
    within(languages).getByRole('button', { name: 'Қазақша' }).getAttribute('aria-pressed'),
  ).toBe('true');
  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Inspection report');
});

it('clears the previous PDF state during a language refetch and ignores its late response', async () => {
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  const fetch = mockReportApi(() => pending);
  renderReport();
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith('/api/inspections/37/report.pdf?lang=en', expect.anything()),
  );
  fetch.mockImplementationOnce(async () => Response.json(fixture.failure, { status: 500 }));
  fireEvent.click(screen.getByRole('button', { name: 'Қазақша' }));
  expect(await screen.findByText(fixture.failure.detail)).toBeTruthy();
  await act(async () => finish(fixturePdf()));
  expect(screen.getByRole('alert').textContent).toContain(fixture.failure.detail);
  expect(screen.queryByText('Report is ready. PDF preview will appear here.')).toBeNull();
});

it.each<[Lang, string, string]>([
  ['ru', 'Отчёт об инспекции', 'Покрытие не рассчитано'],
  ['kk', 'Инспекция есебі', 'Қамту есептелмеген'],
  ['en', 'Inspection report', 'Coverage not computed'],
])(
  'localizes %s and defaults the PDF request to the UI language',
  async (lang, title, coverage) => {
    setLang(lang);
    const fetch = mockReportApi();
    renderReport();
    expect(await screen.findByText(coverage)).toBeTruthy();
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(title);
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        `/api/inspections/37/report.pdf?lang=${lang}`,
        expect.anything(),
      ),
    );
  },
);

it('opens /inspections/notre-dame/report with the API summary and language choice, without a banner', async () => {
  const fetch = mockReportApi();
  renderReport('notre-dame');
  await screen.findByText('Report is ready. PDF preview will appear here.');
  expect(screen.getAllByText(fixture.sites[0].name)).toHaveLength(2);
  expect(screen.getByRole('group', { name: 'PDF language' })).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(fetch).toHaveBeenCalledWith('/api/inspections/37/report.pdf?lang=en', expect.anything());
});

it.each(['999', 'unknown'])(
  'shows the API failure for unknown id %s rather than a blank page',
  async (id) => {
    mockReportApi();
    renderReport(id);
    expect((await screen.findByRole('alert')).textContent).toContain(fixture.notFound.detail);
    expect(screen.getByRole('group', { name: 'PDF language' })).toBeTruthy();
  },
);

it('requires a session before fetching any report data', () => {
  const fetch = mockReportApi();
  renderReport('37', false);
  expect(screen.getByRole('button', { name: 'Sign in' })).toBeTruthy();
  expect(fetch).not.toHaveBeenCalled();
  expect(screen.queryByRole('article')).toBeNull();
});
