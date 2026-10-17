import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { setLang } from '../i18n';
import { createDemo } from '../fixtures/demo';
import * as bundleApi from '../lib/bundle';
import { AppShell } from './AppShell';
import { AuthProvider } from '../auth/AuthProvider';
import { RequireAuth } from '../auth/RequireAuth';
import { authHeader } from '../auth/auth';

vi.mock('../viewer/ViewerScreen', () => ({
  ViewerScreen: ({ bundle, embedded }: { bundle: bundleApi.Bundle; embedded: boolean }) => (
    <div data-testid="embedded-viewer" data-run-id={bundle.runId} data-embedded={embedded} />
  ),
}));

const clients: QueryClient[] = [];
function renderRoute(route = '/', signedIn = true) {
  if (signedIn) sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <MemoryRouter initialEntries={[route]}>
          <Routes>
            <Route element={<RequireAuth />}>
              <Route path="/" element={<AppShell />} />
              <Route path="/inspections/demo" element={<AppShell demo />} />
              <Route path="/inspections/:id" element={<AppShell />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  setLang('en');
});
afterEach(() => {
  sessionStorage.clear();
  clients.splice(0).forEach((client) => client.clear());
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it('shows a server error and retries without substituting demo inspections', async () => {
  const fetch = vi
    .fn()
    .mockRejectedValueOnce(new TypeError('Failed to fetch'))
    .mockResolvedValueOnce(Response.json([]));
  vi.stubGlobal('fetch', fetch);
  renderRoute('/inspections/not-a-run');
  expect((await screen.findByRole('alert')).textContent).toContain('Could not load inspections');
  expect(screen.queryByText('Tank farm survey')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('shows the labelled demo with its viewer and stages without requesting the backend', () => {
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  renderRoute('/inspections/demo');
  expect(screen.getByRole('heading', { name: 'Tank farm survey' })).toBeTruthy();
  expect(screen.getByText(/Synthetic map, illustrated frames/)).toBeTruthy();
  expect(screen.getAllByText('Demo').length).toBeGreaterThan(0);
  expect(screen.getByTestId('embedded-viewer')).toBeTruthy();
  expect(screen.getByText('3D reconstruction')).toBeTruthy();
  expect(fetch).not.toHaveBeenCalled();
});

it('shows the failed demo stages at /inspections/demo without backend requests', () => {
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  renderRoute('/inspections/demo');
  expect(screen.getByText('Not enough overlapping views')).toBeTruthy();
  expect(screen.getByText('What to do')).toBeTruthy();
  expect(screen.getByText('Not run')).toBeTruthy();
  const processing = screen.getByRole('list', { name: 'Processing steps' });
  expect(
    within(processing)
      .getAllByRole('listitem')
      .map((row) => row.getAttribute('data-state')),
  ).toEqual(['done', 'done', 'failed', 'skipped']);
  expect(within(processing).getByText('Video import')).toBeTruthy();
  expect(within(processing).getByText('Frame extraction & quality')).toBeTruthy();
  expect(
    screen.getByText(
      'Film the asset again from several angles. Move slowly and keep the same surfaces visible between adjacent views.',
    ),
  ).toBeTruthy();
  expect(fetch).not.toHaveBeenCalled();
});

// Regression (A3 -> 2026-10-17): home showed only the demo site and never asked for real runs.
it('lists the real runs on home, not the demo', async () => {
  const fetch = vi.fn().mockResolvedValue(
    Response.json([
      { run_id: 'notre-dame-p3', source: { name: 'notre_dame.webm' } },
      { run_id: 'room', source: { name: 'IMG_8315.MOV' }, created_at: '2026-10-16T15:00:00Z' },
    ]),
  );
  vi.stubGlobal('fetch', fetch);
  renderRoute('/');
  // the run name and its date; the source file only as a tooltip
  const notreDame = await screen.findByRole('link', { name: /^notre-dame-p3/ });
  expect(notreDame.getAttribute('title')).toBe('notre_dame.webm');
  const room = screen.getByRole('link', { name: /^room/ });
  expect(room.textContent).toMatch(/^room · 16 Oct/);
  expect(room.getAttribute('href')).toBe('/inspections/room');
  expect(screen.queryByText('Tank farm survey')).toBeNull();
  expect(screen.queryByTestId('embedded-viewer')).toBeNull();
  expect(fetch).toHaveBeenCalledWith('/api/runs', expect.anything());
});

it('keeps demo data out of real inspection routes', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json([])));
  renderRoute('/inspections/not-a-run');
  expect(await screen.findByRole('heading', { name: 'Inspection not found' })).toBeTruthy();
  expect(screen.queryByText('Tank farm survey')).toBeNull();
});

it('reports a backend authentication failure for a previously stored session', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 401 })));
  renderRoute('/inspections/not-a-run');
  expect((await screen.findByRole('alert')).textContent).toContain(
    'Access requires authentication',
  );
});

