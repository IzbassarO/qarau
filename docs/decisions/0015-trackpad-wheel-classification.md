# ADR 0015: Distinguish trackpad gestures from mouse wheel ticks

Date: 2026-10-17. Status: accepted.

## Context

The viewer must support a MacBook trackpad: two-finger scrolling orbits, Shift
scrolling pans, and pinching zooms. Existing mouse buttons and wheel zoom remain
available. `WheelEvent` does not report which hardware produced it, so identical
pixel deltas cannot be distinguished perfectly without a heuristic.

## Decision

The pure `classifyWheel` function in `web/src/viewer/input.ts` applies these rules
in order:

1. Ctrl wheel always means pinch zoom, including Ctrl+Shift and line/page deltas.
   macOS browsers synthesize Ctrl wheel for trackpad pinch.
2. Line or page deltas mean mouse zoom.
3. A pixel delta is a mouse notch if it is an isolated integer vertical tick of
   magnitude at least 50. For Shift events, an isolated horizontal tick is also
   accepted because browsers can move the vertical wheel delta into `deltaX`.
4. Three identical preceding confirmed mouse ticks establish a stable step.
   Integer multiples of that step also mean mouse zoom, including smaller notches.
   The DOM adapter records only events already classified as mouse zoom, normalizes
   line deltas by 16 pixels and page deltas by viewport height, and keeps at most six
   recent consecutive steps per control instance. Other gestures clear that history;
   a gap longer than 250 ms expires it. The pure classifier accepts explicitly known
   mouse steps and never changes them or shares them globally. Repeated small pixel
   trackpad events cannot train mouse history, so repetition never turns them into zoom.
5. Other pixel deltas mean trackpad orbit, or trackpad pan while Shift is held.
   Alt and Meta do not change classification. Alt independently scales speed by 0.25.

The canvas registers the wheel listener with `passive: false` and prevents the
default action before checking whether navigation is enabled. This prevents both
document scrolling and browser pinch zoom while a source frame owns the camera.
Mouse zoom accepts a Shift-remapped X delta; the remaining mouse mappings are
unchanged.

## Consequences

Fractional/diagonal and repeated small pixel trackpad scroll never becomes zoom.
Coarse integer trackpad events can resemble mouse ticks; smooth high-resolution
mice can resemble trackpads until confirmed line/page ticks establish a stable
step. This is an unavoidable ambiguity of
the browser event API. Ctrl wheel remains an explicit zoom override. Unit tests
cover the distinctions, Shift remapping, stable history and independent controls.

No dependencies are added. Existing Three.js and Vitest packages declare MIT
licences, which allow commercial use. Classification and scaling are own code.
