import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { PerspectiveCamera, Vector3 } from 'three';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createDemo } from '../fixtures/demo';
import { LANGS, setLang, type Lang } from '../i18n';
import * as bundleApi from '../lib/bundle';
import { ViewerEngine } from '../viewer/engine';
import type { Axis } from '../viewer/gizmo';
import { ViewerPage } from './ViewerPage';

const { renderScene } = vi.hoisted(() => ({ renderScene: vi.fn() }));

// Keep real navigation, input handlers, camera math and UI; jsdom only lacks WebGL.
vi.mock('three', async (importOriginal) => {
  const actual = await importOriginal<typeof import('three')>();
  return {
    ...actual,
    WebGLRenderer: class {
      setPixelRatio() {}
      getPixelRatio() {
        return 1;
      }
      setClearColor() {}
      setSize() {}
      setViewport() {}
      setScissor() {}
      setScissorTest() {}
      clear() {}
      clearDepth() {}
      render = renderScene;
      dispose() {}
    },
  };
});

let now = 0;
let nextFrame = 0;
const pendingFrames = new Map<number, FrameRequestCallback>();

beforeEach(() => {
  localStorage.clear();
  setLang('en');
  vi.clearAllMocks();
  now = 0;
  nextFrame = 0;
  pendingFrames.clear();
  vi.spyOn(performance, 'now').mockImplementation(() => now);
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    pendingFrames.set(++nextFrame, callback);
    return nextFrame;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => pendingFrames.delete(id));
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderViewer(runId: string) {
  return render(
    <MemoryRouter initialEntries={[`/viewer/${runId}`]}>
      <Routes>
        <Route path="/viewer/:runId" element={<ViewerPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderDemo() {
  return renderViewer('demo');
}

function colourBundle(color?: Uint8Array): bundleApi.Bundle {
  return { ...createDemo('en').bundle, runId: 'colour-test', color };
}

async function renderColourBundle(bundle: bundleApi.Bundle) {
  vi.spyOn(bundleApi, 'loadManifest').mockResolvedValue(bundle.manifest);
  vi.spyOn(bundleApi, 'loadBundle').mockResolvedValue(bundle);
  const view = renderViewer(bundle.runId);
  const toggle = (await screen.findByRole('button', {
    name: LANGS[document.documentElement.lang as Lang]['show.colorMode'],
  })) as HTMLButtonElement;
  return { ...view, toggle };
}

function advanceFrames(count: number) {
  act(() => {
    for (let i = 0; i < count; i++) {
      now += 1000 / 60;
      const callbacks = Array.from(pendingFrames.values());
      pendingFrames.clear();
      callbacks.forEach((callback) => callback(now));
    }
  });
}

it('loads /viewer/demo from the existing local point cloud without requesting a manifest', () => {
  const fetch = vi.fn().mockRejectedValue(new Error('Unexpected network request'));
  vi.stubGlobal('fetch', fetch);
  const manifest = vi.spyOn(bundleApi, 'loadManifest');
  const load = vi.spyOn(bundleApi, 'loadBundle');
  renderDemo();

  expect(screen.getByLabelText(/Interactive 3D map/)).toBeTruthy();
  const pointCount = createDemo('en').bundle.position.length / 3;
  expect(pointCount).toBeGreaterThan(0);
  expect(screen.getAllByText(pointCount.toLocaleString()).length).toBeGreaterThan(0);
  expect(renderScene).toHaveBeenCalled();
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.queryByText('WebGL is not available in this browser.')).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
  expect(manifest).not.toHaveBeenCalled();
  expect(load).not.toHaveBeenCalled();
});

it('renders six axis buttons and rotates each view around the same pivot at the same distance', () => {
  const axisView = vi.spyOn(ViewerEngine.prototype, 'axisView');
  renderDemo();
  const gizmo = screen.getByRole('group', { name: 'Axis views' });
  expect(within(gizmo).getAllByRole('button')).toHaveLength(6);
  const camera = renderScene.mock.calls[0][1] as PerspectiveCamera;
  const canvas = screen.getByLabelText(/Interactive 3D map/);
  const views: { axis: Axis; name: string; direction: Vector3 }[] = [
    { axis: 'z', name: 'Top view (+Z)', direction: new Vector3(0, 0, -1) },
    { axis: 'x', name: 'Right view (+X)', direction: new Vector3(-1, 0, 0) },
    { axis: '-x', name: 'Left view (−X)', direction: new Vector3(1, 0, 0) },
    { axis: 'y', name: 'Back view (+Y)', direction: new Vector3(0, -1, 0) },
    { axis: '-y', name: 'Front view (−Y)', direction: new Vector3(0, 1, 0) },
    { axis: '-z', name: 'Bottom view (−Z)', direction: new Vector3(0, 0, 1) },
  ];
  let pivot: Vector3 | undefined;
  let distance = 0;
  for (const { axis, name, direction } of views) {
    const initial = camera.quaternion.clone();
    const transforms = within(gizmo)
      .getAllByRole('button')
      .map((button) => button.style.transform);
    const button = within(gizmo).getByRole('button', { name });
    button.focus();
    fireEvent.click(button);
    expect(axisView).toHaveBeenLastCalledWith(axis);
    expect(document.activeElement).toBe(canvas);
    const engine = axisView.mock.contexts.at(-1) as ViewerEngine;
    if (!pivot) {
      pivot = engine.controls.target.clone();
      distance = camera.position.distanceTo(pivot);
    }
    advanceFrames(180);
    expect(camera.quaternion.angleTo(initial)).toBeGreaterThan(0.1);
    expect(camera.getWorldDirection(new Vector3()).distanceTo(direction)).toBeLessThan(0.001);
    expect(engine.controls.target.distanceTo(pivot)).toBeLessThan(1e-9);
    expect(camera.position.distanceTo(pivot)).toBeCloseTo(distance, 6);
    expect(
      within(gizmo)
        .getAllByRole('button')
        .map((axisButton) => axisButton.style.transform),
    ).not.toEqual(transforms);
  }
});

it('shows the fly controls after F and exits with F or Escape', () => {
  renderDemo();
  const canvas = screen.getByLabelText(/Interactive 3D map/);
  const hint = 'Fly mode: WASD move, mouse look, Shift faster, F/Esc exit';
  canvas.focus();
  expect(screen.queryByText(hint)).toBeNull();
  fireEvent.keyDown(canvas, { key: 'f', code: 'KeyF' });
  expect(screen.getByRole('status').textContent).toBe(hint);
  fireEvent.keyDown(canvas, { key: 'f', code: 'KeyF' });
  expect(screen.queryByText(hint)).toBeNull();
  fireEvent.keyDown(canvas, { key: 'f', code: 'KeyF' });
  expect(screen.getByText(hint)).toBeTruthy();
  fireEvent.keyDown(canvas, { key: 'Escape', code: 'Escape' });
  expect(screen.queryByText(hint)).toBeNull();
});

it.each<Lang>(['en', 'ru', 'kk'])('localizes axis controls and the fly hint in %s', (lang) => {
  setLang(lang);
  renderDemo();
  const locale = LANGS[lang];
  const gizmo = screen.getByRole('group', { name: locale['gizmo.label'] });
  expect(within(gizmo).getAllByRole('button')).toHaveLength(6);
  for (const key of [
    'gizmo.positiveX',
    'gizmo.negativeX',
    'gizmo.positiveY',
    'gizmo.negativeY',
    'gizmo.positiveZ',
    'gizmo.negativeZ',
  ] as const)
    expect(within(gizmo).getByRole('button', { name: locale[key] })).toBeTruthy();
  const canvas = screen.getByLabelText(locale['canvas.label']);
  canvas.focus();
  fireEvent.keyDown(canvas, { key: 'f', code: 'KeyF' });
  expect(screen.getByRole('status').textContent).toBe(locale['canvas.flyHint']);
  fireEvent.click(within(gizmo).getByRole('button', { name: locale['gizmo.positiveZ'] }));
  expect(screen.getByRole('status').textContent).toBe(locale['canvas.hint']);
  expect(document.activeElement).toBe(canvas);
  advanceFrames(180);
  const camera = renderScene.mock.calls.at(-1)![1] as PerspectiveCamera;
  expect(camera.getWorldDirection(new Vector3()).distanceTo(new Vector3(0, 0, -1))).toBeLessThan(
    0.001,
  );
});

it('switches between height-blue and real colours without replacing the engine', async () => {
  const setColorMode = vi.spyOn(ViewerEngine.prototype, 'setColorMode');
  const bundle = colourBundle(new Uint8Array(createDemo('en').bundle.position.length).fill(80));
  const { toggle } = await renderColourBundle(bundle);
  const engine = setColorMode.mock.contexts.at(-1) as ViewerEngine;
  expect(toggle.disabled).toBe(false);
  expect(toggle.getAttribute('aria-pressed')).toBe('true');
  expect(within(toggle).getByText(LANGS.en['show.rgb'])).toBeTruthy();
  expect(engine.colorMode).toBe('rgb');

  fireEvent.click(toggle);
  expect(setColorMode).toHaveBeenLastCalledWith('height');
  expect(setColorMode.mock.contexts.at(-1)).toBe(engine);
  expect(engine.colorMode).toBe('height');
  expect(toggle.getAttribute('aria-pressed')).toBe('false');
  expect(within(toggle).getByText(LANGS.en['show.heightBlue'])).toBeTruthy();

  fireEvent.click(toggle);
  expect(setColorMode).toHaveBeenLastCalledWith('rgb');
  expect(engine.colorMode).toBe('rgb');
  expect(toggle.getAttribute('aria-pressed')).toBe('true');
});

it.each<Lang>(['en', 'ru', 'kk'])(
  'starts with localized timeline and frustum controls off and allows opting in (%s)',
  (lang) => {
    setLang(lang);
    const setOptions = vi.spyOn(ViewerEngine.prototype, 'setOptions');
    renderDemo();
    const locale = LANGS[lang];
    const timeline = screen.getByRole('button', { name: locale['show.buildUp'] });
    const frustums = screen.getByRole('button', { name: locale['show.frustums'] });
    expect(timeline.getAttribute('aria-pressed')).toBe('false');
    expect(frustums.getAttribute('aria-pressed')).toBe('false');
    expect(setOptions).toHaveBeenLastCalledWith(
      expect.objectContaining({ buildUp: false, frustums: false }),
    );

    fireEvent.click(timeline);
    fireEvent.click(frustums);
    expect(timeline.getAttribute('aria-pressed')).toBe('true');
    expect(frustums.getAttribute('aria-pressed')).toBe('true');
    expect(setOptions).toHaveBeenLastCalledWith(
      expect.objectContaining({ buildUp: true, frustums: true }),
    );
  },
);

it.each<Lang>(['en', 'ru', 'kk'])(
  'localizes the colour toggle and unavailable tooltip in %s',
  (lang) => {
    setLang(lang);
    renderDemo();
    const locale = LANGS[lang];
    const toggle = screen.getByRole('button', {
      name: locale['show.colorMode'],
    }) as HTMLButtonElement;
    expect(toggle.disabled).toBe(true);
    expect(within(toggle).getByText(locale['show.heightBlue'])).toBeTruthy();
    expect(toggle.title).toBe(locale['show.rgbUnavailable']);
    const tooltip = screen.getByRole('tooltip');
    expect(tooltip.textContent).toBe(locale['show.rgbUnavailable']);
    expect(toggle.getAttribute('aria-describedby')).toBe(tooltip.id);
  },
);

it.each([undefined, new Uint8Array(3)])(
  'keeps the toggle disabled when the bundle has missing or incomplete RGB (%s)',
  async (color) => {
    const setColorMode = vi.spyOn(ViewerEngine.prototype, 'setColorMode');
    const { toggle } = await renderColourBundle(colourBundle(color));
    expect(toggle.disabled).toBe(true);
    expect(toggle.getAttribute('aria-pressed')).toBe('false');
    setColorMode.mockClear();
    fireEvent.click(toggle);
    expect(setColorMode).not.toHaveBeenCalled();
    expect(toggle.title).toBe(LANGS.en['show.rgbUnavailable']);
  },
);

it('remembers an explicit colour choice when the viewer is mounted again', async () => {
  const setColorMode = vi.spyOn(ViewerEngine.prototype, 'setColorMode');
  const bundle = colourBundle(new Uint8Array(createDemo('en').bundle.position.length).fill(80));
  const first = await renderColourBundle(bundle);
  expect(localStorage.getItem('qarau.viewer.colorMode.v2')).toBeNull();
  fireEvent.click(first.toggle);
  expect(localStorage.getItem('qarau.viewer.colorMode.v2')).toBe('height');
  first.unmount();

  const second = await renderColourBundle(bundle);
  expect(second.toggle.getAttribute('aria-pressed')).toBe('false');
  expect(setColorMode).toHaveBeenLastCalledWith('height');
});

it('uses height for a map without RGB while preserving the saved real-colour preference', async () => {
  localStorage.setItem('qarau.viewer.colorMode.v2', 'rgb');
  const setColorMode = vi.spyOn(ViewerEngine.prototype, 'setColorMode');
  const unavailable = await renderColourBundle(colourBundle());
  expect(unavailable.toggle.disabled).toBe(true);
  expect(unavailable.toggle.getAttribute('aria-pressed')).toBe('false');
  expect(setColorMode).toHaveBeenLastCalledWith('height');
  expect(localStorage.getItem('qarau.viewer.colorMode.v2')).toBe('rgb');
  unavailable.unmount();

  const available = await renderColourBundle(
    colourBundle(new Uint8Array(createDemo('en').bundle.position.length).fill(80)),
  );
  expect(available.toggle.getAttribute('aria-pressed')).toBe('true');
  expect(setColorMode).toHaveBeenLastCalledWith('rgb');
});

it('ignores the legacy height preference and does not persist a default', async () => {
  localStorage.setItem('qarau.viewer.colorMode', 'height');
  const { toggle } = await renderColourBundle(
    colourBundle(new Uint8Array(createDemo('en').bundle.position.length).fill(80)),
  );
  expect(toggle.getAttribute('aria-pressed')).toBe('true');
  expect(localStorage.getItem('qarau.viewer.colorMode.v2')).toBeNull();
});

it('defaults to real colours when a saved colour preference is invalid', async () => {
  localStorage.setItem('qarau.viewer.colorMode.v2', 'invalid');
  const { toggle } = await renderColourBundle(
    colourBundle(new Uint8Array(createDemo('en').bundle.position.length).fill(80)),
  );
  expect(toggle.disabled).toBe(false);
  expect(toggle.getAttribute('aria-pressed')).toBe('true');
  expect(within(toggle).getByText(LANGS.en['show.rgb'])).toBeTruthy();
});

it('allows colour changes when browser storage is unavailable', async () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('Storage unavailable');
  });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('Storage unavailable');
  });
  const setColorMode = vi.spyOn(ViewerEngine.prototype, 'setColorMode');
  const { toggle } = await renderColourBundle(
    colourBundle(new Uint8Array(createDemo('en').bundle.position.length).fill(80)),
  );
  expect(toggle.getAttribute('aria-pressed')).toBe('true');
  fireEvent.click(toggle);
  expect(toggle.getAttribute('aria-pressed')).toBe('false');
  expect(setColorMode).toHaveBeenLastCalledWith('height');
  expect(screen.queryByRole('alert')).toBeNull();
});
