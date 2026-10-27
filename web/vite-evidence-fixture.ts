import type { IncomingMessage, ServerResponse } from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import fixture from './src/test/fixtures/finding.json' with { type: 'json' };

/** Legacy local API adapter for viewer finding previews. The client also bundles
 * the reference finding, so this optional Referer-based shim is no longer required
 * for screenshots, compiled deployments or proxied APIs. It never writes records.
 */
export function serveEvidenceFixture(
  req: IncomingMessage,
  res: ServerResponse,
  root: string,
): boolean {
  if (req.method !== 'GET' || !req.headers.referer) return false;
  let referer: URL;
  try {
    referer = new URL(req.headers.referer);
  } catch {
    return false;
  }
  if (referer.pathname !== '/viewer/notre-dame-p3e' || !referer.searchParams.has('finding'))
    return false;
  if (!fs.existsSync(path.join(root, 'notre-dame-p3e/web/manifest.json'))) return false;
  const pathname = (req.url ?? '').split('?')[0];
  let body: unknown;
  switch (pathname) {
    case '/api/inspections':
      body = [
        {
          id: 41,
          site_id: 1,
          mode: 'quick_scan',
          status: 'done',
          created_at: '2026-10-21T10:00:00Z',
        },
      ];
      break;
    case '/api/inspections/41/runs':
      body = [
        {
          id: 'fixture-notre-dame-run',
          inspection_id: 41,
          run_dir: 'notre-dame-p3e',
          status: 'done',
          progress: 100,
        },
      ];
      break;
    case '/api/inspections/41/findings':
      body = [fixture.finding];
      break;
    case '/api/findings/701':
      body = fixture.finding;
      break;
    case '/api/findings/701/decisions':
      body = fixture.decisions;
      break;
    default:
      return false;
  }
  res.statusCode = 200;
  res.setHeader('Content-Type', 'application/json');
  res.setHeader('Cache-Control', 'no-store');
  res.end(JSON.stringify(body));
  return true;
}
