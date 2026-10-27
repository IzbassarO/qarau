import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { LANGS, setLang, type Lang } from '../i18n';
import { authHeader } from './auth';
import { AuthProvider } from './AuthProvider';
import { LoginPage } from './LoginPage';

const clients: QueryClient[] = [];
function renderLogin() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <LoginPage />
      </AuthProvider>
    </QueryClientProvider>,
  );
}

function fillForm() {
  fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'test-operator' } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'test-api-value' } });
}

beforeEach(() => {
  sessionStorage.clear();
  setLang('en');
});
afterEach(() => {
  clients.splice(0).forEach((client) => client.clear());
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

it.each<Lang>(['en', 'ru', 'kk'])(
  'renders a localized form with no initial error in %s',
  (lang) => {
    setLang(lang);
    renderLogin();
    const t = LANGS[lang];
    expect(screen.getByRole('heading', { name: t['auth.title'] })).toBeTruthy();
    expect(screen.getByLabelText(t['auth.username']).getAttribute('autocomplete')).toBe('username');
    expect(screen.getByLabelText(t['auth.password']).getAttribute('type')).toBe('password');
    expect(screen.getByRole('button', { name: t['auth.signIn'] })).toBeTruthy();
    expect(screen.getByRole('main').className).toBe('qv login-page');
    expect(
      screen.getByRole('heading', { name: t['auth.heroTitle'] }).closest('section')?.className,
    ).toBe('qv-hero login-hero');
    expect(screen.getByRole('heading', { name: t['auth.title'] }).className).toBe('qv-title');
    expect(screen.getByLabelText(t['auth.username']).className).toBe('qv-input');
    expect(screen.getByLabelText(t['auth.password']).className).toBe('qv-input');
    expect(screen.getByRole('button', { name: t['auth.signIn'] }).className).toBe(
      'qv-btn qv-btn--lg qv-btn--block',
    );
    expect(screen.getByRole('group', { name: t['language.label'] }).className).toBe(
      'qv-segment shell-languages',
    );
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByRole('navigation')).toBeNull();
  },
);

it.each([401, 403])(
  'shows a human-readable error on HTTP %i and stores nothing',
  async (status) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status })));
    renderLogin();
    fillForm();
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect((await screen.findByRole('alert')).textContent).toBe(
      LANGS.en['auth.invalidCredentials'],
    );
    expect(screen.getByRole('alert').className).toBe('qv-notice login-error');
    expect(authHeader()).toEqual({});
    expect(sessionStorage.length).toBe(0);
    expect((screen.getByLabelText('Password') as HTMLInputElement).value).toBe('');
    expect((screen.getByRole('button', { name: 'Sign in' }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  },
);

it('disables submission while checking and stores the validated session on success', async () => {
  let finish!: (response: Response) => void;
  const fetch = vi.fn(() => new Promise<Response>((resolve) => (finish = resolve)));
  vi.stubGlobal('fetch', fetch);
  renderLogin();
  fillForm();
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  expect((screen.getByRole('button', { name: 'Signing in…' }) as HTMLButtonElement).disabled).toBe(
    true,
  );
  expect(authHeader()).toEqual({});
  finish(Response.json([]));
  await waitFor(() => expect(authHeader()).toEqual({ Authorization: 'Bearer test-api-value' }));
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('offers a retry after a connection failure', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
  renderLogin();
  fillForm();
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  expect((await screen.findByRole('alert')).textContent).toBe(LANGS.en['auth.connection']);
  expect(authHeader()).toEqual({});
  expect((screen.getByRole('button', { name: 'Sign in' }) as HTMLButtonElement).disabled).toBe(
    false,
  );
});
