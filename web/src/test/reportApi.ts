import { vi } from 'vitest';
import fixture from './fixtures/report-summary.json';
import { reportJson } from './reportRoutes';

/** Mock API routes, not summary assembly. */
export function mockReportApi(
  pdf: () => Response | Promise<Response> = fixturePdf,
  routes: Record<string, () => Response> = {},
) {
  const fetch = vi.fn(async (input: string | URL | Request) => {
    const path = String(input);
    if (path in routes) return routes[path]();
    if (path.startsWith('/api/inspections/37/report.pdf?lang=')) return pdf();
    const { status, json } = reportJson(path);
    return Response.json(json, { status });
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

export function fixturePdf() {
  return new Response(fixture.pdf, {
    headers: { 'Content-Type': 'application/pdf', 'X-Report-SHA256': fixture.hash },
  });
}
