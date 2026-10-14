import fs from 'node:fs';
import path from 'node:path';
import type { ServerResponse } from 'node:http';
import type { Plugin } from 'vite';

const TYPES: Record<string, string> = {
  '.json': 'application/json',
  '.jpg': 'image/jpeg',
  '.png': 'image/png',
};

/** Dev-only stand-in for the API: GET /api/runs, /api/runs/:id/manifest.json, /api/runs/:id/files/*. */
export function devRuns(): Plugin {
  return {
    name: 'qarau-dev-runs',
    apply: 'serve',
    configureServer(server) {
      const root = path.resolve(process.env.QARAU_RUNS_DIR ?? '../data/runs');
      const send = (
        res: ServerResponse,
        code: number,
        body: string | Buffer,
        type = 'text/plain',
      ) => {
        res.statusCode = code;
        res.setHeader('Content-Type', type);
        res.end(body);
      };
      // resolve rel under base; null if it escapes (path traversal)
      const inside = (base: string, rel: string) => {
        const p = path.resolve(base, rel);
        return p.startsWith(base + path.sep) ? p : null;
      };
      server.middlewares.use('/api/runs', (req, res, next) => {
        if (req.method !== 'GET') return next();
        const parts = decodeURIComponent((req.url ?? '/').split('?')[0])
          .split('/')
          .filter(Boolean);
        if (parts.length === 0) {
          const out = [];
          for (const id of fs.existsSync(root) ? fs.readdirSync(root) : []) {
            try {
              const m = JSON.parse(
                fs.readFileSync(path.join(root, id, 'web/manifest.json'), 'utf8'),
              );
              out.push({ run_id: id, source: m.source, stats: m.stats });
            } catch {
              /* not a run */
            }
          }
          return send(res, 200, JSON.stringify(out), TYPES['.json']);
        }
        const dir = inside(root, parts[0]);
        if (!dir) return send(res, 400, 'bad path');
        let file: string | null;
        if (parts.length === 2 && parts[1] === 'manifest.json')
          file = path.join(dir, 'web/manifest.json');
        else if (parts[1] === 'files' && parts.length > 2)
          file = inside(dir, parts.slice(2).join('/'));
        else return send(res, 404, 'not found');
        if (!file || !fs.existsSync(file) || !fs.statSync(file).isFile())
          return send(res, 404, 'not found');
        send(
          res,
          200,
          fs.readFileSync(file),
          TYPES[path.extname(file)] ?? 'application/octet-stream',
        );
      });
    },
  };
}
