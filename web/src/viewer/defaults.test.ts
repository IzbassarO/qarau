import { expect, it } from 'vitest';
import { createDemo } from '../fixtures/demo';
import type { Bundle } from '../lib/bundle';
import { defaultViewState } from './defaults';

function bundleWithRgb(): Bundle {
  const bundle = createDemo('en').bundle;
  return { ...bundle, color: new Uint8Array(bundle.manifest.points.count * 3).fill(80) };
}

it('defaults to real colours when every point has RGB', () => {
  expect(defaultViewState(bundleWithRgb()).colorMode).toBe('rgb');
});

it.each([undefined, new Uint8Array(3)])(
  'falls back to height without complete RGB (%s)',
  (color) => {
    expect(defaultViewState({ ...bundleWithRgb(), color }).colorMode).toBe('height');
  },
);

it('respects an explicit saved height choice for an RGB bundle', () => {
  expect(defaultViewState(bundleWithRgb(), 'height').colorMode).toBe('height');
});

it('respects an explicit saved RGB choice for an RGB bundle', () => {
  expect(defaultViewState(bundleWithRgb(), 'rgb').colorMode).toBe('rgb');
});

it('falls back to height for a saved RGB choice when RGB is unavailable', () => {
  expect(defaultViewState(createDemo('en').bundle, 'rgb').colorMode).toBe('height');
});

it.each([true, false])(
  'defaults to timeline colouring off and frustums hidden (RGB: %s)',
  (rgb) => {
    const bundle = rgb ? bundleWithRgb() : createDemo('en').bundle;
    expect(defaultViewState(bundle)).toMatchObject({ buildUp: false, frustums: false });
  },
);

it.each([true, false])('defaults to far points hidden in Quick Scan (far: %s)', (far) => {
  const bundle = createDemo('en').bundle;
  expect(defaultViewState({ ...bundle, far: far ? bundle.far : undefined }).showFarLayer).toBe(
    false,
  );
});
