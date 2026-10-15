# A2a — viewer navigation plan

1. Read the project guide, engine, ViewerScreen, UI, and viewer design.
2. Add pure navigation math: cursor-ray pivot picking, orbit with polar clamp,
   cursor zoom with an advancing pivot, screen-space pan, bounds, point collision,
   frame-rate-independent damping, and documented wheel input mapping.
3. Add `NavigationControls`: canvas listeners, pointer capture, update/dispose,
   and engine camera synchronization. Pick the pivot only at pointer-down;
   register no double-click handler. Use a spatial index for large-cloud queries.
4. Replace the engine's previous controls and update its render loop.
   Preserve toolbar, keyboard, evidence selection, and video-camera poses.
5. Update the controls specification and existing EN/RU/KK hints.
6. Test picking/fallback, repeated close zoom, bounds, nearest collision, input
   mapping, damping, orbit radius, and DOM cleanup without WebGL.
7. Run lint/Prettier, TypeScript, Vitest, and the production build.
   Exercise the viewer in a browser and capture the cloud without an error overlay.

Own navigation code; no new dependency. Installed Three.js, React, and Vitest
licence files were checked: all use MIT, which permits commercial use.

Browser verification requires a runner that permits a local server and Chromium;
the current sandbox previously rejected both with permission errors.
