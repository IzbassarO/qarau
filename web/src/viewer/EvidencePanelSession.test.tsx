import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AuthProvider } from '../auth/AuthProvider';
import { getMe, meOptions } from '../lib/me';
import { setLang } from '../i18n';
import type { Finding } from '../lib/findings';
import fixture from '../test/fixtures/finding.json';
import me from '../test/fixtures/me.json';
import { EvidencePanel } from './EvidencePanel';

const clients: QueryClient[] = [];
function mountPanel() {
  vi.stubGlobal(
    'URL',
    Object.assign(class extends URL {}, {
      createObjectURL: vi.fn(() => 'blob:fixture-evidence'),
      revokeObjectURL: vi.fn(),
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { gcTime: 0 } } });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <EvidencePanel
          findings={[fixture.finding as Finding]}
          selection="first"
          loading={false}
          error={false}
          onClose={() => {}}
        />
      </AuthProvider>
    </QueryClientProvider>,
  );
  return client;
}

afterEach(() => {
  clients.splice(0).forEach((client) => client.clear());
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

it.each([
  ['en', 'Confirm', 'Reject', 'Reclass', 'Comment', 'No decisions yet'],
  ['ru', 'Подтвердить', 'Отклонить', 'Изменить класс', 'Комментарий', 'Решений пока нет'],
  ['kk', 'Растау', 'Қабылдамау', 'Сыныбын өзгерту', 'Түсініктеме', 'Әзірге шешім жоқ'],
] as const)(
  'resolves the signed-in engineer and places actions before the log in %s',
  async (lang, confirm, reject, reclass, comment, empty) => {
    setLang(lang);
    sessionStorage.setItem('qarau.auth.session', 'Bearer fixture-engineer-session');
    const fetch = vi.fn(async (url: string) => {
      if (url === '/api/me') return Response.json(me);
      if (url === '/api/findings/701/decisions') return Response.json([]);
      if (fixture.finding.evidence.some((evidence) => evidence.image_url === url))
        return new Response('fixture image', { headers: { 'Content-Type': 'image/jpeg' } });
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetch);
    mountPanel();
    expect(await screen.findByRole('button', { name: confirm })).toBeTruthy();
    expect(screen.getByRole('button', { name: reject })).toBeTruthy();
    expect(screen.getByRole('button', { name: reclass })).toBeTruthy();
    const field = screen.getByRole('textbox', { name: comment });
    const log = await screen.findByText(empty);
    expect(field.compareDocumentPosition(log) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    const frames = screen.getAllByTestId('evidence-frame');
    expect(frames).toHaveLength(3);
    expect(frames[2].compareDocumentPosition(field) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(fetch).toHaveBeenCalledWith('/api/me', {
      signal: expect.any(AbortSignal),
      headers: { Authorization: 'Bearer fixture-engineer-session' },
    });
    await waitFor(() =>
      expect(
        screen
          .getAllByRole('img')
          .every((image) => image.getAttribute('src') === 'blob:fixture-evidence'),
      ).toBe(true),
    );
    expect(screen.queryByRole('alert')).toBeNull();
  },
);

it('marks the log busy during a repeated /api/me read even when a role is cached', async () => {
  setLang('en');
  sessionStorage.setItem('qarau.auth.session', 'Bearer fixture-engineer-session');
  let identityReads = 0;
  let finish!: (response: Response) => void;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (url === '/api/me') {
        identityReads++;
        if (identityReads === 1) return Response.json(me);
        return new Promise<Response>((resolve) => (finish = resolve));
      }
      if (url === '/api/findings/701/decisions') return Response.json([]);
      if (fixture.finding.evidence.some((evidence) => evidence.image_url === url))
        return new Response('fixture image', { headers: { 'Content-Type': 'image/jpeg' } });
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  const client = mountPanel();
  const log = screen.getByRole('region', { name: 'Decision log' });
  const panel = screen.getByRole('complementary', { name: 'Finding' });
  await waitFor(() => expect(log.getAttribute('aria-busy')).toBe('false'));
  expect(panel.getAttribute('aria-busy')).toBe('false');
  let refresh!: ReturnType<typeof getMe>;
  act(() => {
    refresh = client.fetchQuery({
      ...meOptions(),
      staleTime: 0,
    });
  });
  await waitFor(() => expect(log.getAttribute('aria-busy')).toBe('true'));
  expect(panel.getAttribute('aria-busy')).toBe('true');
  expect(identityReads).toBe(2);
  expect(screen.getByRole('button', { name: 'Confirm' })).toBeTruthy();
  await act(async () => {
    finish(Response.json(me));
    await refresh;
  });
  await waitFor(() => expect(log.getAttribute('aria-busy')).toBe('false'));
  expect(panel.getAttribute('aria-busy')).toBe('false');
});

it.each(['/api/me', '/api/findings/701/decisions'])(
  'keeps the log section busy until %s finishes',
  async (pendingPath) => {
    setLang('en');
    sessionStorage.setItem('qarau.auth.session', 'Bearer fixture-engineer-session');
    let finish!: (response: Response) => void;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url === pendingPath) return new Promise<Response>((resolve) => (finish = resolve));
        if (url === '/api/me') return Response.json(me);
        if (url === '/api/findings/701/decisions') return Response.json([]);
        if (fixture.finding.evidence.some((evidence) => evidence.image_url === url))
          return new Response('fixture image', { headers: { 'Content-Type': 'image/jpeg' } });
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    mountPanel();
    const log = screen.getByRole('region', { name: 'Decision log' });
    const panel = screen.getByRole('complementary', { name: 'Finding' });
    expect(log.getAttribute('aria-busy')).toBe('true');
    if (pendingPath === '/api/me') {
      expect(panel.getAttribute('aria-busy')).toBe('true');
      await screen.findByText('No decisions yet');
      for (const name of ['Confirm', 'Reject', 'Reclass'])
        expect(screen.queryByRole('button', { name })).toBeNull();
    } else {
      await screen.findByRole('button', { name: 'Confirm' });
      expect(screen.getByRole('status').textContent).toBe('Loading decisions');
    }
    expect(log.getAttribute('aria-busy')).toBe('true');
    finish(Response.json(pendingPath === '/api/me' ? me : []));
    await waitFor(() => expect(log.getAttribute('aria-busy')).toBe('false'));
    expect(panel.getAttribute('aria-busy')).toBe('false');
    expect(screen.getByRole('region', { name: 'Decision log' })).toBe(log);
    expect(screen.getByText('No decisions yet')).toBeTruthy();
    for (const name of ['Confirm', 'Reject', 'Reclass'])
      expect(screen.getByRole('button', { name })).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  },
);

it.each(['/api/me', '/api/findings/701/decisions'])(
  'clears the busy state and shows a human-readable error when %s fails',
  async (failedPath) => {
    setLang('en');
    sessionStorage.setItem('qarau.auth.session', 'Bearer fixture-engineer-session');
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url === failedPath)
          return Response.json({ detail: 'Service unavailable' }, { status: 503 });
        if (url === '/api/me') return Response.json(me);
        if (url === '/api/findings/701/decisions') return Response.json([]);
        if (fixture.finding.evidence.some((evidence) => evidence.image_url === url))
          return new Response('fixture image', { headers: { 'Content-Type': 'image/jpeg' } });
        throw new Error(`Unexpected request: ${url}`);
      }),
    );
    mountPanel();
    const log = screen.getByRole('region', { name: 'Decision log' });
    await screen.findByRole('alert');
    await waitFor(() => expect(log.getAttribute('aria-busy')).toBe('false'));
    expect(screen.getByRole('complementary', { name: 'Finding' }).getAttribute('aria-busy')).toBe(
      'false',
    );
    expect(screen.getByRole('alert').textContent).toMatch(/[a-z]+ [a-z]+/i);
    expect(screen.queryByText('Loading decisions')).toBeNull();
    if (failedPath === '/api/me') {
      expect(screen.getByRole('alert').textContent).toBe(
        'Could not check your role. Reload the page to try again.',
      );
      for (const name of ['Confirm', 'Reject', 'Reclass'])
        expect(screen.queryByRole('button', { name })).toBeNull();
      expect(screen.getByText('No decisions yet')).toBeTruthy();
    }
  },
);
