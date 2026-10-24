import { File as NodeFile } from 'node:buffer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createInspection, uploadFile, type UploadSession, type UploadProgress } from './upload';

const video = (size = 10_485_760) =>
  new NodeFile([new Uint8Array(size)], 'survey.mp4', { type: 'video/mp4' }) as unknown as File;
const complete = { run_id: 'run-1', file: 'video.mp4', size: 10_485_760 };
const puts = (fetch: ReturnType<typeof vi.fn>) =>
  fetch.mock.calls.filter(([, init]) => init?.method === 'PUT');

function mockApi(put?: (offset: number, body: Blob) => Promise<Response> | Response) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/inspections')
      return Response.json(
        {
          id: 7,
          site_id: 1,
          mode: 'quick_scan',
          status: 'created',
          created_at: '2026-10-23T00:00:00Z',
        },
        { status: 201 },
      );
    if (url.endsWith('/video'))
      return Response.json({ upload_id: 9, run_id: 'run-1' }, { status: 201 });
    if (url.endsWith('/complete')) return Response.json(complete);
    if (init?.method !== 'PUT') return Response.json({ next_offset: 4_194_304 });
    const offset = Number(new URL(url, 'http://localhost').searchParams.get('offset'));
    const body = init.body as Blob;
    return put ? put(offset, body) : Response.json({ next_offset: offset + body.size });
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

beforeEach(() => sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value'));
afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it('creates a quick scan using the exact B1 fields and bearer session', async () => {
  const fetch = mockApi();
  await expect(
    createInspection({
      siteId: 1,
      filename: 'survey.mp4',
      size: 10_485_760,
      contentType: 'video/mp4',
    }),
  ).resolves.toMatchObject({ id: 7 });
  expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/inspections', {
    method: 'POST',
    signal: undefined,
    headers: { Authorization: 'Bearer test-api-value', 'Content-Type': 'application/json' },
    body: '{"site_id":1,"mode":"quick_scan"}',
  });
});

it('sends 10485760 bytes as exactly three chunks and completes with the full-file SHA-256', async () => {
  const fetch = mockApi();
  await expect(uploadFile(7, video(), { chunkSize: 4_194_304 })).resolves.toEqual({
    runId: 'run-1',
  });
  const chunks = puts(fetch);
  expect(chunks).toHaveLength(3);
  expect(
    chunks.map(([url]) => Number(new URL(url, 'http://localhost').searchParams.get('offset'))),
  ).toEqual([0, 4_194_304, 8_388_608]);
  expect(chunks.map(([, init]) => (init.body as Blob).size)).toEqual([
    4_194_304, 4_194_304, 2_097_152,
  ]);
  expect(fetch.mock.calls[0]).toEqual([
    '/api/inspections/7/video',
    expect.objectContaining({ body: '{"name":"survey.mp4","size":10485760}' }),
  ]);
  expect(fetch).toHaveBeenLastCalledWith(
    '/api/inspections/7/video/complete',
    expect.objectContaining({
      body: '{"upload_id":9,"sha256":"e5b844cc57f57094ea4585e235f36c78c1cd222262bb89d53c94dcb4d6b3e55d"}',
    }),
  );
  for (const [, init] of fetch.mock.calls)
    expect(init?.headers).toMatchObject({ Authorization: 'Bearer test-api-value' });
  expect(chunks[0][1]?.headers).toMatchObject({ 'Content-Type': 'application/octet-stream' });
});

