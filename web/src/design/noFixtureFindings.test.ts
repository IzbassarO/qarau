import { readdirSync, readFileSync } from 'node:fs';
import { relative, resolve } from 'node:path';
import { expect, it } from 'vitest';

function sources(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name);
    return entry.isDirectory()
      ? sources(path)
      : /\.tsx?$/.test(entry.name) && !/\.(?:test|spec)\.tsx?$/.test(entry.name)
        ? [path]
        : [];
  });
}

it('keeps fabricated findings and test fixture imports out of client sources', () => {
  const root = resolve(process.cwd(), 'src');
  const files = sources(root);
  expect(files.length).toBeGreaterThan(0);
  for (const path of files) {
    const source = readFileSync(path, 'utf8');
    for (const forbidden of ['fixtures/evidence', 'Corrosion', '1.2 m²', 'test/fixtures/finding']) {
      expect(source, `${relative(root, path)} contains ${forbidden}`).not.toContain(forbidden);
    }
  }
});
