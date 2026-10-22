import { expect, it } from 'vitest';
import { createDemo } from '../fixtures/demo';
import type { Bundle } from '../lib/bundle';
import { defaultViewState } from './defaults';

function bundleWithRgb(): Bundle {
  const bundle = createDemo('en').bundle;
  return { ...bundle, color: new Uint8Array(bundle.manifest.points.count * 3).fill(80) };
}

it('keeps Orbit as the default navigation mode', () => {
  expect(defaultViewState(createDemo('en').bundle).mode).toBe('orbit');
});

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

it('opens a dense layer in height colour and respects saved colour preferences', () => {
  const bundle = bundleWithRgb();
  bundle.dense = {
    count: 1,
    position: new Float32Array([0, 0, 0]),
    color: new Uint8Array([20, 40, 60]),
    frameOffsets: new Uint32Array([0, 0]),
    frameIds: new Uint32Array(),
    densifier: 'colmap',
    frameLinksSource: 'colmap_fusion',
  };
  expect(defaultViewState(bundle).colorMode).toBe('height');
  expect(defaultViewState(bundle, 'rgb').colorMode).toBe('rgb');
  expect(defaultViewState(bundle, 'height').colorMode).toBe('height');
  // Dense RGB remains usable when the sparse layer has no colour file.
  expect(defaultViewState({ ...bundle, color: undefined }, 'rgb').colorMode).toBe('rgb');
});

it('preserves the default for legacy dense metadata stored on the sparse cloud', () => {
  const bundle = bundleWithRgb();
  bundle.manifest.dense = { method: 'depth_prior', frames_used: 2, points: 3 };
  expect(defaultViewState(bundle).colorMode).toBe('rgb');
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
