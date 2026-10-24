import { readdirSync, readFileSync } from 'node:fs';
import { relative, resolve } from 'node:path';
import ts from 'typescript';
import { expect, it } from 'vitest';

const namedColors = new Set(
  `aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue
  blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk
  crimson cyan darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta
  darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen darkslateblue darkslategray
  darkslategrey darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue firebrick
  floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green greenyellow grey
  honeydew hotpink indianred indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon
  lightblue lightcoral lightcyan lightgoldenrodyellow lightgray lightgreen lightgrey lightpink
  lightsalmon lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue lightyellow
  lime limegreen linen magenta maroon mediumaquamarine mediumblue mediumorchid mediumpurple
  mediumseagreen mediumslateblue mediumspringgreen mediumturquoise mediumvioletred midnightblue
  mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab orange orangered orchid
  palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru pink plum powderblue
  purple rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell
  sienna silver skyblue slateblue slategray slategrey snow springgreen steelblue tan teal thistle
  tomato transparent turquoise violet wheat white whitesmoke yellow yellowgreen`.split(/\s+/),
);
const isSize = (property: string) =>
  /^(?:font-size|(?:padding|margin|gap)(?:-|$)|(?:row|column)-gap|(?:border(?:-(?:top|bottom)-(?:left|right))?-radius))/.test(
    property,
  );
