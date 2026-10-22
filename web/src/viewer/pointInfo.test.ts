import { describe, expect, it } from 'vitest';
import type { Bundle, FrameInfo } from '../lib/bundle';
import { buildDensePointInfo, buildPointInfo } from './pointInfo';

function frame(i: number, t: number | null = i * 1.25): FrameInfo {
  return {
    i,
    t,
    image: `frames/${i}.jpg`,
    thumb: `thumbs/${i}.jpg`,
    kept: true,
    drop_reason: null,
    blur: null,
    registered: true,
    camera: {
      width: 100,
      height: 80,
      model: 'PINHOLE',
      params: [50, 50, 50, 40],
      K: [50, 50, 50, 40],
    },
    pose: { qvec: [1, 0, 0, 0], tvec: [0, 0, 0] },
  };
}

function bundle(tracks = true): Bundle {
  return {
    runId: 'point-test',
    manifest: {
      version: 1,
      run_id: 'stale-id',
      source: { kind: 'video', name: 'test', fps: 60 },
      world: { up: [0, 0, 1], bounds: { min: [-1, -1, -1], max: [3, 4, 5] }, z_range: [-1, 5] },
      points: { count: 3, position: '', first_seen: '', num_obs: '' },
      tracks: tracks
        ? { point_offsets: '', point_frames: '', frame_offsets: '', frame_points: '' }
        : undefined,
      frames: [frame(0), frame(7, 2.75), frame(9, 4.5)],
      stats: { points: 3, frames_extracted: 3, frames_dropped: 0, frames_registered: 3 },
    },
    position: new Float32Array([0, 0, 2, 0, 0, -2, 10, 0, 2]),
    firstSeen: new Uint16Array([0, 65535, 65535]),
    assembled: new Uint32Array([1, 1, 1]),
    pointOffsets: tracks ? new Uint32Array([0, 2, 2, 2]) : new Uint32Array(),
    pointFrames: tracks ? new Uint16Array([0, 1]) : new Uint16Array(),
    frameOffsets: tracks ? new Uint32Array([0, 1, 2, 2]) : new Uint32Array(),
    frameInPoints: tracks ? new Uint32Array([0, 0]) : new Uint32Array(),
  };
}

