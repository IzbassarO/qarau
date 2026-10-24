import { describe, expect, it, vi, afterEach } from 'vitest';
import {
  fetchRuns,
  getInspection,
  listInspections,
  groupRunsIntoSites,
  inspectionStatus,
  UNASSIGNED_SITE_ID,
  type RunSummary,
} from './inspections';
import catalog from '../test/fixtures/run-catalog.json';
import legacyCatalog from '../test/fixtures/legacy-export-catalog.json';

afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

describe('groupRunsIntoSites', () => {
  it('groups runs by real site identity and preserves inspection metadata', () => {
    const runs: RunSummary[] = [
      {
        run_id: 'a',
        site: { id: 'north', name: 'North' },
        title: 'Walkdown',
        status: 'running',
        created_at: '2026-10-14T10:00:00Z',
      },
      { run_id: 'b', site_id: 'north', site_name: 'North' },
      { run_id: 'c', site: { id: 'south', name: 'South' } },
    ];
    const sites = groupRunsIntoSites(runs);
    expect(sites).toHaveLength(2);
    expect(sites[0]).toMatchObject({ id: 'north', name: 'North' });
    expect(sites[0].inspections.map((item) => item.id)).toEqual(['a', 'b']);
    expect(sites[0].inspections[0]).toEqual({
      id: 'a',
      siteId: 'north',
      title: 'Walkdown',
      source: null,
      createdAt: '2026-10-14T10:00:00Z',
      status: 'running',
    });
  });

  it('uses one unassigned bucket and leaves missing dates/statuses unknown', () => {
    const sites = groupRunsIntoSites([
      { run_id: 'video', source: { name: 'survey.mp4', kind: 'video' } },
      { run_id: 'images', site: null, source: null, stats: null },
    ]);
    expect(sites).toHaveLength(1);
    expect(sites[0]).toMatchObject({ id: UNASSIGNED_SITE_ID, name: '' });
    expect(sites[0].inspections).toContainEqual({
      id: 'video',
      siteId: UNASSIGNED_SITE_ID,
      title: 'video',
      source: 'survey.mp4',
      createdAt: null,
      status: 'unknown',
    });
    expect(sites[0].inspections.find((item) => item.id === 'images')?.title).toBe('images');
    expect(groupRunsIntoSites([])).toEqual([]);
  });

  it('sorts newest first within sites and across sites, with unknown dates last', () => {
    const runs: RunSummary[] = [
      { run_id: 'old', site_id: 'a', created_at: '2026-01-01T00:00:00Z' },
      { run_id: 'missing', site_id: 'a' },
      { run_id: 'new', site_id: 'a', created_at: '2026-10-14T10:00:00+05:00' },
      { run_id: 'newer', site_id: 'b', created_at: '2026-10-14T08:00:00Z' },
      { run_id: 'invalid', created_at: 'invalid' },
    ];
    const before = structuredClone(runs);
    const sites = groupRunsIntoSites(runs);
    expect(sites.map((site) => site.id)).toEqual(['b', 'a', UNASSIGNED_SITE_ID]);
    expect(sites[1].inspections.map((item) => item.id)).toEqual(['new', 'old', 'missing']);
    expect(sites[2].inspections[0].createdAt).toBeNull();
    expect(runs).toEqual(before);
  });
});

it('rejects failed HTTP requests and malformed run lists without a fixture fallback', async () => {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValueOnce(new Response('', { status: 503 }))
      .mockResolvedValueOnce(Response.json({ runs: [] })),
  );
  await expect(fetchRuns()).rejects.toThrow('503');
  await expect(fetchRuns()).rejects.toThrow('Invalid runs response');
});

it('sends the stored authorization header with inspection requests', async () => {
  sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
  const fetch = vi.fn().mockResolvedValue(Response.json([]));
  vi.stubGlobal('fetch', fetch);
  await fetchRuns();
  expect(fetch).toHaveBeenCalledWith('/api/runs', {
    signal: undefined,
    headers: { Authorization: 'Bearer test-api-value' },
  });
});

