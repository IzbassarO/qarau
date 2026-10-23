import { afterEach, expect, it, vi } from 'vitest';
import { ApiUnavailableError, readApi } from './api';
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
