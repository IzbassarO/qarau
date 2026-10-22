import { afterEach, describe, expect, it, vi } from 'vitest';
import { readDenseFixture } from '../fixtures/dense-bundle/readFixture';
import {
  FarLayerError,
  framesForDensePoint,
  hasRgb,
  loadBundle,
  loadManifest,
  runUrl,
  type DenseManifest,
  type Finding,
  type Manifest,
} from './bundle';

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

describe('optional dense layer with frame links (ADR 0017)', () => {
  async function parseFixture(
    dense: Manifest['dense'],
    changeFixture?: (fixture: ReturnType<typeof readDenseFixture>) => void,
  ) {
    const fixture = readDenseFixture();
    fixture.manifest.dense = dense;
    changeFixture?.(fixture);
    const prefix = runUrl(routeId, '');
    const fetch = vi.fn(async (url: string) => {
      if (url === `/api/runs/${encodeURIComponent(routeId)}/manifest.json`)
        return Response.json(fixture.manifest);
      const body = url.startsWith(prefix) ? fixture.files.get(url.slice(prefix.length)) : undefined;
      return body === undefined
        ? new Response(null, { status: 404 })
        : new Response(Uint8Array.from(body).buffer);
    });
    vi.stubGlobal('fetch', fetch);
    const bundle = await loadBundle(routeId, await loadManifest(routeId), vi.fn());
    return { bundle, fetch };
  }

  it('reads the four-point fixture as typed arrays and returns its exact CSR frame IDs', async () => {
    const { bundle } = await parseFixture(readDenseFixture().manifest.dense);
    const dense = bundle.dense!;
    expect(dense.count).toBe(4);
    expect(dense.position).toBeInstanceOf(Float32Array);
    expect(dense.position).toHaveLength(12);
    expect(dense.position.subarray(0, 3)).toEqual(new Float32Array([0, 0, 0.25]));
    expect(dense.color).toBeInstanceOf(Uint8Array);
    expect(dense.color).toEqual(
      new Uint8Array([220, 50, 30, 30, 220, 50, 50, 30, 220, 220, 200, 30]),
    );
    expect(dense.frameOffsets).toBeInstanceOf(Uint32Array);
    expect(dense.frameOffsets).toEqual(new Uint32Array([0, 2, 3, 3, 5]));
    expect(dense.frameIds).toBeInstanceOf(Uint32Array);
    expect(dense.frameIds).toEqual(new Uint32Array([0, 2, 1, 0, 1]));
    expect(Array.from(framesForDensePoint(dense, 0))).toEqual([0, 2]);
    expect(Array.from(framesForDensePoint(dense, 1))).toEqual([1]);
    expect(Array.from(framesForDensePoint(dense, 2))).toEqual([]);
    expect(Array.from(framesForDensePoint(dense, 3))).toEqual([0, 1]);
    expect(framesForDensePoint(dense, 0).buffer).toBe(dense.frameIds.buffer);
    expect(dense.densifier).toBe('fake');
    expect(dense.frameLinksSource).toBe('colmap_fusion');
    expect(bundle.manifest.points.count).toBe(8);
    expect(bundle.position).toHaveLength(24);
  });

  it('sends the authenticated session with every dense array request', async () => {
    sessionStorage.setItem('qarau.auth.session', 'Bearer dense-fixture-session');
    const { fetch } = await parseFixture(readDenseFixture().manifest.dense);
    for (const path of [
      'web/dense.position.f32',
      'web/dense.color.u8',
      'web/dense.frame_offsets.u32',
      'web/dense.frame_ids.u32',
    ])
      expect(fetch).toHaveBeenCalledWith(runUrl(routeId, path), {
        headers: { Authorization: 'Bearer dense-fixture-session' },
      });
  });

  it.each(['position.f32', 'color.u8', 'frame_offsets.u32', 'frame_ids.u32'])(
    'rejects a truncated dense %s buffer',
    async (filename) => {
      await expect(
        parseFixture(readDenseFixture().manifest.dense, ({ files }) => {
          const path = `web/dense.${filename}`;
          const bytes = files.get(path)!;
          files.set(path, bytes.subarray(0, bytes.length - 1));
        }),
      ).rejects.toThrow('invalid dense buffer lengths');
    },
  );

  it('rejects an offsets descriptor that does not declare count + 1 entries', async () => {
    const dense = readDenseFixture().manifest.dense as DenseManifest;
    dense.frame_offsets.length = 4;
    await expect(parseFixture(dense)).rejects.toThrow('invalid dense manifest');
  });

  it.each([
    ['nonzero first offset', 'frame_offsets.u32', [1, 2, 3, 3, 5], 'invalid dense frame offsets'],
    ['nonmonotone offsets', 'frame_offsets.u32', [0, 2, 1, 3, 5], 'invalid dense frame offsets'],
    [
      'offset beyond frame ids',
      'frame_offsets.u32',
      [0, 2, 6, 3, 5],
      'invalid dense frame offsets',
    ],
    ['wrong terminal offset', 'frame_offsets.u32', [0, 2, 3, 3, 4], 'invalid dense frame offsets'],
    ['out-of-range frame id', 'frame_ids.u32', [0, 3, 1, 0, 1], 'invalid dense frame id'],
  ] as const)('rejects %s in dense CSR links', async (_, filename, values, message) => {
    await expect(
      parseFixture(readDenseFixture().manifest.dense, ({ files }) => {
        const bytes = files.get(`web/dense.${filename}`)!;
        values.forEach((value, index) => bytes.writeUInt32LE(value, index * 4));
      }),
    ).rejects.toThrow(message);
  });

  it('preserves the older depth-prior metadata without loading a separate dense layer', async () => {
    const { bundle, fetch } = await parseFixture({
      method: 'depth_prior',
      frames_used: 3,
      points: 8,
    });
    expect(bundle.manifest.dense).toEqual({ method: 'depth_prior', frames_used: 3, points: 8 });
    expect(bundle.dense).toBeUndefined();
    expect(bundle.position).toHaveLength(24);
    expect(fetch.mock.calls.some(([url]) => url.includes('/dense.'))).toBe(false);
  });

  it('keeps a bundle without dense unchanged', async () => {
    const { bundle, fetch } = await parseFixture(undefined);
    expect(bundle.manifest.dense).toBeUndefined();
    expect(bundle.dense).toBeUndefined();
    expect(bundle.position).toHaveLength(24);
    expect(fetch.mock.calls.some(([url]) => url.includes('/dense.'))).toBe(false);
  });
});

