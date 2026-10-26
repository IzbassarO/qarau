import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { LANGS, setLang, type Lang } from '../i18n';
import { createDemo } from '../fixtures/demo';
import * as bundleApi from '../lib/bundle';
import * as uploadApi from '../lib/upload';
import { AppShell } from './AppShell';
import { AuthProvider } from '../auth/AuthProvider';
import { RequireAuth } from '../auth/RequireAuth';
import { authHeader } from '../auth/auth';
import catalog from '../test/fixtures/run-catalog.json';
import legacyCatalog from '../test/fixtures/legacy-export-catalog.json';

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

it.each<Lang>(['en', 'ru', 'kk'])(
  'shows the empty inspection hint exactly once (%s)',
  async (lang) => {
    setLang(lang);
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async () => Response.json([])),
    );
    renderRoute('/');
    await screen.findByText(LANGS[lang]['shell.noSites']);
    const main = within(screen.getByRole('main'));
    expect(screen.getByRole('main').className).toBe('qv-page shell-main');
    expect(screen.getByRole('main').closest('.qv-app')?.className).toBe('qv qv-app app-shell');
    expect(main.getByRole('heading').className).toBe('qv-heading');
    expect(main.getByRole('heading').parentElement?.className).toBe('qv-empty empty-state');
    expect(main.getByRole('button', { name: LANGS[lang]['inspection.new'] }).className).toBe(
      'qv-btn qv-btn--secondary',
    );
    expect(main.getAllByText(LANGS[lang]['shell.emptyDescription'])).toHaveLength(1);
    const hint =
      lang === 'en'
        ? /Select an inspection/
        : lang === 'ru'
          ? /Выберите инспекцию/
          : /тексеруді таңдаңыз/;
    expect(main.getAllByText(hint)).toHaveLength(1);
  },
);

