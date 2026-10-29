import { StrictMode } from 'react';
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
import coverageFixture from '../test/fixtures/report-coverage.json';
import { fixturePdf, mockReportApi } from '../test/reportApi';
import { ReportPage } from './ReportPage';

const pdfJs = vi.hoisted(() => ({
  render: vi.fn(),
  getPage: vi.fn(),
  getDocument: vi.fn(),
  cancel: vi.fn(),
  destroy: vi.fn(),
}));
vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: {},
  getDocument: pdfJs.getDocument,
}));

// Fixed English queries shared by the state and Blob-lifecycle scenarios.
const pdfRegion = () => screen.getByRole('region', { name: 'PDF preview' });
const previewCanvas = () => screen.getByRole('img', { name: 'PDF preview' });
const missingPreview = () => screen.queryByRole('img', { name: 'PDF preview' });
const downloadButton = () => screen.getByRole('button', { name: 'Download PDF' });
const downloadLink = () => screen.getByRole('link', { name: 'Download PDF' });
const findDownload = () => screen.findByRole('link', { name: 'Download PDF' });
const missingDownload = () => screen.queryByRole('link', { name: 'Download PDF' });
const statusText = () => screen.getByRole('status').textContent;
const missingAlert = () => screen.queryByRole('alert');
const findAlert = () => screen.findByRole('alert');
const languageChoice = () => screen.getByRole('group', { name: 'PDF language' });

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function pdfCalls(fetch: ReturnType<typeof mockReportApi>, path = '/report.pdf') {
  return fetch.mock.calls.filter(([url]) => String(url).includes(path));
}

const clients: QueryClient[] = [];
function renderReport(
  id = '37',
  signedIn = true,
  entry = `/inspections/${id}/report`,
  strict = false,
) {
  if (signedIn) sessionStorage.setItem('qarau.auth.session', 'Bearer test-report-session');
  const client = new QueryClient({ defaultOptions: { queries: { gcTime: 0 } } });
  clients.push(client);
  const page = (
    <QueryClientProvider client={client}>
      <AuthProvider>
        <MemoryRouter initialEntries={[entry]}>
          <Routes>
            <Route element={<RequireAuth />}>
              <Route path="/inspections" element={<AppShell />} />
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
    </QueryClientProvider>
  );
  return render(strict ? <StrictMode>{page}</StrictMode> : page);
}

