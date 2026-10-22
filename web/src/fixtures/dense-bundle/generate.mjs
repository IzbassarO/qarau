import { writeFileSync } from 'node:fs';
import { Buffer } from 'node:buffer';
import { URL } from 'node:url';

// Recreate the tiny checked-in exporter-format binary assets with Node alone.
// Frame IDs are manifest.frames indices, distinct from the frames' i fields.
function write(name, values, bytes, put) {
  const buffer = Buffer.alloc(values.length * bytes);
  values.forEach((value, index) => buffer[put](value, index * bytes));
  writeFileSync(new URL(name, import.meta.url), buffer);
}

write(
  'points.position.f32',
  [-1, -1, -1, 1, -1, -1, -1, 1, -1, 1, 1, -1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, 1, 1],
  4,
  'writeFloatLE',
);
write('points.color.u8', Array(8).fill([80, 160, 220]).flat(), 1, 'writeUInt8');
write('points.first_seen.u16', [0, 0, 0, 0, 0, 0, 0, 0], 2, 'writeUInt16LE');
write('points.num_obs.u16', [0, 0, 0, 0, 0, 0, 0, 0], 2, 'writeUInt16LE');
write(
  'dense.position.f32',
  [0, 0, 0.25, -0.55, 0.25, 0.4, 0.45, -0.3, -0.25, 0, 0.6, -0.55],
  4,
  'writeFloatLE',
);
write('dense.color.u8', [220, 50, 30, 30, 220, 50, 50, 30, 220, 220, 200, 30], 1, 'writeUInt8');
write('dense.frame_offsets.u32', [0, 2, 3, 3, 5], 4, 'writeUInt32LE');
write('dense.frame_ids.u32', [0, 2, 1, 0, 1], 4, 'writeUInt32LE');
