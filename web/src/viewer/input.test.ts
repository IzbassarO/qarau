import { describe, expect, it } from 'vitest';
import { classifyWheel, observerKey, observerMovement, wheelStep } from './input';
import { createObserverState, step } from './observer';
import { Box3, Vector3 } from 'three';

describe('Observer controls', () => {
  it.each([
    ['w', 'ArrowUp', 'forward'],
    ['s', 'ArrowDown', 'backward'],
    ['a', 'ArrowLeft', 'left'],
    ['d', 'ArrowRight', 'right'],
  ] as const)('maps %s and %s to %s and moves relative to yaw', (letter, arrow, action) => {
    const physical = observerKey({ key: 'ц', code: `Key${letter.toUpperCase()}` });
    const arrowKey = observerKey({ key: arrow, code: arrow });
    const wasd = observerMovement(new Set([physical]), 'control');
    const arrows = observerMovement(new Set([arrowKey]), 'control');
    expect(wasd[action]).toBe(true);
    expect(arrows).toEqual(wasd);
    const state = createObserverState(new Vector3(), new Vector3(-1, 0, 0));
    const ctx = {
      bounds: new Box3(new Vector3(-10, -10, -10), new Vector3(10, 10, 10)),
      points: [],
      minSpeed: 1,
      maxSpeed: 1,
      damping: 0,
    };
    const moved = step(state, arrows, 1, ctx).position;
    const expected = {
      forward: [-1, 0, 0],
      backward: [1, 0, 0],
      left: [0, -1, 0],
      right: [0, 1, 0],
    }[action];
    expect(moved.distanceTo(new Vector3(...expected))).toBeLessThan(1e-12);
  });

  it('maps Space/Shift to up/down and boosts only with the configured modifier', () => {
    expect(observerKey({ key: ' ', code: 'Space' })).toBe('space');
    expect(observerMovement(new Set(['space', 'shift']), 'control')).toMatchObject({
      up: true,
      down: true,
      boost: false,
    });
    expect(observerMovement(new Set(['control']), 'control').boost).toBe(true);
    expect(observerMovement(new Set(['alt']), 'alt').boost).toBe(true);
    expect(observerMovement(new Set(['alt']), 'control').boost).toBe(false);
  });

  it.each([
    { deltaX: 2.4, deltaY: -8 },
    { deltaY: 120 },
    { deltaY: 3, deltaMode: 1, shiftKey: true },
  ])('routes wheel without Ctrl to Observer look, preserving pinch: %j', (event) => {
    expect(classifyWheel(event, [], 'observer')).toBe('look');
    expect(classifyWheel({ ...event, ctrlKey: true }, [], 'observer')).toBe('pinch-zoom');
    expect(classifyWheel(event, [], 'orbit')).not.toBe('look');
  });
});

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
