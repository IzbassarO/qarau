import { afterEach, expect, it, vi } from 'vitest';
import { fetchReportPdf, fetchReportSummary, reportInspectionForRun } from './report';
import fixture from '../test/fixtures/report-summary.json';
import report from '../test/fixtures/report.json';
import coverageFixture from '../test/fixtures/report-coverage.json';

function mockReportApi(
  pdf: () => Response | Promise<Response> = () =>
    new Response(report.body, { status: report.status, headers: report.headers }),
  overrides: Record<string, () => Response> = {},
) {
  const routes: Record<string, unknown> = {
    '/api/inspections/37': fixture.inspection,
    '/api/inspections': [fixture.inspection],
    '/api/inspections/37/runs': fixture.runs,
    '/api/sites': fixture.sites,
    '/api/inspections/37/findings': fixture.findings,
    ...Object.fromEntries(
      Object.entries(fixture.decisions).map(([id, data]) => [
        `/api/findings/${id}/decisions`,
        data,
      ]),
    ),
  };
  const fetch = vi.fn(async (input: string | URL | Request) => {
    const path = String(input);
    if (path in overrides) return overrides[path]();
    if (path.startsWith('/api/inspections/37/report.pdf?lang=')) return pdf();
    if (path === '/api/inspections/demo' || path === '/api/inspections/notre-dame')
      return Response.json(fixture.validation, { status: 422 });
    return path in routes
      ? Response.json(routes[path])
      : Response.json(fixture.notFound, { status: 404 });
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

it('fetches authenticated PDF and header hash', async () => {
  sessionStorage.setItem('qarau.auth.session', 'Bearer test-report-session');
  const fetch = mockReportApi();
  const result = await fetchReportPdf(37, 'ru');
  expect(result.state).toBe('ready');
  if (result.state !== 'ready') throw new Error('Expected ready');
  expect(result.hash).toBe('bf98d56a3f72c5e19345b6fe682380d399174770df752e8031c9fa427263bc26');
  expect(result.hash).toBe(report.headers['X-Report-SHA256']);
  expect(result.blob).toBeInstanceOf(Blob);
  expect(result.blob.type).toBe('application/pdf');
  expect(await result.blob.text()).toBe(report.body);
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/inspections/37/report.pdf?lang=ru', {
    signal: undefined,
    headers: { Authorization: 'Bearer test-report-session' },
  });
});

it('handles HTTP 202', async () => {
  mockReportApi(() => new Response(null, { status: 202 }));
  await expect(fetchReportPdf(37, 'en')).resolves.toEqual({ state: 'generating' });
});

it.each([400, 401, 403, 404, 409, 500, 503])('preserves HTTP %i detail/status', async (status) => {
  mockReportApi(() => Response.json(fixture.failure, { status }));
  await expect(fetchReportPdf(37, 'en')).rejects.toMatchObject({
    message: fixture.failure.detail,
    status,
  });
});

it('preserves validation and plain errors', async () => {
  mockReportApi(() => Response.json(fixture.validation, { status: 422 }));
  await expect(fetchReportPdf(37, 'en')).rejects.toMatchObject({
    message: fixture.validation.detail[0].msg,
    status: 422,
  });
  mockReportApi(() => new Response('Internal Server Error', { status: 500 }));
  await expect(fetchReportPdf(37, 'en')).rejects.toMatchObject({
    message: 'Internal Server Error',
    status: 500,
  });
});

it('rejects HTML and missing PDF hash', async () => {
  mockReportApi(
    () => new Response('<html>App</html>', { headers: { 'Content-Type': 'text/html' } }),
  );
  await expect(fetchReportPdf(37, 'en')).resolves.toEqual({ state: 'failed', message: '' });
  mockReportApi(
    () => new Response(fixture.pdf, { headers: { 'Content-Type': 'application/pdf' } }),
  );
  await expect(fetchReportPdf(37, 'en')).resolves.toEqual({ state: 'failed', message: '' });
});

it('handles network errors and aborts', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
  await expect(fetchReportPdf(37, 'en')).resolves.toEqual({ state: 'failed', message: '' });
  const controller = new AbortController();
  controller.abort();
  await expect(fetchReportPdf(37, 'en', controller.signal)).rejects.toThrow('Failed to fetch');
});

