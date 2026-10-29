import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AuthProvider, useAuth } from './AuthProvider';
import me from '../test/fixtures/me.json';

function Identity({ resolveRole }: { resolveRole: boolean }) {
  const auth = useAuth({ resolveRole });
  return (
    <>
      <output data-testid="role">{auth.role ?? 'unknown'}</output>
      <output data-testid="error">{String(auth.roleError)}</output>
      <button onClick={auth.signOut}>Sign out</button>
    </>
  );
}

const clients: QueryClient[] = [];
function mount(resolveRole = true) {
  const client = new QueryClient({ defaultOptions: { queries: { gcTime: 0 } } });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <Identity resolveRole={resolveRole} />
      </AuthProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => sessionStorage.clear());
afterEach(() => {
  clients.splice(0).forEach((client) => client.clear());
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

it('keeps anonymous visitors read-only without requesting account data', () => {
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  mount();
  expect(screen.getByTestId('role').textContent).toBe('viewer');
  expect(fetch).not.toHaveBeenCalled();
});

it('does not request a role for signed-in views that do not need permissions', () => {
  sessionStorage.setItem('qarau.auth.session', 'Bearer test-session');
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  mount(false);
  expect(screen.getByTestId('role').textContent).toBe('unknown');
  expect(fetch).not.toHaveBeenCalled();
});

it.each(['viewer', 'engineer', 'admin'] as const)(
  'provides the server role %s and clears it on sign-out',
  async (role) => {
    sessionStorage.setItem('qarau.auth.session', 'Bearer test-session');
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Response.json({ ...me, role })),
    );
    mount();
    expect(await screen.findByText(role)).toBeTruthy();
    expect(screen.getByTestId('error').textContent).toBe('false');
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(screen.getByTestId('role').textContent).toBe('viewer');
    expect(sessionStorage.length).toBe(0);
  },
);

it('reports role lookup failure without granting a role', async () => {
  sessionStorage.setItem('qarau.auth.session', 'Bearer test-session');
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
  mount();
  expect(await screen.findByText('true')).toBeTruthy();
  expect(screen.getByTestId('role').textContent).toBe('unknown');
});
