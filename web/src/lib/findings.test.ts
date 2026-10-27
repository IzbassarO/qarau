import { afterEach, expect, it, vi } from 'vitest';
import fixture from '../test/fixtures/finding.json';
import {
  findingKeys,
  getFinding,
  listDecisions,
  listFindings,
  listFindingsForRun,
  postDecision,
} from './findings';

afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

it('reads inspection findings, detail and decisions with exact URLs and the session header', async () => {
  sessionStorage.setItem('qarau.auth.session', 'Bearer test-session');
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(Response.json([fixture.finding]))
    .mockResolvedValueOnce(Response.json(fixture.finding))
    .mockResolvedValueOnce(Response.json(fixture.decisions));
  vi.stubGlobal('fetch', fetch);
  const signal = new AbortController().signal;
  await expect(listFindings(41, signal)).resolves.toEqual([fixture.finding]);
  await expect(getFinding(701, signal)).resolves.toEqual(fixture.finding);
  await expect(listDecisions(701, signal)).resolves.toEqual(fixture.decisions);
  expect(fetch.mock.calls).toEqual([
    ['/api/inspections/41/findings', { signal, headers: { Authorization: 'Bearer test-session' } }],
    ['/api/findings/701', { signal, headers: { Authorization: 'Bearer test-session' } }],
    ['/api/findings/701/decisions', { signal, headers: { Authorization: 'Bearer test-session' } }],
  ]);
});

it('resolves a viewer run directory to the DB run ID and filters out other runs', async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(Response.json([{ id: 41 }]))
    .mockResolvedValueOnce(
      Response.json([
        { id: 'fixture-notre-dame-run', inspection_id: 41, run_dir: 'notre-dame-p3e' },
        { id: 'another-run', inspection_id: 41, run_dir: 'another-dir' },
      ]),
    )
    .mockResolvedValueOnce(
      Response.json([fixture.finding, { ...fixture.finding, id: 702, run_id: 'another-run' }]),
    );
  vi.stubGlobal('fetch', fetch);
  await expect(listFindingsForRun('notre-dame-p3e')).resolves.toEqual([fixture.finding]);
  expect(fetch.mock.calls.map(([url]) => url)).toEqual([
    '/api/inspections',
    '/api/inspections/41/runs',
    '/api/inspections/41/findings',
  ]);
});

it('returns an authoritative empty list when the run has no associated inspection', async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json([]));
  vi.stubGlobal('fetch', fetch);
  await expect(listFindingsForRun('legacy-export')).resolves.toEqual([]);
  expect(fetch).toHaveBeenCalledOnce();
});

it.each([
  { verdict: 'confirm' as const },
  { verdict: 'reject' as const, comment: 'Not a defect.' },
  {
    verdict: 'reclass' as const,
    comment: 'Coating only.',
    new_class: 'coating' as const,
    new_severity: 'watch' as const,
  },
])('posts the exact $verdict body, preserving nullable output fields', async (body) => {
  sessionStorage.setItem('qarau.auth.session', 'Bearer test-session');
  const fetch = vi.fn().mockResolvedValue(Response.json(fixture.decisions[0], { status: 201 }));
  vi.stubGlobal('fetch', fetch);
  await expect(postDecision(701, body)).resolves.toEqual(fixture.decisions[0]);
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/findings/701/decisions', {
    method: 'POST',
    signal: undefined,
    headers: { Authorization: 'Bearer test-session', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
});

it.each([401, 403, 404, 422, 503])(
  'propagates HTTP %i without silently returning an empty result',
  async (status) => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() => Promise.resolve(new Response(null, { status }))),
    );
    await expect(getFinding(701)).rejects.toThrow();
    await expect(postDecision(701, { verdict: 'confirm' })).rejects.toThrow(String(status));
  },
);

it('uses distinct query keys for the run, finding and append-only decisions', () => {
  expect(findingKeys.run('notre-dame-p3e')).toEqual(['findings', 'run', 'notre-dame-p3e']);
  expect(findingKeys.detail(701)).toEqual(['findings', 'detail', 701]);
  expect(findingKeys.decisions(701)).toEqual(['findings', 'detail', 701, 'decisions']);
});

it('uses an explicit catalog run association without reading unrelated inspection runs', async () => {
  const fetch = vi.fn(async (url: string) => {
    if (url === '/api/inspections')
      return Response.json([
        { id: 41, run_id: 'fixture-notre-dame-run' },
        { id: 101, run_id: 'unrelated-failed-run' },
      ]);
    if (url === '/api/inspections/41/findings')
      return Response.json([
        fixture.finding,
        { ...fixture.finding, id: 702, run_id: 'previous-run' },
      ]);
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal('fetch', fetch);
  await expect(listFindingsForRun('fixture-notre-dame-run')).resolves.toEqual([fixture.finding]);
  expect(fetch.mock.calls.map(([url]) => url)).toEqual([
    '/api/inspections',
    '/api/inspections/41/findings',
  ]);
});
