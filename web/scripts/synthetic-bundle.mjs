// Writes a viewer bundle v1 (docs/notes/m1-plan.md section 3) for a synthetic scene:
// a tower, a ground plane and a ring of cameras orbiting the tower.
// usage: node scripts/synthetic-bundle.mjs [--points 200000] [--frames 120] [--out ../data/runs/synthetic]
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';

const args = Object.fromEntries(
  process.argv
    .slice(2)
    .join(' ')
    .split('--')
    .filter(Boolean)
    .map((s) => s.trim().split(/\s+/)),
);
const N = Number(args.points ?? 200000);
const F = Number(args.frames ?? 120);
const outDir = path.resolve(args.out ?? '../data/runs/synthetic');
const runId = path.basename(outDir);
const webDir = path.join(outDir, 'web');
fs.mkdirSync(path.join(webDir, 'thumbs'), { recursive: true });
const t0 = Date.now();

// seeded RNG (mulberry32)
let seed = 42;
const rnd = () => {
  seed = (seed + 0x6d2b79f5) | 0;
  let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};

// ---- scene: tower (radius 4, height 30, 40% of points) + ground (120 x 120, rest)
const TR = 4,
  TH = 30,
  G = 60;
const pos = new Float32Array(N * 3);
const col = new Uint8Array(N * 3);
const nrm = new Float32Array(N * 2); // outward xy normal for tower points, 0 for ground
for (let i = 0; i < N; i++) {
  if (rnd() < 0.4) {
    const a = rnd() * Math.PI * 2,
      z = rnd() * TH;
    pos.set([TR * Math.cos(a), TR * Math.sin(a), z], i * 3);
    nrm.set([Math.cos(a), Math.sin(a)], i * 2);
    const band = Math.floor(z / 3) % 2 ? 40 : 0;
    col.set([150 + band + rnd() * 30, 110 + band + rnd() * 20, 90 + rnd() * 20], i * 3);
  } else {
    pos.set([(rnd() * 2 - 1) * G, (rnd() * 2 - 1) * G, rnd() * 0.3], i * 3);
    const g = 70 + rnd() * 50;
    col.set([g, g + 15, g - 10], i * 3);
  }
}

// ---- cameras: ring around the tower, slowly climbing
const W = 1280,
  H = 720,
  FX = 900,
  FY = 900,
  CX = W / 2,
  CY = H / 2;
const MAXD = Number(args.range ?? 38);
const frames = [];
const cams = [];
for (let i = 0; i < F; i++) {
  const a = (i / F) * Math.PI * 2 * 1.25;
  const r = 36;
  const C = [r * Math.cos(a), r * Math.sin(a), 6 + (i / F) * 18];
  const tgt = [0, 0, 12];
  const f = norm(sub(tgt, C));
  const x = norm(cross(f, [0, 0, 1])); // right
  const y = cross(f, x); // down
  const R = [x, y, f]; // rows: world -> cam
  const tvec = R.map((row) => -dot(row, C));
  const registered = !(i % 17 === 5); // a few lost frames
  const dropped = !registered && i % 34 === 5;
  cams.push({ C, R });
  frames.push({
    i,
    t: i * 0.5,
    image: `frames/${String(i).padStart(6, '0')}.png`,
    thumb: `web/thumbs/${String(i).padStart(6, '0')}.png`,
    kept: !dropped,
    drop_reason: dropped ? 'blur' : null,
    blur: Math.round((80 + rnd() * 120) * 10) / 10,
    registered,
    camera: {
      width: W,
      height: H,
      model: 'SIMPLE_RADIAL',
      params: [FX, CX, CY, 0],
      K: [FX, FY, CX, CY],
    },
    ...(registered ? { pose: { qvec: quat(R), tvec }, center: C } : {}),
    n_points: 0,
  });
}

