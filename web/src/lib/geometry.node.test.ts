import { execFileSync } from 'node:child_process';
import { expect, it } from 'vitest';

it('loads the shared geometry module and its token JSON through native Node ESM', () => {
  // Playwright collects this module through bundle.ts without Vite's JSON loader.
  const output = execFileSync(
    process.execPath,
    [
      '--experimental-strip-types',
      '--input-type=module',
      '--eval',
      'import { RAMP } from "./src/lib/geometry.ts"; process.stdout.write(JSON.stringify(RAMP));',
    ],
    { cwd: process.cwd(), encoding: 'utf8' },
  );

  expect(JSON.parse(output)).toEqual(['#0E2A5C', '#2E6FD8', '#9CC3FF', '#EAF2FF']);
});
