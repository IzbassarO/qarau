import { authHeader } from '../auth/auth';
import type { InspectionPayload } from './inspections';
import { Sha256 } from './sha256';

export const UPLOAD_CHUNK_SIZE = 4_194_304;
// B1 has no advertised limit yet; frontend policy, documented in ADR U2.
export const MAX_VIDEO_BYTES = 10 * 1024 ** 3;
export const VIDEO_ACCEPT = 'video/*';
export type UploadErrorCode = 'unsupported_type' | 'too_large' | 'network' | 'cancelled' | 'server';

export class UploadError extends Error {
  constructor(
    public code: UploadErrorCode,
    public status?: number,
  ) {
    super(code);
  }
}

export function validateVideo(file: Pick<Blob, 'size' | 'type'>): void {
  if (!file.type.startsWith('video/')) throw new UploadError('unsupported_type');
  if (file.size > MAX_VIDEO_BYTES) throw new UploadError('too_large');
  // The dialog gives empty files their own actionable validation message.
  if (file.size === 0) throw new UploadError('server');
}

export interface UploadProgress {
  sentBytes: number;
  totalBytes: number;
  percent: number;
  bytesPerSecond: number;
}

/** Kept only by the dialog, so manual retry can resume the same server partial. */
export interface UploadSession {
  uploadId?: number;
  runId?: string;
  sha256?: string;
  completionAttempted?: boolean;
}

interface UploadOptions {
  chunkSize?: number;
  onProgress?: (progress: UploadProgress) => void;
  signal?: AbortSignal;
  session?: UploadSession;
  now?: () => number;
  speedWindowMs?: number;
}

function checkAbort(signal?: AbortSignal): void {
  if (signal?.aborted) throw new UploadError('cancelled');
}

async function request(url: string, init: RequestInit, signal?: AbortSignal): Promise<unknown> {
  checkAbort(signal);
  try {
    const response = await fetch(url, {
      ...init,
      signal,
      headers: { ...authHeader(), ...init.headers },
    });
    checkAbort(signal);
    if (!response.ok)
      throw new UploadError(
        response.status === 415
          ? 'unsupported_type'
          : response.status === 413
            ? 'too_large'
            : 'server',
        response.status,
      );
    const payload: unknown = await response.json();
    checkAbort(signal);
    return payload;
  } catch (error) {
    checkAbort(signal);
    if (error instanceof UploadError) throw error;
    throw new UploadError(error instanceof TypeError ? 'network' : 'server');
  }
}

export async function createInspection(
  input: { siteId: number; filename: string; size: number; contentType: string },
  signal?: AbortSignal,
): Promise<InspectionPayload> {
  validateVideo({ size: input.size, type: input.contentType });
  const payload = await request(
    '/api/inspections',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ site_id: input.siteId, mode: 'quick_scan' }),
    },
    signal,
  );
  if (
    !payload ||
    typeof payload !== 'object' ||
    !('id' in payload) ||
    !Number.isInteger(payload.id)
  )
    throw new UploadError('server');
  return payload as InspectionPayload;
}

function backoff(ms: number, signal?: AbortSignal): Promise<void> {
  checkAbort(signal);
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(new UploadError('cancelled'));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', abort);
      resolve();
    }, ms);
    signal?.addEventListener('abort', abort, { once: true });
  });
}

function nextOffset(payload: unknown, total: number): number {
  const value = (payload as { next_offset?: unknown } | null)?.next_offset;
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0 || value > total)
    throw new UploadError('server');
  return value;
}

async function checksum(file: Blob, signal?: AbortSignal): Promise<string> {
  const hash = new Sha256();
  for (let offset = 0; offset < file.size; offset += UPLOAD_CHUNK_SIZE) {
    checkAbort(signal);
    let bytes: ArrayBuffer;
    try {
      bytes = await file.slice(offset, offset + UPLOAD_CHUNK_SIZE).arrayBuffer();
    } catch {
      checkAbort(signal);
      throw new UploadError('server');
    }
    checkAbort(signal);
    hash.update(new Uint8Array(bytes));
  }
  return hash.hex();
}