// ---- visibility: project every point into every registered camera (like the prototype's vis())
let cap = 1 << 22;
let fp = new Uint32Array(cap);
let nfp = 0;
const frameOffsets = new Uint32Array(F + 1);
for (let i = 0; i < F; i++) {
  frameOffsets[i] = nfp;
  if (!frames[i].registered) continue;
  const { C, R } = cams[i];
  const [r0, r1, r2] = R;
  for (let p = 0; p < N; p++) {
    const dx = pos[p * 3] - C[0],
      dy = pos[p * 3 + 1] - C[1],
      dz = pos[p * 3 + 2] - C[2];
    const zc = r2[0] * dx + r2[1] * dy + r2[2] * dz;
    if (zc < 1 || zc > MAXD) continue;
    const u = FX * ((r0[0] * dx + r0[1] * dy + r0[2] * dz) / zc) + CX;
    if (u < 0 || u >= W) continue;
    const v = FY * ((r1[0] * dx + r1[1] * dy + r1[2] * dz) / zc) + CY;
    if (v < 0 || v >= H) continue;
    // tower surface faces away from the camera on the far side: cheap self-occlusion
    if ((nrm[p * 2] !== 0 || nrm[p * 2 + 1] !== 0) && nrm[p * 2] * dx + nrm[p * 2 + 1] * dy > 0)
      continue;
    if (nfp === cap) {
      cap *= 2;
      const n = new Uint32Array(cap);
      n.set(fp);
      fp = n;
    }
    fp[nfp++] = p;
  }
  frames[i].n_points = nfp - frameOffsets[i];
}
frameOffsets[F] = nfp;
const frameP = fp.slice(0, nfp);

// ---- point -> frames CSR, first_seen, num_obs
const pointOffsets = new Uint32Array(N + 1);
for (let k = 0; k < nfp; k++) pointOffsets[frameP[k] + 1]++;
for (let p = 0; p < N; p++) pointOffsets[p + 1] += pointOffsets[p];
const pointFrames = new Uint16Array(nfp);
const cursor = pointOffsets.slice(0, N);
for (let i = 0; i < F; i++)
  for (let k = frameOffsets[i]; k < frameOffsets[i + 1]; k++) pointFrames[cursor[frameP[k]]++] = i;
const firstSeen = new Uint16Array(N);
const numObs = new Uint8Array(N);
for (let p = 0; p < N; p++) {
  const n = pointOffsets[p + 1] - pointOffsets[p];
  firstSeen[p] = n ? pointFrames[pointOffsets[p]] : 65535;
  numObs[p] = Math.min(255, n);
}

// ---- write
const w = (name, arr) =>
  fs.writeFileSync(
    path.join(webDir, name),
    Buffer.from(arr.buffer, arr.byteOffset, arr.byteLength),
  );
w('points.position.f32', pos);
w('points.color.u8', col);
w('points.first_seen.u16', firstSeen);
w('points.num_obs.u8', numObs);
w('tracks.frame_offsets.u32', frameOffsets);
w('tracks.frame_points.u32', frameP);
w('tracks.point_offsets.u32', pointOffsets);
w('tracks.point_frames.u16', pointFrames);

// placeholder thumbnails: 320x180 PNG, flat colour per frame + a marker bar at the frame's position
for (const fr of frames) {
  const px = Buffer.alloc(320 * 180 * 3);
  const hue = (fr.i / F) * 120;
  for (let y = 0; y < 180; y++)
    for (let x = 0; x < 320; x++) {
      const o = (y * 320 + x) * 3;
      const bar = y > 160 && x < (fr.i / F) * 320;
      px[o] = bar ? 230 : 30 + hue * 0.3;
      px[o + 1] = bar ? 230 : 50 + y * 0.3;
      px[o + 2] = bar ? 230 : 80 + (x / 320) * 60;
    }
  fs.writeFileSync(
    path.join(webDir, 'thumbs', `${String(fr.i).padStart(6, '0')}.png`),
    png(320, 180, px),
  );
}

