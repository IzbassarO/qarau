import type { Manifest } from '../../../src/lib/bundle';

/** Small on-wire bundle: explicit little-endian buffers, no filesystem or random data. */
export function makeFarBundle() {
  const objectCount = 300;
  const farCount = 200;
  const position = Buffer.alloc(objectCount * 12);
  const color = Buffer.alloc(objectCount * 3);
  const firstSeen = Buffer.alloc(objectCount * 2);
  const numObs = Buffer.alloc(objectCount * 2);
  for (let i = 0; i < objectCount; i++) {
    // A 10 x 10 x 3 grid spanning [0, 1] on each axis.
    position.writeFloatLE((i % 10) / 9, i * 12);
    position.writeFloatLE((Math.floor(i / 10) % 10) / 9, i * 12 + 4);
    position.writeFloatLE(Math.floor(i / 100) / 2, i * 12 + 8);
    color.set([80, 160, 220], i * 3);
    firstSeen.writeUInt16LE(0, i * 2);
    numObs.writeUInt16LE(1, i * 2);
  }

  const farPosition = Buffer.alloc(farCount * 12);
  const farColor = Buffer.alloc(farCount * 3, 128);
  for (let i = 0; i < farCount; i++) {
    // Fibonacci sphere of radius 3, centred on the object cube.
    const z = 1 - (2 * (i + 0.5)) / farCount;
    const radius = Math.sqrt(1 - z * z);
    const angle = i * Math.PI * (3 - Math.sqrt(5));
    farPosition.writeFloatLE(0.5 + 3 * radius * Math.cos(angle), i * 12);
    farPosition.writeFloatLE(0.5 + 3 * radius * Math.sin(angle), i * 12 + 4);
    farPosition.writeFloatLE(0.5 + 3 * z, i * 12 + 8);
  }

  const manifest = {
    version: 1,
    run_id: 'synthetic-far',
    source: { kind: 'images', name: 'Synthetic far-layer fixture' },
    world: {
      up: [0, 0, 1],
      bounds: { min: [0, 0, 0], max: [1, 1, 1] },
      z_range: [0, 1],
    },
    points: {
      count: objectCount,
      position: 'web/points.position.f32',
      color: 'web/points.color.u8',
      first_seen: 'web/points.first_seen.u16',
      num_obs: 'web/points.num_obs.u16',
    },
    far: {
      count: farCount,
      position: 'web/far.position.f32',
      color: 'web/far.color.u8',
      rule: 'synthetic shell outside the object cube',
    },
    frames: [
      {
        i: 0,
        image: 'web/frame.svg',
        thumb: 'web/frame.svg',
        kept: true,
        drop_reason: null,
        blur: null,
        registered: false,
      },
    ],
    stats: {
      frames_extracted: 1,
      frames_dropped: 0,
      frames_registered: 0,
      points: objectCount,
    },
  } satisfies Manifest;

  const files = new Map([
    [manifest.points.position, position],
    [manifest.points.color, color],
    [manifest.points.first_seen, firstSeen],
    [manifest.points.num_obs, numObs],
    [manifest.far.position, farPosition],
    [manifest.far.color, farColor],
    [
      manifest.frames[0].thumb,
      Buffer.from(
        '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="90"><rect width="160" height="90" fill="#0b0c0e"/></svg>',
      ),
    ],
  ]);
  return { manifest, files };
}
