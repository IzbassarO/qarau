import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { authHeader, login, logout, sessionHeader } from './auth';

const credentials = { username: 'test-operator', password: 'test-api-value' };

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});
afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it('validates the backend Bearer contract before storing the session only in sessionStorage', async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json([]));
  vi.stubGlobal('fetch', fetch);
  expect(authHeader()).toEqual({});
  await login(credentials);
  expect(fetch).toHaveBeenCalledWith('/api/runs', {
    headers: { Authorization: `Bearer ${credentials.password}` },
    cache: 'no-store',
    redirect: 'error',
  });
  expect(sessionStorage.getItem('qarau.auth.session')).toBe(`Bearer ${credentials.password}`);
  expect(authHeader()).toEqual({ Authorization: `Bearer ${credentials.password}` });
  expect(localStorage.length).toBe(0);
  expect(JSON.stringify(Object.values(sessionStorage))).not.toContain(credentials.username);
});

it('does not store an unvalidated session while the request is pending', async () => {
  let finish!: (response: Response) => void;
  vi.stubGlobal(
    'fetch',
    vi.fn(() => new Promise<Response>((resolve) => (finish = resolve))),
  );
  const request = login(credentials);
  expect(sessionHeader()).toBeNull();
  expect(sessionStorage.length).toBe(0);
  finish(Response.json([]));
  await request;
  expect(sessionHeader()).toBe(`Bearer ${credentials.password}`);
});

it.each([401, 403])(
  'rejects HTTP %i and leaves no session or stale credentials',
  async (status) => {
    sessionStorage.setItem('qarau.auth.session', 'Bearer previous-test-session');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status })));
    await expect(login(credentials)).rejects.toMatchObject({ code: 'invalidCredentials' });
    expect(sessionStorage.length).toBe(0);
    expect(authHeader()).toEqual({});
  },
);

it.each([204, 500])('rejects an unexpected HTTP %i without saving credentials', async (status) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status })));
  await expect(login(credentials)).rejects.toMatchObject({ code: 'connection' });
  expect(sessionStorage.length).toBe(0);
});

it('reports a network failure without storing credentials', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
  await expect(login(credentials)).rejects.toMatchObject({ code: 'connection' });
  expect(sessionStorage.length).toBe(0);
});

it.each([
  { username: ' ', password: 'test-api-value' },
  { username: 'test-operator', password: ' ' },
])('rejects empty input before calling the backend', async (input) => {
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  await expect(login(input)).rejects.toMatchObject({ code: 'required' });
  expect(fetch).not.toHaveBeenCalled();
  expect(sessionStorage.length).toBe(0);
});

it('clears the session and authorization header on logout, preserving unrelated settings', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json([])));
  await login(credentials);
  sessionStorage.setItem('unrelated', 'keep');
  logout();
  expect(sessionStorage.getItem('qarau.auth.session')).toBeNull();
  expect(authHeader()).toEqual({});
  expect(sessionStorage.getItem('unrelated')).toBe('keep');
});

it('reports unavailable session storage instead of signing in', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json([])));
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('Storage unavailable');
  });
  await expect(login(credentials)).rejects.toMatchObject({ code: 'storage' });
  expect(sessionHeader()).toBeNull();
});
