import { afterEach, expect, it, vi } from 'vitest';
import { ApiUnavailableError, readApi, rerunInspectionStep, startProcessing } from './api';
import { listSites } from './sites';

afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

it('recognizes Vite HTML and missing-route responses as absent API capabilities', async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      new Response('<!doctype html><html></html>', {
        headers: { 'Content-Type': 'text/html; charset=utf-8' },
      }),
    )
    .mockResolvedValueOnce(new Response('', { status: 404 }))
    .mockResolvedValueOnce(new Response('', { status: 405 }))
    .mockResolvedValueOnce(new Response('', { status: 501 }));
  vi.stubGlobal('fetch', fetch);
  for (let i = 0; i < 4; i++)
    await expect(readApi('/api/sites')).rejects.toBeInstanceOf(ApiUnavailableError);
});

it.each([401, 403, 500, 503])(
  'preserves HTTP %i as a real failure, not an absent capability',
  async (status) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status })));
    const result = readApi('/api/sites');
    await expect(result).rejects.not.toBeInstanceOf(ApiUnavailableError);
    await expect(result).rejects.toThrow(String(status));
  },
);

it('keeps malformed JSON and transport failures visible', async () => {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValueOnce(
        new Response('invalid JSON', { headers: { 'Content-Type': 'application/json' } }),
      )
      .mockRejectedValueOnce(new TypeError('offline')),
  );
  await expect(readApi('/api/sites')).rejects.not.toBeInstanceOf(ApiUnavailableError);
  await expect(readApi('/api/sites')).rejects.toThrow('offline');
});

it('loads real site records with the bearer session and rejects malformed site records', async () => {
  sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(Response.json([{ id: 4, org_id: 2, name: 'Pump station' }]))
    .mockResolvedValueOnce(Response.json([{ id: 'fake-id', name: 'Wrong type' }]));
  vi.stubGlobal('fetch', fetch);
  await expect(listSites()).resolves.toEqual([{ id: 4, org_id: 2, name: 'Pump station' }]);
  expect(fetch).toHaveBeenNthCalledWith(1, '/api/sites', {
    signal: undefined,
    headers: { Authorization: 'Bearer test-api-value' },
  });
  await expect(listSites()).rejects.toThrow('Invalid sites response');
});

it('posts a step rerun to S1 with encoded identifiers and the session header', async () => {
  sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
  const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 202 }));
  vi.stubGlobal('fetch', fetch);
  await rerunInspectionStep('7', 'run/7', 'sparse');
  expect(fetch).toHaveBeenCalledExactlyOnceWith(
    '/api/inspections/7/runs/run%2F7/steps/sparse/rerun',
    { method: 'POST', headers: { Authorization: 'Bearer test-api-value' } },
  );
});

it.each([401, 403, 404, 409, 503])('preserves a failed rerun response (%i)', async (status) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status })));
  await expect(rerunInspectionStep('7', 'run-7', 'frames')).rejects.toThrow(String(status));
});

it('queues the uploaded run and treats an already queued run as started', async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(Response.json({ run_id: 'run-7', status: 'queued' }, { status: 202 }))
    .mockResolvedValueOnce(Response.json({ detail: 'run is already queued' }, { status: 409 }))
    .mockResolvedValueOnce(Response.json({ detail: 'forbidden' }, { status: 403 }));
  vi.stubGlobal('fetch', fetch);
  await startProcessing(7, 'run-7');
  expect(fetch).toHaveBeenCalledWith(
    '/api/inspections/7/runs',
    expect.objectContaining({ method: 'POST', body: JSON.stringify({ run_id: 'run-7' }) }),
  );
  await expect(startProcessing(7, 'run-7')).resolves.toBeUndefined();
  await expect(startProcessing(7)).rejects.toThrow('403');
  expect(fetch.mock.calls[2][1].body).toBe('{}');
});