describe('buildPointInfo', () => {
  it('uses point tracks, resolves frame indices to IDs, timestamps and route asset URLs, and counts views', () => {
    const info = buildPointInfo(bundle(), 0);
    expect(info.index).toBe(0);
    expect(info.position).toEqual({ x: 0, y: 0, z: 2 });
    expect(info.unit).toBe('model');
    expect(info.visibilitySource).toBe('track');
    expect(info.frames).toEqual([
      { frameId: 0, thumbnailUrl: '/api/runs/point-test/files/thumbs/0.jpg', timeSec: 0 },
      { frameId: 7, thumbnailUrl: '/api/runs/point-test/files/thumbs/7.jpg', timeSec: 2.75 },
    ]);
    expect(info.views).toBe(2);
  });

  it('does not replace an empty track with estimated observations', () => {
    const data = bundle();
    data.pointOffsets.fill(0);
    data.pointFrames = new Uint16Array();
    expect(buildPointInfo(data, 0)).toMatchObject({
      visibilitySource: 'track',
      views: 0,
      frames: [],
    });
  });

  it('honours reverse frame links when point-major tracks are absent', () => {
    const data = bundle();
    data.pointOffsets = new Uint32Array();
    data.pointFrames = new Uint16Array();
    expect(buildPointInfo(data, 0)).toMatchObject({ visibilitySource: 'track', views: 2 });
  });

  it('prefers existing reverse links even without a manifest tracks declaration', () => {
    const data = bundle();
    data.manifest.tracks = undefined;
    data.pointOffsets = new Uint32Array();
    data.pointFrames = new Uint16Array();
    expect(buildPointInfo(data, 0)).toMatchObject({ visibilitySource: 'track', views: 2 });
    expect(buildPointInfo(data, 2)).toMatchObject({ visibilitySource: 'track', views: 0 });
  });

  it.each([
    [0, 1, 1, 999],
    [0, 2, 1, 2],
    [1, 1, 2, 2],
  ])('estimates visibility when reverse offsets are malformed (%s)', (...offsets) => {
    const data = bundle();
    data.pointOffsets = new Uint32Array();
    data.frameOffsets = new Uint32Array(offsets);
    expect(buildPointInfo(data, 0)).toMatchObject({ visibilitySource: 'estimated', views: 3 });
  });

  it('uses fixture asset resolution and deduplicates frame links', () => {
    const data = bundle();
    data.assetUrl = (path) => `/fixtures/${path}`;
    data.pointFrames = new Uint16Array([1, 1]);
    const info = buildPointInfo(data, 0);
    expect(info.frames[0].thumbnailUrl).toBe('/fixtures/thumbs/7.jpg');
    expect(info.views).toBe(1);
  });

  it('falls back to camera projection: includes inside, excludes behind and outside points', () => {
    const data = bundle(false);
    expect(buildPointInfo(data, 0)).toMatchObject({ visibilitySource: 'estimated', views: 3 });
    expect(buildPointInfo(data, 1).frames).toEqual([]);
    expect(buildPointInfo(data, 2).frames).toEqual([]);
    data.position.set([0, 10, 2], 6);
    expect(buildPointInfo(data, 2).views).toBe(0);
  });

  it('uses camera-from-world rotation and translation and ignores unregistered cameras', () => {
    const data = bundle(false);
    data.manifest.frames[0].pose = { qvec: [0, 0, 1, 0], tvec: [0, 0, 3] };
    data.manifest.frames[1].pose!.tvec = [0, 0, -3];
    data.manifest.frames[2].registered = false;
    expect(buildPointInfo(data, 0).frames.map((f) => f.frameId)).toEqual([0]);
  });

  it.each(['SIMPLE_RADIAL', 'RADIAL'])(
    'applies %s distortion before testing image bounds',
    (model) => {
      const data = bundle(false);
      data.position.set([1.8, 0, 2]);
      data.manifest.frames = [frame(0)];
      expect(buildPointInfo(data, 0).views).toBe(1);
      data.manifest.frames[0].camera = {
        width: 100,
        height: 80,
        model,
        params: [50, 50, 40, 1, 0.1],
        K: [50, 50, 50, 40],
      };
      expect(buildPointInfo(data, 0).views).toBe(0);
    },
  );

  it('preserves absent, null and nonfinite timestamps as null without inferring FPS', () => {
    const data = bundle();
    for (const t of [undefined, null, NaN, Infinity, -1]) {
      data.manifest.frames[1].t = t;
      expect(buildPointInfo(data, 0).frames[1].timeSec).toBeNull();
    }
    expect(buildPointInfo(data, 0).frames[0].timeSec).toBe(0);
  });

  it('keeps height, distance and confidence unavailable without their explicit metadata', () => {
    const info = buildPointInfo(bundle(), 0);
    expect(info.height).toEqual({ available: false, reason: 'needs_scale' });
    expect(info.distance).toEqual({ available: false, reason: 'needs_scale' });
    expect(info.confidence).toEqual({ available: false, reason: 'needs_confidence' });
  });

  it('enables scaled coordinates, height and distance only from explicit metric scale', () => {
    const data = bundle();
    data.position.set([0, 3, 4]);
    data.manifest.world.scale_m_per_unit = 2;
    expect(buildPointInfo(data, 0)).toMatchObject({
      position: { x: 0, y: 6, z: 8 },
      unit: 'm',
      height: { available: true, value: 8 },
      distance: { available: true, value: 10 },
      confidence: { available: false, reason: 'needs_confidence' },
    });
    data.manifest.world.up = [0, 2, 0];
    expect(buildPointInfo(data, 0).height).toEqual({ available: true, value: 6 });
  });

  it.each([0, -1, NaN, Infinity])('rejects invalid metric scale %s', (scale) => {
    const data = bundle();
    data.manifest.world.scale_m_per_unit = scale;
    expect(buildPointInfo(data, 0)).toMatchObject({
      unit: 'model',
      height: { available: false },
      distance: { available: false },
    });
  });

  it('reads only a valid per-point confidence array, independently of scale', () => {
    const data = bundle();
    data.pointConfidence = new Float32Array([0.75, NaN, 2]);
    expect(buildPointInfo(data, 0).confidence).toEqual({ available: true, value: 0.75 });
    expect(buildPointInfo(data, 1).confidence.available).toBe(false);
    expect(buildPointInfo(data, 2).confidence.available).toBe(false);
    data.pointConfidence = new Float32Array([0.75]);
    expect(buildPointInfo(data, 0).confidence.available).toBe(false);
  });

  it.each([-1, 3, 0.5, NaN])('rejects invalid point index %s', (index) => {
    expect(() => buildPointInfo(bundle(), index)).toThrow(RangeError);
  });
});

