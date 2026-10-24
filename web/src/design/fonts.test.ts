import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import tokens from './tokens.json';

const sourceRoot = resolve(process.cwd(), 'src');
const main = readFileSync(resolve(sourceRoot, 'main.tsx'), 'utf8');
const css = readFileSync(resolve(sourceRoot, 'index.css'), 'utf8');
const fontCss = readFileSync(resolve(sourceRoot, 'design/tokens.css'), 'utf8');

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name);
    return entry.isDirectory()
      ? sourceFiles(path)
      : /\.(?:[cm]?[jt]sx?|css|html|json)$/.test(entry.name) &&
          !/\.(?:test|spec)\.[jt]sx?$/.test(entry.name)
        ? [path]
        : [];
  });
}

describe('self-hosted v8 typography', () => {
  it.each([
    '@fontsource/geologica/600.css',
    '@fontsource/ibm-plex-sans/400.css',
    '@fontsource/ibm-plex-sans/500.css',
    '@fontsource/ibm-plex-sans/600.css',
    '@fontsource/ibm-plex-sans/400-italic.css',
    '@fontsource/lora/400-italic.css',
    '@fontsource/jetbrains-mono/400.css',
    '@fontsource/jetbrains-mono/500.css',
  ])('imports %s locally', (path) => {
    expect(`${main}\n${css}`).toContain(`import '${path}';`);
  });

  it('contains no retired font family or font CDN in application sources', () => {
    const paths = [...sourceFiles(sourceRoot), resolve(process.cwd(), 'index.html')];
    expect(paths.length).toBeGreaterThan(1);
    for (const path of paths) {
      const source = readFileSync(path, 'utf8');
      expect(source, path).not.toMatch(/ibm-plex-mono|IBM Plex Mono/i);
      expect(source, path).not.toMatch(/fonts\.(?:googleapis|gstatic)\./i);
      expect(source, path).not.toMatch(/@(?:import|font-face)[^;{}]*https?:\/\//i);
    }
  });

  it('names the v8 families in the JSON and CSS font tokens', () => {
    expect(tokens.font.family).toEqual({
      display: '"Geologica", "IBM Plex Sans", system-ui, sans-serif',
      sans: '"IBM Plex Sans", system-ui, sans-serif',
      serif: '"Lora", Georgia, serif',
      mono: '"JetBrains Mono", ui-monospace, Menlo, monospace',
    });
    expect(fontCss).toMatch(
      /--font-display:\s*'Geologica', 'IBM Plex Sans', system-ui, sans-serif;/,
    );
    expect(fontCss).toMatch(/--font-sans:\s*'IBM Plex Sans', system-ui, sans-serif;/);
    expect(fontCss).toMatch(/--font-serif:\s*'Lora', Georgia, serif;/);
    expect(fontCss).toMatch(/--font-mono:\s*'JetBrains Mono', ui-monospace, Menlo, monospace;/);
  });
});
