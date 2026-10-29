import { afterEach, expect, it, vi } from 'vitest';
import { getMe } from './me';
import me from '../test/fixtures/me.json';

afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

it.each(['viewer', 'engineer', 'admin'] as const)(
  'resolves the server-declared %s role, ignoring the entered username',
  async (role) => {
    sessionStorage.setItem('qarau.auth.session', 'Bearer test-session');
    sessionStorage.setItem('qarau.auth.userId', 'admin');
    const fetch = vi.fn().mockResolvedValue(Response.json({ ...me, role }));
    vi.stubGlobal('fetch', fetch);
    const signal = new AbortController().signal;
    await expect(getMe(signal)).resolves.toEqual({ ...me, role });
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
  await expect(getMe()).rejects.toBeInstanceOf(Error);
});
