import { afterEach, expect, it, vi } from 'vitest';
import { LANGS } from '../i18n';
import fixture from '../test/fixtures/finding.json';
import decisions from '../test/fixtures/decisions.json';
import { ApiError } from './api';
import {
  listDecisions,
  postDecision,
  decisionErrorKey,
  findingKeys,
  formatArea,
  getFinding,
  listFindings,
} from './findings';

afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

it('reads a run list and detail with exact API URLs and the Authorization header', async () => {
  sessionStorage.setItem('qarau.auth.session', 'Bearer test-session');
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(Response.json([{ id: 41 }]))
    .mockResolvedValueOnce(
      Response.json([
        { id: 'fixture-notre-dame-run', inspection_id: 41, run_dir: 'notre-dame-p3e' },
      ]),
    )
    .mockResolvedValueOnce(Response.json([fixture.finding]))
    .mockResolvedValueOnce(Response.json(fixture.finding));
  vi.stubGlobal('fetch', fetch);
  const signal = new AbortController().signal;
  await expect(listFindings('notre-dame-p3e', signal)).resolves.toEqual([fixture.finding]);
  const finding = await getFinding(701, signal);
  expect(finding).toEqual(fixture.finding);
  expect(finding).toMatchObject({
    id: 701,
    inspection_id: 41,
    run_id: 'fixture-notre-dame-run',
    class: 'corrosion',
    severity: 'critical',
    status: 'confirmed',
    area_units: 3.5,
    area_m2: 1.234,
    scale: 'reference: measured sheet',
    point_indices: [0, 1, 2],
  });
  expect(finding.evidence).toHaveLength(3);
  expect(finding.evidence[0]).toEqual({
    frame_id: 0,
    image_url: '/api/runs/notre-dame-p3e/files/frames/000000.jpg',
    outline: [
      [250, 100],
      [320, 110],
      [330, 240],
      [260, 230],
    ],
    confidence: 0.987654,
  });
  expect(fetch.mock.calls).toEqual([
    ['/api/inspections', { signal, headers: { Authorization: 'Bearer test-session' } }],
    ['/api/inspections/41/runs', { signal, headers: { Authorization: 'Bearer test-session' } }],
    ['/api/inspections/41/findings', { signal, headers: { Authorization: 'Bearer test-session' } }],
    ['/api/findings/701', { signal, headers: { Authorization: 'Bearer test-session' } }],
  ]);
});

it('filters findings from other runs in the same inspection', async () => {
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
  await expect(listFindings('notre-dame-p3e')).resolves.toEqual([fixture.finding]);
});

it('returns an authoritative empty list when the run has no associated inspection', async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json([]));
  vi.stubGlobal('fetch', fetch);
  await expect(listFindings('legacy-export')).resolves.toEqual([]);
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/inspections', {
    signal: undefined,
    headers: {},
  });
});

it('preserves nullable area and comment fields from the API', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      Response.json({
        ...fixture.finding,
        area_m2: null,
        comment: null,
        scale: 'not set',
      }),
    ),
  );
  await expect(getFinding(701)).resolves.toMatchObject({
    id: 701,
    area_m2: null,
    comment: null,
    scale: 'not set',
  });
});

it.each([
  '/api/inspections',
  '/api/inspections/41/runs',
  '/api/inspections/41/findings',
  '/api/findings/701',
])('throws a typed error on HTTP 500 at %s', async (path) => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (url === path) return new Response(null, { status: 500 });
      if (url === '/api/inspections') return Response.json([{ id: 41 }]);
      if (url === '/api/inspections/41/runs')
        return Response.json([
          { id: 'fixture-notre-dame-run', inspection_id: 41, run_dir: 'notre-dame-p3e' },
        ]);
      throw new Error(`Unexpected request: ${url}`);
    }),
  );
  const request = path === '/api/findings/701' ? getFinding(701) : listFindings('notre-dame-p3e');
  await expect(request).rejects.toBeInstanceOf(ApiError);
  await expect(request).rejects.toMatchObject({ status: 500, path });
});

