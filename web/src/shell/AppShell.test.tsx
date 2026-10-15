import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { setLang } from '../i18n';
import { createDemo } from '../fixtures/demo';
import * as bundleApi from '../lib/bundle';
import { AppShell } from './AppShell';

vi.mock('../viewer/ViewerScreen', () => ({
  ViewerScreen: ({ bundle, embedded }: { bundle: bundleApi.Bundle; embedded: boolean }) => (
    <div data-testid="embedded-viewer" data-run-id={bundle.runId} data-embedded={embedded} />
  ),
}));

const clients: QueryClient[] = [];
function renderRoute(route = '/') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  clients.push(client);
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path="/" element={<AppShell />} />
          <Route path="/inspections/demo" element={<AppShell demo />} />
          <Route path="/inspections/:id" element={<AppShell />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => setLang('en'));
afterEach(() => {
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
  renderRoute();
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

it('keeps demo data out of the empty home and real inspection routes', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(async () => Response.json([])),
  );
  const home = renderRoute();
  await screen.findByText('Your sites and inspections will appear here.');
  expect(screen.queryByText('Tank farm survey')).toBeNull();
  expect(screen.queryByTestId('embedded-viewer')).toBeNull();
  home.unmount();
  renderRoute('/inspections/not-a-run');
  expect(await screen.findByRole('heading', { name: 'Inspection not found' })).toBeTruthy();
  expect(screen.queryByText('Tank farm survey')).toBeNull();
});

it('reports authentication errors without a local login flow', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 401 })));
  renderRoute();
  expect((await screen.findByRole('alert')).textContent).toContain(
    'Access requires authentication',
  );
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
  expect(screen.getByRole('heading', { name: 'survey.mp4' })).toBeTruthy();
  expect(screen.getByText('Date unavailable')).toBeTruthy();
  expect(screen.getAllByText('Status unavailable').length).toBeGreaterThan(0);
  expect(screen.queryByText('Completed')).toBeNull();
  expect(screen.queryByText(/Synthetic map/)).toBeNull();
});
