import type { IncomingMessage, ServerResponse } from 'node:http';
import fs from 'node:fs';
import { afterEach, expect, it, vi } from 'vitest';
import fixture from '../test/fixtures/finding.json';
import { serveEvidenceFixture } from '../../vite-evidence-fixture';

afterEach(() => vi.restoreAllMocks());

function request(
  url: string,
  referer = 'http://localhost/viewer/notre-dame-p3e?finding=first',
  method = 'GET',
) {
  const req = { url, method, headers: { referer } } as IncomingMessage;
  const res = { statusCode: 0, setHeader: vi.fn(), end: vi.fn() };
  const served = serveEvidenceFixture(req, res as unknown as ServerResponse, '/fixture-runs');
  return { served, res };
}

it('serves the preview from the checked-in API fixture only for an explicit finding deep link', () => {
  vi.spyOn(fs, 'existsSync').mockReturnValue(true);
  const { served, res } = request('/api/findings/701');
  expect(served).toBe(true);
  expect(res.statusCode).toBe(200);
  expect(res.end).toHaveBeenCalledExactlyOnceWith(JSON.stringify(fixture.finding));
  const list = request('/api/inspections/41/findings');
  expect(list.res.end).toHaveBeenCalledExactlyOnceWith(JSON.stringify([fixture.finding]));
});

it('does not replace other screens, unknown finding IDs or write routes', () => {
  vi.spyOn(fs, 'existsSync').mockReturnValue(true);
  expect(request('/api/inspections', 'http://localhost/inspections').served).toBe(false);
  expect(request('/api/inspections', 'http://localhost/viewer/notre-dame-p3e').served).toBe(false);
  expect(request('/api/findings/999').served).toBe(false);
  expect(request('/api/findings/701/decisions', undefined, 'POST').served).toBe(false);
});

it('does not advertise evidence if the source bundle is unavailable', () => {
  vi.spyOn(fs, 'existsSync').mockReturnValue(false);
  expect(request('/api/findings/701').served).toBe(false);
});
