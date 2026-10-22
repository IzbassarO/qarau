import { readFileSync } from 'node:fs';
import manifestJson from './manifest.json' with { type: 'json' };
import type { Manifest } from '../../lib/bundle';

/** Node-only test helper: Vitest and Playwright read the same checked-in bundle. */
export function readDenseFixture() {
  const manifest = structuredClone(manifestJson) as Manifest;
  const filenames = [
    'points.position.f32',
    'points.color.u8',
    'points.first_seen.u16',
    'points.num_obs.u16',
    'dense.position.f32',
    'dense.color.u8',
    'dense.frame_offsets.u32',
    'dense.frame_ids.u32',
    'frame.svg',
  ];
  const files = new Map(
    filenames.map((name) => [`web/${name}`, readFileSync(new URL(name, import.meta.url))]),
  );
  return { manifest, files };
}