it('refetches the inspection list, removes the dialog query and selects the uploaded inspection', async () => {
  let uploaded = false;
  const inspection = {
    id: 7,
    site_id: 1,
    mode: 'quick_scan' as const,
    status: 'created',
    created_at: '2026-10-23T12:00:00Z',
  };
  vi.spyOn(uploadApi, 'createInspection').mockResolvedValue(inspection);
  vi.spyOn(uploadApi, 'uploadFile').mockImplementation(async () => {
    uploaded = true;
    return { runId: 'run-7' };
  });
  const manifest = vi.spyOn(bundleApi, 'loadManifest');
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (url === '/api/sites') return Response.json([{ id: 1, org_id: 1, name: 'North site' }]);
      if (url === '/api/inspections') return Response.json(uploaded ? [inspection] : []);
      if (url === '/api/inspections/7')
        return Response.json({ ...inspection, run_id: 'run-7', steps: [] });
      if (url === '/api/inspections/7/runs')
        return Response.json([{ id: 'run-7', status: 'uploaded' }]);
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  renderRoute('/?new-inspection');
  await screen.findByRole('option', { name: 'North site' });
  fireEvent.change(screen.getByLabelText('Site'), { target: { value: '1' } });
  fireEvent.change(screen.getByLabelText(/Choose a video/), {
    target: { files: [new File(['video'], 'tiny.mp4', { type: 'video/mp4' })] },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Upload video' }));
  expect(await screen.findByRole('heading', { name: 'Inspection 7' })).toBeTruthy();
  expect(screen.queryByRole('dialog')).toBeNull();
  const entry = screen.getByRole('link', { name: /Inspection 7/ });
  expect(entry.getAttribute('href')).toBe('/inspections/7');
  expect(entry.getAttribute('aria-current')).toBe('page');
  expect(within(entry).getByText('Uploaded')).toBeTruthy();
  expect(await screen.findByText('Video uploaded, waiting for processing.')).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(manifest).not.toHaveBeenCalled();
});

it('renders processing stages from selected inspection detail, ahead of a stale list status', async () => {
  const manifest = vi.spyOn(bundleApi, 'loadManifest');
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (url === '/api/sites') return Response.json([{ id: 1, org_id: 1, name: 'North site' }]);
      const inspection = { id: 7, site_id: 1, status: 'uploaded' };
      if (url === '/api/inspections') return Response.json([inspection]);
      if (url === '/api/inspections/7')
        return Response.json({
          ...inspection,
          status: 'running',
          stages: [
            { name: 'ingest', status: 'done', duration_s: 1 },
            { name: 'frames', status: 'running' },
            { name: 'reconstruction', status: 'pending' },
            { name: 'export', status: 'pending' },
          ],
        });
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  renderRoute('/inspections/7');
  const processing = await screen.findByRole('list', { name: 'Processing steps' });
  await waitFor(() =>
    expect(
      within(processing)
        .getAllByRole('listitem')
        .map((row) => row.getAttribute('data-state')),
    ).toEqual(['done', 'running', 'pending', 'pending']),
  );
  expect(screen.getByText('Processing', { selector: '.status-label' })).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(manifest).not.toHaveBeenCalled();
});

it('shows a server error and retries without substituting demo inspections', async () => {
  const fetch = vi
    .fn()
    .mockRejectedValueOnce(new TypeError('Failed to fetch'))
    .mockImplementation(async () => Response.json([]));
  vi.stubGlobal('fetch', fetch);
  renderRoute('/inspections/not-a-run');
  expect((await screen.findByRole('alert')).textContent).toContain('Could not load inspections');
  expect(screen.getByRole('alert').className).toBe('qv-notice api-error');
  expect(screen.getByRole('button', { name: 'Try again' }).className).toBe(
    'qv-btn qv-btn--secondary',
  );
  expect(screen.queryByText('Tank farm survey')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  expect(fetch).toHaveBeenCalledWith('/api/inspections', expect.anything());
  expect(fetch).toHaveBeenCalledTimes(3);
});

it('shows the labelled demo with its viewer and stages without requesting the backend', () => {
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  renderRoute('/inspections/demo');
  expect(screen.getByRole('heading', { name: 'Tank farm survey' })).toBeTruthy();
  expect(
    screen.getByText(
      'Example inspection with a sample map, images and processing steps. These are not inspection results.',
    ),
  ).toBeTruthy();
  expect(screen.getAllByText('Example').length).toBeGreaterThan(0);
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

// Home must use the inspection API and never substitute demo data.
it('lists the real inspections on home, not the demo', async () => {
  const fetch = vi.fn(async (url: string) =>
    Response.json(
      url === '/api/sites'
        ? [{ id: 1, org_id: 1, name: 'North site' }]
        : [
            { id: 1, site_id: 1, title: 'notre-dame-p3', status: 'uploaded' },
            {
              id: 2,
              site_id: 1,
              title: 'room',
              status: 'failed',
              created_at: '2026-10-16T15:00:00Z',
            },
          ],
    ),
  );
  vi.stubGlobal('fetch', fetch);
  renderRoute('/');
  // the run name and its date; the source file only as a tooltip
  const notreDame = await screen.findByRole('link', { name: /^notre-dame-p3/ });
  expect(notreDame.getAttribute('title')).toBe('notre-dame-p3');
  expect(within(notreDame).getByText('Uploaded')).toBeTruthy();
  const room = screen.getByRole('link', { name: /^room/ });
  expect(within(room).getByText('room').className).toBe('inspection-link-name');
  expect(within(room).getByRole('time').textContent).toBe('16 Oct');
  expect(within(room).getByRole('time').getAttribute('datetime')).toBe('2026-10-16T15:00:00Z');
  expect(room.getAttribute('href')).toBe('/inspections/2');
  expect(within(room).getByText('Failed')).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.queryByText('Tank farm survey')).toBeNull();
  expect(screen.queryByTestId('embedded-viewer')).toBeNull();
  expect(fetch).toHaveBeenCalledWith('/api/inspections', expect.anything());
});

it('keeps demo data out of real inspection routes', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(async () => Response.json([])),
  );
  renderRoute('/inspections/not-a-run');
  expect(await screen.findByRole('heading', { name: 'Inspection not found' })).toBeTruthy();
  expect(screen.queryByText('Tank farm survey')).toBeNull();
});

it('shows S1 steps from inspection detail and keeps unfamiliar inspection statuses visible', async () => {
  const fetch = vi.fn(async (url: string) => {
    if (url === '/api/sites') return Response.json([{ id: 1, org_id: 1, name: 'North site' }]);
    const inspection = { id: 7, site_id: 1, status: 'awaiting_review' };
    if (url === '/api/inspections') return Response.json([inspection]);
    if (url === '/api/inspections/7')
      return Response.json({
        ...inspection,
        run_id: 'run-7',
        steps: [
          { name: 'probe', status: 'done', duration_s: 1 },
          { name: 'frames', status: 'done', duration_s: 4.2 },
          { name: 'sparse', status: 'running' },
          { name: 'export', status: 'pending' },
        ],
      });
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal('fetch', fetch);
  const manifest = vi.spyOn(bundleApi, 'loadManifest');
  renderRoute('/inspections/7');
  await screen.findByText('Video check');
  const steps = await screen.findByRole('list', { name: 'Processing steps' });
  expect(
    within(steps)
      .getAllByRole('listitem')
      .map((row) => row.getAttribute('data-state')),
  ).toEqual(['done', 'done', 'running', 'pending']);
  expect(within(steps).getByText('Video check')).toBeTruthy();
  expect(within(steps).getByText('3D reconstruction')).toBeTruthy();
  expect(screen.getAllByText('awaiting_review')).toHaveLength(2);
  expect(screen.queryByText('Completed')).toBeNull();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(manifest).not.toHaveBeenCalled();
  expect(fetch).toHaveBeenCalledWith('/api/inspections/7', expect.anything());
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
  const fetch = vi.fn().mockImplementation(async () => Response.json([]));
  vi.stubGlobal('fetch', fetch);
  renderRoute('/inspections/private-run', false);
  fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'test-operator' } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'test-api-value' } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  expect(await screen.findByRole('navigation', { name: 'Sites & inspections' })).toBeTruthy();
  expect(await screen.findByRole('heading', { name: 'Inspection not found' })).toBeTruthy();
  expect(fetch).toHaveBeenCalledWith('/api/inspections', {
    signal: expect.any(AbortSignal),
    headers: { Authorization: 'Bearer test-api-value' },
  });
  expect(clients.at(-1)!.getQueryData(['inspections'])).toEqual([]);
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
    vi.fn(async (url: string) => {
      if (url === '/api/sites') return Response.json([{ id: 1, org_id: 1, name: 'North site' }]);
      const inspection = {
        id: 7,
        site_id: 1,
        title: 'backend-run',
        status: 'done',
        run_id: 'backend-run',
        created_at: '',
      };
      return Response.json(url === '/api/inspections' ? [inspection] : inspection);
    }),
  );
  renderRoute('/inspections/7');
  const viewer = await screen.findByTestId('embedded-viewer');
  expect(viewer.getAttribute('data-run-id')).toBe('backend-run');
  expect(viewer.getAttribute('data-embedded')).toBe('true');
  expect(manifest).toHaveBeenCalledWith('backend-run');
  expect(load).toHaveBeenCalledWith('backend-run', bundle.manifest, expect.any(Function));
  expect(screen.getByRole('heading', { name: 'backend-run' })).toBeTruthy();
  expect(screen.getByText('demo')).toBeTruthy(); // source comes from the API bundle, not invented metadata
  expect(screen.getByText('Date unavailable')).toBeTruthy();
  expect(screen.getAllByText('Completed')).toHaveLength(2);
  expect(screen.getAllByText('Unknown state')).toHaveLength(4);
  expect(screen.queryByText(/Example inspection with a sample map/)).toBeNull();
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
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (url === '/api/sites') return Response.json([{ id: 1, org_id: 1, name: 'North site' }]);
      const inspection = { id: 7, site_id: 1, status: 'done', run_id: 'backend-run' };
      return Response.json(url === '/api/inspections' ? [inspection] : inspection);
    }),
  );
  renderRoute('/inspections/7');
  expect((await screen.findByTestId('embedded-viewer')).getAttribute('data-run-id')).toBe(
    'backend-run',
  );
  expect(screen.queryByText('Could not load the inspection')).toBeNull();
});