beforeEach(() => {
  sessionStorage.clear();
  pdfJs.render.mockReset().mockReturnValue({ promise: Promise.resolve(), cancel: pdfJs.cancel });
  pdfJs.destroy.mockReset().mockResolvedValue(undefined);
  pdfJs.getPage.mockReset().mockResolvedValue({
    getViewport: () => ({ width: 450, height: 240 }),
    render: pdfJs.render,
  });
  pdfJs.getDocument.mockReset().mockImplementation(() => ({
    promise: Promise.resolve({ numPages: 3, getPage: pdfJs.getPage }),
    destroy: pdfJs.destroy,
  }));
  setLang('en');
  vi.stubGlobal(
    'URL',
    class extends URL {
      static createObjectURL = vi.fn(() => 'blob:report-en');
      static revokeObjectURL = vi.fn();
    },
  );
});
afterEach(() => {
  clients.splice(0).forEach((client) => client.clear());
  sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it('renders the API summary', async () => {
  mockReportApi();
  const view = renderReport();
  const document = await screen.findByRole('article', { name: 'Summary' });
  await screen.findByText(fixture.findings[0].comment);
  expect(within(document).getAllByText(fixture.sites[0].name)).toHaveLength(2);
  expect(view.container.querySelector('time')?.getAttribute('datetime')).toBe(
    fixture.inspection.created_at,
  );
  expect(view.container.querySelector('time')?.textContent).toContain('21 October 2026');
  expect(screen.getByText(fixture.findings[0].scale)).toBeTruthy();
  expect(screen.getByText(/Scale not set/)).toBeTruthy();
  const critical = screen.getByRole('region', { name: 'Critical' });
  expect(within(critical).getByText('Findings: 1')).toBeTruthy();
  expect(within(critical).getByText(fixture.findings[0].comment)).toBeTruthy();
  expect(within(critical).getByText('Confirmed')).toBeTruthy();
  expect(within(critical).getByText('Alan Bekov, confirmed')).toBeTruthy();
  expect(within(critical).getByText('Dana Sadyk, rejected')).toBeTruthy();
  const watch = screen.getByRole('region', { name: 'Watch' });
  expect(within(watch).getByText('Findings: 2')).toBeTruthy();
  expect(within(watch).getByText(fixture.findings[1].comment)).toBeTruthy();
  expect(within(watch).getByText('Rejected')).toBeTruthy();
  expect(within(watch).getByText('Dana Sadyk, rejected')).toBeTruthy();
  expect(within(watch).getByText(fixture.findings[2].comment)).toBeTruthy();
  expect(within(watch).getByText('Proposed')).toBeTruthy();
  expect(within(watch).getByText('No decision yet')).toBeTruthy();
  expect(screen.queryByText(/confidence/i)).toBeNull();
});

it('stays busy until the first page is painted', async () => {
  mockReportApi();
  vi.mocked(URL.createObjectURL).mockImplementationOnce(() => {
    expect(pdfRegion().getAttribute('aria-busy')).toBe('true');
    expect(document.querySelector('iframe')).toBeNull();
    expect(missingDownload()).toBeNull();
    return 'blob:report-en';
  });
  renderReport();
  const download = await findDownload();
  await screen.findByText('1 of 3 pages');
  expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  expect(pdfRegion().getAttribute('aria-busy')).toBe('false');
  expect(previewCanvas().tagName).toBe('CANVAS');
  expect(download.getAttribute('href')).toBe('blob:report-en');
  expect(download.getAttribute('download')).toBe('report-37-en.pdf');
  expect(missingAlert()).toBeNull();
});

it('opens Report from the loaded list', async () => {
  const { promise: pending, resolve: release } = deferred<void>();
  const fetch = mockReportApi();
  const original = fetch.getMockImplementation()!;
  fetch.mockImplementation(async (input) => {
    if (String(input) === '/api/inspections') await pending;
    return original(input);
  });
  renderReport('37', true, '/inspections');
  const list = screen.getByRole('region', { name: 'Inspections' });
  expect(list.getAttribute('aria-busy')).toBe('true');
  expect(screen.queryByRole('link', { name: 'Report' })).toBeNull();
  await act(async () => release());
  const report = await within(list).findByRole('link', { name: 'Report' });
  expect(list.getAttribute('aria-busy')).toBe('false');
  expect(report.getAttribute('href')).toBe('/inspections/37/report');
  expect(within(list).getByText('Tank T-104, north wall')).toBeTruthy();
  expect(missingAlert()).toBeNull();
  fireEvent.click(report);
  const download = await findDownload();
  expect(download.getAttribute('download')).toBe('report-37-en.pdf');
  await screen.findByText('1 of 3 pages');
  expect(pdfRegion().getAttribute('aria-busy')).toBe('false');
  expect(previewCanvas().tagName).toBe('CANVAS');
  expect(screen.getByTitle(fixture.hash).textContent).toBe('bf98d56a3f72…7263bc26');
  expect(languageChoice()).toBeTruthy();
  expect(screen.getAllByText('Tank T-104, north wall')).toHaveLength(2);
  expect(missingAlert()).toBeNull();
});

it('handles absent coverage', async () => {
  expect(fixture.coverage).toBeNull();
  mockReportApi();
  renderReport();
  expect(await screen.findByText('Coverage not computed')).toBeTruthy();
});

it.each([
  ['en', 'Inspected and clear: 61.5 %. Findings: 23.1 %. Not seen: 15.4 %.'],
  ['ru', 'Осмотрено без находок: 61.5 %. С находками: 23.1 %. Не осмотрено: 15.4 %.'],
  ['kk', 'Ақаусыз тексерілген: 61.5 %. Ақаулар бар: 23.1 %. Қаралмаған: 15.4 %.'],
] as const)('renders API coverage in %s', async (lang, line) => {
  setLang(lang);
  const fetch = mockReportApi(undefined, {
    '/api/inspections/37/coverage': () => Response.json(coverageFixture),
  });
  renderReport();
  expect(await screen.findByText(line)).toBeTruthy();
  expect(screen.getByText(/north-wall-upper/)).toBeTruthy();
  expect(screen.queryByText(/north-wall-lower/)).toBeNull();
  expect(fetch).toHaveBeenCalledWith(
    '/api/inspections/37/coverage',
    expect.objectContaining({ headers: { Authorization: 'Bearer test-report-session' } }),
  );
});

it('handles uncomputed coverage', async () => {
  mockReportApi(undefined, {
    '/api/inspections/37/coverage': () =>
      Response.json({ computed: false, totals: {}, regions: [] }),
  });
  renderReport();
  expect(await screen.findByText('Coverage not computed')).toBeTruthy();
  expect(screen.queryByText(/Inspected and clear/)).toBeNull();
});

it('renders both severities, decisions and coverage', async () => {
  mockReportApi(undefined, {
    '/api/inspections/37/coverage': () => Response.json(coverageFixture),
    '/api/inspections/37/findings': () => Response.json([fixture.findings[0], fixture.findings[1]]),
  });
  renderReport();
  await screen.findByText('Report is ready.');
  expect(screen.getAllByText('Tank T-104, north wall')).toHaveLength(2);
  expect(screen.getByText(/21 October 2026/)).toBeTruthy();
  const critical = screen.getByRole('region', { name: 'Critical' });
  expect(within(critical).getByText('Findings: 1')).toBeTruthy();
  expect(within(critical).getByText('Confirmed')).toBeTruthy();
  expect(within(critical).getByText('Alan Bekov, confirmed')).toBeTruthy();
  const watch = screen.getByRole('region', { name: 'Watch' });
  expect(within(watch).getByText('Findings: 1')).toBeTruthy();
  expect(within(watch).getByText('Rejected')).toBeTruthy();
  expect(within(watch).getByText('Dana Sadyk, rejected')).toBeTruthy();
  expect(
    screen.getByText('Inspected and clear: 61.5 %. Findings: 23.1 %. Not seen: 15.4 %.'),
  ).toBeTruthy();
});

it('shows reconstruction failure and clears busy', async () => {
  mockReportApi(() =>
    Response.json({ detail: 'Report generation failed: no reconstruction' }, { status: 409 }),
  );
  renderReport();
  expect(await screen.findByText('Report generation failed: no reconstruction')).toBeTruthy();
  expect(pdfRegion().getAttribute('aria-busy')).toBe('false');
  expect(missingPreview()).toBeNull();
});

it('shares the PDF Blob and hash', async () => {
  const fetch = mockReportApi();
  const view = renderReport();
  const download = await findDownload();
  const frame = previewCanvas();
  expect(frame.tagName).toBe('CANVAS');
  expect(view.container.querySelector('iframe, embed, object')).toBeNull();
  expect(await screen.findByText('1 of 3 pages')).toBeTruthy();
  expect(pdfJs.getPage).toHaveBeenCalledWith(1);
  expect(pdfJs.render).toHaveBeenCalledWith(expect.objectContaining({ canvas: frame }));
  expect(screen.getByRole('article').hasAttribute('data-theme')).toBe(false);
  const hash = screen.getByTitle(fixture.hash);
  expect(hash.textContent).toBe('bf98d56a3f72…7263bc26');
  expect(hash.getAttribute('title')).toBe(fixture.hash);
  expect(hash.className).toBe('qv-mono');
  const createdBlob = vi.mocked(URL.createObjectURL).mock.calls[0][0] as Blob;
  expect(await createdBlob.text()).toBe(fixture.pdf);
  expect(download.tagName).toBe('A');
  expect(download.getAttribute('href')).toBe('blob:report-en');
  expect(download.getAttribute('download')).toBe('report-37-en.pdf');
  expect(download.closest('.report-download')?.contains(hash)).toBe(true);
  expect(download.isConnected).toBe(true);
  expect(pdfCalls(fetch, 'report.pdf')).toHaveLength(1);
  view.unmount();
  expect(URL.revokeObjectURL).toHaveBeenCalledExactlyOnceWith('blob:report-en');
});

it('replaces and revokes the Blob on language change', async () => {
  const fetch = mockReportApi();
  vi.mocked(URL.createObjectURL)
    .mockReturnValueOnce('blob:report-en')
    .mockReturnValueOnce('blob:report-kk');
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  const view = renderReport();
  await waitFor(() => expect(pdfRegion().getAttribute('aria-busy')).toBe('false'));
  const { promise: pending, resolve: finish } = deferred<Response>();
  fetch.mockImplementationOnce(() => pending);
  fireEvent.click(screen.getByRole('button', { name: 'Қазақша' }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith('/api/inspections/37/report.pdf?lang=kk', expect.anything()),
  );
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:report-en');
  expect(missingPreview()).toBeNull();
  const unavailableDownload = downloadButton();
  expect((unavailableDownload as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(unavailableDownload);
  expect(click).not.toHaveBeenCalled();
  expect(screen.queryByTitle(fixture.hash)).toBeNull();
  expect(statusText()).toBe('Generating report…');
  await act(async () => finish(fixturePdf()));
  await waitFor(() => expect(pdfRegion().getAttribute('aria-busy')).toBe('false'));
  expect(previewCanvas().tagName).toBe('CANVAS');
  const link = downloadLink() as HTMLAnchorElement;
  expect(link.getAttribute('href')).toBe('blob:report-kk');
  expect(link.download).toBe('report-37-kk.pdf');
  view.unmount();
  expect(vi.mocked(URL.revokeObjectURL).mock.calls).toEqual([
    ['blob:report-en'],
    ['blob:report-kk'],
  ]);
});

it('preserves coverage prose', async () => {
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

it('transitions from generating to ready', async () => {
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  const { promise: pending, resolve: finish } = deferred<Response>();
  const fetch = mockReportApi(() => pending);
  renderReport();
  expect(statusText()).toBe('Generating report…');
  expect(missingPreview()).toBeNull();
  const unavailableDownload = downloadButton();
  expect((unavailableDownload as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(unavailableDownload);
  expect(click).not.toHaveBeenCalled();
  expect(screen.queryByTitle(fixture.hash)).toBeNull();
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith('/api/inspections/37/report.pdf?lang=en', expect.anything()),
  );
  expect(statusText()).toBe('Generating report…');
  expect(pdfRegion().getAttribute('aria-busy')).toBe('true');
  await act(async () => finish(fixturePdf()));
  expect(await screen.findByText('Report is ready.')).toBeTruthy();
  expect(pdfRegion().getAttribute('aria-busy')).toBe('false');
  expect(missingAlert()).toBeNull();
  const download = await findDownload();
  expect(download.getAttribute('href')).toBe('blob:report-en');
  expect(unavailableDownload.isConnected).toBe(false);
});

it('keeps HTTP 202 generating', async () => {
  const fetch = mockReportApi(() => new Response(null, { status: 202 }));
  renderReport();
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith('/api/inspections/37/report.pdf?lang=en', expect.anything()),
  );
  expect(statusText()).toBe('Generating report…');
  expect(screen.queryByText('Report is ready.')).toBeNull();
});

it('shows API failure beside the summary', async () => {
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  mockReportApi(() => Response.json(fixture.failure, { status: 500 }));
  renderReport();
  const alert = await findAlert();
  expect(within(alert).getByRole('heading').textContent).toBe('Could not generate report');
  expect(within(alert).getByText(fixture.failure.detail)).toBeTruthy();
  expect(screen.getByText(fixture.findings[0].comment)).toBeTruthy();
  expect(missingPreview()).toBeNull();
  const unavailableDownload = downloadButton();
  expect((unavailableDownload as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(unavailableDownload);
  expect(click).not.toHaveBeenCalled();
  expect(screen.queryByTitle(fixture.hash)).toBeNull();
});

it.each<[Lang, string, string]>([
  ['en', 'Try again', 'Report is ready.'],
  ['ru', 'Повторить попытку', 'Отчёт готов.'],
  ['kk', 'Қайталап көру', 'Есеп дайын.'],
])('retries PDF generation in %s', async (lang, retry, ready) => {
  setLang(lang);
  const { promise: pending, resolve: finish } = deferred<Response>();
  const response = vi
    .fn<() => Response | Promise<Response>>()
    .mockImplementationOnce(() => Response.json(fixture.failure, { status: 503 }))
    .mockImplementationOnce(() => pending);
  const fetch = mockReportApi(response);
  renderReport();
  const alert = await findAlert();
  expect(alert.textContent).toContain(fixture.failure.detail);
  expect(document.querySelector('.report-state')?.getAttribute('aria-busy')).toBe('false');
  fireEvent.click(within(alert).getByRole('button', { name: retry }));
  await waitFor(() => expect(response).toHaveBeenCalledTimes(2));
  expect(document.querySelector('.report-state')?.getAttribute('aria-busy')).toBe('true');
  expect(missingAlert()).toBeNull();
  expect(screen.queryByRole('link', { name: /PDF/ })).toBeNull();
  expect(document.querySelector('iframe')).toBeNull();
  await act(async () => finish(fixturePdf()));
  expect(await screen.findByText(ready)).toBeTruthy();
  const download = await screen.findByRole('link', { name: /PDF/ });
  expect(download.getAttribute('download')).toBe(`report-37-${lang}.pdf`);
  expect(screen.getByTitle(fixture.hash)).toBeTruthy();
  expect(document.querySelector('.report-state')?.getAttribute('aria-busy')).toBe('false');
  expect(pdfCalls(fetch)).toHaveLength(2);
  expect(fetch.mock.calls.filter(([path]) => String(path) === '/api/inspections/37')).toHaveLength(
    1,
  );
});

it('retries summary before PDF', async () => {
  const fetch = mockReportApi();
  fetch.mockImplementationOnce(async () => Response.json(fixture.failure, { status: 503 }));
  renderReport();
  const alert = await findAlert();
  expect(alert.textContent).toContain(fixture.failure.detail);
  expect(screen.queryByText(fixture.findings[0].comment)).toBeNull();
  expect(pdfCalls(fetch)).toHaveLength(0);
  fireEvent.click(within(alert).getByRole('button', { name: 'Try again' }));
  expect(await screen.findByText('Report is ready.')).toBeTruthy();
  expect(await findDownload()).toBeTruthy();
  expect(screen.getByText(fixture.findings[0].comment)).toBeTruthy();
  expect(missingAlert()).toBeNull();
  expect(fetch.mock.calls.filter(([path]) => String(path) === '/api/inspections/37')).toHaveLength(
    2,
  );
  expect(pdfCalls(fetch)).toHaveLength(1);
});

it('switches PDF to kk independently of UI', async () => {
  const fetch = mockReportApi();
  renderReport();
  await screen.findByText('Report is ready.');
  const languages = languageChoice();
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
    within(languageChoice()).getByRole('button', { name: 'Қазақша' }).getAttribute('aria-pressed'),
  ).toBe('true');
  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Inspection report');
});

it.each([
  ['ru', 'Русский'],
  ['en', 'English'],
] as const)('switches PDF from kk to %s', async (lang, label) => {
  setLang('kk');
  const fetch = mockReportApi();
  renderReport();
  await screen.findByText('Есеп дайын.');
  fireEvent.click(screen.getByRole('button', { name: label }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      `/api/inspections/37/report.pdf?lang=${lang}`,
      expect.anything(),
    ),
  );
  expect(screen.getByRole('button', { name: label }).getAttribute('aria-pressed')).toBe('true');
  expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Инспекция есебі');
});

it('ignores a superseded PDF response', async () => {
  const { promise: pending, resolve: finish } = deferred<Response>();
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
  expect(screen.queryByText('Report is ready.')).toBeNull();
});

it.each<[Lang, string, string]>([
  ['ru', 'Отчёт об инспекции', 'Покрытие не рассчитано'],
  ['kk', 'Инспекция есебі', 'Қамту есептелмеген'],
  ['en', 'Inspection report', 'Coverage not computed'],
])('defaults PDF to UI language %s', async (lang, title, coverage) => {
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
});

it.each(['demo', 'notre-dame'])('resolves report alias %s', async (id) => {
  const fetch = mockReportApi();
  renderReport(id);
  await screen.findByText('Report is ready.');
  expect(screen.getAllByText(fixture.sites[0].name)).toHaveLength(2);
  expect(languageChoice()).toBeTruthy();
  expect(missingAlert()).toBeNull();
  expect(fetch).toHaveBeenCalledWith('/api/inspections/37/report.pdf?lang=en', expect.anything());
});

it.each(['999', 'unknown'])('shows unknown inspection %s', async (id) => {
  mockReportApi();
  renderReport(id);
  expect((await findAlert()).textContent).toContain(fixture.notFound.detail);
  expect(languageChoice()).toBeTruthy();
});

it('requires authentication', () => {
  const fetch = mockReportApi();
  renderReport('37', false);
  expect(screen.getByRole('button', { name: 'Sign in' })).toBeTruthy();
  expect(fetch).not.toHaveBeenCalled();
  expect(screen.queryByRole('article')).toBeNull();
});

const reportLabels = [
  {
    lang: 'ru',
    critical: 'Критично',
    watch: 'Наблюдать',
    finding: 'Находка',
    status: 'Статус',
    corrosion: 'Коррозия',
    crack: 'Трещина',
    coating: 'Покрытие',
    other: 'Другое',
    proposed: 'Предложено',
    confirmed: 'Подтверждено',
    rejected: 'Отклонено',
    lowEvidence: 'Нужно больше свидетельств',
    empty: 'Находок нет',
    ready: 'Отчёт готов.',
    criticalCount: 'Находки: 1',
    watchCount: 'Находки: 2',
  },
  {
    lang: 'kk',
    critical: 'Сындарлы',
    watch: 'Бақылау',
    finding: 'Ақау',
    status: 'Күйі',
    corrosion: 'Тоттану',
    crack: 'Жарық',
    coating: 'Жабын',
    other: 'Басқа',
    proposed: 'Ұсынылған',
    confirmed: 'Расталған',
    rejected: 'Қабылданбаған',
    lowEvidence: 'Қосымша дәлел қажет',
    empty: 'Ақаулар жоқ',
    ready: 'Есеп дайын.',
    criticalCount: 'Ақаулар: 1',
    watchCount: 'Ақаулар: 2',
  },
] as const;

it.each(reportLabels)('translates finding tables in $lang', async (labels) => {
  setLang(labels.lang);
  mockReportApi();
  renderReport();
  await screen.findByText(labels.ready);
  expect(screen.getAllByRole('columnheader', { name: labels.finding })).toHaveLength(2);
  expect(screen.getAllByRole('columnheader', { name: labels.status })).toHaveLength(2);
  const critical = screen.getByRole('region', { name: labels.critical });
  expect(within(critical).getByText(labels.criticalCount)).toBeTruthy();
  expect(within(critical).getByText(labels.corrosion)).toBeTruthy();
  expect(within(critical).getByText(labels.confirmed)).toBeTruthy();
  const watch = screen.getByRole('region', { name: labels.watch });
  expect(within(watch).getByText(labels.watchCount)).toBeTruthy();
  for (const text of [labels.crack, labels.coating, labels.proposed, labels.rejected])
    expect(within(watch).getByText(text)).toBeTruthy();
  for (const text of [
    'Critical',
    'Watch',
    'Finding',
    'Status',
    'Proposed',
    'Confirmed',
    'Rejected',
  ])
    expect(screen.queryByText(text, { exact: true })).toBeNull();
});

it.each(reportLabels)('translates empty/other/low-evidence in $lang', async (labels) => {
  setLang(labels.lang);
  mockReportApi(undefined, {
    '/api/inspections/37/findings': () =>
      Response.json([{ ...fixture.findings[2], class: 'other', status: 'low_evidence' }]),
  });
  renderReport();
  await screen.findByText(labels.ready);
  const watch = screen.getByRole('region', { name: labels.watch });
  expect(within(watch).getByText(labels.other)).toBeTruthy();
  expect(within(watch).getByText(labels.lowEvidence)).toBeTruthy();
  expect(
    within(screen.getByRole('region', { name: labels.critical })).getByText(labels.empty),
  ).toBeTruthy();
  expect(screen.queryByText('Other', { exact: true })).toBeNull();
  expect(screen.queryByText('No findings', { exact: true })).toBeNull();
});

it('spells out the Kazakh month', async () => {
  setLang('kk');
  mockReportApi();
  const view = renderReport();
  await screen.findByText('Есеп дайын.');
  const date = view.container.querySelector('time');
  expect(date?.getAttribute('datetime')).toBe(fixture.inspection.created_at);
  expect(date?.textContent).toContain('2026 жылғы');
  expect(date?.textContent).toContain('қазан');
  expect(date?.textContent).not.toMatch(/\bM\d{1,2}\b/);
});

it('keeps languages in toolbar and state before summary', async () => {
  const fetch = mockReportApi();
  renderReport();
  const ready = await screen.findByText('Report is ready.');
  const main = screen.getByRole('main');
  const topbar = screen.getByRole('banner');
  const languages = within(topbar).getByRole('group', { name: 'PDF language' });
  expect(await within(topbar).findByRole('link', { name: 'Download PDF' })).toBeTruthy();
  expect(within(topbar).getByTitle(fixture.hash).textContent).toBe('bf98d56a3f72…7263bc26');
  const heading = within(main).getByRole('heading', { name: 'Summary' });
  expect(languages.compareDocumentPosition(heading)).toBe(4);
  expect(ready.compareDocumentPosition(heading)).toBe(4);
  fireEvent.click(within(languages).getByRole('button', { name: 'Қазақша' }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith('/api/inspections/37/report.pdf?lang=kk', expect.anything()),
  );
});

it('places preview before summary', async () => {
  mockReportApi();
  renderReport();
  const download = await findDownload();
  expect(download.getAttribute('download')).toBe('report-37-en.pdf');
  const preview = previewCanvas();
  const summary = screen.getByRole('article', { name: 'Summary' });
  expect(preview.compareDocumentPosition(summary)).toBe(4);
  expect(pdfRegion().contains(preview)).toBe(true);
  expect(screen.getByRole('main').contains(preview)).toBe(true);
});

it('keeps busy while pdf.js is painting and shows the document page count afterwards', async () => {
  const painting = deferred<void>();
  pdfJs.render.mockReturnValueOnce({ promise: painting.promise, cancel: pdfJs.cancel });
  mockReportApi();
  const view = renderReport();
  await waitFor(() => expect(pdfJs.render).toHaveBeenCalledTimes(1));
  expect(pdfRegion().getAttribute('aria-busy')).toBe('true');
  expect(screen.queryByText('Report is ready.')).toBeNull();
  expect(screen.queryByText('1 of 3 pages')).toBeNull();
  expect(view.container.querySelector('iframe')).toBeNull();
  await act(async () => painting.resolve());
  expect(await screen.findByText('1 of 3 pages')).toBeTruthy();
  expect(pdfRegion().getAttribute('aria-busy')).toBe('false');
  expect(new TextDecoder().decode(pdfJs.getDocument.mock.calls[0][0].data)).toBe(fixture.pdf);
});

it.each(['open', 'render'])(
  'shows a plain preview failure when pdf.js cannot %s, keeping the PDF downloadable',
  async (step) => {
    if (step === 'open')
      pdfJs.getDocument.mockImplementationOnce(() => ({
        promise: Promise.reject(new Error('pdf.js internal diagnostic')),
        destroy: pdfJs.destroy,
      }));
    else
      pdfJs.render.mockImplementationOnce(() => ({
        promise: Promise.reject(new Error('pdf.js internal diagnostic')),
        cancel: pdfJs.cancel,
      }));
    mockReportApi();
    const view = renderReport();
    const alert = await findAlert();
    expect(alert.textContent).toContain('Could not preview report');
    expect(alert.textContent).toContain(
      'The PDF preview could not be displayed. You can still download the PDF.',
    );
    expect(alert.textContent).not.toContain('pdf.js internal diagnostic');
    expect(pdfRegion().getAttribute('aria-busy')).toBe('false');
    expect(screen.queryByText('Report is ready.')).toBeNull();
    expect(downloadLink().getAttribute('href')).toBe('blob:report-en');
    expect(screen.getByTitle(fixture.hash)).toBeTruthy();
    expect(view.container.querySelector('iframe')).toBeNull();
    fireEvent.click(within(alert).getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('1 of 3 pages')).toBeTruthy();
  },
);

it.each(['fetch', 'paint'])('clears busy before 30 seconds when %s hangs', async (step) => {
  if (step === 'paint')
    pdfJs.render.mockReturnValueOnce({ promise: new Promise(() => {}), cancel: pdfJs.cancel });
  mockReportApi(step === 'fetch' ? () => new Promise(() => {}) : undefined);
  const timer = vi.spyOn(window, 'setTimeout');
  renderReport();
  if (step === 'paint') await waitFor(() => expect(pdfJs.render).toHaveBeenCalled());
  const deadline = timer.mock.calls.find(([, duration]) => duration === 28_000)?.[0];
  expect(deadline).toBeTypeOf('function');
  await act(async () => (deadline as () => void)());
  expect((await findAlert()).textContent).toContain('The report is taking too long. Try again.');
  expect(pdfRegion().getAttribute('aria-busy')).toBe('false');
  if (step === 'paint') expect(downloadLink().getAttribute('href')).toBe('blob:report-en');
});

it('cancels rendering and destroys the worker when leaving the page', async () => {
  pdfJs.render.mockReturnValueOnce({ promise: new Promise(() => {}), cancel: pdfJs.cancel });
  pdfJs.cancel.mockClear();
  mockReportApi();
  const view = renderReport();
  await waitFor(() => expect(pdfJs.render).toHaveBeenCalledTimes(1));
  view.unmount();
  expect(pdfJs.cancel).toHaveBeenCalledTimes(1);
  expect(pdfJs.destroy).toHaveBeenCalledTimes(1);
});

it('fetches a language PDF only once under the app StrictMode lifecycle', async () => {
  const kazakh = deferred<Response>();
  const fetch = mockReportApi();
  const original = fetch.getMockImplementation()!;
  fetch.mockImplementation(async (input) =>
    String(input) === '/api/inspections/37/report.pdf?lang=kk' ? kazakh.promise : original(input),
  );
  renderReport('notre-dame', true, '/inspections/notre-dame/report', true);
  await screen.findByText('Report is ready.');
  fireEvent.click(screen.getByRole('button', { name: 'Қазақша' }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith('/api/inspections/37/report.pdf?lang=kk', expect.anything()),
  );
  expect(pdfCalls(fetch).map(([url]) => url)).toEqual([
    '/api/inspections/37/report.pdf?lang=en',
    '/api/inspections/37/report.pdf?lang=kk',
  ]);
  expect(pdfRegion().getAttribute('aria-busy')).toBe('true');
  await act(async () => kazakh.resolve(fixturePdf()));
  await screen.findByText('Report is ready.');
  expect(screen.getByText('1 of 3 pages')).toBeTruthy();
  expect(downloadLink().getAttribute('download')).toBe('report-notre-dame-kk.pdf');
  expect(pdfCalls(fetch).map(([url]) => url)).toEqual([
    '/api/inspections/37/report.pdf?lang=en',
    '/api/inspections/37/report.pdf?lang=kk',
  ]);
});