it('assembles summary from API routes', async () => {
  const fetch = mockReportApi();
  const summary = await fetchReportSummary('37');
  expect(summary.inspectionId).toBe(37);
  expect(summary.object).toBe(fixture.sites[0].name);
  expect(summary.date).toBe(fixture.inspection.created_at);
  expect(summary.coverage).toBeNull();
  expect(summary.findings[0]).toMatchObject(fixture.findings[0]);
  expect(summary.findings[0].decisions).toEqual(fixture.decisions['71']);
  expect(summary.findings[1]).toMatchObject(fixture.findings[1]);
  expect(summary.findings[1].decisions).toEqual(fixture.decisions['72']);
  expect(summary.findings[2].decisions).toEqual([]);
  expect(fetch.mock.calls.map(([path]) => path)).toEqual([
    '/api/inspections/37',
    '/api/sites',
    '/api/inspections/37/findings',
    '/api/inspections/37/coverage',
    '/api/findings/71/decisions',
    '/api/findings/72/decisions',
    '/api/findings/73/decisions',
  ]);
});

it.each(['demo', 'notre-dame'])('resolves report alias %s', async (id) => {
  const fetch = mockReportApi();
  const summary = await fetchReportSummary(id);
  expect(summary.inspectionId).toBe(37);
  expect(summary.object).toBe(fixture.sites[0].name);
  expect(fetch).toHaveBeenCalledWith('/api/inspections/37', expect.anything());
});

it('preserves API coverage', async () => {
  mockReportApi(undefined, {
    '/api/inspections/37/coverage': () => Response.json(coverageFixture),
  });
  expect((await fetchReportSummary('37')).coverage).toEqual(coverageFixture);
});

it.each([403, 500])('preserves coverage HTTP %i errors', async (status) => {
  mockReportApi(undefined, {
    '/api/inspections/37/coverage': () =>
      Response.json({ detail: 'Coverage data is unavailable' }, { status }),
  });
  await expect(fetchReportSummary('37')).rejects.toThrow('Coverage data is unavailable');
});

it.each(['notre-dame', 'notre-dame-p3e'])('resolves viewer %s', async (runId) => {
  mockReportApi();
  await expect(reportInspectionForRun(runId)).resolves.toBe(37);
});

it('uses explicit run association', async () => {
  const fetch = vi.fn().mockResolvedValue(
    Response.json([
      { id: 37, run_id: 'notre-dame-p3e' },
      { id: 38, run_id: 'another-run' },
    ]),
  );
  vi.stubGlobal('fetch', fetch);
  await expect(reportInspectionForRun('notre-dame-p3e')).resolves.toBe(37);
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/inspections', {
    signal: undefined,
    headers: {},
  });
});

it('handles unassociated viewers', async () => {
  mockReportApi();
  await expect(reportInspectionForRun('unknown')).resolves.toBeNull();
});

it('preserves unknown-inspection errors', async () => {
  mockReportApi();
  await expect(fetchReportSummary('unknown')).rejects.toMatchObject({ status: 404 });
});

it('preserves authorization/server errors', async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json(fixture.failure, { status: 403 }));
  vi.stubGlobal('fetch', fetch);
  await expect(fetchReportSummary('notre-dame')).rejects.toThrow(fixture.failure.detail);
  expect(fetch).toHaveBeenCalledTimes(1);
});

// Empty bodies and empty JSON are not computed coverage.
it.each([null, {}, { computed: false }, ''])('handles empty coverage %j', async (coverage) => {
  mockReportApi(undefined, {
    '/api/inspections/37/coverage': () =>
      coverage === '' ? new Response(null, { status: 204 }) : Response.json(coverage),
  });
  expect((await fetchReportSummary('37')).coverage).toBeNull();
});
