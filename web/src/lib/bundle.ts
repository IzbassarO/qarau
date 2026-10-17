import { cumulativeAssembled } from './geometry';
import { authHeader } from '../auth/auth';

export interface FrameInfo {
  i: number;
  t: number;
  image: string;
  thumb: string;
  kept: boolean;
  drop_reason: string | null;
  blur: number | null;
  registered: boolean;
  // camera, pose, center and n_points exist only for registered frames (plan §3)
  camera?: { width: number; height: number; model: string; params: number[]; K: number[] };
  pose?: { qvec: number[]; tvec: number[] };
  center?: number[];
  n_points?: number;
}

export interface Stats {
  frames_extracted: number;
  frames_dropped: number;
  frames_registered: number;
  points: number;
  mean_track_length?: number;
  mean_reproj_error_px?: number;
  processing_s?: Record<string, number>;
}

export interface Source {
  kind: 'video' | 'images';
  name: string;
  duration_s?: number;
  fps?: number;
  width?: number;
  height?: number;
}

export interface Manifest {
  version: number;
  run_id: string;
  source: Source;
  world: {
    up: number[];
    bounds: { min: number[]; max: number[] };
    z_range: [number, number];
    focus?: { center: number[]; radius: number; method?: string };
  };
  points: {
    count: number;
    position: string;
    color?: string;
    first_seen: string;
    num_obs: string;
    source?: string;
  };
  /** ADR 0003 / P3b: separate XYZ float32 and RGB uint8 triplets, without tracks. */
  far?: { count: number; position: string; color: string; rule: string };
  dense?: { method: string; frames_used?: number; points?: number };
  findings?: string;
  tracks: {
    frame_offsets: string;
    frame_points: string;
    point_offsets: string;
    point_frames: string;
  };
  frames: FrameInfo[];
  stats: Stats;
}

export type Severity = 'critical' | 'watch';

export interface Finding {
  id: number;
  class: string;
  prompt?: string;
  severity: Severity;
  status: 'confirmed' | 'low_evidence';
  confidence?: number;
  n_points: number;
  centroid: number[];
  bbox: { min: number[]; max: number[] };
  size?: { value: number; unit: 'm2' | 'rel' };
  frames: number[];
  max_angle_deg?: number;
}

export interface FindingsFile {
  version: number;
  findings: Finding[];
  point_finding: string;
}

export interface Bundle {
  runId: string;
  /** Optional resolver for self-contained fixtures; real runs use the existing API. */
  assetUrl?: (rel: string) => string;
  manifest: Manifest;
  position: Float32Array;
  /** Real per-point RGB, when the bundle includes a complete colour buffer. */
  color?: Uint8Array;
  far?: { position: Float32Array; color: Uint8Array };
  /** A declared far layer failed; the object cloud remains usable. */
  farError?: 'missing' | 'broken';
  firstSeen: Uint16Array;
  frameOffsets: Uint32Array;
  frameInPoints: Uint32Array;
  pointOffsets: Uint32Array;
  pointFrames: Uint16Array;
  /** cumulative[i] = points with first_seen <= i */
  assembled: Uint32Array;
  /** v2, optional: findings and the per-point finding id (0 = none) */
  findings?: Finding[];
  pointFinding?: Uint16Array;
  /** set when the manifest lists findings but they could not be read */
  findingsError?: 'missing' | 'broken';
}

export class BundleError extends Error {
  constructor(
    public code: 'noManifest' | 'badVersion' | 'generic',
    message: string,
  ) {
    super(message);
  }
}

/** Recoverable optional-layer failure: consumers render `bundle` with its warning. */
export class FarLayerError extends Error {
  readonly name = 'FarLayerError';

  constructor(
    public reason: 'missing' | 'broken',
    message: string,
    public bundle: Bundle,
  ) {
    super(`far layer: ${message}`);
  }
}

export const runUrl = (runId: string, rel: string) =>
  `/api/runs/${encodeURIComponent(runId)}/files/${rel}`;

/** Real colours are available only when every point has an RGB triplet. */
export function hasRgb(bundle: Bundle): boolean {
  return (
    bundle.color instanceof Uint8Array &&
    bundle.color.length > 0 &&
    bundle.color.length === bundle.manifest.points.count * 3
  );
}

