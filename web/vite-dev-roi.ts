import { randomUUID, timingSafeEqual } from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { parse, serialize } from './src/viewer/roi.ts';

/** Matches the production API's declared-role convention; shared-token auth is M1 only. */
export const ROI_ROLES = { GET: 'viewer', PUT: 'editor', DELETE: 'editor' } as const;
const SITE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]*$/;
const MAX_BODY_BYTES = 64 * 1024;

class RequestError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

function isMissing(error: unknown): boolean {
  return error instanceof Error && 'code' in error && error.code === 'ENOENT';
}

/** Reject symlinks rather than letting a site or ROI file escape the catalog. */
async function directory(parent: string, name: string, create: boolean): Promise<string> {
  const result = path.join(parent, name);
  if (create) {
    try {
      await fs.mkdir(result);
    } catch (error) {
      if (!(error instanceof Error && 'code' in error && error.code === 'EEXIST')) throw error;
    }
  }
  const stat = await fs.lstat(result);
  if (stat.isSymbolicLink() || !stat.isDirectory())
    throw new RequestError(400, 'Invalid catalog path');
  return result;
}

async function roiPath(root: string, siteId: string, create: boolean): Promise<string> {
  if (create) await fs.mkdir(root, { recursive: true });
  const catalog = await fs.realpath(root);
  const sites = await directory(catalog, 'sites', create);
  const site = await directory(sites, siteId, create);
  const file = path.join(site, 'roi.json');
  try {
    const stat = await fs.lstat(file);
    if (stat.isSymbolicLink() || !stat.isFile()) throw new RequestError(400, 'Invalid region file');
  } catch (error) {
    if (!isMissing(error)) throw error;
  }
  return file;
}

function requireRole(req: IncomingMessage, token: string, role: string): void {
  if (!role) throw new Error('Every region route must declare a role');
  if (!token) return; // Same explicit development-mode policy as backend/app/auth.py.
  const authorization = req.headers.authorization ?? '';
  const supplied = Buffer.from(authorization.replace(/^Bearer /, ''));
  const expected = Buffer.from(token);
  if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected))
    throw new RequestError(401, 'Invalid or missing token');
}

async function requestBody(req: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];
  let bytes = 0;
  for await (const chunk of req) {
    const data = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    bytes += data.length;
    if (bytes > MAX_BODY_BYTES) throw new RequestError(413, 'Region request is too large');
    chunks.push(data);
  }
  return Buffer.concat(chunks).toString('utf8');
}

async function writeAtomic(file: string, body: string): Promise<void> {
  const temporary = path.join(path.dirname(file), `.roi-${randomUUID()}.tmp`);
  try {
    await fs.writeFile(temporary, body + '\n', { flag: 'wx', mode: 0o600 });
    await fs.rename(temporary, file);
  } finally {
    await fs.rm(temporary, { force: true });
  }
}

/** Development catalog adapter. Mount at /api/sites; production needs the FastAPI router. */
export function createRoiMiddleware(root: string, token = '') {
  return async (req: IncomingMessage, res: ServerResponse, next: () => void) => {
    const route = (req.url ?? '/').split('?')[0].match(/^\/(.+)\/roi\/?$/);
    if (!route) return next();
    const send = (status: number, body?: string) => {
      res.statusCode = status;
      res.setHeader('Cache-Control', 'no-store');
      if (body !== undefined) res.setHeader('Content-Type', 'application/json');
      res.end(body);
    };
    try {
      const method = req.method ?? '';
      if (!Object.hasOwn(ROI_ROLES, method)) {
        res.setHeader('Allow', 'GET, PUT, DELETE');
        return send(405, JSON.stringify({ detail: 'Method not allowed' }));
      }
      requireRole(req, token, ROI_ROLES[method as keyof typeof ROI_ROLES]);
      let siteId: string;
      try {
        siteId = decodeURIComponent(route[1]);
      } catch {
        throw new RequestError(400, 'Invalid site id');
      }
      if (!SITE_ID.test(siteId) || siteId.includes('..'))
        throw new RequestError(400, 'Invalid site id');
      if (method === 'PUT') {
        let region;
        try {
          const parsed = parse(await requestBody(req));
          if (!parsed.ok) throw new RequestError(422, 'Invalid region document');
          region = parsed.value;
        } catch (error) {
          if (error instanceof RequestError) throw error;
          throw new RequestError(422, 'Invalid region document');
        }
        if (region.site_id !== siteId)
          throw new RequestError(422, 'Region site does not match the URL');
        const file = await roiPath(root, siteId, true);
        await writeAtomic(file, serialize(region));
        return send(200, serialize(region));
      }
      try {
        const file = await roiPath(root, siteId, false);
        if (method === 'DELETE') {
          await fs.rm(file, { force: true });
          return send(204);
        }
        const parsed = parse(await fs.readFile(file, 'utf8'));
        if (!parsed.ok) throw new Error('Invalid stored region');
        const region = parsed.value;
        if (region.site_id !== siteId) throw new Error('Stored region site mismatch');
        return send(200, serialize(region));
      } catch (error) {
        if (!isMissing(error)) throw error;
        if (method === 'DELETE') return send(204);
        throw new RequestError(404, 'No region saved for this site');
      }
    } catch (error) {
      const status = error instanceof RequestError ? error.status : 500;
      if (status === 401) res.setHeader('WWW-Authenticate', 'Bearer');
      return send(
        status,
        JSON.stringify({
          detail: error instanceof RequestError ? error.message : 'Could not access saved region',
        }),
      );
    }
  };
}
