import type { Connect } from 'vite';

/** Avoid Node 26's Buffer.byteLength fast-call abort when Vite ends a string response.
 * https://github.com/nodejs/node/issues/66870
 * Keep the conversion local to this response; binary bodies and overloads pass through.
 */
export const byteResponses: Connect.NextHandleFunction = (_request, response, next) => {
  const end = response.end;
  response.end = function (...args: unknown[]) {
    if (typeof args[0] === 'string') {
      const encoding = typeof args[1] === 'string' ? (args[1] as BufferEncoding) : undefined;
      args[0] = Buffer.from(args[0], encoding);
    }
    return Reflect.apply(end, this, args);
  };
  next();
};