describe('buildDensePointInfo', () => {
  function denseBundle(): Bundle {
    return {
      ...bundle(),
      dense: {
        count: 4,
        position: new Float32Array([0, 3, 4, 1, 1, 2, 0, 0, 2, -2, 1, 3]),
        color: new Uint8Array(12),
        frameOffsets: new Uint32Array([0, 2, 3, 3, 5]),
        frameIds: new Uint32Array([0, 2, 1, 0, 1]),
        densifier: 'colmap',
        frameLinksSource: 'colmap_fusion',
      },
    };
  }

  it('uses dense coordinates and CSR frame indices mapped to the manifest frame ids', () => {
    const data = denseBundle();
    data.assetUrl = (path) => `/fixtures/dense/${path}`;
    expect(buildDensePointInfo(data, 0)).toMatchObject({
      index: 0,
      position: { x: 0, y: 3, z: 4 },
      unit: 'model',
      visibilitySource: 'colmap_fusion',
      synthetic: false,
      views: 2,
      frames: [
        { frameId: 0, thumbnailUrl: '/fixtures/dense/thumbs/0.jpg', timeSec: 0 },
        { frameId: 9, thumbnailUrl: '/fixtures/dense/thumbs/9.jpg', timeSec: 4.5 },
      ],
      confidence: { available: false, reason: 'needs_confidence' },
    });
    expect(buildDensePointInfo(data, 1).frames).toEqual([
      { frameId: 7, thumbnailUrl: '/fixtures/dense/thumbs/7.jpg', timeSec: 2.75 },
    ]);
  });

  it('keeps an empty CSR row empty even when sparse tracks and camera projections see the point', () => {
    expect(buildDensePointInfo(denseBundle(), 2)).toMatchObject({
      visibilitySource: 'colmap_fusion',
      frames: [],
      views: 0,
    });
  });

  it('shares explicit metric calibration without reusing sparse-point confidence', () => {
    const data = denseBundle();
    data.manifest.world.scale_m_per_unit = 2;
    data.pointConfidence = new Float32Array([0.75, 0.8, 0.9]);
    expect(buildDensePointInfo(data, 0)).toMatchObject({
      position: { x: 0, y: 6, z: 8 },
      unit: 'm',
      height: { available: true, value: 8 },
      distance: { available: true, value: 10 },
      confidence: { available: false, reason: 'needs_confidence' },
    });
  });

  it('preserves sparse-derived provenance and marks fake densifier points as synthetic', () => {
    const data = denseBundle();
    data.dense!.frameLinksSource = 'sparse_tracks';
    data.dense!.densifier = 'fake';
    expect(buildDensePointInfo(data, 0)).toMatchObject({
      visibilitySource: 'sparse_tracks',
      synthetic: true,
      views: 2,
    });
    data.dense!.frameLinksSource = 'another_provider';
    expect(buildDensePointInfo(data, 0).visibilitySource).toBe('unknown');
  });

  it.each([-1, 4, 0.5, NaN])('rejects invalid dense point index %s', (index) => {
    expect(() => buildDensePointInfo(denseBundle(), index)).toThrow(RangeError);
  });

  it('rejects picking a dense point when the bundle has no dense layer', () => {
    expect(() => buildDensePointInfo(bundle(), 0)).toThrow(RangeError);
  });
});
