import { LANGS, type Lang } from '../i18n';
import type { Bundle, FrameInfo } from '../lib/bundle';
import type { RunSummary } from '../lib/inspections';
import { groupRunsIntoSites } from '../lib/inspections';

/** Deterministic, synthetic geometry shared by /inspections/demo and /viewer/demo. */
function createBundle(): Bundle {
  const points: number[] = [];
  for (let x = -14; x <= 14; x += 0.4) for (let y = -9; y <= 9; y += 0.4) points.push(x, y, 0);
  for (const cx of [-5, 5]) {
    for (let angle = 0; angle < Math.PI * 2; angle += 0.035) {
      for (let z = 0; z <= 6; z += 0.18)
        points.push(cx + 3 * Math.cos(angle), 3 * Math.sin(angle), z);
      for (let radius = 0; radius <= 3; radius += 0.2)
        points.push(cx + radius * Math.cos(angle), radius * Math.sin(angle), 6);
    }
  }
  for (let x = -12; x <= 12; x += 0.15)
    for (let angle = 0; angle < Math.PI * 2; angle += 0.15)
      points.push(x, -5 + 0.4 * Math.cos(angle), 1.5 + 0.4 * Math.sin(angle));
  const position = new Float32Array(points);
  const count = position.length / 3;
  const frameCount = 12;
  const offsets = new Uint32Array(frameCount + 1);
  const framePoints = new Uint32Array(count * frameCount);
  for (let f = 0; f < frameCount; f++) {
    offsets[f] = f * count;
    for (let p = 0; p < count; p++) framePoints[f * count + p] = p;
  }
  offsets[frameCount] = count * frameCount;
  const pointOffsets = new Uint32Array(count + 1);
  const pointFrames = new Uint16Array(count * frameCount);
  for (let p = 0; p < count; p++) {
    pointOffsets[p] = p * frameCount;
    for (let f = 0; f < frameCount; f++) pointFrames[p * frameCount + f] = f;
  }
  pointOffsets[count] = count * frameCount;
  const frames: FrameInfo[] = Array.from({ length: frameCount }, (_, i) => ({
    i,
    t: i * 2,
    image: `demo-${i}.svg`,
    thumb: 'demo-frame.svg',
    kept: true,
    drop_reason: null,
    blur: null,
    registered: true,
    center: [-12 + i * 2, -16, 10],
    camera: {
      width: 640,
      height: 360,
      model: 'PINHOLE',
      params: [500, 500, 320, 180],
      K: [500, 0, 320, 0, 500, 180, 0, 0, 1],
    },
    pose: { qvec: [1, 0, 0, 0], tvec: [12 - i * 2, 16, -10] },
    n_points: count,
  }));
  return {
    runId: 'demo',
    assetUrl: () => new URL('./demo-frame.svg', import.meta.url).href,
    manifest: {
      version: 1,
      run_id: 'demo',
      source: { kind: 'video', name: 'demo', duration_s: 22 },
      world: {
        up: [0, 0, 1],
        bounds: { min: [-14, -9, 0], max: [14, 9, 6] },
        z_range: [0, 6],
        focus: { center: [0, 0, 2], radius: 16 },
      },
      points: { count, position: '', color: '', first_seen: '', num_obs: '' },
      tracks: { frame_offsets: '', frame_points: '', point_offsets: '', point_frames: '' },
      frames,
      stats: {
        points: count,
        frames_extracted: frameCount,
        frames_dropped: 0,
        frames_registered: frameCount,
        processing_s: { frames: 4.2, reconstruction: 38.6, export: 1.8 },
      },
    },
    position,
    color: new Uint8Array(count * 3).fill(180),
    firstSeen: new Uint16Array(count),
    frameOffsets: offsets,
    frameInPoints: framePoints,
    pointOffsets,
    pointFrames,
    assembled: new Uint32Array(frameCount).fill(count),
  };
}

// Retain one bundle across locale switches so the embedded viewer keeps its state.
let bundle: Bundle | undefined;
export function createDemo(lang: Lang) {
  const t = (key: keyof typeof LANGS.en) => LANGS[lang][key];
  bundle ??= createBundle();
  const run: RunSummary = {
    run_id: 'demo',
    site: { id: 'demo-site', name: t('demo.siteName') },
    title: t('demo.inspectionTitle'),
    created_at: '2026-10-14T10:30:00+05:00',
    status: 'failed',
    stats: {
      ...bundle.manifest.stats,
      processing_s: { ingest: 1.2, frames: 4.2, reconstruction: 38.6 },
    },
    stages: [
      {
        name: 'ingest',
        status: 'ok',
        started_at: '2026-10-14T05:30:00.000Z',
        finished_at: '2026-10-14T05:30:01.200Z',
      },
      {
        name: 'frames',
        status: 'ok',
        started_at: '2026-10-14T05:30:01.200Z',
        finished_at: '2026-10-14T05:30:05.400Z',
      },
      {
        name: 'reconstruction',
        status: 'failed',
        started_at: '2026-10-14T05:30:05.400Z',
        finished_at: '2026-10-14T05:30:44.000Z',
        error_code: 'too_few_registered_frames',
      },
      { name: 'export', status: 'skipped' },
    ],
  };
  const sites = groupRunsIntoSites([run]);
  return { run, sites, inspection: sites[0].inspections[0], bundle };
}
