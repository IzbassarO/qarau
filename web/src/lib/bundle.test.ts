import { afterEach, describe, expect, it, vi } from 'vitest';
import { hasRgb, loadBundle, loadManifest, runUrl, type Finding, type Manifest } from './bundle';

afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

const routeId = 'copied run';
const rgb = new Uint8Array([12, 34, 56, 78, 90, 123]);

function manifest(color?: string): Manifest {
  return {
    version: 2,
    run_id: 'original',
    source: { kind: 'images', name: 'survey' },
    world: { up: [0, 0, 1], bounds: { min: [0, 0, 0], max: [1, 2, 3] }, z_range: [0, 3] },
    points: {
      count: 2,
      position: 'position.bin',
      color,
      first_seen: 'first-seen.bin',
      num_obs: '',
    },
    tracks: {
      frame_offsets: 'frame-offsets.bin',
      frame_points: 'frame-points.bin',
      point_offsets: 'point-offsets.bin',
      point_frames: 'point-frames.bin',
    },
    frames: [
      {
        i: 0,
        t: 0,
        image: 'frame.jpg',
        thumb: 'thumb.jpg',
        kept: true,
        drop_reason: null,
        blur: null,
        registered: true,
      },
    ],
    stats: { frames_extracted: 1, frames_dropped: 0, frames_registered: 1, points: 2 },
  };
}

function files(): Record<string, ArrayBuffer | string> {
  return {
    'position.bin': new Float32Array([0, 0, 0, 1, 2, 3]).buffer,
    'color.bin': rgb.buffer,
    'first-seen.bin': new Uint16Array([0, 65535]).buffer,
    'frame-offsets.bin': new Uint32Array([0, 1]).buffer,
    'frame-points.bin': new Uint32Array([0]).buffer,
    'point-offsets.bin': new Uint32Array([0, 1, 1]).buffer,
    'point-frames.bin': new Uint16Array([0]).buffer,
  };
}

