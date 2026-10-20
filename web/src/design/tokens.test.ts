import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import tokens from './tokens.json';

const css = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8').replace(
  /\/\*[\s\S]*?\*\//g,
  '',
);

// Read balanced blocks so a dark :root can never satisfy a light assertion.
function blocks(source: string): { selector: string; body: string }[] {
  const result = [];
  let cursor = 0;
  while (cursor < source.length) {
    const open = source.indexOf('{', cursor);
    if (open < 0) break;
    let end = open + 1;
    let depth = 1;
    while (depth && end < source.length) {
      if (source[end] === '{') depth++;
      if (source[end] === '}') depth--;
      end++;
    }
    expect(depth).toBe(0);
    result.push({
      selector: source.slice(cursor, open).trim(),
      body: source.slice(open + 1, end - 1),
    });
    cursor = end;
  }
  return result;
}

function declarations(source: string) {
  return [...source.matchAll(/(--[\w-]+)\s*:\s*([^;{}]+);/g)].map(
    ([, name, value]) => [name, value.trim()] as const,
  );
}

const topLevel = blocks(css.slice(css.indexOf(':root')));
const root = topLevel.filter(({ selector }) => selector === ':root');
const darkMedia = topLevel.filter(
  ({ selector }) => selector === '@media (prefers-color-scheme: dark)',
);
const darkRoots = darkMedia
  .flatMap(({ body }) => blocks(body))
  .filter(({ selector }) => selector === ':root');
const theme = topLevel.filter(({ selector }) => selector === '@theme inline');

function contrast(first: string, second: string) {
  const luminance = (hex: string) => {
    const channels = [1, 3, 5].map((offset) => {
      const value = parseInt(hex.slice(offset, offset + 2), 16) / 255;
      return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    });
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
  };
  const values = [luminance(first), luminance(second)].sort((a, b) => a - b);
  return (values[1] + 0.05) / (values[0] + 0.05);
}

describe('platform-neutral design tokens', () => {
  it('preserves the existing viewer finding and coverage palette', () => {
    expect(tokens.shared.color).toMatchObject({
      'finding-critical': '#f0565c',
      'finding-watch': '#f2a33a',
      'finding-not-seen': '#6b727d',
      'finding-inspected': '#5b9bff',
      'viewer-canvas': '#000000',
    });
  });

  it.each(['light', 'dark'] as const)('meets AA contrast for %s text and form controls', (mode) => {
    const palette = tokens[mode];
    for (const background of [palette.bg, palette.surface, palette['surface-raised']]) {
      for (const foreground of [
        palette.text,
        palette['text-muted'],
        palette.accent,
        palette.error,
      ]) {
        expect(contrast(foreground, background)).toBeGreaterThanOrEqual(4.5);
      }
      expect(contrast(palette['focus-ring'], background)).toBeGreaterThanOrEqual(3);
    }
    expect(contrast(palette['on-accent'], palette.accent)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(palette['on-accent'], palette['accent-hover'])).toBeGreaterThanOrEqual(4.5);
    expect(contrast(palette['border-strong'], palette.bg)).toBeGreaterThanOrEqual(3);
    expect(
      contrast(tokens.shared.color['viewer-text'], tokens.shared.color['viewer-canvas']),
    ).toBeGreaterThanOrEqual(4.5);
  });

  it('has matching light/dark colour sets and no CSS expressions in JSON', () => {
    expect(Object.keys(tokens.dark).sort()).toEqual(Object.keys(tokens.light).sort());
    expect(JSON.stringify(tokens)).not.toMatch(/var\(|calc\(|@media|\d(?:px|rem)\b/);
    expect(tokens.shared.color['viewer-canvas']).toBe('#000000');
  });

  it('declares every light and shared value exactly once in the top-level root', () => {
    expect(root).toHaveLength(1);
    const actual = declarations(root[0].body);
    for (const [name, value] of Object.entries(tokens.light)) {
      expect(actual.filter(([key]) => key === `--color-${name}`)).toEqual([
        [`--color-${name}`, value],
      ]);
    }
    for (const [group, values] of Object.entries(tokens.shared)) {
      if (group === 'aliases') continue;
      for (const [name, value] of Object.entries(values)) {
        expect(actual.filter(([key]) => key === `--${group}-${name}`)).toEqual([
          [`--${group}-${name}`, String(value)],
        ]);
      }
    }
    expect(root[0].body).toMatch(/color-scheme:\s*light dark;/);
  });

  it('declares every dark value only inside prefers-color-scheme: dark', () => {
    expect(darkMedia).toHaveLength(1);
    expect(darkRoots).toHaveLength(1);
    expect(declarations(darkRoots[0].body).sort()).toEqual(
      Object.entries(tokens.dark)
        .map(([name, value]) => [`--color-${name}`, value])
        .sort(),
    );
  });

  it('keeps CSS length conversions and existing viewer aliases tied to the source tokens', () => {
    const actual = new Map(declarations(root[0].body));
    for (const name of Object.keys(tokens.shared.font).filter((name) => name.startsWith('size-'))) {
      expect(actual.get(`--type-${name.slice(5)}`)).toBe(`calc(var(--font-${name}) * 1px)`);
    }
    for (const [group, prefix] of [
      ['space', 'spacing'],
      ['radius', 'round'],
      ['layout', 'size'],
    ] as const) {
      for (const name of Object.keys(tokens.shared[group])) {
        expect(actual.get(`--${prefix}-${name}`)).toBe(`calc(var(--${group}-${name}) * 1px)`);
      }
    }
    expect(theme).toHaveLength(1);
    expect(declarations(theme[0].body).sort()).toEqual(
      Object.entries({
        ...tokens.shared.aliases,
        'color-bg': 'color-bg',
        'color-surface': 'color-surface',
      })
        .map(([name, target]) => [`--${name}`, `var(--${target})`])
        .sort(),
    );
  });

  it('has no extra undeclared colour custom properties anywhere in index.css', () => {
    const declared = new Set([
      ...Object.keys(tokens.light).map((name) => `--color-${name}`),
      ...Object.keys(tokens.shared.color).map((name) => `--color-${name}`),
      ...Object.keys(tokens.shared.aliases).map((name) => `--${name}`),
    ]);
    for (const [name] of declarations(css).filter(([name]) => name.startsWith('--color-'))) {
      expect(declared.has(name), name).toBe(true);
    }
  });
});