it('resumes the failed second chunk from the server offset, with four PUTs total', async () => {
  vi.useFakeTimers();
  let chunkFailed!: () => void;
  const failure = new Promise<void>((resolve) => {
    chunkFailed = resolve;
  });
  let attempt = 0;
  const fetch = mockApi((offset, body) => {
    if (++attempt === 2) {
      chunkFailed();
      throw new TypeError('offline');
    }
    return Response.json({ next_offset: offset + body.size });
  });
  const result = uploadFile(7, video(), { chunkSize: 4_194_304 });
  await failure;
  await vi.advanceTimersByTimeAsync(499);
  expect(puts(fetch)).toHaveLength(2);
  expect(fetch).toHaveBeenCalledTimes(3);
  await vi.advanceTimersByTimeAsync(1);
  await vi.waitFor(() => expect(puts(fetch)).toHaveLength(4));
  await expect(result).resolves.toEqual({ runId: 'run-1' });
  expect(
    puts(fetch).map(([url]) => new URL(url, 'http://localhost').searchParams.get('offset')),
  ).toEqual(['0', '4194304', '4194304', '8388608']);
  expect(fetch.mock.calls[3][0]).toBe('/api/inspections/7/video/chunks?upload_id=9');
  expect(fetch.mock.calls[3][1]?.method).toBeUndefined();
});

it('uses the returned offset when a failed response hid successfully stored bytes', async () => {
  vi.useFakeTimers();
  let chunkFailed!: () => void;
  const failure = new Promise<void>((resolve) => {
    chunkFailed = resolve;
  });
  let attempt = 0;
  const fetch = mockApi((offset, body) => {
    if (++attempt === 1) {
      chunkFailed();
      throw new TypeError('lost response');
    }
    return Response.json({ next_offset: offset + body.size });
  });
  const result = uploadFile(7, video());
  await failure;
  await vi.advanceTimersByTimeAsync(500);
  await vi.waitFor(() => expect(puts(fetch)).toHaveLength(3));
  await result;
  expect(
    puts(fetch).map(([url]) => new URL(url, 'http://localhost').searchParams.get('offset')),
  ).toEqual(['0', '4194304', '8388608']);
});

it('reconciles a B1 409 offset conflict and resumes from the exact server offset', async () => {
  vi.useFakeTimers();
  const fetch = mockApi();
  fetch
    .mockResolvedValueOnce(Response.json({ upload_id: 9, run_id: 'run-1' }))
    .mockResolvedValueOnce(new Response(null, { status: 409 }))
    .mockResolvedValueOnce(Response.json({ next_offset: 2 }));
  const result = uploadFile(7, video(10), { chunkSize: 4 }).catch((error) => error);
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(6));
  expect(await result).toEqual({ runId: 'run-1' });
  expect(
    puts(fetch).map(([url]) => new URL(url, 'http://localhost').searchParams.get('offset')),
  ).toEqual(['0', '2', '6']);
  expect(puts(fetch).map(([, init]) => (init.body as Blob).size)).toEqual([4, 4, 4]);
  expect(fetch.mock.calls[2][0]).toBe('/api/inspections/7/video/chunks?upload_id=9');
  expect(fetch.mock.calls[2][1]?.method).toBeUndefined();
});

it('reports progress [40, 80, 100] and speed from an injected moving-window clock', async () => {
  vi.useFakeTimers();
  vi.setSystemTime(0);
  mockApi((offset, body) => {
    vi.advanceTimersByTime(body.size === 2_097_152 ? 500 : 1000);
    return Response.json({ next_offset: offset + body.size });
  });
  const progress: UploadProgress[] = [];
  await uploadFile(7, video(), {
    chunkSize: 4_194_304,
    onProgress: (value) => progress.push(value),
    now: Date.now,
    speedWindowMs: 1500,
  });
  expect(progress.map((value) => Math.round(value.percent))).toEqual([40, 80, 100]);
  expect(progress.map((value) => value.sentBytes)).toEqual([4_194_304, 8_388_608, 10_485_760]);
  expect(progress.map((value) => value.totalBytes)).toEqual([10_485_760, 10_485_760, 10_485_760]);
  expect(progress.map((value) => value.bytesPerSecond)).toEqual([4_194_304, 4_194_304, 4_194_304]);
});

