import type { Connect } from 'vite';

/** Send bytes around Node 26's HTTP fast-call abort; preserve binary bodies/overloads.
 * https://github.com/nodejs/node/issues/66870
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
