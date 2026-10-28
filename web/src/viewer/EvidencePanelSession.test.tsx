import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AuthProvider } from '../auth/AuthProvider';
import { setLang } from '../i18n';
import type { Finding } from '../lib/findings';
import fixture from '../test/fixtures/finding.json';
import { EvidencePanel } from './EvidencePanel';

const clients: QueryClient[] = [];
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
      if (url === '/api/users')
        return Response.json(
          { detail: "role 'engineer' may not use a 'admin' route" },
          { status: 403 },
        );
      if (url === '/api/findings/701/decisions') return Response.json([]);
      if (fixture.finding.evidence.some((evidence) => evidence.image_url === url))
        return new Response('fixture image', { headers: { 'Content-Type': 'image/jpeg' } });
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetch);
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
    expect(fetch).toHaveBeenCalledWith('/api/users', {
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
