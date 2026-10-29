import fixture from './fixtures/report-summary.json' with { type: 'json' };

const routes: Record<string, unknown> = {
  '/api/inspections/37': fixture.inspection,
  '/api/inspections': [fixture.inspection],
  '/api/inspections/37/runs': fixture.runs,
  '/api/sites': fixture.sites,
  '/api/inspections/37/findings': fixture.findings,
  ...Object.fromEntries(
    Object.entries(fixture.decisions).map(([id, data]) => [`/api/findings/${id}/decisions`, data]),
  ),
};

export function reportJson(path: string) {
  if (path === '/api/inspections/demo' || path === '/api/inspections/notre-dame')
    return { status: 422, json: fixture.validation };
  return path in routes
    ? { status: 200, json: routes[path] }
    : { status: 404, json: fixture.notFound };
}
