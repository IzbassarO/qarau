import { expect, it } from 'vitest';
import { fitContain, polygonToSvgPoints } from './outline';

it('centres a 1920×1080 frame inside a 400×400 box with the literal required mapping', () => {
  const fit = fitContain(1920, 1080, 400, 400);
  expect(fit.scale).toBeCloseTo(0.20833, 4);
  expect(fit.offsetX).toBe(0);
  expect(fit.offsetY).toBe(87.5);
  expect(
    polygonToSvgPoints(
      [
        [0, 0],
        [1920, 1080],
      ],
      fit,
    ),
  ).toBe('0,87.5 400,312.5');
});

it('centres a portrait frame with horizontal letterboxing', () => {
  const fit = fitContain(100, 200, 400, 200);
  expect(fit).toEqual({ scale: 1, offsetX: 150, offsetY: 0 });
  expect(
    polygonToSvgPoints(
      [
        [0, 0],
        [100, 200],
      ],
      fit,
    ),
  ).toBe('150,0 250,200');
  expect(polygonToSvgPoints([], fit)).toBe('');
});

it.each([0, -1, NaN, Infinity])(
  'handles an unmeasured or invalid frame dimension (%s)',
  (width) => {
    expect(fitContain(width, 100, 400, 400)).toEqual({ scale: 0, offsetX: 0, offsetY: 0 });
  },
);
