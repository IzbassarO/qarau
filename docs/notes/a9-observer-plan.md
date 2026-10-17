# A9 Observer mode

Orbit remains the default. Observer replaces the existing F flight and uses the
engine's Z-up coordinates: yaw zero faces +Y. No dependencies or models are added;
the existing Three.js, React, Vite and Vitest dependencies use MIT licences.

1. Add pure Observer movement with horizontal yaw-relative input, vertical
   Space/Shift, damped velocity/look, nearest-point distance speed and only a loose
   outer bound. Reuse the navigation spatial index for efficient nearest queries.
2. Integrate pointer lock on canvas click, drag fallback, wheel look, configurable
   Ctrl/Alt boost, F mode switching and V third-person view. Esc releases the mouse
   while Observer stays active. Orbit constraints apply again on returning to Orbit.
3. Add a cached top-view minimap with a live circle/view cone, and a third-person
   ring/direction marker. Show a 1.75 m capsule only when the existing explicit
   `world.scale_m_per_unit` is finite and positive, converting metres to model units.
4. Wire `?mode=observer`, mode buttons/settings and translated controls in EN/RU/KK.
5. Cover pure math, input routing and actual viewer lifecycle with Vitest. Run lint
   (including Prettier), TypeScript, all unit tests and the production build. Tester
   owns screenshots. Stop any server immediately after verification.

Observer keeps its mode while settings have focus, suspends keyboard input during
editing, and resumes movement after third-person controls return focus to the canvas.
An explicit Observer deep link takes precedence over opening the point-info tool.

Verification: lint/Prettier, typecheck, all 486 tests (28 files), and build pass.
The real Aegviidu manifest has no metric calibration. Browser navigation could not
run because the sandbox denies opening the Vite port (`listen EPERM ::1:5199`).
No server remains on that port; no screenshots were taken.
