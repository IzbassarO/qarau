import type { IncomingMessage, ServerResponse } from 'node:http';
import { expect, it, vi } from 'vitest';
import { devReport } from '../../dev-report';

function request(url: string, method = 'GET') {
  const headers: Record<string, string> = {};
  const response = {
    statusCode: 200,
    setHeader: (key: string, value: string) => {
      headers[key] = value;
    },
    end: vi.fn(),
  };
  const next = vi.fn();
  devReport({ url, method } as IncomingMessage, response as unknown as ServerResponse, next);
  return { response, headers, next };
}

it('serves the local demo PDF with its content hash in every report language', () => {
  for (const lang of ['ru', 'kk', 'en']) {
    const { response, headers, next } = request(`/api/inspections/0/report.pdf?lang=${lang}`);
    expect(next).not.toHaveBeenCalled();
    expect(response.statusCode).toBe(200);
    expect(headers['Content-Type']).toBe('application/pdf');
    expect(headers['X-Report-SHA256']).toBe(
      'a278f21895176e40699b4de5dda3b7aa385e82e13995d4bab0beff341aa5d558',
    );
    expect(response.end.mock.calls[0][0].length).toBe(619);
  }
});

it('uses the viewer demo metadata and reports no invented findings or coverage', () => {
  expect(JSON.parse(request('/api/inspections/demo').response.end.mock.calls[0][0])).toEqual({
    id: 0,
    site_id: 0,
    mode: 'quick_scan',
    status: 'failed',
    run_id: 'demo',
    created_at: '2026-10-14T10:30:00+05:00',
  });
  expect(JSON.parse(request('/api/sites').response.end.mock.calls[0][0])).toEqual([
    { id: 0, name: 'Tank farm №3' },
  ]);
  expect(JSON.parse(request('/api/inspections/0/findings').response.end.mock.calls[0][0])).toEqual(
    [],
  );
  expect(JSON.parse(request('/api/inspections/0/coverage').response.end.mock.calls[0][0])).toEqual({
    computed: false,
  });
});

it('leaves other inspections and mutations to the configured API', () => {
  expect(request('/api/inspections/37').next).toHaveBeenCalledTimes(1);
  expect(request('/api/inspections/demo', 'POST').next).toHaveBeenCalledTimes(1);
  expect(request('/api/inspections/0/report.pdf?lang=de').response.statusCode).toBe(422);
});
