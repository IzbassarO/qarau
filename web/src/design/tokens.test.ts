import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import tokens from './tokens.json';

const readCss = (path: string) =>
  readFileSync(resolve(process.cwd(), path), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
const css = readCss('src/design/tokens.css');
const index = readCss('src/index.css');
const bundle = readCss('src/design/bundle.css');

// Fixed v8 fixtures from the owner-provided export, independent of the app's tokens.
const palettes = {
  light: {
    bg: '#f4f4f3',
    surface: '#ffffff',
    'surface-raised': '#ffffff',
    'surface-sunken': '#f7f7f6',
    selection: '#ececea',
    line: '#e5e5e3',
    'line-strong': '#c9c9c6',
    ink: '#111111',
    'ink-muted': '#60605c',
    'ink-faint': '#8f8f8a',
    accent: '#111111',
    'accent-hover': '#2c2c2c',
    'accent-soft': '#ececea',
    'on-accent': '#ffffff',
    critical: '#c42a18',
    'critical-soft': '#fbe9e6',
    watch: '#a65400',
    'watch-soft': '#fbeedc',
    ok: '#1a7040',
    'ok-soft': '#e1f3e8',
    unseen: '#6f6f6b',
  },
  dark: {
    bg: '#161616',
    surface: '#1e1e1e',
    'surface-raised': '#262626',
    'surface-sunken': '#1a1a1a',
    selection: '#303030',
    line: '#333333',
    'line-strong': '#4d4d4d',
    ink: '#f2f2f0',
    'ink-muted': '#a6a6a2',
    'ink-faint': '#707070',
    accent: '#f2f2f0',
    'accent-hover': '#dcdcda',
    'accent-soft': '#303030',
    'on-accent': '#111111',
    critical: '#ff7466',
    'critical-soft': '#4a2320',
    watch: '#f5a742',
    'watch-soft': '#45321a',
    ok: '#6bd39b',
    'ok-soft': '#1b3a28',
    unseen: '#9a9a96',
  },
  midnight: {
    bg: '#0b0d11',
    surface: '#12151a',
    'surface-raised': '#191d23',
    'surface-sunken': '#0e1014',
    selection: '#222730',
    line: '#232830',
    'line-strong': '#3a414b',
    ink: '#eef0f3',
    'ink-muted': '#a3a9b3',
    'ink-faint': '#6b727c',
    accent: '#eef0f3',
    'accent-hover': '#d5d9df',
    'accent-soft': '#222730',
    'on-accent': '#0b0d11',
    critical: '#ff7f6e',
    'critical-soft': '#4a2420',
    watch: '#f9b857',
    'watch-soft': '#44321a',
    ok: '#7ddfa8',
    'ok-soft': '#1a3a2a',
    unseen: '#8f98a3',
  },
};
const shadows = {
  light: {
    'shadow-sm': '0 1px 2px rgba(20, 26, 34, 0.06)',
    'shadow-md': '0 6px 16px rgba(20, 26, 34, 0.1), 0 1px 3px rgba(20, 26, 34, 0.06)',
    'shadow-lg': '0 24px 48px rgba(20, 26, 34, 0.18), 0 4px 12px rgba(20, 26, 34, 0.08)',
    'shadow-btn': 'inset 0 1px 0 rgba(255, 255, 255, 0.18), 0 1px 2px rgba(15, 18, 23, 0.18)',
    'shadow-focus': '0 0 0 3px rgba(17, 17, 17, 0.18)',
  },
  dark: {
    'shadow-sm': '0 1px 2px rgba(0, 0, 0, 0.35)',
    'shadow-md': '0 8px 24px rgba(0, 0, 0, 0.45), 0 1px 2px rgba(0, 0, 0, 0.5)',
    'shadow-lg': '0 24px 64px rgba(0, 0, 0, 0.6), 0 4px 12px rgba(0, 0, 0, 0.45)',
    'shadow-btn': 'inset 0 1px 0 rgba(255, 255, 255, 0.12), 0 1px 2px rgba(0, 0, 0, 0.5)',
    'shadow-focus': '0 0 0 3px rgba(242, 242, 240, 0.25)',
  },
  midnight: {
    'shadow-sm': '0 1px 2px rgba(0, 0, 0, 0.5)',
    'shadow-md': '0 8px 24px rgba(0, 0, 0, 0.55), 0 1px 2px rgba(0, 0, 0, 0.6)',
    'shadow-lg': '0 24px 64px rgba(0, 0, 0, 0.7), 0 4px 12px rgba(0, 0, 0, 0.5)',
    'shadow-btn': 'inset 0 1px 0 rgba(255, 255, 255, 0.12), 0 1px 2px rgba(0, 0, 0, 0.6)',
    'shadow-focus': '0 0 0 3px rgba(238, 240, 243, 0.25)',
  },
};
const spaces = { '1': 4, '2': 8, '3': 12, '4': 16, '5': 20, '6': 24, '8': 32, '10': 40, '12': 48 };
const radii = { sm: 6, md: 8, lg: 12, xl: 16 };
const layout = {
  sidebar: 248,
  topbar: 48,
  row: 56,
  rail: 40,
  'side-panel': 460,
  timeline: 108,
  'dialog-min': 640,
  'dialog-max': 920,
  'report-document': 794,
  phone: 390,
};
const families = {
  display: '"Geologica", "IBM Plex Sans", system-ui, sans-serif',
  sans: '"IBM Plex Sans", system-ui, sans-serif',
  serif: '"Lora", Georgia, serif',
  mono: '"JetBrains Mono", ui-monospace, Menlo, monospace',
};
const typography = {
  't-hero': {
    family: 'display',
    size: 44,
    'line-height': 48,
    weight: 600,
    'letter-spacing': -0.025,
  },
  't-display': {
    family: 'display',
    size: 30,
    'line-height': 36,
    weight: 600,
    'letter-spacing': -0.02,
  },
  't-title': {
    family: 'display',
    size: 22,
    'line-height': 28,
    weight: 600,
    'letter-spacing': -0.015,
  },
  't-heading': {
    family: 'display',
    size: 16,
    'line-height': 22,
    weight: 600,
    'letter-spacing': -0.005,
  },
  't-body': { family: 'sans', size: 14, 'line-height': 20, weight: 400 },
  't-body-strong': { family: 'sans', size: 14, 'line-height': 20, weight: 600 },
  't-body-italic': { family: 'sans', size: 14, 'line-height': 20, weight: 400, style: 'italic' },
  't-caption': { family: 'sans', size: 13, 'line-height': 18, weight: 400 },
  't-label': { family: 'sans', size: 13, 'line-height': 18, weight: 500 },
  't-quote': { family: 'serif', size: 18, 'line-height': 26, weight: 400, style: 'italic' },
  't-mono': { family: 'mono', size: 13, 'line-height': 20, weight: 400 },
  't-mono-large': {
    family: 'mono',
    size: 28,
    'line-height': 32,
    weight: 500,
    'letter-spacing': -0.02,
  },
};
const component = {
  'color-white': '#ffffff',
  'color-black': '#111111',
  'color-transparent': 'transparent',
  'radius-question': 15,
  'font-size-detail': 12.5,
  'font-size-body-plus': 13.5,
  'font-size-control-large': 15,
  'shadow-toggle-knob': '0 1px 2px rgba(0, 0, 0, 0.25)',
  'shadow-thumbnail-inset': 'inset 0 0 0 1px rgba(255, 255, 255, 0.06)',
  'shadow-thumbnail-selected': '0 1px 3px rgba(0, 0, 0, 0.4)',
  'mask-critical-fill': 'rgba(210, 50, 31, 0.14)',
  'mask-watch-fill': 'rgba(182, 93, 0, 0.14)',
  'color-thumbnail-tag': 'rgba(19, 19, 19, 0.72)',
  'color-dialog-scrim': 'rgba(13, 15, 19, 0.45)',
  'color-map-pill': 'rgba(13, 15, 19, 0.7)',
  'signin-hero-bg': '#121212',
  'signin-hero-ink': '#f4f4f2',
  'signin-hero-gradient':
    'radial-gradient(900px 640px at 20% 0%, #3a3a3a 0%, rgba(58, 58, 58, 0) 60%), radial-gradient(700px 500px at 100% 100%, #2a2a2a 0%, rgba(42, 42, 42, 0) 60%)',
  'signin-hero-sub': 'rgba(244, 244, 242, 0.78)',
  'signin-hero-logos': 'rgba(244, 244, 242, 0.5)',
  'signin-hero-logo-strong': 'rgba(244, 240, 232, 0.8)',
  'shadow-map-pin': '0 0 0 4px rgba(255, 255, 255, 0.25)',
  'color-hud-key': 'rgba(26, 25, 23, 0.78)',
  'color-hud-key-border': 'rgba(236, 232, 223, 0.45)',
  'color-map-target': 'rgba(26, 25, 23, 0.8)',
  'color-map-menu-scrim': 'rgba(26, 25, 23, 0.55)',
  'color-photo-dot': 'rgba(255, 255, 255, 0.45)',
  'color-media-overlay': 'rgba(19, 19, 19, 0.7)',
  'color-video-time': 'rgba(0, 0, 0, 0.45)',
  'select-indicator-image':
    'linear-gradient(45deg, transparent 50%, currentColor 50%), linear-gradient(135deg, currentColor 50%, transparent 50%)',
};

// Read balanced blocks so a dark selector can never satisfy a light assertion.
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
    ([, name, value]) => [name, value.trim().replace(/\s+/g, ' ')] as const,
  );
}
const tokenBlocks = blocks(css);
const shared = tokenBlocks.filter(({ selector }) => selector === ':root');
const indexBlocks = blocks(index.slice(index.indexOf(':root')));
const compatibility = new Map(
  declarations(indexBlocks.find(({ selector }) => selector === ':root')!.body),
);

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