describe('optional far layer (ADR 0003)', () => {
  const far: NonNullable<Manifest['far']> = {
    count: 1,
    position: 'web/far.position.f32',
    color: 'web/far.color.u8',
    rule: 'nearest camera distance > 2.5 x median',
  };
  const position = new Float32Array([100, 200, 300]);
  const color = new Uint8Array([21, 43, 65]);
  const farFiles = () => ({
    ...files(),
    [far.position]: position.buffer,
    [far.color]: color.buffer,
  });

  it('loads far XYZ and RGB into separate typed arrays without changing object data', async () => {
    serveFiles(farFiles());
    const original = await loadBundle(routeId, manifest('color.bin'), vi.fn());
    const progress = vi.fn();
    const bundle = await loadBundle(routeId, { ...manifest('color.bin'), far }, progress);
    expect(bundle.far?.position).toBeInstanceOf(Float32Array);
    expect(bundle.far?.position).toEqual(position);
    expect(bundle.far?.color).toBeInstanceOf(Uint8Array);
    expect(bundle.far?.color).toEqual(color);
    expect(bundle.farError).toBeUndefined();
    expect(bundle.manifest.points.count).toBe(2);
    expect(bundle.position).toHaveLength(6);
    expect(bundle.far?.position.buffer).not.toBe(bundle.position.buffer);
    for (const key of [
      'position',
      'color',
      'firstSeen',
      'frameOffsets',
      'frameInPoints',
      'pointOffsets',
      'pointFrames',
      'assembled',
    ] as const)
      expect(bundle[key]).toEqual(original[key]);
    expect(progress).toHaveBeenLastCalledWith(1);
  });

  it('loads a legacy bundle unchanged without fetching far arrays', async () => {
    const fetch = serveFiles();
    const bundle = await loadBundle(routeId, manifest(), vi.fn());
    expect(bundle.far).toBeUndefined();
    expect(bundle.farError).toBeUndefined();
    expect(bundle.position).toEqual(new Float32Array([0, 0, 0, 1, 2, 3]));
    expect(bundle.firstSeen).toEqual(new Uint16Array([0, 65535]));
    expect(fetch).toHaveBeenCalledTimes(6);
  });

  it('accepts an empty exported far layer', async () => {
    serveFiles({ ...files(), [far.position]: new ArrayBuffer(0), [far.color]: new ArrayBuffer(0) });
    const bundle = await loadBundle(routeId, { ...manifest(), far: { ...far, count: 0 } }, vi.fn());
    expect(bundle.far).toEqual({ position: new Float32Array(), color: new Uint8Array() });
  });

  it.each([
    ['position', undefined, 'missing'],
    ['color', undefined, 'missing'],
    ['position', new ArrayBuffer(8), 'broken'],
    ['position', new ArrayBuffer(13), 'broken'],
    ['position', new ArrayBuffer(24), 'broken'],
    ['color', new ArrayBuffer(2), 'broken'],
    ['color', new ArrayBuffer(4), 'broken'],
  ] as const)('raises a recoverable typed error for %s (%s)', async (field, buffer, reason) => {
    const assets: Record<string, ArrayBuffer | string> = farFiles();
    if (buffer === undefined) delete assets[far[field]];
    else assets[far[field]] = buffer;
    serveFiles(assets);
    const error = await loadBundle(routeId, { ...manifest(), far }, vi.fn()).catch(
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(FarLayerError);
    const failure = error as FarLayerError;
    expect(failure.message).toContain('far layer:');
    expect(failure.reason).toBe(reason);
    expect(failure.bundle.far).toBeUndefined();
    expect(failure.bundle.farError).toBe(reason);
    expect(failure.bundle.position).toEqual(new Float32Array([0, 0, 0, 1, 2, 3]));
    expect(failure.bundle.assembled).toEqual(new Uint32Array([1]));
  });

  it.each([-1, 0.5, NaN])('rejects invalid far count %s as a typed error', async (count) => {
    serveFiles(farFiles());
    await expect(
      loadBundle(routeId, { ...manifest(), far: { ...far, count } }, vi.fn()),
    ).rejects.toBeInstanceOf(FarLayerError);
  });
});

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

it('loads bundles without track files so point info can estimate visibility', async () => {
  const fetch = serveFiles();
  const bundle = await loadBundle(routeId, { ...manifest(), tracks: undefined }, vi.fn());
  expect(bundle.pointOffsets).toHaveLength(0);
  expect(bundle.pointFrames).toHaveLength(0);
  expect(bundle.frameOffsets).toEqual(new Uint32Array([0, 0]));
  expect(bundle.frameInPoints).toHaveLength(0);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('loads only explicitly declared per-point confidence and preserves metric calibration', async () => {
  const m = manifest();
  m.world.scale_m_per_unit = 0.5;
  m.points.confidence = 'confidence.bin';
  serveFiles({ ...files(), 'confidence.bin': new Float32Array([0.75, 0.5]).buffer });
  const bundle = await loadBundle(routeId, m, vi.fn());
  expect(bundle.pointConfidence).toEqual(new Float32Array([0.75, 0.5]));
  expect(bundle.manifest.world.scale_m_per_unit).toBe(0.5);
});

it.each([undefined, new ArrayBuffer(3), new ArrayBuffer(4)])(
  'leaves unreadable or incomplete point confidence unavailable (%s)',
  async (confidence) => {
    const m = manifest();
    m.points.confidence = 'confidence.bin';
    serveFiles({ ...files(), ...(confidence ? { 'confidence.bin': confidence } : {}) });
    const bundle = await loadBundle(routeId, m, vi.fn());
    expect(bundle.pointConfidence).toBeUndefined();
    expect(bundle.position).toHaveLength(6);
  },
);

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