it.each(['/', '/inspections/demo', '/inspections/private-run'])(
  'shows only the login form when signed out at %s, without fetching protected data',
  (route) => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    renderRoute(route, false);
    expect(screen.getByRole('form', { name: 'Sign in to the control center' })).toBeTruthy();
    expect(screen.getByLabelText('Username')).toBeTruthy();
    expect(screen.getByLabelText('Password')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeTruthy();
    expect(screen.queryByRole('navigation')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByTestId('embedded-viewer')).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  },
);

it('restores the signed-in sidebar from sessionStorage after remounting', () => {
  const first = renderRoute();
  expect(screen.getByRole('navigation', { name: 'Sites & inspections' })).toBeTruthy();
  first.unmount();
  renderRoute('/', false);
  expect(screen.getByRole('navigation', { name: 'Sites & inspections' })).toBeTruthy();
  expect(screen.queryByLabelText('Password')).toBeNull();
});

it('opens the requested inspection after login and returns to the form on sign-out', async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json([]));
  vi.stubGlobal('fetch', fetch);
  renderRoute('/inspections/private-run', false);
  fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'test-operator' } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'test-api-value' } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  expect(await screen.findByRole('navigation', { name: 'Sites & inspections' })).toBeTruthy();
  expect(await screen.findByRole('heading', { name: 'Inspection not found' })).toBeTruthy();
  expect(fetch).toHaveBeenLastCalledWith('/api/runs', {
    signal: expect.any(AbortSignal),
    headers: { Authorization: 'Bearer test-api-value' },
  });
  expect(clients.at(-1)!.getQueryData(['runs'])).toEqual([]);
  fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
  expect(screen.getByRole('button', { name: 'Sign in' })).toBeTruthy();
  expect(screen.queryByRole('navigation')).toBeNull();
  expect(authHeader()).toEqual({});
  expect(clients.at(-1)!.getQueryCache().getAll()).toHaveLength(0);
});

it('keeps the shell hidden after rejected credentials', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
  renderRoute('/', false);
  fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'test-operator' } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'rejected-test-value' } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  expect((await screen.findByRole('alert')).textContent).toContain(
    'Sign-in details were not accepted',
  );
  expect(screen.queryByRole('navigation')).toBeNull();
  expect(authHeader()).toEqual({});
});

it('loads a selected backend run into the existing embedded viewer with truthful metadata', async () => {
  const bundle = { ...createDemo('en').bundle, runId: 'backend-run' };
  const manifest = vi.spyOn(bundleApi, 'loadManifest').mockResolvedValue(bundle.manifest);
  const load = vi.spyOn(bundleApi, 'loadBundle').mockResolvedValue(bundle);
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      Response.json([
        {
          run_id: 'backend-run',
          source: { kind: 'video', name: 'survey.mp4' },
          stats: { processing_s: { frames: 4.2 } },
        },
      ]),
    ),
  );
  renderRoute('/inspections/backend-run');
  const viewer = await screen.findByTestId('embedded-viewer');
  expect(viewer.getAttribute('data-run-id')).toBe('backend-run');
  expect(viewer.getAttribute('data-embedded')).toBe('true');
  expect(manifest).toHaveBeenCalledWith('backend-run');
  expect(load).toHaveBeenCalledWith('backend-run', bundle.manifest, expect.any(Function));
  expect(screen.getByRole('heading', { name: 'backend-run' })).toBeTruthy();
  expect(screen.getByText('survey.mp4')).toBeTruthy(); // the source file, beside the date
  expect(screen.getByText('Date unavailable')).toBeTruthy();
  expect(screen.getAllByText('Status unavailable').length).toBeGreaterThan(0);
  expect(screen.queryByText('Completed')).toBeNull();
  expect(screen.queryByText(/Synthetic map/)).toBeNull();
});

it('keeps the embedded object viewer available after a recoverable far-layer failure', async () => {
  const bundle: bundleApi.Bundle = {
    ...createDemo('en').bundle,
    runId: 'backend-run',
    far: undefined,
    farError: 'broken',
  };
  vi.spyOn(bundleApi, 'loadManifest').mockResolvedValue(bundle.manifest);
  vi.spyOn(bundleApi, 'loadBundle').mockRejectedValue(
    new bundleApi.FarLayerError('broken', 'wrong far array length', bundle),
  );
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json([{ run_id: 'backend-run' }])));
  renderRoute('/inspections/backend-run');
  expect((await screen.findByTestId('embedded-viewer')).getAttribute('data-run-id')).toBe(
    'backend-run',
  );
  expect(screen.queryByText('Could not load the inspection')).toBeNull();
});
