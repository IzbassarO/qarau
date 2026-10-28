import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { authHeader, fetchSessionRole, login, logout, sessionHeader, sessionUserId } from './auth';
import me from '../test/fixtures/me.json';

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
  expect(sessionUserId()).toBe(credentials.username);
  expect(sessionStorage.getItem('qarau.auth.userId')).toBe(credentials.username);
});

it('does not store an unvalidated session while the request is pending', async () => {
  let finish!: (response: Response) => void;
  vi.stubGlobal(
    'fetch',
    vi.fn(() => new Promise<Response>((resolve) => (finish = resolve))),
  );
  const request = login(credentials);
  expect(sessionHeader()).toBeNull();
  expect(sessionUserId()).toBeNull();
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
  expect(sessionUserId()).toBeNull();
  expect(sessionStorage.getItem('qarau.auth.userId')).toBeNull();
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

it('uses the validated session username as a preference id, never the Bearer credential', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json([])));
  await login({ ...credentials, username: '  operator-a  ' });
  expect(sessionUserId()).toBe('operator-a');
  await login({ ...credentials, username: 'operator-b' });
  expect(sessionUserId()).toBe('operator-b');
  logout();
  sessionStorage.setItem('qarau.auth.userId', 'stale-operator');
  expect(sessionUserId()).toBeNull();
});

it.each(['viewer', 'engineer', 'admin'] as const)(
  'resolves the server-declared %s role, ignoring the entered username',
  async (role) => {
    sessionStorage.setItem('qarau.auth.session', 'Bearer test-session');
    sessionStorage.setItem('qarau.auth.userId', 'admin');
    const fetch = vi.fn().mockResolvedValue(Response.json({ ...me, role }));
    vi.stubGlobal('fetch', fetch);
    const signal = new AbortController().signal;
    await expect(fetchSessionRole(signal)).resolves.toBe(role);
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/me', {
      signal,
      headers: { Authorization: 'Bearer test-session' },
    });
  },
);

it.each([
  Response.json({ detail: "role 'engineer' may not use a 'admin' route" }, { status: 403 }),
  Response.json({ detail: 'Access denied' }, { status: 403 }),
  Response.json({ detail: 'invalid or missing token' }, { status: 401 }),
  Response.json({ ...me, role: 'admin' }, { status: 500 }),
  Response.json({ ...me, role: 'unknown' }),
  Response.json([]),
  Response.json({}, { status: 200 }),
  new Response('<html>SPA fallback</html>', { headers: { 'Content-Type': 'text/html' } }),
])('never grants a role for unknown responses %#', async (response) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));
  await expect(fetchSessionRole()).rejects.toBeInstanceOf(Error);
});