export async function uploadFile(
  inspectionId: string | number,
  file: File,
  {
    chunkSize = UPLOAD_CHUNK_SIZE,
    onProgress,
    signal,
    session = {},
    now = Date.now,
    speedWindowMs = 5000,
  }: UploadOptions = {},
): Promise<{ runId: string }> {
  checkAbort(signal);
  validateVideo(file);
  if (
    !Number.isSafeInteger(chunkSize) ||
    chunkSize < 1 ||
    !Number.isFinite(speedWindowMs) ||
    speedWindowMs <= 0
  )
    throw new UploadError('server');
  const base = `/api/inspections/${encodeURIComponent(inspectionId)}/video`;
  session.sha256 ??= await checksum(file, signal);
  checkAbort(signal);
  let reconcile = session.uploadId !== undefined;
  if (!reconcile) {
    const started = (await request(
      base,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: file.name, size: file.size }),
      },
      signal,
    )) as { upload_id?: number; run_id?: string } | null;
    if (!Number.isInteger(started?.upload_id) || typeof started?.run_id !== 'string')
      throw new UploadError('server');
    session.uploadId = started!.upload_id;
    session.runId = started!.run_id;
  }
  const chunks = `${base}/chunks?upload_id=${session.uploadId}`;
  let offset = 0;
  let failures = 0;
  let lastReported = -1;
  let samples = [{ time: now(), bytes: 0 }];
  const progress = (sentBytes: number) => {
    if (sentBytes === lastReported) return;
    lastReported = sentBytes;
    const time = now();
    while (samples.length > 1 && samples[1].time <= time - speedWindowMs) samples.shift();
    const first = samples[0];
    const elapsed = time - first.time;
    onProgress?.({
      sentBytes,
      totalBytes: file.size,
      percent: (sentBytes / file.size) * 100,
      bytesPerSecond: elapsed > 0 ? Math.max(0, ((sentBytes - first.bytes) * 1000) / elapsed) : 0,
    });
    samples.push({ time, bytes: sentBytes });
    checkAbort(signal);
  };
  while (offset < file.size) {
    checkAbort(signal);
    try {
      if (reconcile) {
        offset = nextOffset(await request(chunks, {}, signal), file.size);
        reconcile = false;
        // A resumed prefix was transferred earlier; don't count it in this session's speed.
        samples = [{ time: now(), bytes: offset }];
        if (offset > 0) progress(offset);
        if (offset === file.size) break;
      }
      const end = Math.min(offset + chunkSize, file.size);
      const acknowledged = nextOffset(
        await request(
          `${chunks}&offset=${offset}`,
          {
            method: 'PUT',
            headers: { 'Content-Type': 'application/octet-stream' },
            body: file.slice(offset, end),
          },
          signal,
        ),
        file.size,
      );
      if (acknowledged < end) throw new UploadError('server');
      offset = acknowledged;
      failures = 0;
      progress(offset);
    } catch (error) {
      checkAbort(signal);
      const retryable =
        error instanceof UploadError &&
        (error.code === 'network' || error.status === 409 || (error.status ?? 0) >= 500);
      if (!retryable || ++failures >= 3) throw error;
      await backoff(500 * 2 ** (failures - 1), signal);
      reconcile = true;
    }
  }
  checkAbort(signal);
  // B1 completion is not idempotent. A lost successful response must not strand Retry
  // on "already complete". Only the matching run's API state can confirm that outcome.
  if (session.completionAttempted) {
    const runs = await request(
      `/api/inspections/${encodeURIComponent(inspectionId)}/runs`,
      {},
      signal,
    );
    if (!Array.isArray(runs)) throw new UploadError('server');
    const run = runs.find((item) => item?.id === session.runId);
    if (
      run &&
      ['uploaded', 'queued', 'running', 'done', 'completed', 'failed'].includes(run.status)
    )
      return { runId: session.runId! };
  }
  session.completionAttempted = true;
  const completed = (await request(
    `${base}/complete`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ upload_id: session.uploadId, sha256: session.sha256 }),
    },
    signal,
  )) as { run_id?: string } | null;
  if (typeof completed?.run_id !== 'string') throw new UploadError('server');
  return { runId: completed.run_id };
}
