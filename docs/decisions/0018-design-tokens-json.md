# 0018 — Shared design tokens in JSON

Status: accepted

The web, desktop and mobile clients need the same palette, type and spacing. Keep
the source values in `web/src/design/tokens.json`, with light/dark colour sets and
shared colour, font, space, radius and layout groups. Fonts use system stacks per
the UX1a brief. Dimensions are numbers in logical pixels; weights and line heights
are also numbers. There are no CSS declarations or expressions in the JSON.

`index.css` declares the exact values as custom properties. It converts numeric
dimensions to CSS lengths centrally, and components reference those lengths.
Dark colours override light colours through `prefers-color-scheme`. The viewer
canvas is black in both themes. Reserved finding colours do not encode processing
states or navigation. Preserve the viewer's existing red, orange, gray and blue
values when introducing these tokens. A separate berry colour identifies form errors.

Shared aliases store token names, rather than repeated palette values, to preserve
the existing viewer's Tailwind utilities without editing viewer source. `@theme
inline` registers these references; existing bg/surface names reference the values
in the unlayered root declarations. This keeps opacity modifiers available too.

Vitest reads CSS directly and checks exact values, scope, numeric conversions,
aliases and undeclared colour properties. No generation step or dependency is
introduced. A separate source check rejects colour/size literals and prohibited
effects in auth and shell components, including React style objects.