export async function loadManifest(runId: string): Promise<Manifest> {
  const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/manifest.json`, {
    headers: authHeader(),
  });
  if (res.status === 404) throw new BundleError('noManifest', 'manifest.json not found');
  if (!res.ok) throw new BundleError('generic', `manifest: HTTP ${res.status}`);
  const m = (await res.json()) as Manifest;
  if (m.version !== 1 && m.version !== 2)
    throw new BundleError('badVersion', `manifest version ${m.version}`);
  return m;
}

async function fetchBuffer(url: string, onBytes: (loaded: number, total: number) => void) {
  const res = await fetch(url, { headers: authHeader() });
  if (!res.ok || !res.body) throw new BundleError('generic', `${url}: HTTP ${res.status}`);
  const total = Number(res.headers.get('content-length') ?? 0);
  const chunks: Uint8Array[] = [];
  let loaded = 0;
  const reader = res.body.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    loaded += value.length;
    onBytes(loaded, total);
  }
  const out = new Uint8Array(loaded);
  let o = 0;
  for (const c of chunks) {
    out.set(c, o);
    o += c.length;
  }
  return out.buffer;
}

/** Never throws: a missing or broken findings file becomes `findingsError`, shown in the UI. */
async function loadFindings(
  m: Manifest,
  get: (rel: string) => Promise<ArrayBuffer>,
): Promise<Pick<Bundle, 'findings' | 'pointFinding' | 'findingsError'>> {
  let file: FindingsFile;
  try {
    file = JSON.parse(new TextDecoder().decode(await get(m.findings!))) as FindingsFile;
  } catch (e) {
    // a 404 is "missing"; HTTP errors elsewhere and bad JSON are "broken"
    return { findingsError: String(e).includes('HTTP 404') ? 'missing' : 'broken' };
  }
  try {
    const pf = new Uint16Array(await get(file.point_finding));
    const ok =
      Array.isArray(file.findings) &&
      pf.length === m.points.count &&
      file.findings.every((f) => Array.isArray(f.frames) && Array.isArray(f.centroid));
    return ok ? { findings: file.findings, pointFinding: pf } : { findingsError: 'broken' };
  } catch (e) {
    return { findingsError: String(e).includes('HTTP 404') ? 'missing' : 'broken' };
  }
}

/** Download manifest + all typed arrays; progress is 0..1 over bytes. */
export async function loadBundle(
  runId: string, // the route id: a copied run may carry a stale manifest.run_id
  manifest: Manifest,
  onProgress: (p: number) => void,
): Promise<Bundle> {
  const prog = new Map<string, [number, number]>();
  const report = () => {
    let l = 0;
    let t = 0;
    for (const [a, b] of prog.values()) {
      l += a;
      t += Math.max(a, b);
    }
    onProgress(t ? l / t : 0);
  };
  const get = (rel: string) =>
    fetchBuffer(runUrl(runId, rel), (l, t) => {
      prog.set(rel, [l, t]);
      report();
    });
  const { points: p, tracks: k } = manifest;
  const [pos, col, fs, fo, fp, po, pf] = await Promise.all([
    get(p.position),
    // Colour is optional: missing or invalid RGB must not hide usable geometry.
    p.color?.trim() ? get(p.color).catch(() => undefined) : undefined,
    get(p.first_seen),
    get(k.frame_offsets),
    get(k.frame_points),
    get(k.point_offsets),
    get(k.point_frames),
  ]);
  const firstSeen = new Uint16Array(fs);
  const fnd = manifest.findings ? await loadFindings(manifest, get) : {};
  const bundle: Bundle = {
    ...fnd,
    runId,
    manifest,
    position: new Float32Array(pos),
    color:
      col && col.byteLength > 0 && col.byteLength === p.count * 3 ? new Uint8Array(col) : undefined,
    firstSeen,
    frameOffsets: new Uint32Array(fo),
    frameInPoints: new Uint32Array(fp),
    pointOffsets: new Uint32Array(po),
    pointFrames: new Uint16Array(pf),
    assembled: cumulativeAssembled(firstSeen, manifest.frames.length),
  };
  if (manifest.far !== undefined) {
    try {
      const far = manifest.far;
      if (
        !far ||
        !Number.isSafeInteger(far.count) ||
        far.count < 0 ||
        typeof far.position !== 'string' ||
        !far.position.trim() ||
        typeof far.color !== 'string' ||
        !far.color.trim()
      )
        throw new Error('invalid far manifest (count, position or color)');
      const buffers = await Promise.allSettled([get(far.position), get(far.color)]);
      const [position, color] = buffers.map((result) => {
        if (result.status === 'rejected') throw result.reason;
        return result.value;
      });
      if (position.byteLength !== far.count * 12 || color.byteLength !== far.count * 3)
        throw new Error(
          `expected ${far.count} XYZ/RGB triplets; got ${position.byteLength} position bytes and ${color.byteLength} color bytes`,
        );
      bundle.far = { position: new Float32Array(position), color: new Uint8Array(color) };
    } catch (error) {
      const reason = String(error).includes('HTTP 404') ? 'missing' : 'broken';
      bundle.farError = reason;
      throw new FarLayerError(reason, String(error), bundle);
    }
  }
  return bundle;
}
