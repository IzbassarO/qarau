import type { ObserverInput } from './observer';
import type { ObserverBoostKey } from './settings';

export type WheelAction = 'pinch-zoom' | 'trackpad-orbit' | 'trackpad-pan' | 'mouse-zoom' | 'look';

/** Physical WASD continues to work with Russian and Kazakh keyboard layouts. */
export function observerKey(event: Pick<KeyboardEvent, 'key' | 'code'>): string {
  if (/^Key[WASDFV]$/.test(event.code)) return event.code.slice(3).toLowerCase();
  return event.code === 'Space' || event.key === ' ' ? 'space' : event.key.toLowerCase();
}

export const OBSERVER_MOVEMENT_KEYS = new Set([
  'w',
  'a',
  's',
  'd',
  'arrowup',
  'arrowdown',
  'arrowleft',
  'arrowright',
  'space',
  'shift',
  'control',
  'alt',
]);

export function observerMovement(
  keys: ReadonlySet<string>,
  boostKey: ObserverBoostKey,
): ObserverInput {
  return {
    forward: keys.has('w') || keys.has('arrowup'),
    backward: keys.has('s') || keys.has('arrowdown'),
    left: keys.has('a') || keys.has('arrowleft'),
    right: keys.has('d') || keys.has('arrowright'),
    up: keys.has('space'),
    down: keys.has('shift'),
    boost: keys.has(boostKey),
  };
}

export interface WheelInput {
  deltaX?: number;
  deltaY?: number;
  deltaMode?: number;
  ctrlKey?: boolean;
  shiftKey?: boolean;
  altKey?: boolean;
  metaKey?: boolean;
}

/** An isolated integer axis tick, including browsers' Shift-to-X wheel remapping. */
export function wheelStep(event: WheelInput): number | undefined {
  const x = event.deltaX ?? 0;
  const y = event.deltaY ?? 0;
  const step = !x ? Math.abs(y) : event.shiftKey && !y ? Math.abs(x) : 0;
  return step > 0 && Number.isInteger(step) ? step : undefined;
}

/**
 * Hardware is not exposed by WheelEvent. History belongs to the caller, never a
 * global: three equal confirmed mouse ticks establish a stable mouse step.
 * Callers must never train this history on unclassified pixel scrolls; repeated
 * small trackpad ticks must remain orbit/pan. Ambiguous pixels favour trackpads.
 */
export function classifyWheel(
  event: WheelInput,
  knownMouseSteps: readonly number[] = [],
  mode: 'orbit' | 'observer' = 'orbit',
): WheelAction {
  if (event.ctrlKey) return 'pinch-zoom';
  if (mode === 'observer') return 'look';
  if (event.deltaMode === 1 || event.deltaMode === 2) return 'mouse-zoom';
  const step = wheelStep(event);
  if (step !== undefined) {
    if (step >= 50) return 'mouse-zoom';
    const last = knownMouseSteps.slice(-3);
    const stableStep = last[0];
    if (
      last.length === 3 &&
      stableStep > 0 &&
      last.every((tick) => tick === stableStep) &&
      step % stableStep === 0
    )
      return 'mouse-zoom';
  }
  return event.shiftKey ? 'trackpad-pan' : 'trackpad-orbit';
}