function serveFiles(assets = files()) {
  const fetch = vi.fn(async (url: string) => {
    const prefix = runUrl(routeId, '');
    const body = url.startsWith(prefix) ? assets[url.slice(prefix.length)] : undefined;
    return body === undefined ? new Response('', { status: 404 }) : new Response(body);
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

it('sends the session header with the protected manifest and every bundle file', async () => {
  sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
  const fetchManifest = vi.fn().mockResolvedValue(Response.json(manifest()));
  vi.stubGlobal('fetch', fetchManifest);
  await loadManifest(routeId);
  expect(fetchManifest).toHaveBeenCalledWith(
    `/api/runs/${encodeURIComponent(routeId)}/manifest.json`,
    {
      headers: { Authorization: 'Bearer test-api-value' },
    },
  );
  const fetchFiles = serveFiles();
  await loadBundle(routeId, manifest(), vi.fn());
  for (const [, options] of fetchFiles.mock.calls as unknown as [string, RequestInit][])
    expect(options.headers).toEqual({ Authorization: 'Bearer test-api-value' });
});

describe('optional bundle RGB', () => {
  it('loads real RGB without changing point, track or assembly data', async () => {
    const fetch = serveFiles();
    const progress = vi.fn();
    const bundle = await loadBundle(routeId, manifest('color.bin'), progress);

    expect(bundle.color).toEqual(rgb);
    expect(hasRgb(bundle)).toBe(true);
    expect(bundle.position).toEqual(new Float32Array([0, 0, 0, 1, 2, 3]));
    expect(bundle.firstSeen).toEqual(new Uint16Array([0, 65535]));
    expect(bundle.frameOffsets).toEqual(new Uint32Array([0, 1]));
    expect(bundle.frameInPoints).toEqual(new Uint32Array([0]));
    expect(bundle.pointOffsets).toEqual(new Uint32Array([0, 1, 1]));
    expect(bundle.pointFrames).toEqual(new Uint16Array([0]));
    expect(bundle.assembled).toEqual(new Uint32Array([1]));
    expect(fetch).toHaveBeenCalledWith(runUrl(routeId, 'color.bin'), { headers: {} });
    expect(progress).toHaveBeenLastCalledWith(1);
  });

  it.each([undefined, '', '   '])('skips an absent colour path (%j)', async (color) => {
    const fetch = serveFiles();
    const bundle = await loadBundle(routeId, manifest(color), vi.fn());

    expect(bundle.color).toBeUndefined();
    expect(hasRgb(bundle)).toBe(false);
    expect(bundle.position).toHaveLength(6);
    expect(fetch).toHaveBeenCalledTimes(6);
  });

  it.each([404, 503, 'network'])(
    'keeps the geometry usable when RGB fails with %s',
    async (failure) => {
      const assets = files();
      const fetch = serveFiles(assets);
      fetch.mockImplementation(async (url: string) => {
        if (url === runUrl(routeId, 'color.bin')) {
          if (failure === 'network') throw new TypeError('Failed to fetch');
          return new Response('', { status: Number(failure) });
        }
        return new Response(assets[url.slice(runUrl(routeId, '').length)]);
      });

      const bundle = await loadBundle(routeId, manifest('color.bin'), vi.fn());
      expect(bundle.color).toBeUndefined();
      expect(hasRgb(bundle)).toBe(false);
      expect(bundle.position).toHaveLength(6);
      expect(bundle.assembled).toEqual(new Uint32Array([1]));
    },
  );

  it.each([0, 3, 5, 7])('rejects a colour buffer with %i bytes for two points', async (length) => {
    serveFiles({ ...files(), 'color.bin': new Uint8Array(length).buffer });
    const bundle = await loadBundle(routeId, manifest('color.bin'), vi.fn());
    expect(bundle.color).toBeUndefined();
    expect(hasRgb(bundle)).toBe(false);
  });

  it('requires a nonempty Uint8Array with one RGB triplet per point', async () => {
    serveFiles();
    const bundle = await loadBundle(routeId, manifest('color.bin'), vi.fn());
    expect(hasRgb({ ...bundle, color: undefined })).toBe(false);
    expect(hasRgb({ ...bundle, color: new Uint8Array(3) })).toBe(false);
    expect(hasRgb({ ...bundle, color: new Float32Array(6) as unknown as Uint8Array })).toBe(false);
    expect(
      hasRgb({
        ...bundle,
        color: new Uint8Array(0),
        manifest: { ...bundle.manifest, points: { ...bundle.manifest.points, count: 0 } },
      }),
    ).toBe(false);
  });

  it('still rejects a required geometry file when RGB is optional', async () => {
    const assets = files();
    delete assets['position.bin'];
    serveFiles(assets);
    await expect(loadBundle(routeId, manifest(), vi.fn())).rejects.toThrow('HTTP 404');
  });

  it('preserves finding data when RGB is unavailable', async () => {
    const finding: Finding = {
      id: 1,
      class: 'corrosion',
      severity: 'critical',
      status: 'confirmed',
      n_points: 1,
      centroid: [0, 0, 0],
      bbox: { min: [0, 0, 0], max: [0, 0, 0] },
      frames: [0],
    };
    serveFiles({
      ...files(),
      'findings.json': JSON.stringify({
        version: 2,
        findings: [finding],
        point_finding: 'point-finding.bin',
      }),
      'point-finding.bin': new Uint16Array([1, 0]).buffer,
    });
    const bundle = await loadBundle(
      routeId,
      { ...manifest('missing-color.bin'), findings: 'findings.json' },
      vi.fn(),
    );
    expect(bundle.findings).toEqual([finding]);
    expect(bundle.pointFinding).toEqual(new Uint16Array([1, 0]));
    expect(bundle.findingsError).toBeUndefined();
    expect(hasRgb(bundle)).toBe(false);
  });

  it('keeps a missing findings error visible when RGB is also unavailable', async () => {
    serveFiles();
    const bundle = await loadBundle(routeId, { ...manifest(), findings: 'missing.json' }, vi.fn());
    expect(bundle.findingsError).toBe('missing');
    expect(bundle.findings).toBeUndefined();
    expect(hasRgb(bundle)).toBe(false);
  });
});