const zs = Float32Array.from({ length: N }, (_, p) => pos[p * 3 + 2]).sort();
const mn = [Infinity, Infinity, Infinity],
  mx = [-Infinity, -Infinity, -Infinity];
for (let p = 0; p < N; p++)
  for (let k = 0; k < 3; k++) {
    mn[k] = Math.min(mn[k], pos[p * 3 + k]);
    mx[k] = Math.max(mx[k], pos[p * 3 + k]);
  }
const reg = frames.filter((f) => f.registered).length;
const manifest = {
  version: 1,
  run_id: runId,
  source: {
    kind: 'video',
    name: 'synthetic_tower.mp4',
    duration_s: F * 0.5,
    fps: 2,
    width: W,
    height: H,
  },
  world: {
    up: [0, 0, 1],
    world_from_colmap: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
    bounds: { min: mn, max: mx },
    z_range: [zs[Math.floor(N * 0.02)], zs[Math.floor(N * 0.98)]],
  },
  points: {
    count: N,
    position: 'web/points.position.f32',
    color: 'web/points.color.u8',
    first_seen: 'web/points.first_seen.u16',
    num_obs: 'web/points.num_obs.u8',
  },
  tracks: {
    frame_offsets: 'web/tracks.frame_offsets.u32',
    frame_points: 'web/tracks.frame_points.u32',
    point_offsets: 'web/tracks.point_offsets.u32',
    point_frames: 'web/tracks.point_frames.u16',
  },
  frames,
  stats: {
    frames_extracted: F,
    frames_dropped: frames.filter((f) => !f.kept).length,
    frames_registered: reg,
    points: N,
    mean_track_length: nfp / N,
    mean_reproj_error_px: 0.5,
    processing_s: { frames: 0, reconstruction: 0, export: (Date.now() - t0) / 1000 },
  },
};
fs.writeFileSync(path.join(webDir, 'manifest.json'), JSON.stringify(manifest));
console.log(
  `${outDir}: ${N} points, ${F} frames (${reg} registered), ${nfp} observations, ${(Date.now() - t0) / 1000}s`,
);

// ---- helpers
function sub(a, b) {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}
function dot(a, b) {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}
function cross(a, b) {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}
function norm(a) {
  const l = Math.hypot(...a);
  return a.map((v) => v / l);
}
// rotation matrix (rows) -> quaternion [w,x,y,z]
function quat(m) {
  const tr = m[0][0] + m[1][1] + m[2][2];
  let q;
  if (tr > 0) {
    const s = Math.sqrt(tr + 1) * 2;
    q = [s / 4, (m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s];
  } else if (m[0][0] > m[1][1] && m[0][0] > m[2][2]) {
    const s = Math.sqrt(1 + m[0][0] - m[1][1] - m[2][2]) * 2;
    q = [(m[2][1] - m[1][2]) / s, s / 4, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s];
  } else if (m[1][1] > m[2][2]) {
    const s = Math.sqrt(1 + m[1][1] - m[0][0] - m[2][2]) * 2;
    q = [(m[0][2] - m[2][0]) / s, (m[0][1] + m[1][0]) / s, s / 4, (m[1][2] + m[2][1]) / s];
  } else {
    const s = Math.sqrt(1 + m[2][2] - m[0][0] - m[1][1]) * 2;
    q = [(m[1][0] - m[0][1]) / s, (m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, s / 4];
  }
  return q;
}
function png(w, h, rgb) {
  const raw = Buffer.alloc((w * 3 + 1) * h);
  for (let y = 0; y < h; y++) rgb.copy(raw, y * (w * 3 + 1) + 1, y * w * 3, (y + 1) * w * 3);
  const chunk = (type, data) => {
    const len = Buffer.alloc(4);
    len.writeUInt32BE(data.length);
    const td = Buffer.concat([Buffer.from(type), data]);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(zlib.crc32(td) >>> 0);
    return Buffer.concat([len, td, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0);
  ihdr.writeUInt32BE(h, 4);
  ihdr[8] = 8;
  ihdr[9] = 2;
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}
