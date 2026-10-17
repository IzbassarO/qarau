import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { Readable } from 'node:stream';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { createRoiMiddleware, ROI_ROLES } from '../../vite-dev-roi';

const region = {
  version: 1,
  site_id: 'notre-dame',
  source_run: 'notre-dame',
  frame: 'reconstruction',
  up_axis: 'z',
  min: [-10, -20, -3],
  max: [10, 20, 30],
  created_at: '2026-10-17T12:00:00Z',
  created_by: 'engineer',
};

let root: string;
beforeEach(async () => {
  root = await fs.mkdtemp(path.join(os.tmpdir(), 'qarau-a7-roi-'));
});
afterEach(async () => {
  await fs.rm(root, { recursive: true, force: true });
});

async function request(
  method: string,
  url = '/notre-dame/roi',
  body?: unknown,
  authorization?: string,
  token = '',
) {
  const payload = typeof body === 'string' ? body : body === undefined ? '' : JSON.stringify(body);
  const req = Object.assign(Readable.from(payload ? [payload] : []), {
    method,
    url,
    headers: { authorization },
  }) as IncomingMessage;
  const response = {
    statusCode: 0,
    headers: {} as Record<string, string>,
    body: '',
    setHeader(name: string, value: string) {
      this.headers[name] = value;
    },
    end(value?: string) {
      this.body = value ?? '';
    },
  };
  let next = false;
  // Construct a new middleware for each request, so reloads cannot depend on memory.
  await createRoiMiddleware(root, token)(req, response as unknown as ServerResponse, () => {
    next = true;
  });
  return { ...response, next };
}

describe('development catalog ROI persistence', () => {
  it('declares a role for every route method', () => {
    expect(ROI_ROLES).toEqual({ GET: 'viewer', PUT: 'editor', DELETE: 'editor' });
  });

  it('stores atomically and loads across fresh middleware instances', async () => {
    expect((await request('PUT', undefined, region)).statusCode).toBe(200);
    const loaded = await request('GET');
    expect(loaded.statusCode).toBe(200);
    expect(JSON.parse(loaded.body)).toEqual(region);
    const site = path.join(root, 'sites', region.site_id);
    expect(JSON.parse(await fs.readFile(path.join(site, 'roi.json'), 'utf8'))).toEqual(region);
    expect(await fs.readdir(site)).toEqual(['roi.json']);
    const replacement = { ...region, source_run: 'later-inspection', max: [12, 23, 34] };
    expect((await request('PUT', undefined, replacement)).statusCode).toBe(200);
    expect(JSON.parse((await request('GET')).body)).toEqual(replacement);
  });

  it('returns 404 for an absent region and deletes idempotently', async () => {
    expect((await request('GET')).statusCode).toBe(404);
    expect((await request('DELETE')).statusCode).toBe(204);
    await request('PUT', undefined, region);
    expect((await request('DELETE')).statusCode).toBe(204);
    expect((await request('GET')).statusCode).toBe(404);
  });

  it.each([
    {},
    { ...region, version: 2 },
    { ...region, site_id: 'another-site' },
    { ...region, min: [15, 0, 0], max: [1, 2, 3] },
    { ...region, max: [1, null, 3] },
    '{broken json',
  ])('returns 422 for an invalid document: %j', async (body) => {
    expect((await request('PUT', undefined, body)).statusCode).toBe(422);
    expect((await request('GET')).statusCode).toBe(404);
  });

  it.each(['GET', 'PUT', 'DELETE'])('rejects missing or incorrect auth on %s', async (method) => {
    const rejected = await request(method, undefined, region, undefined, 'test-token');
    expect(rejected.statusCode).toBe(401);
    expect(rejected.headers['WWW-Authenticate']).toBe('Bearer');
    expect(
      (await request(method, undefined, region, 'Bearer wrong', 'test-token')).statusCode,
    ).toBe(401);
  });

  it('accepts the configured shared token', async () => {
    const saved = await request('PUT', undefined, region, 'Bearer test-token', 'test-token');
    expect(saved.statusCode).toBe(200);
    expect(
      (await request('GET', undefined, undefined, 'Bearer test-token', 'test-token')).statusCode,
    ).toBe(200);
  });

  it.each(['/../roi', '/..%2Foutside/roi', '/%2E%2E/roi', '/%zz/roi', '/site%5Coutside/roi'])(
    'rejects traversal and malformed site ids: %s',
    async (url) => {
      expect((await request('PUT', url, region)).statusCode).toBe(400);
      expect(await fs.readdir(root)).toEqual([]);
    },
  );

  it('rejects sites and files pointing outside the catalog through symlinks', async () => {
    const outside = await fs.mkdtemp(path.join(os.tmpdir(), 'qarau-a7-roi-outside-'));
    try {
      await fs.mkdir(path.join(root, 'sites'));
      await fs.symlink(outside, path.join(root, 'sites', region.site_id));
      expect((await request('PUT', undefined, region)).statusCode).toBe(400);
      expect(await fs.readdir(outside)).toEqual([]);
      await fs.unlink(path.join(root, 'sites', region.site_id));
      await request('PUT', undefined, region);
      const file = path.join(root, 'sites', region.site_id, 'roi.json');
      await fs.unlink(file);
      await fs.symlink(path.join(outside, 'roi.json'), file);
      expect((await request('PUT', undefined, region)).statusCode).toBe(400);
      expect((await request('GET')).statusCode).toBe(400);
    } finally {
      await fs.rm(outside, { recursive: true, force: true });
    }
  });

  it('surfaces corrupt storage and storage errors as 500', async () => {
    await request('PUT', undefined, region);
    await fs.writeFile(path.join(root, 'sites', region.site_id, 'roi.json'), '{corrupt');
    expect((await request('GET')).statusCode).toBe(500);
    await fs.rm(path.join(root, 'sites'), { recursive: true });
    await fs.writeFile(path.join(root, 'sites'), 'not a directory');
    expect((await request('GET')).statusCode).toBe(400);
  });

  it('rejects oversized bodies and reports unsupported methods', async () => {
    expect((await request('PUT', undefined, 'a'.repeat(65537))).statusCode).toBe(413);
    const response = await request('POST');
    expect(response.statusCode).toBe(405);
    expect(response.headers.Allow).toBe('GET, PUT, DELETE');
    expect((await request('GET', '/not-a-region')).next).toBe(true);
  });
});