const cssName = (property: string) =>
  property.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`);

function violations(source: string, kind: 'css' | 'tsx') {
  const clean = source.replace(/\/\*[\s\S]*?\*\//g, '');
  const errors: string[] = [];
  if (/#(?:[\da-f]{8}|[\da-f]{6}|[\da-f]{4}|[\da-f]{3})\b/i.test(clean)) errors.push('hex colour');
  if (/\b(?:rgba?|hsla?)\s*\(/i.test(clean)) errors.push('functional colour');
  if (/gradient\s*\(/i.test(clean)) errors.push('gradient');
  if (/backdrop-?filter/i.test(clean)) errors.push('backdrop filter');

  function declaration(property: string, value: string, numeric = false) {
    const name = cssName(property);
    const bare = value.replace(/var\(--[\w-]+\)/g, '');
    if (name === 'text-shadow' && bare.trim() !== 'none') errors.push('text shadow');
    if (/color|background|border|fill|stroke|shadow|outline/.test(name)) {
      if (
        bare
          .toLowerCase()
          .split(/[^a-z]+/)
          .some((word) => namedColors.has(word))
      ) {
        errors.push(`named colour in ${name}`);
      }
    }
    if (isSize(name)) {
      if (numeric && Number(value) !== 0) errors.push(`numeric size in ${name}`);
      if (
        [...value.matchAll(/(-?(?:\d*\.)?\d+)(px|rem)\b/g)].some((match) => Number(match[1]) !== 0)
      ) {
        errors.push(`literal size in ${name}`);
      }
    }
  }

  if (kind === 'css') {
    for (const [, property, value] of clean.matchAll(/([\w-]+)\s*:\s*([^;{}]+)[;}]/g)) {
      declaration(property, value);
    }
  } else {
    const file = ts.createSourceFile(
      'component.tsx',
      clean,
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX,
    );
    function visit(node: ts.Node) {
      if (
        ts.isJsxAttribute(node) &&
        node.name.getText(file) === 'style' &&
        node.initializer &&
        ts.isJsxExpression(node.initializer) &&
        node.initializer.expression &&
        ts.isObjectLiteralExpression(node.initializer.expression)
      ) {
        for (const property of node.initializer.expression.properties) {
          if (!ts.isPropertyAssignment(property)) continue;
          const name = ts.isStringLiteral(property.name)
            ? property.name.text
            : property.name.getText(file);
          const value = property.initializer;
          declaration(
            name,
            ts.isStringLiteralLike(value) ? value.text : value.getText(file),
            ts.isNumericLiteral(value) || ts.isPrefixUnaryExpression(value),
          );
        }
      }
      ts.forEachChild(node, visit);
    }
    visit(file);
  }
  return errors;
}

function files(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name);
    return entry.isDirectory()
      ? files(path)
      : /\.(?:css|tsx)$/.test(entry.name) && !entry.name.endsWith('.test.tsx')
        ? [path]
        : [];
  });
}

// Fixture images and point-cloud data are source evidence, not interface styling.
// Keep their pixels out of the UI palette audit; test sources pin fixed expected colours.
function uiFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) return entry.name === 'fixtures' ? [] : uiFiles(path);
    return /\.(?:css|tsx?|svg|json)$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)
      ? [path]
      : [];
  });
}

function withoutComments(source: string, path: string) {
  if (!/\.tsx?$/.test(path)) return source.replace(/\/\*[\s\S]*?\*\//g, '');
  const scanner = ts.createScanner(ts.ScriptTarget.Latest, false, ts.LanguageVariant.JSX, source);
  const comments: [number, number][] = [];
  while (scanner.scan() !== ts.SyntaxKind.EndOfFileToken) {
    if (
      scanner.getToken() === ts.SyntaxKind.SingleLineCommentTrivia ||
      scanner.getToken() === ts.SyntaxKind.MultiLineCommentTrivia
    ) {
      comments.push([scanner.getTokenPos(), scanner.getTextPos()]);
    }
  }
  for (const [start, end] of comments.reverse()) {
    source = source.slice(0, start) + ' '.repeat(end - start) + source.slice(end);
  }
  return source;
}

function colourLiterals(source: string, path: string) {
  const clean = withoutComments(source, path);
  const errors: string[] = [];
  if (/#(?:[\da-f]{8}|[\da-f]{6}|[\da-f]{4}|[\da-f]{3})\b/i.test(clean)) {
    errors.push('hex colour');
  }
  // RGB strings calculated from source data are not palette literals.
  if (/\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\s*\(\s*[-+.\d]/i.test(clean)) {
    errors.push('functional colour');
  }
  if (path.endsWith('.css')) {
    for (const [, property, value] of clean.matchAll(/([\w-]+)\s*:\s*([^;{}]+)[;}]/g)) {
      if (!/color|background|border|fill|stroke|shadow|outline/.test(property)) continue;
      const bare = value.replace(/var\(--[\w-]+\)/g, '');
      if (
        bare
          .toLowerCase()
          .split(/[^a-z]+/)
          .some((word) => namedColors.has(word))
      ) {
        errors.push(`named colour in ${property}`);
      }
    }
  } else {
    const file = ts.createSourceFile(
      'source.tsx',
      clean,
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX,
    );
    function visit(node: ts.Node) {
      if (ts.isStringLiteralLike(node) && namedColors.has(node.text.toLowerCase())) {
        errors.push('named colour');
      }
      if (ts.isStringLiteralLike(node) && node.text.includes('var(')) {
        const bare = node.text.replace(/var\(--[\w-]+\)/g, '');
        if (
          bare
            .toLowerCase()
            .split(/[^a-z]+/)
            .some((word) => namedColors.has(word))
        ) {
          errors.push('named colour in token fallback');
        }
      }
      if (ts.isNumericLiteral(node) && /^0x[\da-f]{6,8}$/i.test(node.getText(file))) {
        const parent = node.parent;
        const name =
          ts.isVariableDeclaration(parent) || ts.isPropertyAssignment(parent)
            ? parent.name.getText(file)
            : ts.isCallExpression(parent) || ts.isNewExpression(parent)
              ? parent.expression.getText(file)
              : '';
        if (/color|fill|stroke/i.test(name)) errors.push('numeric colour');
      }
      ts.forEachChild(node, visit);
    }
    visit(file);
    // SVG source is XML, so inspect its paint attributes explicitly.
    if (path.endsWith('.svg')) {
      for (const [, value] of clean.matchAll(/(?:fill|stroke|color)=["']([^"']+)["']/g)) {
        if (namedColors.has(value.toLowerCase())) errors.push('named SVG colour');
      }
    }
  }
  return errors;
}

function gradientViolations(source: string, path: string) {
  let clean = withoutComments(source, path);
  if (path.endsWith('.css')) {
    // The owner-approved sign-in hero is the only decorative gradient in the UI.
    clean = clean.replace(/([^{}]+)\{([^{}]*)\}/g, (block, selector: string, body: string) => {
      if (selector.trim() !== '.qv-hero::before') return block;
      return `${selector}{${body.replace(/background\s*:\s*var\(--signin-hero-gradient\)\s*;/g, '')}}`;
    });
  }
  const errors: string[] = [];
  if (/gradient\s*\(/i.test(clean)) errors.push('gradient');
  if (/--signin-hero-gradient\b/.test(clean)) errors.push('sign-in hero gradient outside hero');
  return errors;
}

it('keeps all interface colours in token files and the gradient on the sign-in hero', () => {
  const sourceDirectory = resolve(process.cwd(), 'src');
  const paths = uiFiles(sourceDirectory).filter(
    (path) => !/^design\/tokens\.(?:css|json)$/.test(relative(sourceDirectory, path)),
  );
  expect(paths.length).toBeGreaterThan(0);
  for (const path of paths) {
    const source = readFileSync(path, 'utf8');
    expect(colourLiterals(source, path), path).toEqual([]);
    expect(gradientViolations(source, path), path).toEqual([]);
  }
  const bundle = readFileSync(resolve(sourceDirectory, 'design/bundle.css'), 'utf8');
  expect(bundle.match(/var\(--signin-hero-gradient\)/g)).toHaveLength(1);
});

it.each([
  ['component.css', '.a { color: #123456; }'],
  ['component.css', '.a { background: rgba(0, 0, 0, .5); }'],
  ['component.css', '.a { color: hsl(0 50% 50%); }'],
  ['component.css', '.a { background: white; }'],
  ['component.ts', 'const color = "#123456";'],
  ['component.ts', 'const color = 0xffaabb;'],
  ['component.tsx', '<path fill="red" />'],
  ['component.tsx', '<path fill="var(--ink, red)" />'],
  ['component.svg', '<path fill="red" />'],
  ['component.json', '{ "palette": "#123456" }'],
] as const)('rejects colour literals in %s: %s', (path, source) => {
  expect(colourLiterals(source, path).length).toBeGreaterThan(0);
});

it('allows computed evidence colours and token references', () => {
  expect(colourLiterals('const rgb = `rgb(${channels.join(",")})`;', 'component.ts')).toEqual([]);
  expect(colourLiterals('.a { color: var(--ink); }', 'component.css')).toEqual([]);
});

it('allows only the named gradient on the sign-in hero', () => {
  expect(
    gradientViolations(
      '.qv-hero::before { background: var(--signin-hero-gradient); }',
      'component.css',
    ),
  ).toEqual([]);
  for (const source of [
    '.a { background: linear-gradient(var(--a), var(--b)); }',
    '.a { background: var(--signin-hero-gradient); }',
    '.qv-hero { background: var(--signin-hero-gradient); }',
    '.qv-hero::before { color: var(--signin-hero-gradient); }',
    '.qv-hero::before, .a { background: var(--signin-hero-gradient); }',
  ]) {
    expect(gradientViolations(source, 'component.css').length).toBeGreaterThan(0);
  }
});

it('uses tokens throughout auth and shell CSS/TSX', () => {
  const paths = ['shell', 'auth'].flatMap((folder) => files(resolve(process.cwd(), 'src', folder)));
  expect(paths.length).toBeGreaterThan(0);
  for (const path of paths) {
    expect(
      violations(readFileSync(path, 'utf8'), path.endsWith('.css') ? 'css' : 'tsx'),
      path,
    ).toEqual([]);
  }
});

it.each([
  ['css', '.a { color: #123456; }'],
  ['css', '.a { background: rgba(0, 0, 0, .5); }'],
  ['css', '.a { color: hsl(0 50% 50%); }'],
  ['css', '.a { background: white; }'],
  ['css', '.a { padding: 0 8px; }'],
  ['css', '.a { margin-left: 1rem; }'],
  ['css', '.a { font-size: 14px; }'],
  ['css', '.a { row-gap: 1px; }'],
  ['css', '.a { border-top-left-radius: 4px; }'],
  ['css', '.a { background: linear-gradient(var(--a), var(--b)); }'],
  ['css', '.a { backdrop-filter: blur(4px); }'],
  ['css', '.a { text-shadow: 0 0 4px currentColor; }'],
  ['tsx', '<div style={{ color: "red" }} />'],
  ['tsx', '<div style={{ backgroundColor: "navy" }} />'],
  ['tsx', '<div style={{ color: "var(--color-text, red)" }} />'],
  ['tsx', '<div style={{ gap: "1rem", padding: 4 }} />'],
  ['tsx', '<div style={{ fontSize: 14, borderRadius: "4px" }} />'],
  ['tsx', '<div style={{ marginTop: -4 }} />'],
  ['tsx', '<div style={{ backdropFilter: "blur(4px)" }} />'],
  ['tsx', '<div style={{ textShadow: "0 0 4px currentColor" }} />'],
] as const)('rejects %s literals: %s', (kind, source) => {
  expect(violations(source, kind).length).toBeGreaterThan(0);
});

it('allows token references, zero spacing, 1px borders and SVG geometry', () => {
  expect(
    violations(
      '.a { color: var(--color-text); padding: 0 var(--spacing-2); margin: 0px; border: 1px solid var(--color-border); font-size: var(--type-md); }',
      'css',
    ),
  ).toEqual([]);
  expect(violations('<svg viewBox="0 0 24 24"><path d="M3 3h18" /></svg>', 'tsx')).toEqual([]);
  expect(
    violations(
      '<div style={{ color: "var(--color-text)", gap: 0, margin: "0rem", borderRadius: "var(--round-sm)" }} />',
      'tsx',
    ),
  ).toEqual([]);
});
