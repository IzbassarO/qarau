import type { IncomingMessage, ServerResponse } from 'node:http';
import { vi } from 'vitest';
import { devReport } from '../../dev-report';

/** Exercise the real dev API handlers without a listening server. */
export function mockDevReportApi(hang?: string) {
  const pending = new Set<string>();
  const signals: AbortSignal[] = [];
  const fetch = vi.fn((input: string | URL | Request, options?: RequestInit) => {
    const path = String(input);
    if (path === hang) {
      const signal = options!.signal!;
      signals.push(signal);
      pending.add(path);
      return new Promise<Response>((_resolve, reject) => {
        const abort = () => {
          pending.delete(path);
          reject(new DOMException('Request aborted', 'AbortError'));
        };
        if (signal.aborted) abort();
        else signal.addEventListener('abort', abort, { once: true });
      });
    }
    const headers = new Headers();
    let body: string | Uint8Array = '';
    const response = {
      statusCode: 200,
      setHeader: (name: string, value: string) => headers.set(name, value),
      end: (value: string | Uint8Array) => {
        body = value;
      },
    };
    devReport(
      { url: path, method: 'GET' } as IncomingMessage,
      response as unknown as ServerResponse,
      () => {
        response.statusCode = 404;
        headers.set('Content-Type', 'application/json');
        body = '{"detail":"Not found"}';
      },
    );
    return Promise.resolve(
      new Response(body as BodyInit, { status: response.statusCode, headers }),
    );
  });
  vi.stubGlobal('fetch', fetch);
  return { fetch, pending, signals };
}
