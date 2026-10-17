import { describe, expect, it, vi, afterEach } from 'vitest';
import { fetchRuns, groupRunsIntoSites, UNASSIGNED_SITE_ID, type RunSummary } from './inspections';

afterEach(() => vi.unstubAllGlobals());

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
      title: 'survey.mp4',
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
