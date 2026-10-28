import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { AuthProvider } from '../auth/AuthProvider';
import { setLang } from '../i18n';
import { ApiError } from '../lib/api';
import * as api from '../lib/findings';
import type { Decision } from '../lib/findings';
import findingFixture from '../test/fixtures/finding.json';
import decisions from '../test/fixtures/decisions.json';
import me from '../test/fixtures/me.json';
import { DecisionForm } from './DecisionForm';
import { EvidencePanel } from './EvidencePanel';

vi.mock('../lib/findings', async (original) => ({
  ...(await original<typeof import('../lib/findings')>()),
  postDecision: vi.fn(),
}));

const clients: QueryClient[] = [];
function mount(children: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { gcTime: 0 } } });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>{children}</AuthProvider>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  setLang('en');
  vi.resetAllMocks();
  sessionStorage.clear();
  vi.stubGlobal(
    'URL',
    Object.assign(class extends URL {}, {
      createObjectURL: vi.fn(() => 'blob:fixture-evidence'),
      revokeObjectURL: vi.fn(),
    }),
  );
});
afterEach(() => {
  clients.splice(0).forEach((client) => client.clear());
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

it.each(['Reject', 'Reclass'])(
  '%s without a comment or with whitespace never calls POST',
  (name) => {
    mount(<DecisionForm findingId={701} />);
    fireEvent.click(screen.getByRole('button', { name }));
    expect(api.postDecision).not.toHaveBeenCalled();
    expect(screen.getByRole('alert').textContent).toBe(
      'Add a comment before rejecting or reclassifying this finding.',
    );
    fireEvent.change(screen.getByLabelText('Comment'), { target: { value: ' \n\t ' } });
    fireEvent.click(screen.getByRole('button', { name }));
    expect(api.postDecision).not.toHaveBeenCalled();
  },
);

it('reject with Rust is paint posts that literal comment and the reject verdict', async () => {
  vi.mocked(api.postDecision).mockResolvedValue(decisions[2] as Decision);
  mount(<DecisionForm findingId={701} />);
  fireEvent.change(screen.getByLabelText('Comment'), { target: { value: 'Rust is paint' } });
  fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
  await waitFor(() =>
    expect(api.postDecision).toHaveBeenCalledExactlyOnceWith(701, {
      verdict: 'reject',
      comment: 'Rust is paint',
    }),
  );
});

it('requires a new class for reclass, then posts the class and comment', async () => {
  vi.mocked(api.postDecision).mockResolvedValue(decisions[0] as Decision);
  mount(<DecisionForm findingId={701} />);
  fireEvent.change(screen.getByLabelText('Comment'), {
    target: { value: 'Coating has peeled away.' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Reclass' }));
  expect(api.postDecision).not.toHaveBeenCalled();
  expect(screen.getByRole('alert').textContent).toBe(
    'Choose a new class before reclassifying this finding.',
  );
  fireEvent.change(screen.getByLabelText('New class'), { target: { value: 'coating' } });
  fireEvent.click(screen.getByRole('button', { name: 'Reclass' }));
  await waitFor(() =>
    expect(api.postDecision).toHaveBeenCalledExactlyOnceWith(701, {
      verdict: 'reclass',
      comment: 'Coating has peeled away.',
      new_class: 'coating',
    }),
  );
});

it('disables all actions while pending and allows confirm without a comment', async () => {
  let finish!: (decision: Decision) => void;
  vi.mocked(api.postDecision).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  mount(<DecisionForm findingId={701} />);
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
  await waitFor(() =>
    expect(api.postDecision).toHaveBeenCalledExactlyOnceWith(701, {
      verdict: 'confirm',
      comment: '',
    }),
  );
  for (const name of ['Confirm', 'Reject', 'Reclass'])
    expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(true);
  finish(decisions[1] as Decision);
  await waitFor(() =>
    expect((screen.getByRole('button', { name: 'Confirm' }) as HTMLButtonElement).disabled).toBe(
      false,
    ),
  );
});

it('shows the API error and preserves the comment for retry', async () => {
  vi.mocked(api.postDecision).mockRejectedValue(
    new ApiError(
      '/api/findings/701/decisions',
      409,
      'This inspection is locked. Open a new review.',
    ),
  );
  mount(<DecisionForm findingId={701} />);
  fireEvent.change(screen.getByLabelText('Comment'), { target: { value: 'Rust is paint' } });
  fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
  expect((await screen.findByRole('alert')).textContent).toBe(
    'This inspection is locked. Open a new review.',
  );
  expect((screen.getByLabelText('Comment') as HTMLTextAreaElement).value).toBe('Rust is paint');
});

function RefetchedPanel() {
  const query = useQuery(api.findingOptions(701));
  return (
    <EvidencePanel
      findings={query.data ? [query.data] : undefined}
      selection="701"
      loading={query.isPending}
      error={query.isError}
      onClose={() => {}}
    />
  );
}

it.each(['viewer', 'engineer'] as const)(
  'uses the mocked /api/me %s role to gate all three actions',
  async (role) => {
    sessionStorage.setItem('qarau.auth.session', 'Bearer fixture-session');
    const fetch = vi.fn(async (url: string) => {
      if (url === '/api/me') return Response.json({ ...me, role });
      if (url === '/api/findings/701/decisions') return Response.json([]);
      if (findingFixture.finding.evidence.some((evidence) => evidence.image_url === url))
        return new Response('fixture image', { headers: { 'Content-Type': 'image/jpeg' } });
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetch);
    mount(
      <EvidencePanel
        findings={[findingFixture.finding as api.Finding]}
        selection="first"
        loading={false}
        error={false}
        onClose={() => {}}
      />,
    );
    const log = screen.getByRole('region', { name: 'Decision log' });
    await waitFor(() => expect(log.getAttribute('aria-busy')).toBe('false'));
    for (const name of ['Confirm', 'Reject', 'Reclass']) {
      if (role === 'viewer') expect(screen.queryByRole('button', { name })).toBeNull();
      else expect(screen.getByRole('button', { name })).toBeTruthy();
    }
    if (role === 'viewer') expect(screen.queryByLabelText('Comment')).toBeNull();
    else expect(screen.getByLabelText('Comment')).toBeTruthy();
    expect(screen.getByText('No decisions yet')).toBeTruthy();
    expect(fetch).toHaveBeenCalledWith('/api/me', {
      signal: expect.any(AbortSignal),
      headers: { Authorization: 'Bearer fixture-session' },
    });
    expect(screen.queryByRole('alert')).toBeNull();
    expect(api.postDecision).not.toHaveBeenCalled();
  },
);

it('after a successful POST shows the mocked finding refetch status and the refetched log', async () => {
  sessionStorage.setItem('qarau.auth.session', 'Bearer fixture-engineer-session');
  let findingReads = 0;
  let decisionReads = 0;
  let finishRefetch!: (response: Response) => void;
  const fetch = vi.fn(async (url: string) => {
    if (url === '/api/me') return Response.json(me);
    if (url === '/api/findings/701') {
      findingReads++;
      if (findingReads === 1) return Response.json(findingFixture.finding);
      return new Promise<Response>((resolve) => (finishRefetch = resolve));
    }
    if (findingFixture.finding.evidence.some((evidence) => evidence.image_url === url))
      return new Response('fixture image', { headers: { 'Content-Type': 'image/jpeg' } });
    if (url === '/api/findings/701/decisions') {
      decisionReads++;
      return Response.json(decisionReads === 1 ? [] : [decisions[2]]);
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal('fetch', fetch);
  // The mutation response has no finding status; the displayed value must come from GET.
  vi.mocked(api.postDecision).mockResolvedValue(decisions[2] as Decision);
  mount(<RefetchedPanel />);
  expect(await screen.findByText('Confirmed')).toBeTruthy();
  await screen.findByText('No decisions yet');
  fireEvent.change(await screen.findByLabelText('Comment'), { target: { value: 'Rust is paint' } });
  fireEvent.click(screen.getByRole('button', { name: 'Reject' }));
  await waitFor(() => expect(findingReads).toBe(2));
  expect(screen.getByText('Status').parentElement?.querySelector('dd')?.textContent).toBe(
    'Confirmed',
  );
  finishRefetch(Response.json({ ...findingFixture.finding, status: 'rejected' }));
  await waitFor(() =>
    expect(screen.getByText('Status').parentElement?.querySelector('dd')?.textContent).toBe(
      'Rejected',
    ),
  );
  expect(
    fetch.mock.calls.map(([url]) => url).filter((url) => url.startsWith('/api/findings/')),
  ).toEqual([
    '/api/findings/701',
    '/api/findings/701/decisions',
    '/api/findings/701',
    '/api/findings/701/decisions',
  ]);
  expect(await screen.findByText('Alex Kim')).toBeTruthy();
  expect(screen.getByText('Rust is paint')).toBeTruthy();
});