describe('platform-neutral v8 design tokens', () => {
  it('keeps exactly one value set, the fixed v8 palette', () => {
    expect(tokens.version).toBe(8);
    expect(tokens).not.toHaveProperty('v8');
    expect(tokens).not.toHaveProperty('light');
    expect(tokens).not.toHaveProperty('dark');
    expect(tokens).not.toHaveProperty('shared');
    expect(tokens.themes).toEqual(palettes);
    expect(tokens.map).toEqual({
      ground: '#131313',
      grid: '#262626',
      text: '#ededeb',
      'height-ramp': ['#0E2A5C', '#2E6FD8', '#9CC3FF', '#EAF2FF'],
    });
    expect(tokens.space).toEqual(spaces);
    expect(tokens.radius).toEqual(radii);
    expect(tokens.layout).toEqual(layout);
    expect(tokens.font.family).toEqual(families);
    expect(tokens.font.style).toEqual(typography);
    expect(tokens.component).toEqual(component);
  });

  it.each(['light', 'dark', 'midnight'] as const)(
    'declares the exact %s palette, map and v8 shadows in its own selector',
    (mode) => {
      const selector = mode === 'light' ? ":root,\n[data-theme='light']" : `[data-theme='${mode}']`;
      const theme = tokenBlocks.filter((block) => block.selector === selector);
      expect(theme).toHaveLength(1);
      expect(declarations(theme[0].body).sort()).toEqual(
        Object.entries({
          ...palettes[mode],
          'map-ground': '#131313',
          'map-grid': '#262626',
          'map-text': '#ededeb',
          ...shadows[mode],
        })
          .map(([name, value]) => [`--${name}`, value])
          .sort(),
      );
    },
  );

  it.each(['light', 'dark', 'midnight'] as const)(
    'keeps AA contrast for readable %s text',
    (mode) => {
      const palette = tokens.themes[mode];
      for (const background of [
        palette.bg,
        palette.surface,
        palette['surface-raised'],
        palette['surface-sunken'],
      ]) {
        for (const foreground of [
          palette.ink,
          palette['ink-muted'],
          palette.accent,
          palette.critical,
          palette.watch,
          palette.ok,
          palette.unseen,
        ]) {
          expect(contrast(foreground, background)).toBeGreaterThanOrEqual(4.5);
        }
      }
      expect(contrast(palette['on-accent'], palette.accent)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(palette['on-accent'], palette['accent-hover'])).toBeGreaterThanOrEqual(4.5);
      expect(contrast(tokens.map.text, tokens.map.ground)).toBeGreaterThanOrEqual(4.5);
    },
  );

  it.each([
    ['light', '#c9c9c6', 1.51, '0 0 0 3px rgba(17, 17, 17, 0.18)'],
    ['dark', '#4d4d4d', 2.14, '0 0 0 3px rgba(242, 242, 240, 0.25)'],
    ['midnight', '#3a414b', 1.89, '0 0 0 3px rgba(238, 240, 243, 0.25)'],
  ] as const)(
    'preserves owner-approved %s border and focus values',
    (mode, border, ratio, focus) => {
      expect(tokens.themes[mode]['line-strong']).toBe(border);
      expect(contrast(tokens.themes[mode]['line-strong'], tokens.themes[mode].bg)).toBeCloseTo(
        ratio,
        2,
      );
      const selector = mode === 'light' ? ":root,\n[data-theme='light']" : `[data-theme='${mode}']`;
      const actual = new Map(
        declarations(tokenBlocks.find((block) => block.selector === selector)!.body),
      );
      expect(actual.get('--shadow-focus')).toBe(focus);
    },
  );

  it('declares shared lengths, families and typography through v8 custom properties', () => {
    expect(shared).toHaveLength(1);
    const entries = declarations(shared[0].body);
    const actual = new Map(entries);
    expect(actual.size).toBe(entries.length);
    for (const [group, values] of Object.entries({ space: spaces, radius: radii, layout })) {
      for (const [name, value] of Object.entries(values)) {
        expect(actual.get(`--${group}-${name}`)).toBe(`${value}px`);
      }
    }
    for (const [name, value] of Object.entries(families)) {
      expect(actual.get(`--font-${name}`)?.replace(/'/g, '"')).toBe(value);
    }
    for (const [name, style] of Object.entries(typography)) {
      expect(actual.get(`--${name}-family`)).toBe(`var(--font-${style.family})`);
      expect(actual.get(`--${name}-size`)).toBe(`${style.size}px`);
      expect(actual.get(`--${name}-line-height`)).toBe(`${style['line-height']}px`);
      expect(actual.get(`--${name}-weight`)).toBe(String(style.weight));
      if ('letter-spacing' in style) {
        expect(actual.get(`--${name}-letter-spacing`)).toBe(`${style['letter-spacing']}em`);
      }
      if ('style' in style) expect(actual.get(`--${name}-style`)).toBe('italic');
    }
    for (const [name, value] of Object.entries(component)) {
      expect(actual.get(`--${name}`)).toBe(typeof value === 'number' ? `${value}px` : value);
    }
  });

  it('names the former bundle literals without changing their fixed values', () => {
    expect(bundle).not.toMatch(/#(?:fff|111)\b|\b(?:12\.5|13\.5|15)px\b/i);
    for (const name of [
      'font-size-detail',
      'font-size-body-plus',
      'font-size-control-large',
      'signin-hero-gradient',
    ]) {
      expect(bundle).toContain(`var(--${name})`);
    }
    for (const selector of ['.t-body-italic', '.t-quote']) {
      expect(
        blocks(bundle).some(
          (block) =>
            block.selector
              .split(',')
              .map((part) => part.trim())
              .includes(selector) && /font-style:\s*italic;/.test(block.body),
        ),
      ).toBe(true);
    }
  });

  it('imports v8 styles and maps every legacy colour name to v8', () => {
    expect(index).toContain("@import './design/tokens.css';");
    expect(index).toContain("@import './design/bundle.css';");
    expect(index).not.toMatch(/prefers-color-scheme|#[\da-f]{3,8}\b|rgba?\(|hsla?\(/i);
    const aliases = {
      bg: 'bg',
      surface: 'surface',
      'surface-raised': 'surface-raised',
      border: 'line',
      'border-strong': 'line-strong',
      text: 'ink',
      'text-muted': 'ink-muted',
      accent: 'accent',
      'accent-hover': 'accent-hover',
      'on-accent': 'on-accent',
      'focus-ring': 'accent',
      error: 'ink',
      backdrop: 'color-dialog-scrim',
      'finding-critical': 'critical',
      'finding-watch': 'watch',
      'finding-not-seen': 'unseen',
      'finding-inspected': 'ok',
      'viewer-canvas': 'map-ground',
      'viewer-text': 'map-text',
      'viewer-current': 'map-text',
      'viewer-later': 'map-grid',
    };
    for (const [name, target] of Object.entries(aliases)) {
      expect(compatibility.get(`--color-${name}`)).toBe(`var(--${target})`);
    }
    for (const [name, value] of compatibility) {
      expect(value, name).toMatch(/var\(--[\w-]+\)/);
    }
    const declared = new Set([...declarations(css), ...declarations(index)].map(([name]) => name));
    for (const [, reference] of index.matchAll(/var\((--[\w-]+)\)/g)) {
      expect(declared.has(reference), reference).toBe(true);
    }
    expect(index).toMatch(/\[data-theme='dark'\]\s*\{\s*color-scheme:\s*dark;/);
    expect(index).toMatch(/:focus-visible\s*\{[^}]*box-shadow:\s*var\(--shadow-focus\);/);
  });
});