it('lists inspection statuses from the API and uses the B1 sole run status before S1', async () => {
  const fetch = vi.fn(async (url: string) => {
    if (url === '/api/sites') return Response.json([{ id: 1, org_id: 1, name: 'North site' }]);
    if (url === '/api/inspections/7/runs')
      return Response.json([{ id: 'run-7', inspection_id: 7, status: 'uploaded' }]);
    return Response.json([
      {
        id: 7,
        site_id: 1,
        mode: 'quick_scan',
        status: 'created',
        created_at: '2026-10-23T12:00:00Z',
      },
      {
        id: 8,
        site_id: 1,
        mode: 'quick_scan',
        status: 'failed',
        created_at: '2026-10-22T12:00:00Z',
      },
      {
        id: 9,
        site_id: 1,
        mode: 'monitoring',
        status: 'future_state',
        created_at: '2026-10-21T12:00:00Z',
      },
    ]);
  });
  vi.stubGlobal('fetch', fetch);
  const sites = await listInspections();
  expect(sites[0].name).toBe('North site');
  expect(sites[0].inspections.map(({ id, status }) => [id, status])).toEqual([
    ['7', 'uploaded'],
    ['8', 'failed'],
    ['9', 'unknown'],
  ]);
  expect(sites[0].inspections[0].runId).toBe('run-7');
  expect(fetch.mock.calls.some(([url]) => url === '/api/runs')).toBe(false);
});

it('does not guess the current run from UUID ordering when B1 has multiple runs', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) =>
      Response.json(
        url.endsWith('/runs')
          ? [
              { id: 'a', status: 'uploaded' },
              { id: 'b', status: 'done' },
            ]
          : { id: 7, site_id: 1, status: 'created' },
      ),
    ),
  );
  expect(await getInspection('7')).toMatchObject({ status: 'created', runId: undefined });
});

it('resolves the S1 latest run id while B1 still reports inspection status created', async () => {
  const fetch = vi.fn(async (url: string) => {
    if (url === '/api/inspections/7')
      return Response.json({
        id: 7,
        site_id: 1,
        status: 'created',
        run_id: 'a-latest',
        steps: [],
      });
    if (url === '/api/inspections/7/runs')
      return Response.json([
        { id: 'a-latest', status: 'uploaded' },
        { id: 'z-older', status: 'done' },
      ]);
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal('fetch', fetch);
  expect(await getInspection('7')).toMatchObject({
    status: 'uploaded',
    runId: 'a-latest',
    steps: [],
  });
  expect(fetch.mock.calls.map(([url]) => url)).toEqual([
    '/api/inspections/7',
    '/api/inspections/7/runs',
  ]);
});

it('retains an unfamiliar status from the inspection API as display text', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) =>
      Response.json(
        url === '/api/sites'
          ? [{ id: 1, org_id: 1, name: 'North site' }]
          : [{ id: 7, site_id: 1, status: 'awaiting_review' }],
      ),
    ),
  );
  expect((await listInspections())[0].inspections[0]).toMatchObject({
    status: 'unknown',
    reportedStatus: 'awaiting_review',
  });
});

it('reads inspection detail and stage fields from its own endpoint with auth', async () => {
  sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
  const fetch = vi.fn().mockResolvedValue(
    Response.json({
      id: 7,
      site_id: 1,
      status: 'running',
      stages: [
        { name: 'ingest', status: 'done' },
        { name: 'frames', status: 'running' },
      ],
    }),
  );
  vi.stubGlobal('fetch', fetch);
  const detail = await getInspection('7');
  expect(detail.stages).toEqual([
    { name: 'ingest', status: 'done' },
    { name: 'frames', status: 'running' },
  ]);
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/inspections/7', {
    signal: undefined,
    headers: { Authorization: 'Bearer test-api-value' },
  });
});

it.each(['uploaded', 'queued', 'running', 'done', 'failed'] as const)(
  'preserves inspection status %s',
  (status) => {
    expect(inspectionStatus(status)).toBe(status);
  },
);

it('maps legacy completed to done and unfamiliar states to unknown', () => {
  expect(inspectionStatus('completed')).toBe('done');
  expect(inspectionStatus('future_state')).toBe('unknown');
});