it.each<Lang>(['en', 'ru', 'kk'])('opens /?new-inspection without an error in %s', async (lang) => {
  setLang(lang);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) =>
      Response.json(url === '/api/sites' ? [{ id: 1, org_id: 1, name: 'North site' }] : []),
    ),
  );
  renderRoute('/?new-inspection');
  expect(screen.getByRole('dialog', { name: LANGS[lang]['inspection.new'] })).toBeTruthy();
  expect(await screen.findByRole('option', { name: 'North site' })).toBeTruthy();
  expect(screen.getByLabelText(LANGS[lang]['upload.site'])).toBeTruthy();
  expect(screen.getByLabelText(new RegExp(LANGS[lang]['upload.choose']))).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
});

it('lists legacy exported runs with visible status chips when inspection CRUD is absent', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) =>
      url === '/api/runs'
        ? Response.json(catalog)
        : new Response('<!doctype html><html></html>', {
            headers: { 'Content-Type': 'text/html' },
          }),
    ),
  );
  renderRoute('/');
  const uploaded = await screen.findByRole('link', { name: /Walkdown upload/ });
  expect(within(uploaded).getByText('Uploaded').className).toBe(
    'qv-status qv-status--muted qv-caption inspection-status-chip uploaded',
  );
  expect(
    within(screen.getByRole('link', { name: /Failed survey/ })).getByText('Failed'),
  ).toBeTruthy();
  expect(
    within(screen.getByRole('link', { name: /Legacy export/ })).getByText('Status unavailable'),
  ).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
});

