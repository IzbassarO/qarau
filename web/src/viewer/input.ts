export type WheelAction = 'pinch-zoom' | 'trackpad-orbit' | 'trackpad-pan' | 'mouse-zoom';

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
): WheelAction {
  if (event.ctrlKey) return 'pinch-zoom';
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
