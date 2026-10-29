import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { IncomingMessage, ServerResponse } from 'node:http';
import en from './src/i18n/en.json' with { type: 'json' };

/** Dev-only API for the same synthetic site as /viewer/demo. Never bundled in the client. */
export function devReport(req: IncomingMessage, res: ServerResponse, next: () => void) {
  if (req.method !== 'GET') return next();
  const url = new URL(req.url ?? '/', 'http://localhost');
  const json = (body: unknown, status = 200) => {
    res.statusCode = status;
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify(body));
  };
  if (url.pathname === '/api/inspections/demo')
    return json({
      id: 0,
      site_id: 0,
      mode: 'quick_scan',
      status: 'failed',
      run_id: 'demo',
      created_at: '2026-10-14T10:30:00+05:00',
    });
  if (url.pathname === '/api/sites') return json([{ id: 0, name: en['demo.siteName'] }]);
  if (url.pathname === '/api/inspections/0/findings') return json([]);
  if (url.pathname === '/api/inspections/0/coverage') return json({ computed: false });
  if (url.pathname !== '/api/inspections/0/report.pdf') return next();
  if (!['ru', 'kk', 'en'].includes(url.searchParams.get('lang') ?? ''))
    return json({ detail: 'Unsupported report language' }, 422);
  // Original project document, shared with the browser e2e. No fixture findings.
  const pdf = readFileSync(resolve('src/test/fixtures/report.pdf'));
  res.statusCode = 200;
  res.setHeader('Content-Type', 'application/pdf');
  res.setHeader('X-Report-SHA256', createHash('sha256').update(pdf).digest('hex'));
  res.end(pdf);
}