it('opens a legacy exported map using its run id and keeps unreported processing states unknown', async () => {
  const bundle = { ...createDemo('en').bundle, runId: 'catalog-export' };
  const manifest = vi.spyOn(bundleApi, 'loadManifest').mockResolvedValue(bundle.manifest);
  vi.spyOn(bundleApi, 'loadBundle').mockResolvedValue(bundle);
  const fetch = vi.fn(async (url: string) =>
    url === '/api/runs' ? Response.json(catalog) : new Response('', { status: 404 }),
  );
  vi.stubGlobal('fetch', fetch);
  renderRoute('/inspections/catalog-export');
  // A disabled detail query can still have old cache data from a previous API connection.
  clients
    .at(-1)!
    .setQueryData(['inspection', 'catalog-export'], { status: 'done', runId: 'unrelated-run' });
  expect((await screen.findByTestId('embedded-viewer')).getAttribute('data-run-id')).toBe(
    'catalog-export',
  );
  expect(manifest).toHaveBeenCalledWith('catalog-export');
  expect(screen.getByRole('heading', { name: 'Legacy export' })).toBeTruthy();
  expect(screen.getAllByText('Unknown state')).toHaveLength(4);
  expect(screen.queryByText('Completed')).toBeNull();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(fetch.mock.calls.map(([url]) => url)).toEqual(['/api/inspections', '/api/runs']);
});

it('populates the home sidebar from /api/runs when the API inspection table is empty', async () => {
  const fetch = vi.fn(async (url: string) => {
    if (url === '/api/inspections') return Response.json([]);
    if (url === '/api/runs') return Response.json(legacyCatalog);
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal('fetch', fetch);
  renderRoute('/');
  const entry = await screen.findByRole('link', { name: /walkdown-2026-10-23/ });
  expect(entry.getAttribute('href')).toBe('/inspections/walkdown-2026-10-23');
  const navigation = within(screen.getByRole('navigation', { name: 'Sites & inspections' }));
  expect(
    navigation.getByRole('link', { name: /tank-survey-2026-10-22/ }).getAttribute('href'),
  ).toBe('/inspections/tank-survey-2026-10-22');
  expect(navigation.getAllByText('Status unavailable')).toHaveLength(2);
  expect(navigation.queryByText('Your sites and inspections will appear here.')).toBeNull();
  expect(navigation.queryByText('Completed')).toBeNull();
  expect(navigation.getByRole('button', { name: 'New inspection' })).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(fetch.mock.calls.map(([url]) => url)).toEqual(['/api/inspections', '/api/runs']);
});
