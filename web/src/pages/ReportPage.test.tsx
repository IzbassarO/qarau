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
function renderReport(id = '37', signedIn = true, entry = `/inspections/${id}/report`) {
  if (signedIn) sessionStorage.setItem('qarau.auth.session', 'Bearer test-report-session');
  const client = new QueryClient({ defaultOptions: { queries: { gcTime: 0 } } });
  clients.push(client);
  return render(
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
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
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

it('renders fixture object, date, scale, severity groups, API statuses and decision names', async () => {
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

it('keeps the PDF section busy until the Blob URL is attached to preview and download', async () => {
  mockReportApi();
  vi.mocked(URL.createObjectURL).mockImplementationOnce(() => {
    expect(screen.getByRole('region', { name: 'PDF preview' }).getAttribute('aria-busy')).toBe(
      'true',
    );
    expect(document.querySelector('iframe')).toBeNull();
    expect(screen.queryByRole('link', { name: 'Download PDF' })).toBeNull();
    return 'blob:report-en';
  });
  renderReport();
  const download = await screen.findByRole('link', { name: 'Download PDF' });
  expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('region', { name: 'PDF preview' }).getAttribute('aria-busy')).toBe(
    'false',
  );
  expect(screen.getByTitle('PDF preview').getAttribute('src')).toBe('blob:report-en');
  expect(download.getAttribute('href')).toBe('blob:report-en');
  expect(download.getAttribute('download')).toBe('report-37-en.pdf');
  expect(screen.queryByRole('alert')).toBeNull();
});

it('waits for the inspection list, then follows Report to a ready PDF without an error banner', async () => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
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
  expect(screen.queryByRole('alert')).toBeNull();
  fireEvent.click(report);
  const download = await screen.findByRole('link', { name: 'Download PDF' });
  expect(download.getAttribute('download')).toBe('report-37-en.pdf');
  expect(screen.getByRole('region', { name: 'PDF preview' }).getAttribute('aria-busy')).toBe(
    'false',
  );
  expect(screen.getByTitle('PDF preview').getAttribute('src')).toBe('blob:report-en');
  expect(screen.getByTitle(fixture.hash).textContent).toBe('bf98d56a3f72…7263bc26');
  expect(screen.getByRole('group', { name: 'PDF language' })).toBeTruthy();
  expect(screen.getAllByText('Tank T-104, north wall')).toHaveLength(2);
  expect(screen.queryByRole('alert')).toBeNull();
});

it('shows coverage not computed for the fixture’s null coverage', async () => {
  expect(fixture.coverage).toBeNull();
  mockReportApi();
  renderReport();
  expect(await screen.findByText('Coverage not computed')).toBeTruthy();
});

it('previews and downloads the same PDF Blob with the header hash and route filename', async () => {
  const fetch = mockReportApi();
  const view = renderReport();
  const download = await screen.findByRole('link', { name: 'Download PDF' });
  const frame = screen.getByTitle('PDF preview');
  expect(frame.tagName).toBe('IFRAME');
  expect(frame.getAttribute('src')).toBe('blob:report-en');
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
  expect(fetch.mock.calls.filter(([path]) => String(path).includes('report.pdf'))).toHaveLength(1);
  view.unmount();
  expect(URL.revokeObjectURL).toHaveBeenCalledExactlyOnceWith('blob:report-en');
});

it('revokes the old preview on language change, hides it while generating and downloads the new PDF', async () => {
  const fetch = mockReportApi();
  vi.mocked(URL.createObjectURL)
    .mockReturnValueOnce('blob:report-en')
    .mockReturnValueOnce('blob:report-kk');
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  const view = renderReport();
  await waitFor(() =>
    expect(screen.getByTitle('PDF preview').getAttribute('src')).toBe('blob:report-en'),
  );
  let finish!: (response: Response) => void;
  fetch.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Қазақша' }));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith('/api/inspections/37/report.pdf?lang=kk', expect.anything()),
  );
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:report-en');
  expect(screen.queryByTitle('PDF preview')).toBeNull();
  const unavailableDownload = screen.getByRole('button', { name: 'Download PDF' });
  expect((unavailableDownload as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(unavailableDownload);
  expect(click).not.toHaveBeenCalled();
  expect(screen.queryByTitle(fixture.hash)).toBeNull();
  expect(screen.getByRole('status').textContent).toBe('Generating report…');
  await act(async () => finish(fixturePdf()));
  await waitFor(() =>
    expect(screen.getByTitle('PDF preview').getAttribute('src')).toBe('blob:report-kk'),
  );
  const link = screen.getByRole('link', { name: 'Download PDF' }) as HTMLAnchorElement;
  expect(link.getAttribute('href')).toBe('blob:report-kk');
  expect(link.download).toBe('report-37-kk.pdf');
  view.unmount();
  expect(vi.mocked(URL.revokeObjectURL).mock.calls).toEqual([
    ['blob:report-en'],
    ['blob:report-kk'],
  ]);
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
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  let finish!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  const fetch = mockReportApi(() => pending);
  renderReport();
  expect(screen.getByRole('status').textContent).toBe('Generating report…');
  expect(screen.queryByTitle('PDF preview')).toBeNull();
  const unavailableDownload = screen.getByRole('button', { name: 'Download PDF' });
  expect((unavailableDownload as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(unavailableDownload);
  expect(click).not.toHaveBeenCalled();
  expect(screen.queryByTitle(fixture.hash)).toBeNull();
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith('/api/inspections/37/report.pdf?lang=en', expect.anything()),
  );
  expect(screen.getByRole('status').textContent).toBe('Generating report…');
  expect(screen.getByRole('region', { name: 'PDF preview' }).getAttribute('aria-busy')).toBe(
    'true',
  );
  await act(async () => finish(fixturePdf()));
  expect(await screen.findByText('Report is ready.')).toBeTruthy();
  expect(screen.getByRole('region', { name: 'PDF preview' }).getAttribute('aria-busy')).toBe(
    'false',
  );
  expect(screen.queryByRole('alert')).toBeNull();
  const download = await screen.findByRole('link', { name: 'Download PDF' });
  expect(download.getAttribute('href')).toBe('blob:report-en');
  expect(unavailableDownload.isConnected).toBe(false);
});

it('shows generating for an accepted response without displaying a ready state', async () => {
  const fetch = mockReportApi(() => new Response(null, { status: 202 }));
  renderReport();
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith('/api/inspections/37/report.pdf?lang=en', expect.anything()),
  );
  expect(screen.getByRole('status').textContent).toBe('Generating report…');
  expect(screen.queryByText('Report is ready.')).toBeNull();
});

it('shows the failed PDF state with the API’s words while keeping the summary', async () => {
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  mockReportApi(() => Response.json(fixture.failure, { status: 500 }));
  renderReport();
  const alert = await screen.findByRole('alert');
  expect(within(alert).getByRole('heading').textContent).toBe('Could not generate report');
  expect(within(alert).getByText(fixture.failure.detail)).toBeTruthy();
  expect(screen.getByText(fixture.findings[0].comment)).toBeTruthy();
  expect(screen.queryByTitle('PDF preview')).toBeNull();
  const unavailableDownload = screen.getByRole('button', { name: 'Download PDF' });
  expect((unavailableDownload as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(unavailableDownload);
  expect(click).not.toHaveBeenCalled();
  expect(screen.queryByTitle(fixture.hash)).toBeNull();
});

it.each<[Lang, string, string]>([
  ['en', 'Try again', 'Report is ready.'],
  ['ru', 'Повторить попытку', 'Отчёт готов.'],
  ['kk', 'Қайталап көру', 'Есеп дайын.'],
])(
  'retries a failed PDF in %s, showing generating until the new response is ready',
  async (lang, retry, ready) => {
    setLang(lang);
    let finish!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    const response = vi
      .fn<() => Response | Promise<Response>>()
      .mockImplementationOnce(() => Response.json(fixture.failure, { status: 503 }))
      .mockImplementationOnce(() => pending);
    const fetch = mockReportApi(response);
    renderReport();
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain(fixture.failure.detail);
    expect(document.querySelector('.report-state')?.getAttribute('aria-busy')).toBe('false');
    fireEvent.click(within(alert).getByRole('button', { name: retry }));
    await waitFor(() => expect(response).toHaveBeenCalledTimes(2));
    expect(document.querySelector('.report-state')?.getAttribute('aria-busy')).toBe('true');
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByRole('link', { name: /PDF/ })).toBeNull();
    expect(document.querySelector('iframe')).toBeNull();
    await act(async () => finish(fixturePdf()));
    expect(await screen.findByText(ready)).toBeTruthy();
    const download = await screen.findByRole('link', { name: /PDF/ });
    expect(download.getAttribute('download')).toBe(`report-37-${lang}.pdf`);
    expect(screen.getByTitle(fixture.hash)).toBeTruthy();
    expect(document.querySelector('.report-state')?.getAttribute('aria-busy')).toBe('false');
    expect(fetch.mock.calls.filter(([path]) => String(path).includes('/report.pdf'))).toHaveLength(
      2,
    );
    expect(
      fetch.mock.calls.filter(([path]) => String(path) === '/api/inspections/37'),
    ).toHaveLength(1);
  },
);

it('retries summary failure before requesting the PDF and clears the API error', async () => {
  const fetch = mockReportApi();
  fetch.mockImplementationOnce(async () => Response.json(fixture.failure, { status: 503 }));
  renderReport();
  const alert = await screen.findByRole('alert');
  expect(alert.textContent).toContain(fixture.failure.detail);
  expect(screen.queryByText(fixture.findings[0].comment)).toBeNull();
  expect(fetch.mock.calls.filter(([path]) => String(path).includes('/report.pdf'))).toHaveLength(0);
  fireEvent.click(within(alert).getByRole('button', { name: 'Try again' }));
  expect(await screen.findByText('Report is ready.')).toBeTruthy();
  expect(await screen.findByRole('link', { name: 'Download PDF' })).toBeTruthy();
  expect(screen.getByText(fixture.findings[0].comment)).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(fetch.mock.calls.filter(([path]) => String(path) === '/api/inspections/37')).toHaveLength(
    2,
  );
  expect(fetch.mock.calls.filter(([path]) => String(path).includes('/report.pdf'))).toHaveLength(1);
});

it('requests report.pdf?lang=kk when Қазақша is chosen and keeps the UI language', async () => {
  const fetch = mockReportApi();
  renderReport();
  await screen.findByText('Report is ready.');
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

it.each([
  ['ru', 'Русский'],
  ['en', 'English'],
] as const)(
  'requests lang=%s when the PDF language choice changes from Kazakh',
  async (lang, label) => {
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
  },
);

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
  expect(screen.queryByText('Report is ready.')).toBeNull();
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

it.each(['demo', 'notre-dame'])(
  'opens /inspections/%s/report with the API summary and language choice, without a banner',
  async (id) => {
    const fetch = mockReportApi();
    renderReport(id);
    await screen.findByText('Report is ready.');
    expect(screen.getAllByText(fixture.sites[0].name)).toHaveLength(2);
    expect(screen.getByRole('group', { name: 'PDF language' })).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(fetch).toHaveBeenCalledWith('/api/inspections/37/report.pdf?lang=en', expect.anything());
  },
);

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

it.each(reportLabels)(
  'translates finding groups, columns, classes and statuses in $lang',
  async (labels) => {
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
  },
);

it.each(reportLabels)(
  'translates Other, needs-evidence status and empty groups in $lang',
  async (labels) => {
    setLang(labels.lang);
    const fetch = mockReportApi();
    const original = fetch.getMockImplementation()!;
    fetch.mockImplementation(async (input) => {
      if (String(input) === '/api/inspections/37/findings')
        return Response.json([{ ...fixture.findings[2], class: 'other', status: 'low_evidence' }]);
      return original(input);
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
  },
);

it('renders an explicit Kazakh month on the page instead of a raw ICU month code', async () => {
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

it('places the working PDF language selector in the top bar and the ready state before the summary', async () => {
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

it('places the inline preview before the full summary so it can occupy the first viewport', async () => {
  mockReportApi();
  renderReport();
  const download = await screen.findByRole('link', { name: 'Download PDF' });
  expect(download.getAttribute('download')).toBe('report-37-en.pdf');
  const preview = screen.getByTitle('PDF preview');
  const summary = screen.getByRole('article', { name: 'Summary' });
  expect(preview.compareDocumentPosition(summary)).toBe(4);
  expect(screen.getByRole('region', { name: 'PDF preview' }).contains(preview)).toBe(true);
  expect(screen.getByRole('main').contains(preview)).toBe(true);
});