it('cancel after the first chunk makes no further requests, including complete', async () => {
  const fetch = mockApi();
  const controller = new AbortController();
  await expect(
    uploadFile(7, video(), { signal: controller.signal, onProgress: () => controller.abort() }),
  ).rejects.toMatchObject({ code: 'cancelled' });
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(puts(fetch)).toHaveLength(1);
});

it('does not start a request for an already cancelled upload', async () => {
  const fetch = mockApi();
  const controller = new AbortController();
  controller.abort();
  await expect(uploadFile(7, video(), { signal: controller.signal })).rejects.toMatchObject({
    code: 'cancelled',
  });
  expect(fetch).not.toHaveBeenCalled();
});

it('aborts an in-flight PUT and sends no subsequent PUT or completion', async () => {
  const fetch = mockApi(
    () =>
      new Promise((_resolve, reject) =>
        controller.signal.addEventListener('abort', () =>
          reject(new DOMException('Aborted', 'AbortError')),
        ),
      ),
  );
  const controller = new AbortController();
  const result = uploadFile(7, video(10), { chunkSize: 4, signal: controller.signal }).catch(
    (error) => error,
  );
  await vi.waitFor(() => expect(puts(fetch)).toHaveLength(1));
  expect(puts(fetch)[0][1]?.signal).toBe(controller.signal);
  controller.abort();
  expect(await result).toMatchObject({ code: 'cancelled' });
  expect(puts(fetch)).toHaveLength(1);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('cancels during retry backoff without an offset query or another PUT', async () => {
  vi.useFakeTimers();
  const fetch = mockApi(() => {
    throw new TypeError('offline');
  });
  const controller = new AbortController();
  const result = uploadFile(7, video(100), { signal: controller.signal }).catch((error) => error);
  await vi.waitFor(() => expect(puts(fetch)).toHaveLength(1));
  controller.abort();
  expect(await result).toMatchObject({ code: 'cancelled' });
  await vi.runAllTimersAsync();
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('aborts an in-flight offset lookup without sending another chunk', async () => {
  vi.useFakeTimers();
  const controller = new AbortController();
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/video')) return Response.json({ upload_id: 9, run_id: 'run-1' });
    if (init?.method === 'PUT') throw new TypeError('offline');
    controller.abort();
    throw new DOMException('Aborted', 'AbortError');
  });
  vi.stubGlobal('fetch', fetch);
  const result = uploadFile(7, video(100), { signal: controller.signal }).catch((error) => error);
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
  expect(await result).toMatchObject({ code: 'cancelled' });
  await vi.runAllTimersAsync();
  expect(fetch).toHaveBeenCalledTimes(3);
  expect(puts(fetch)).toHaveLength(1);
  expect(fetch.mock.calls[2][1]?.signal).toBe(controller.signal);
});

describe('typed upload errors', () => {
  it.each([
    [415, 'unsupported_type'],
    [413, 'too_large'],
    [403, 'server'],
    [422, 'server'],
  ] as const)('maps HTTP %i to %s without retrying', async (status, code) => {
    const fetch = mockApi(() => new Response(null, { status }));
    await expect(uploadFile(7, video(100))).rejects.toMatchObject({ code, status });
    expect(puts(fetch)).toHaveLength(1);
  });

  it.each([
    ['network', 0],
    ['server', 500],
    ['server', 409],
  ] as const)('stops after three failed chunk attempts with %s (HTTP %i)', async (code, status) => {
    vi.useFakeTimers();
    const fetch = mockApi(() => {
      if (code === 'network') throw new TypeError('offline');
      return new Response(null, { status });
    });
    const result = uploadFile(7, video(), { chunkSize: 4_194_304 }).catch((error) => error);
    await vi.waitFor(() => expect(puts(fetch)).toHaveLength(3), { timeout: 4000 });
    expect(await result).toMatchObject({ code });
    expect(
      fetch.mock.calls.filter(([url, init]) => url.includes('/chunks?') && init?.method !== 'PUT'),
    ).toHaveLength(2);
    expect(fetch.mock.calls.some(([url]) => url.endsWith('/complete'))).toBe(false);
  });

  it('rejects a file above 10 GiB and unsupported types before any fetch', async () => {
    const fetch = mockApi();
    const tooLarge = { size: 10_737_418_241, type: 'video/mp4' } as File;
    await expect(uploadFile(7, tooLarge)).rejects.toMatchObject({ code: 'too_large' });
    await expect(
      createInspection({
        siteId: 1,
        filename: 'huge.mp4',
        size: 10_737_418_241,
        contentType: 'video/mp4',
      }),
    ).rejects.toMatchObject({ code: 'too_large' });
    const text = new NodeFile(['text'], 'notes.txt', { type: 'text/plain' }) as unknown as File;
    await expect(uploadFile(7, text)).rejects.toMatchObject({ code: 'unsupported_type' });
    expect(fetch).not.toHaveBeenCalled();
  });

  it('rejects malformed offsets instead of looping or claiming successful progress', async () => {
    const fetch = mockApi(() => Response.json({ next_offset: 0 }));
    await expect(uploadFile(7, video(100))).rejects.toMatchObject({ code: 'server' });
    expect(puts(fetch)).toHaveLength(1);
  });
});

it('manual retry reuses the same session and queries its offset', async () => {
  const fetch = mockApi();
  const session: UploadSession = { uploadId: 9, runId: 'run-1', sha256: '0'.repeat(64) };
  await uploadFile(7, video(), { session });
  expect(fetch.mock.calls[0][0]).toBe('/api/inspections/7/video/chunks?upload_id=9');
  expect(
    puts(fetch).map(([url]) => new URL(url, 'http://localhost').searchParams.get('offset')),
  ).toEqual(['4194304', '8388608']);
  expect(fetch.mock.calls.some(([url]) => url.endsWith('/video'))).toBe(false);
});

it('recovers a lost successful completion response without repeating the non-idempotent POST', async () => {
  let completed = false;
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/video')) return Response.json({ upload_id: 9, run_id: 'run-1' });
    if (url.endsWith('/complete')) {
      completed = true;
      throw new TypeError('completion response lost');
    }
    if (url.endsWith('/runs')) return Response.json([{ id: 'run-1', status: 'uploaded' }]);
    if (init?.method === 'PUT') return Response.json({ next_offset: 100 });
    return Response.json({ next_offset: completed ? 100 : 0 });
  });
  vi.stubGlobal('fetch', fetch);
  const file = video(100);
  const session: UploadSession = {};
  await expect(uploadFile(7, file, { session })).rejects.toMatchObject({ code: 'network' });
  await expect(uploadFile(7, file, { session })).resolves.toEqual({ runId: 'run-1' });
  expect(fetch.mock.calls.map(([url]) => url)).toEqual([
    '/api/inspections/7/video',
    '/api/inspections/7/video/chunks?upload_id=9&offset=0',
    '/api/inspections/7/video/complete',
    '/api/inspections/7/video/chunks?upload_id=9',
    '/api/inspections/7/runs',
  ]);
});

it('retries completion when the matching run is still uploading, ignoring other completed runs', async () => {
  const fetch = vi.fn(async (url: string) => {
    if (url.endsWith('/runs'))
      return Response.json([
        { id: 'other-run', status: 'uploaded' },
        { id: 'run-1', status: 'uploading' },
      ]);
    if (url.endsWith('/complete')) return Response.json(complete);
    return Response.json({ next_offset: 100 });
  });
  vi.stubGlobal('fetch', fetch);
  const session: UploadSession = {
    uploadId: 9,
    runId: 'run-1',
    sha256: '0'.repeat(64),
    completionAttempted: true,
  };
  await expect(uploadFile(7, video(100), { session })).resolves.toEqual({ runId: 'run-1' });
  expect(fetch.mock.calls.map(([url]) => url)).toEqual([
    '/api/inspections/7/video/chunks?upload_id=9',
    '/api/inspections/7/runs',
    '/api/inspections/7/video/complete',
  ]);
});
