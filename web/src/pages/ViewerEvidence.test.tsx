import { useEffect } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createDemo } from '../fixtures/demo';
import { setLang } from '../i18n';
import { loadBundle, loadManifest, type Manifest } from '../lib/bundle';
import fixture from '../test/fixtures/finding.json';
import { ViewerPage } from './ViewerPage';

const viewer = vi.hoisted(() => ({ mounts: 0 }));
vi.mock('../lib/bundle', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/bundle')>()),
  loadManifest: vi.fn(),
  loadBundle: vi.fn(),
}));
vi.mock('../viewer/ViewerScreen', () => ({
  ViewerScreen: ({ onFindingSelect }: { onFindingSelect: (id: number) => void }) => {
    useEffect(() => {
      viewer.mounts++;
    }, []);
    return <button onClick={() => onFindingSelect(701)}>Pick finding point</button>;
  },
}));

beforeEach(() => {
  setLang('en');
  viewer.mounts = 0;
  vi.mocked(loadBundle).mockResolvedValue(createDemo('en').bundle);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function Location() {
  return <output data-testid="location">{useLocation().search}</output>;
}

function openViewer(path = '/viewer/notre-dame-p3e?finding=first') {
  const client = new QueryClient({ defaultOptions: { queries: { gcTime: 0 } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/viewer/:runId"
            element={
              <>
                <ViewerPage />
                <Location />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return client;
}

function mockFindings(result: 'loaded' | 'error' | 'empty') {
  const fetch = vi.fn(async (url: string) => {
    if (url === '/api/inspections') return Response.json([{ id: 41 }]);
    if (url === '/api/inspections/41/runs')
      return Response.json([
        { id: 'fixture-notre-dame-run', inspection_id: 41, run_dir: 'notre-dame-p3e' },
      ]);
    if (url === '/api/inspections/41/findings')
      return result === 'error'
        ? new Response(null, { status: 503 })
        : Response.json(result === 'empty' ? [] : [fixture.finding]);
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

it.each(['loaded', 'error', 'empty'] as const)(
  'preserves one panel and a settled %s query across cloud loading and ready',
  async (result) => {
    let resolveManifest!: (manifest: Manifest) => void;
    vi.mocked(loadManifest).mockReturnValue(
      new Promise((resolve) => {
        resolveManifest = resolve;
      }),
    );
    const fetch = mockFindings(result);
    const client = openViewer();
    const panel = screen.getByRole('complementary', { name: 'Finding' });
    const expected =
      result === 'loaded'
        ? 'Corrosion'
        : result === 'empty'
          ? 'No findings'
          : 'Could not load findings. Check your connection and try again.';
    await screen.findByText(expected);
    const requestCount = fetch.mock.calls.length;
    await act(async () => resolveManifest(createDemo('en').bundle.manifest));
    await screen.findByRole('button', { name: 'Pick finding point' });
    expect(screen.getByRole('complementary', { name: 'Finding' })).toBe(panel);
    expect(screen.queryByText('Loading')).toBeNull();
    expect(screen.getByText(expected)).toBeTruthy();
    expect(fetch).toHaveBeenCalledTimes(requestCount);
    client.clear();
  },
);

it('selects an API finding via the viewer callback, keeps other URL params and does not remount', async () => {
  vi.mocked(loadManifest).mockResolvedValue(createDemo('en').bundle.manifest);
  mockFindings('loaded');
  const client = openViewer('/viewer/notre-dame-p3e?mode=orbit');
  await waitFor(() =>
    expect(client.getQueryData(['findings', 'run', 'notre-dame-p3e'])).toBeDefined(),
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Pick finding point' }));
  await screen.findByRole('heading', { name: 'Corrosion' });
  expect(screen.getByTestId('location').textContent).toBe('?mode=orbit&finding=701');
  expect(viewer.mounts).toBe(1);
  fireEvent.click(screen.getByRole('button', { name: 'Close finding' }));
  expect(screen.queryByRole('complementary', { name: 'Finding' })).toBeNull();
  expect(screen.getByTestId('location').textContent).toBe('?mode=orbit');
  expect(viewer.mounts).toBe(1);
  client.clear();
});