it.each(['html', '404'] as const)(
  'lists the actual legacy catalog when CRUD is absent (%s)',
  async (kind) => {
    const fetch = vi.fn(async (url: string) => {
      if (url === '/api/inspections')
        return kind === 'html'
          ? new Response('<!doctype html><html></html>', {
              headers: { 'Content-Type': 'text/html' },
            })
          : new Response('', { status: 404 });
      if (url === '/api/runs') return Response.json(catalog);
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetch);
    const sites = await listInspections();
    expect(sites).toHaveLength(1);
    expect(sites[0].id).toBe('qarau:unassigned');
    expect(sites[0].inspections.map(({ id, status }) => [id, status])).toEqual([
      ['catalog-upload', 'uploaded'],
      ['catalog-failed', 'failed'],
      ['catalog-export', 'unknown'],
    ]);
    expect(sites[0].inspections[2]).toMatchObject({
      title: 'Legacy export',
      runId: 'catalog-export',
      source: 'survey.mp4',
      legacyRun: { run_id: 'catalog-export' },
    });
    expect(fetch.mock.calls.map(([url]) => url)).toEqual(['/api/inspections', '/api/runs']);
  },
);

it.each([401, 403, 500, 503])(
  'never hides HTTP %i by falling back to the run catalog',
  async (status) => {
    const fetch = vi.fn().mockResolvedValue(new Response('', { status }));
    vi.stubGlobal('fetch', fetch);
    await expect(listInspections()).rejects.toThrow(String(status));
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/inspections', {
      signal: undefined,
      headers: {},
    });
  },
);

it('keeps a malformed inspection response visible and never substitutes legacy runs', async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json({ inspections: [] }));
  vi.stubGlobal('fetch', fetch);
  await expect(listInspections()).rejects.toThrow('Invalid inspections response');
  expect(fetch).toHaveBeenCalledOnce();
});

it('keeps real inspection statuses visible when site names are unavailable', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) =>
      url === '/api/sites'
        ? new Response('', { status: 404 })
        : Response.json([
            { id: 7, site_id: 1, status: 'uploaded', created_at: '2026-10-23T12:00:00Z' },
          ]),
    ),
  );
  const sites = await listInspections();
  expect(sites[0]).toMatchObject({
    id: '1',
    name: '',
    inspections: [{ id: '7', status: 'uploaded' }],
  });
});

it('lists existing read-only exports when the fresh inspection database is empty', async () => {
  const fetch = vi.fn(async (url: string) => {
    if (url === '/api/inspections') return Response.json([]);
    if (url === '/api/runs') return Response.json(legacyCatalog);
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal('fetch', fetch);
  const sites = await listInspections();
  expect(sites).toHaveLength(1);
  expect(sites[0].id).toBe('qarau:unassigned');
  expect(sites[0].inspections.map(({ id, title, status }) => [id, title, status])).toEqual([
    ['walkdown-2026-10-23', 'walkdown-2026-10-23', 'unknown'],
    ['tank-survey-2026-10-22', 'tank-survey-2026-10-22', 'unknown'],
  ]);
  expect(sites[0].inspections[0]).toMatchObject({
    runId: 'walkdown-2026-10-23',
    source: 'walkdown.mp4',
    legacyRun: { run_id: 'walkdown-2026-10-23' },
  });
  expect(fetch.mock.calls.map(([url]) => url)).toEqual(['/api/inspections', '/api/runs']);
});

it('keeps genuinely empty inspection and export catalogs empty', async () => {
  const fetch = vi.fn().mockImplementation(async () => Response.json([]));
  vi.stubGlobal('fetch', fetch);
  expect(await listInspections()).toEqual([]);
  expect(fetch.mock.calls.map(([url]) => url)).toEqual(['/api/inspections', '/api/runs']);
});

it('reports a catalog failure when an empty inspection table needs legacy exports', async () => {
  const fetch = vi.fn(async (url: string) =>
    url === '/api/inspections' ? Response.json([]) : new Response('', { status: 503 }),
  );
  vi.stubGlobal('fetch', fetch);
  await expect(listInspections()).rejects.toThrow('503');
  expect(fetch.mock.calls.map(([url]) => url)).toEqual(['/api/inspections', '/api/runs']);
});
