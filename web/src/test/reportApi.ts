import { vi } from 'vitest';
import fixture from './fixtures/report-summary.json';

/** Mock the backend's individual JSON routes, rather than mocking summary assembly. */
export function mockReportApi(pdf: () => Response | Promise<Response> = fixturePdf) {
  const fetch = vi.fn(async (input: string | URL | Request) => {
    const path = String(input);
    if (path.startsWith('/api/inspections/37/report.pdf?lang=')) return pdf();
    if (path === '/api/inspections/notre-dame' || path === '/api/inspections/demo')
      return Response.json(fixture.validation, { status: 422 });
    if (path === '/api/inspections/37') return Response.json(fixture.inspection);
    if (path === '/api/inspections') return Response.json([fixture.inspection]);
    if (path === '/api/inspections/37/runs') return Response.json(fixture.runs);
    if (path === '/api/sites') return Response.json(fixture.sites);
    if (path === '/api/inspections/37/findings') return Response.json(fixture.findings);
    const decisions: Record<string, unknown> = fixture.decisions;
    const match = /^\/api\/findings\/(\d+)\/decisions$/.exec(path);
    if (match && match[1] in decisions) return Response.json(decisions[match[1]]);
    return Response.json(fixture.notFound, { status: 404 });
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

export function fixturePdf() {
  return new Response(fixture.pdf, {
    headers: { 'Content-Type': 'application/pdf', 'X-Report-SHA256': fixture.hash },
  });
}
