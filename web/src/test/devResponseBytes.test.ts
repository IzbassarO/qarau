import { readFileSync } from 'node:fs';
import { IncomingMessage, ServerResponse } from 'node:http';
import { Socket } from 'node:net';
import { resolve } from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';
import { send, type ViteDevServer } from 'vite';
import { byteResponses } from '../../dev-response-bytes';
import { devRuns } from '../../vite-dev-runs';

const sockets: Socket[] = [];

afterEach(() => {
  sockets.splice(0).forEach((socket) => socket.destroy());
  vi.restoreAllMocks();
});

function response(method = 'GET') {
  // Exercise Node's real response implementation without listening on a port.
  const socket = new Socket();
  sockets.push(socket);
  const request = new IncomingMessage(socket);
  request.method = method;
  request.url = '/src/report.tsx';
  const reply = new ServerResponse(request);
  const nativeEnd = vi.spyOn(reply, 'end');
  const next = vi.fn();
  byteResponses(request, reply, next);
  expect(next).toHaveBeenCalledExactlyOnceWith();
  return { request, reply, nativeEnd };
}

it('installs before Vite and API handlers', () => {
  const use = vi.fn();
  const configure = devRuns().configureServer;
  if (typeof configure !== 'function') throw new Error('Expected configureServer hook');
  Reflect.apply(configure, undefined, [{ middlewares: { use } } as unknown as ViteDevServer]);
  expect(use.mock.calls[0]).toEqual([byteResponses]);
  expect(use.mock.calls[1][0]).toBe('/api/runs');
});

it('preserves Vite JS, source maps and headers', () => {
  const { request, reply, nativeEnd } = response();
  send(request, reply, 'export const title = "Қарау / Отчёт";\n', 'js', {
    etag: '"report-module"',
    map: {
      version: 3,
      file: 'report.tsx',
      sources: ['report.tsx'],
      names: [],
      mappings: 'AAAA',
      sourcesContent: ['export const title = "Қарау / Отчёт";\n'],
      toUrl: () => '',
    },
  });
  expect(nativeEnd).toHaveBeenCalledTimes(1);
  const body: unknown = nativeEnd.mock.calls[0][0];
  expect(Buffer.isBuffer(body)).toBe(true);
  if (!Buffer.isBuffer(body)) throw new Error('Expected bytes at Node HTTP boundary');
  expect(body.toString('utf8')).toContain('export const title = "Қарау / Отчёт";\n');
  expect(body.toString('utf8')).toContain('sourceMappingURL=data:application/json;base64,');
  expect(reply.statusCode).toBe(200);
  expect(reply.getHeader('Content-Type')).toBe('text/javascript');
  expect(reply.getHeader('Cache-Control')).toBe('no-cache');
  expect(reply.getHeader('Etag')).toBe('"report-module"');
  expect(reply.writableEnded).toBe(true);
});

it.each([
  ['html', '<h1>Есеп / Отчёт</h1>', 'text/html'],
  ['json', '{"object":"Қарау"}', 'application/json'],
  ['css', '.report { display: grid; }', 'text/css'],
])('preserves Vite %s response text as bytes', (type, content, contentType) => {
  const { request, reply, nativeEnd } = response();
  send(request, reply, content, type, { map: { mappings: '' } });
  const body: unknown = nativeEnd.mock.calls[0][0];
  expect(Buffer.isBuffer(body)).toBe(true);
  if (!Buffer.isBuffer(body)) throw new Error('Expected bytes');
  expect(body.toString('utf8')).toBe(content);
  expect(reply.getHeader('Content-Type')).toBe(contentType);
});

it('preserves PDF bytes', () => {
  const { reply, nativeEnd } = response();
  const pdf = readFileSync(resolve(process.cwd(), 'src/test/fixtures/report.pdf'));
  expect(reply.end(pdf)).toBe(reply);
  expect(nativeEnd.mock.calls[0][0]).toBe(pdf);
  expect(pdf.length).toBe(606);
});

it('preserves end encodings, overloads and return value', () => {
  const encoded = response();
  const done = vi.fn();
  expect(encoded.reply.end('d09ed182d187d191d182', 'hex', done)).toBe(encoded.reply);
  const body: unknown = encoded.nativeEnd.mock.calls[0][0];
  if (!Buffer.isBuffer(body)) throw new Error('Expected bytes');
  expect(body.toString('utf8')).toBe('Отчёт');
  expect(encoded.nativeEnd.mock.calls[0][1]).toBe('hex');
  expect(encoded.nativeEnd.mock.calls[0][2]).toBe(done);

  const text = response();
  expect(text.reply.end('Есеп', done)).toBe(text.reply);
  const textBody: unknown = text.nativeEnd.mock.calls[0][0];
  if (!Buffer.isBuffer(textBody)) throw new Error('Expected bytes');
  expect(textBody.toString('utf8')).toBe('Есеп');
  expect(text.nativeEnd.mock.calls[0][1]).toBe(done);

  const empty = response();
  expect(empty.reply.end(done)).toBe(empty.reply);
  expect(empty.nativeEnd).toHaveBeenCalledExactlyOnceWith(done);
});

it.each(['HEAD', '304'])('keeps Vite %s responses bodyless', (mode) => {
  const { request, reply, nativeEnd } = response(mode === 'HEAD' ? 'HEAD' : 'GET');
  if (mode === '304') request.headers['if-none-match'] = '"cached"';
  send(request, reply, 'export const report = true;', 'js', { etag: '"cached"' });
  expect(nativeEnd).toHaveBeenCalledExactlyOnceWith();
  expect(reply.statusCode).toBe(mode === '304' ? 304 : 200);
});
