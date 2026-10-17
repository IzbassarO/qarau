import { describe, expect, it } from 'vitest';
import { classifyWheel, wheelStep } from './input';

describe('classifyWheel', () => {
  it.each([
    { deltaX: 0, deltaY: 2.4 },
    { deltaX: 0, deltaY: 120, deltaMode: 1 },
    { deltaX: 120, deltaY: 0, shiftKey: true },
  ])('always classifies Ctrl wheel as pinch zoom: %j', (event) => {
    expect(classifyWheel({ ...event, ctrlKey: true })).toBe('pinch-zoom');
  });

  it.each([
    { deltaX: 2.2, deltaY: 3.5 },
    { deltaX: 0, deltaY: 12 },
    { deltaX: 0, deltaY: -40 },
    { deltaX: 1, deltaY: 120 },
    { deltaX: 0, deltaY: 80.5 },
  ])('orbits rather than zooms for pixel trackpad scrolling: %j', (event) => {
    expect(classifyWheel({ ...event, deltaMode: 0 })).toBe('trackpad-orbit');
  });

  it('pans for Shift plus pixel trackpad scrolling; Alt and Meta do not change the action', () => {
    expect(classifyWheel({ deltaX: 2.2, deltaY: 3.5, shiftKey: true })).toBe('trackpad-pan');
    expect(classifyWheel({ deltaX: 0, deltaY: 12, altKey: true, metaKey: true })).toBe(
      'trackpad-orbit',
    );
  });

  it.each([1, 2])('retains mouse zoom for line/page deltas (%i)', (deltaMode) => {
    expect(classifyWheel({ deltaY: 3, deltaMode })).toBe('mouse-zoom');
  });

  it('recognizes isolated large integer mouse notches, including Shift-swapped X', () => {
    expect(classifyWheel({ deltaX: 0, deltaY: -120 })).toBe('mouse-zoom');
    expect(classifyWheel({ deltaX: 0, deltaY: 50 })).toBe('mouse-zoom');
    expect(classifyWheel({ deltaX: 120, deltaY: 0, shiftKey: true })).toBe('mouse-zoom');
    expect(classifyWheel({ deltaX: 3, deltaY: 0, deltaMode: 1, shiftKey: true })).toBe(
      'mouse-zoom',
    );
  });

  it('recognizes multiples of a confirmed mouse step without mutating or sharing history', () => {
    const history = [12, 12, 12];
    expect(classifyWheel({ deltaY: -24 }, history)).toBe('mouse-zoom');
    expect(classifyWheel({ deltaX: 24, shiftKey: true }, history)).toBe('mouse-zoom');
    expect(classifyWheel({ deltaY: -24 })).toBe('trackpad-orbit');
    expect(classifyWheel({ deltaY: 13 }, history)).toBe('trackpad-orbit');
    expect(classifyWheel({ deltaY: 24 }, [12, 13, 12])).toBe('trackpad-orbit');
    expect(history).toEqual([12, 12, 12]);
  });

  it('only records unambiguous integer-axis steps', () => {
    expect(wheelStep({ deltaY: -120 })).toBe(120);
    expect(wheelStep({ deltaX: -120, shiftKey: true })).toBe(120);
    expect(wheelStep({ deltaY: 3.5 })).toBeUndefined();
    expect(wheelStep({ deltaX: 2, deltaY: 120 })).toBeUndefined();
    expect(wheelStep({ deltaX: 120 })).toBeUndefined();
    expect(wheelStep({})).toBeUndefined();
  });
});