it.each([401, 403, 404, 422, 503])(
  'propagates HTTP %i without returning an empty list',
  async (status) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(null, { status })),
    );
    await expect(getFinding(701)).rejects.toBeInstanceOf(ApiError);
    await expect(listFindings('notre-dame-p3e')).rejects.toMatchObject({ status });
  },
);

it('uses distinct query keys for a run and a finding', () => {
  expect(findingKeys.run('notre-dame-p3e')).toEqual(['findings', 'run', 'notre-dame-p3e']);
  expect(findingKeys.detail(701)).toEqual(['findings', 'detail', 701]);
});

it('uses an explicit catalog run association without reading unrelated inspection histories', async () => {
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
  await expect(listFindings('fixture-notre-dame-run')).resolves.toEqual([fixture.finding]);
  expect(fetch.mock.calls.map(([url]) => url)).toEqual([
    '/api/inspections',
    '/api/inspections/41/findings',
  ]);
});

it('formats a scaled API measurement to one decimal in square metres', () => {
  expect(
    formatArea(
      { area_m2: 1.234, area_units: 3.5, scale: 'reference: sheet' },
      (key) => LANGS.en[key],
    ),
  ).toBe('1.2 m²');
});

it.each([null, 1.234])('makes the missing scale explicit in EN (metric area %s)', (area_m2) => {
  expect(formatArea({ area_m2, area_units: 3.5, scale: 'not set' }, (key) => LANGS.en[key])).toBe(
    '3.5 units · scale not set',
  );
});

it('fetches decisions from the exact API path with the session header and abort signal', async () => {
  sessionStorage.setItem('qarau.auth.session', 'Bearer test-session');
  const fetch = vi.fn().mockResolvedValue(Response.json(decisions));
  vi.stubGlobal('fetch', fetch);
  const signal = new AbortController().signal;
  await expect(listDecisions(701, signal)).resolves.toEqual(decisions);
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/findings/701/decisions', {
    signal,
    headers: { Authorization: 'Bearer test-session' },
  });
});

it.each([
  [
    { verdict: 'reject' as const, comment: 'Rust is paint' },
    { verdict: 'reject', comment: 'Rust is paint' },
  ],
  [
    {
      verdict: 'reclass' as const,
      comment: 'Coating has peeled away.',
      new_class: 'coating' as const,
    },
    { verdict: 'reclass', comment: 'Coating has peeled away.', new_class: 'coating' },
  ],
  [
    { verdict: 'confirm' as const, comment: '' },
    { verdict: 'confirm', comment: '' },
  ],
])('posts the API body without changing the comment: %j', async (input, expected) => {
  sessionStorage.setItem('qarau.auth.session', 'Bearer test-session');
  const fetch = vi.fn().mockResolvedValue(Response.json(decisions[2], { status: 201 }));
  vi.stubGlobal('fetch', fetch);
  await expect(postDecision(701, input)).resolves.toEqual(decisions[2]);
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/findings/701/decisions', {
    method: 'POST',
    headers: { Authorization: 'Bearer test-session', 'Content-Type': 'application/json' },
    body: JSON.stringify(expected),
  });
});

it.each([401, 403, 404, 422, 503])(
  'surfaces decision GET and POST failures for HTTP %i in plain words',
  async (status) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Response.json({ detail: 'Could not record this decision.' }, { status })),
    );
    for (const request of [
      listDecisions(701),
      postDecision(701, { verdict: 'reject', comment: 'Rust is paint' }),
    ]) {
      try {
        await request;
        expect.fail('Expected the API error');
      } catch (error) {
        expect(error).toBeInstanceOf(ApiError);
        expect(LANGS.en[decisionErrorKey(error)]).toMatch(/[a-z]+ [a-z]+/i);
        expect(LANGS.en[decisionErrorKey(error)]).not.toBe(String(status));
      }
    }
  },
);
