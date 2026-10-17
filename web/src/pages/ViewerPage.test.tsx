import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import {
  Box3Helper,
  PerspectiveCamera,
  Vector3,
  type BufferGeometry,
  type Points,
  type ShaderMaterial,
} from 'three';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createDemo } from '../fixtures/demo';
import { login, logout } from '../auth/auth';
import { LANGS, setLang, type Lang } from '../i18n';
import * as bundleApi from '../lib/bundle';
import * as roiApi from '../lib/roi';
import { ViewerEngine } from '../viewer/engine';
import type { Axis } from '../viewer/gizmo';
import { saveSensitivity } from '../viewer/settings';
import type { Roi } from '../viewer/roi';
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
  sessionStorage.clear();
  setLang('en');
  vi.clearAllMocks();
  vi.spyOn(roiApi, 'resolveRoiSiteId').mockImplementation(async (runId) => runId);
  vi.spyOn(roiApi, 'loadRoi').mockResolvedValue(null);
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
  sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderViewer(runId: string, query = '') {
  return render(
    <MemoryRouter initialEntries={[`/viewer/${runId}${query}`]}>
      <Routes>
        <Route path="/viewer/:runId" element={<ViewerPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderDemo() {
  return renderViewer('demo');
}

it('opens ?point=0 with the info tool, evidence panel and selected-point marker, and seeks its exact frame', async () => {
  const bundle = createDemo('en').bundle;
  const setPicked = vi.spyOn(ViewerEngine.prototype, 'setPicked');
  const viewThroughFrame = vi.spyOn(ViewerEngine.prototype, 'viewThroughFrame');
  renderViewer('demo', '?point=0');
  const tool = screen.getByRole('button', { name: LANGS.en['pointInfo.tool'] });
  expect(tool.getAttribute('aria-pressed')).toBe('true');
  const panel = screen.getByRole('region', { name: LANGS.en['pointInfo.title'] });
  expect(within(panel).getByText(`${bundle.position[0].toFixed(3)} model units`)).toBeTruthy();
  expect(within(panel).getAllByText(LANGS.en['pointInfo.needsScale'])).toHaveLength(2);
  expect(within(panel).getByText(LANGS.en['pointInfo.needsConfidence'])).toBeTruthy();
  expect(setPicked).toHaveBeenLastCalledWith(0);
  const engine = setPicked.mock.contexts.at(-1) as ViewerEngine;
  expect(engine.scene.getObjectByName('selected-point')!.visible).toBe(true);
  const indices = bundle.pointFrames.subarray(bundle.pointOffsets[0], bundle.pointOffsets[1]);
  expect(indices.length).toBeGreaterThan(0);
  const target = indices.at(-1)!;
  fireEvent.click(
    within(panel).getByRole('button', {
      name: `${LANGS.en['pointInfo.seekFrame']} ${bundle.manifest.frames[target].i}`,
    }),
  );
  expect(
    (screen.getByRole('slider', { name: LANGS.en['timeline.position'] }) as HTMLInputElement).value,
  ).toBe(String(target));
  expect(viewThroughFrame).toHaveBeenLastCalledWith(target);
  expect(screen.queryByRole('alert')).toBeNull();
  fireEvent.click(within(panel).getByRole('button', { name: LANGS.en['pointInfo.close'] }));
  expect(screen.queryByRole('region', { name: LANGS.en['pointInfo.title'] })).toBeNull();
  expect(tool.getAttribute('aria-pressed')).toBe('false');
  expect(engine.scene.getObjectByName('selected-point')!.visible).toBe(false);
});

it.each(['', '-1', '1.5', 'no-point', '999999999', '999999999999999999999'])(
  'ignores invalid ?point=%s with a small notice and no error',
  (value) => {
    renderViewer('demo', `?point=${value}`);
    expect(screen.getByRole('note').textContent).toBe(LANGS.en['pointInfo.invalidPoint']);
    expect(screen.queryByRole('region', { name: LANGS.en['pointInfo.title'] })).toBeNull();
    expect(
      screen.getByRole('button', { name: LANGS.en['pointInfo.tool'] }).getAttribute('aria-pressed'),
    ).toBe('false');
    expect(screen.queryByRole('alert')).toBeNull();
  },
);

it('ignores a point index equal to the bundle point count', () => {
  renderViewer('demo', `?point=${createDemo('en').bundle.manifest.points.count}`);
  expect(screen.getByRole('note').textContent).toBe(LANGS.en['pointInfo.invalidPoint']);
  expect(screen.queryByRole('region', { name: LANGS.en['pointInfo.title'] })).toBeNull();
  expect(screen.queryByRole('alert')).toBeNull();
});

it.each<Lang>(['en', 'ru', 'kk'])(
  'localizes the point tool and invalid-link notice in %s',
  (lang) => {
    setLang(lang);
    renderViewer('demo', '?point=');
    const button = screen.getByRole('button', { name: LANGS[lang]['pointInfo.tool'] });
    expect(button.title).toBe(LANGS[lang]['pointInfo.tool']);
    expect(screen.getByRole('note').textContent).toBe(LANGS[lang]['pointInfo.invalidPoint']);
    fireEvent.click(button);
    expect(button.getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('status').textContent).toBe(LANGS[lang]['pointInfo.hint']);
  },
);

function colourBundle(color?: Uint8Array): bundleApi.Bundle {
  return { ...createDemo('en').bundle, runId: 'colour-test', color };
}

async function renderColourBundle(bundle: bundleApi.Bundle) {
  vi.spyOn(bundleApi, 'loadManifest').mockResolvedValue(bundle.manifest);
  vi.spyOn(bundleApi, 'loadBundle').mockResolvedValue(bundle);
  const resize = vi.spyOn(ViewerEngine.prototype, 'resize');
  const mounted = resize.mock.calls.length;
  const view = renderViewer(bundle.runId);
  const toggle = (await screen.findByRole('button', {
    name: LANGS[document.documentElement.lang as Lang]['show.colorMode'],
  })) as HTMLButtonElement;
  // React commits the DOM first and runs the engine effect in a later task; wait for it,
  // or the test races the effect (slow CI: it ran after the globals were unstubbed).
  await waitFor(() => expect(resize.mock.calls.length).toBeGreaterThan(mounted));
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

it.each<Lang>(['en', 'ru', 'kk'])(
  'starts with the localized far toggle unchecked and switches the layer on (%s)',
  (lang) => {
    setLang(lang);
    const setFarVisible = vi.spyOn(ViewerEngine.prototype, 'setFarVisible');
    renderDemo();
    const toggle = screen.getByRole('button', { name: LANGS[lang]['viewer.show.farPoints'] });
    expect(toggle.getAttribute('aria-pressed')).toBe('false');
    expect(setFarVisible).toHaveBeenLastCalledWith(false);
    const engine = setFarVisible.mock.contexts.at(-1) as ViewerEngine;
    expect(engine.hasFarLayer()).toBe(true);
    expect(engine.scene.getObjectByName('far')!.visible).toBe(false);
    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    expect(setFarVisible).toHaveBeenLastCalledWith(true);
    expect(setFarVisible.mock.contexts.at(-1)).toBe(engine);
    expect(engine.scene.getObjectByName('far')!.visible).toBe(true);
  },
);

it('loads the served P3b manifest and arrays through to the Russian far-points toggle', async () => {
  setLang('ru');
  const demo = createDemo('ru').bundle;
  const runId = 'p3b-contract';
  const manifest: bundleApi.Manifest = {
    ...demo.manifest,
    points: {
      ...demo.manifest.points,
      position: 'web/points.position.f32',
      first_seen: 'web/points.first_seen.u16',
    },
    tracks: {
      frame_offsets: 'web/tracks.frame_offsets.u32',
      frame_points: 'web/tracks.frame_points.u32',
      point_offsets: 'web/tracks.point_offsets.u32',
      point_frames: 'web/tracks.point_frames.u16',
    },
  };
  const arrays = new Map<string, ArrayBuffer>([
    [manifest.points.position, demo.position.slice().buffer],
    [manifest.points.first_seen, demo.firstSeen.slice().buffer],
    [manifest.tracks!.frame_offsets, demo.frameOffsets.slice().buffer],
    [manifest.tracks!.frame_points, demo.frameInPoints.slice().buffer],
    [manifest.tracks!.point_offsets, demo.pointOffsets.slice().buffer],
    [manifest.tracks!.point_frames, demo.pointFrames.slice().buffer],
    [manifest.far!.position, demo.far!.position.slice().buffer],
    [manifest.far!.color, demo.far!.color.slice().buffer],
  ]);
  const fetch = vi.fn(async (url: string) => {
    if (url === `/api/runs/${runId}/manifest.json`) return Response.json(manifest);
    const prefix = bundleApi.runUrl(runId, '');
    const array = arrays.get(url.slice(prefix.length));
    return array ? new Response(array) : new Response(null, { status: 404 });
  });
  vi.stubGlobal('fetch', fetch);
  const setFarVisible = vi.spyOn(ViewerEngine.prototype, 'setFarVisible');
  renderViewer(runId);
  const toggle = await screen.findByRole('button', { name: LANGS.ru['viewer.show.farPoints'] });
  await waitFor(() => expect(setFarVisible).toHaveBeenLastCalledWith(false));
  const engine = setFarVisible.mock.contexts.at(-1) as ViewerEngine;
  expect(engine.hasFarLayer()).toBe(true);
  expect(engine.scene.getObjectByName('far')!.visible).toBe(false);
  expect(toggle.getAttribute('aria-pressed')).toBe('false');
  expect(screen.queryByRole('alert')).toBeNull();
  expect(fetch).toHaveBeenCalledWith(bundleApi.runUrl(runId, 'web/far.position.f32'), {
    headers: {},
  });
  expect(fetch).toHaveBeenCalledWith(bundleApi.runUrl(runId, 'web/far.color.u8'), { headers: {} });
  fireEvent.click(toggle);
  expect(setFarVisible).toHaveBeenLastCalledWith(true);
  expect(engine.scene.getObjectByName('far')!.visible).toBe(true);
});

it.each([undefined, { position: new Float32Array(), color: new Uint8Array() }])(
  'hides the far toggle without usable far points (%s)',
  async (far) => {
    await renderColourBundle({ ...colourBundle(), far });
    expect(screen.queryByRole('button', { name: LANGS.en['viewer.show.farPoints'] })).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
  },
);

it.each<Lang>(['en', 'ru', 'kk'])(
  'recovers the object cloud with a localized far-layer warning (%s)',
  async (lang) => {
    setLang(lang);
    for (const reason of ['missing', 'broken'] as const) {
      const bundle: bundleApi.Bundle = { ...colourBundle(), far: undefined, farError: reason };
      vi.spyOn(bundleApi, 'loadManifest').mockResolvedValue(bundle.manifest);
      vi.spyOn(bundleApi, 'loadBundle').mockRejectedValue(
        new bundleApi.FarLayerError(reason, 'invalid far layer', bundle),
      );
      const view = renderViewer(bundle.runId);
      expect((await screen.findByRole('alert')).textContent).toBe(
        LANGS[lang][reason === 'missing' ? 'viewer.far.errorMissing' : 'viewer.far.errorBroken'],
      );
      await waitFor(() => expect(renderScene).toHaveBeenCalled());
      expect(screen.getByLabelText(LANGS[lang]['canvas.label'])).toBeTruthy();
      expect(
        screen.queryByRole('button', { name: LANGS[lang]['viewer.show.farPoints'] }),
      ).toBeNull();
      view.unmount();
      renderScene.mockClear();
    }
  },
);

it('offers five above-ground axis views around the same pivot and disables the bottom view', () => {
  const axisView = vi.spyOn(ViewerEngine.prototype, 'axisView');
  renderDemo();
  const gizmo = screen.getByRole('group', { name: 'Axis views' });
  expect(within(gizmo).getAllByRole('button')).toHaveLength(6);
  const bottom = within(gizmo).getByRole('button', {
    name: 'Bottom view (−Z)',
  }) as HTMLButtonElement;
  expect(bottom.disabled).toBe(true);
  expect(bottom.title).toBe(LANGS.en['gizmo.groundLimited']);
  fireEvent.click(bottom);
  expect(axisView).not.toHaveBeenCalled();
  const camera = renderScene.mock.calls[0][1] as PerspectiveCamera;
  const canvas = screen.getByLabelText(/Interactive 3D map/);
  const views: { axis: Axis; name: string; direction: Vector3 }[] = [
    { axis: 'z', name: 'Top view (+Z)', direction: new Vector3(0, 0, -1) },
    { axis: 'x', name: 'Right view (+X)', direction: new Vector3(-1, 0, 0) },
    { axis: '-x', name: 'Left view (−X)', direction: new Vector3(1, 0, 0) },
    { axis: 'y', name: 'Back view (+Y)', direction: new Vector3(0, -1, 0) },
    { axis: '-y', name: 'Front view (−Y)', direction: new Vector3(0, 1, 0) },
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

it('keeps disabled modes unchanged for keys 2 and 3 and returns to Orbit with 1', () => {
  const setOrbitMode = vi.spyOn(ViewerEngine.prototype, 'setOrbitMode');
  const setSensitivity = vi.spyOn(ViewerEngine.prototype, 'setSensitivity');
  renderDemo();
  const engine = setSensitivity.mock.contexts.at(-1) as ViewerEngine;
  const canvas = screen.getByLabelText(LANGS.en['canvas.label']);
  const orbit = screen.getByRole('button', { name: 'Orbit' });
  canvas.focus();
  const rotation = engine.camera.quaternion.clone();
  for (const key of ['2', '3']) fireEvent.keyDown(canvas, { key, code: `Digit${key}` });
  expect(setOrbitMode).not.toHaveBeenCalled();
  expect(engine.flyMode).toBe(false);
  expect(engine.camera.quaternion.equals(rotation)).toBe(true);
  expect(orbit.getAttribute('aria-pressed')).toBe('true');

  fireEvent.keyDown(canvas, { key: 'f', code: 'KeyF' });
  expect(engine.flyMode).toBe(true);
  expect(orbit.getAttribute('aria-pressed')).toBe('false');
  for (const key of ['2', '3']) fireEvent.keyDown(canvas, { key, code: `Digit${key}` });
  expect(engine.flyMode).toBe(true);
  expect(setOrbitMode).not.toHaveBeenCalled();
  fireEvent.keyDown(canvas, { key: '1', code: 'Digit1' });
  expect(setOrbitMode).toHaveBeenCalledTimes(1);
  expect(engine.flyMode).toBe(false);
  expect(orbit.getAttribute('aria-pressed')).toBe('true');
  expect(screen.getByRole('status').textContent).toBe(LANGS.en['canvas.hint']);
});

it.each<Lang>(['en', 'ru', 'kk'])(
  'localizes navigation modes, presets, surface alignment and settings (%s)',
  (lang) => {
    setLang(lang);
    renderDemo();
    const locale = LANGS[lang];
    const modes = screen.getByRole('group', { name: locale['viewer.mode.label'] });
    expect(
      within(modes)
        .getByRole('button', { name: locale['viewer.mode.orbit'] })
        .getAttribute('aria-pressed'),
    ).toBe('true');
    for (const key of ['viewer.mode.walk', 'viewer.mode.observer'] as const) {
      const mode = within(modes).getByRole('button', { name: locale[key] }) as HTMLButtonElement;
      expect(mode.disabled).toBe(true);
      expect(mode.title).toBe(locale['viewer.mode.comingSoon']);
    }
    for (const key of [
      'toolbar.top',
      'toolbar.front',
      'toolbar.side',
      'toolbar.threeQuarter',
      'toolbar.reset',
    ] as const)
      expect(screen.getByRole('button', { name: locale[key] })).toBeTruthy();

    const align = screen.getByRole('button', { name: locale['toolbar.alignSurface'] });
    fireEvent.click(align);
    expect(align.getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('status').textContent).toBe(locale['canvas.alignHint']);
    fireEvent.keyDown(screen.getByLabelText(locale['canvas.label']), {
      key: 'Escape',
      code: 'Escape',
    });
    expect(align.getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByRole('status').textContent).toBe(locale['canvas.hint']);

    fireEvent.click(screen.getByText(locale['viewer.settings.title']));
    expect(
      screen.getByRole('slider', { name: locale['viewer.settings.sensitivity'] }),
    ).toBeTruthy();
    expect(screen.getByText(locale['viewer.settings.precision'])).toBeTruthy();
  },
);

it('loads and saves sensitivity for the signed-in identity without recreating the engine', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json([])));
  saveSensitivity('operator-a', 0.5);
  saveSensitivity('operator-b', 2.5);
  await login({ username: 'operator-a', password: 'test-viewer-credential' });
  const setSensitivity = vi.spyOn(ViewerEngine.prototype, 'setSensitivity');
  const resize = vi.spyOn(ViewerEngine.prototype, 'resize');
  renderDemo();
  const engine = setSensitivity.mock.contexts.at(-1) as ViewerEngine;
  const engineMounts = resize.mock.calls.length;
  expect(setSensitivity).toHaveBeenLastCalledWith(0.5);
  expect(engine.controls.sensitivity).toBe(0.5);
  fireEvent.click(screen.getByText('Settings'));
  const slider = screen.getByRole('slider', { name: 'Navigation sensitivity' });
  expect((slider as HTMLInputElement).value).toBe('0.5');
  fireEvent.change(slider, { target: { value: '0.75' } });
  expect(setSensitivity).toHaveBeenLastCalledWith(0.75);
  expect(engine.controls.sensitivity).toBe(0.75);
  expect(localStorage.getItem('qarau.viewer.sensitivity.operator-a')).toBe('0.75');

  await act(async () => {
    logout();
    await login({ username: 'operator-b', password: 'test-viewer-credential' });
  });
  expect(setSensitivity).toHaveBeenLastCalledWith(2.5);
  expect(engine.controls.sensitivity).toBe(2.5);
  expect((slider as HTMLInputElement).value).toBe('2.5');
  expect(localStorage.getItem('qarau.viewer.sensitivity.operator-a')).toBe('0.75');
  expect(localStorage.getItem('qarau.viewer.sensitivity.operator-b')).toBe('2.5');
  expect(setSensitivity.mock.contexts.every((context) => context === engine)).toBe(true);
  expect(resize.mock.calls.length).toBe(engineMounts);
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

async function renderRoiViewer(query = '') {
  const bundle: bundleApi.Bundle = {
    ...createDemo('en').bundle,
    runId: 'notre-dame',
  };
  vi.spyOn(bundleApi, 'loadManifest').mockResolvedValue(bundle.manifest);
  vi.spyOn(bundleApi, 'loadBundle').mockResolvedValue(bundle);
  const resize = vi.spyOn(ViewerEngine.prototype, 'resize');
  const view = renderViewer(bundle.runId, query);
  const locale = LANGS[document.documentElement.lang as Lang];
  const toolbar = await screen.findByRole('toolbar', { name: locale['toolbar.tools'] });
  const tool = within(toolbar).getByRole('button', { name: locale['roi.title'] });
  // A fresh viewer must advertise the tool in text and show its controls without
  // a test-only click. This is also the page the screenshot gate opens directly.
  expect(tool.textContent).toContain(locale['roi.title']);
  expect(tool.getAttribute('aria-pressed')).toBe('true');
  const panel = screen.getByRole('region', { name: locale['roi.title'] });
  await waitFor(() => expect(roiApi.loadRoi).toHaveBeenCalled());
  await waitFor(() =>
    expect(
      (within(panel).getByRole('button', { name: locale['roi.draw'] }) as HTMLButtonElement)
        .disabled,
    ).toBe(false),
  );
  const canvas = screen.getByLabelText(locale['canvas.label']) as HTMLCanvasElement;
  Object.defineProperties(canvas, {
    clientWidth: { configurable: true, value: 800 },
    clientHeight: { configurable: true, value: 600 },
  });
  vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({
    left: 0,
    top: 0,
    right: 800,
    bottom: 600,
    width: 800,
    height: 600,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  });
  const engine = resize.mock.contexts.at(-1) as ViewerEngine;
  engine.resize();
  return { ...view, bundle, canvas, engine, panel, tool, locale };
}

function roiPointer(canvas: HTMLCanvasElement, type: string, x: number, y: number) {
  const event = new MouseEvent(type, {
    button: 0,
    clientX: x,
    clientY: y,
    bubbles: true,
    cancelable: true,
  });
  Object.defineProperty(event, 'pointerId', { value: 7 });
  act(() => canvas.dispatchEvent(event));
}

function drawRoi(view: Awaited<ReturnType<typeof renderRoiViewer>>) {
  fireEvent.click(within(view.panel).getByRole('button', { name: view.locale['roi.draw'] }));
  expect(view.engine.controls.enabled).toBe(false);
  expect(
    view.engine.camera.getWorldDirection(new Vector3()).distanceTo(new Vector3(0, 0, -1)),
  ).toBeLessThan(0.001);
  // Draw in reverse to exercise the actual top-view pointer path and box normalization.
  roiPointer(view.canvas, 'pointerdown', 760, 560);
  roiPointer(view.canvas, 'pointermove', 40, 40);
  roiPointer(view.canvas, 'pointerup', 40, 40);
  expect(view.engine.controls.enabled).toBe(true);
  expect(view.engine.scene.getObjectByName('roi-box')).toBeTruthy();
}

const savedRoi: Roi = {
  version: 1,
  site_id: 'notre-dame',
  source_run: 'notre-dame',
  frame: 'reconstruction',
  up_axis: 'z',
  min: [-1, -1, -1],
  max: [1, 1, 1],
  created_at: '2026-10-17T12:00:00Z',
  created_by: 'roi-operator',
};

function roiCloud(engine: ViewerEngine) {
  return engine.scene.children.find(
    (object) =>
      'geometry' in object && (object as Points<BufferGeometry>).geometry.hasAttribute('firstSeen'),
  ) as Points<BufferGeometry, ShaderMaterial>;
}

it('opens a saved region permalink in top view with clipping enabled before interaction', async () => {
  vi.mocked(roiApi.loadRoi).mockResolvedValue(savedRoi);
  const topView = vi.spyOn(ViewerEngine.prototype, 'topView');
  const view = await renderRoiViewer('?view=top&hideOutside=1');
  expect(topView).toHaveBeenCalledTimes(1);
  expect(topView.mock.contexts[0]).toBe(view.engine);
  const helper = view.engine.scene.getObjectByName('roi-box') as Box3Helper;
  expect(helper.box.min.toArray()).toEqual(savedRoi.min);
  expect(helper.box.max.toArray()).toEqual(savedRoi.max);
  expect(helper.visible).toBe(true);
  const hide = within(view.panel).getByRole('button', { name: LANGS.en['roi.hideOutside'] });
  expect(hide.getAttribute('aria-pressed')).toBe('true');
  expect((hide as HTMLButtonElement).disabled).toBe(false);
  expect(roiCloud(view.engine).material.uniforms.uHideOutside.value).toBe(1);
  expect(within(view.panel).getByText(LANGS.en['roi.saved'])).toBeTruthy();
  expect(view.engine.controls.enabled).toBe(true);
  advanceFrames(180);
  expect(
    view.engine.camera.getWorldDirection(new Vector3()).distanceTo(new Vector3(0, 0, -1)),
  ).toBeLessThan(0.001);
  expect(screen.queryByRole('alert')).toBeNull();
});

it.each(['', '?view=side&hideOutside=true', '?view=TOP&hideOutside=-1'])(
  'keeps normal view and clipping defaults for a saved region with query %s',
  async (query) => {
    vi.mocked(roiApi.loadRoi).mockResolvedValue(savedRoi);
    const topView = vi.spyOn(ViewerEngine.prototype, 'topView');
    const view = await renderRoiViewer(query);
    expect(topView).not.toHaveBeenCalled();
    expect(view.engine.scene.getObjectByName('roi-box')).toBeTruthy();
    const hide = within(view.panel).getByRole('button', { name: LANGS.en['roi.hideOutside'] });
    expect(hide.getAttribute('aria-pressed')).toBe('false');
    expect((hide as HTMLButtonElement).disabled).toBe(false);
    expect(roiCloud(view.engine).material.uniforms.uHideOutside.value).toBe(0);
  },
);

it.each(['absent', 'failed'])(
  'keeps requested clipping disabled when the saved region is %s',
  async (result) => {
    if (result === 'failed') vi.mocked(roiApi.loadRoi).mockRejectedValue(new Error('HTTP 503'));
    const view = await renderRoiViewer('?view=top&hideOutside=1');
    const hide = within(view.panel).getByRole('button', { name: LANGS.en['roi.hideOutside'] });
    expect(hide.getAttribute('aria-pressed')).toBe('false');
    expect((hide as HTMLButtonElement).disabled).toBe(true);
    expect(view.engine.scene.getObjectByName('roi-box')).toBeUndefined();
    expect(roiCloud(view.engine).material.uniforms.uHideOutside.value).toBe(0);
    expect(screen.queryByRole('alert')?.textContent ?? null).toBe(
      result === 'failed' ? LANGS.en['roi.loadError'] : null,
    );
  },
);

it.each<Lang>(['en', 'ru', 'kk'])(
  'shows a labelled ROI toolbar tool and open controls on a fresh Notre Dame route (%s)',
  async (lang) => {
    setLang(lang);
    const view = await renderRoiViewer();
    expect(roiApi.loadRoi).toHaveBeenCalledWith('notre-dame', expect.any(AbortSignal));
    expect(within(view.panel).getByRole('button', { name: LANGS[lang]['roi.draw'] })).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(view.engine.scene.getObjectByName('roi-box')).toBeUndefined();
  },
);

it('draws a region on /viewer/notre-dame, saves it, restores it on reload and clears it on the server', async () => {
  sessionStorage.setItem('qarau.auth.session', 'Bearer test-roi-credential');
  sessionStorage.setItem('qarau.auth.userId', 'roi-operator');
  let saved: Roi | null = null;
  vi.mocked(roiApi.loadRoi).mockImplementation(async () => saved);
  const save = vi.spyOn(roiApi, 'saveRoi').mockImplementation(async (_siteId, value) => {
    saved = structuredClone(value);
    return value;
  });
  const remove = vi.spyOn(roiApi, 'deleteRoi').mockImplementation(async () => {
    saved = null;
  });
  const setHidden = vi.spyOn(ViewerEngine.prototype, 'setHideOutsideRoi');
  const view = await renderRoiViewer();
  const originalPoints = view.bundle.position.slice();
  drawRoi(view);
  const helper = view.engine.scene.getObjectByName('roi-box') as Box3Helper;
  expect(helper.box.min.x).toBeLessThan(helper.box.max.x);
  expect(helper.box.min.y).toBeLessThan(helper.box.max.y);
  expect(helper.box.min.z).toBeLessThan(helper.box.max.z);
  fireEvent.click(within(view.panel).getByRole('button', { name: LANGS.en['roi.hideOutside'] }));
  expect(setHidden).toHaveBeenLastCalledWith(true);
  expect(view.bundle.position).toEqual(originalPoints);

  fireEvent.click(within(view.panel).getByRole('button', { name: LANGS.en['roi.save'] }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  expect(save).toHaveBeenCalledWith(
    'notre-dame',
    expect.objectContaining({
      version: 1,
      site_id: 'notre-dame',
      source_run: 'notre-dame',
      frame: 'reconstruction',
      up_axis: 'z',
      created_by: 'roi-operator',
    }),
  );
  await within(view.panel).findByText(LANGS.en['roi.saved']);
  const savedBounds = { min: helper.box.min.toArray(), max: helper.box.max.toArray() };
  view.unmount();

  const reloaded = await renderRoiViewer();
  const restored = reloaded.engine.scene.getObjectByName('roi-box') as Box3Helper;
  expect(restored.box.min.toArray()).toEqual(savedBounds.min);
  expect(restored.box.max.toArray()).toEqual(savedBounds.max);
  expect(screen.queryByRole('alert')).toBeNull();
  fireEvent.click(within(reloaded.panel).getByRole('button', { name: LANGS.en['roi.clear'] }));
  await waitFor(() => expect(remove).toHaveBeenCalledWith('notre-dame'));
  await waitFor(() => expect(reloaded.engine.scene.getObjectByName('roi-box')).toBeUndefined());
  expect(setHidden).toHaveBeenLastCalledWith(false);
});

it('cancels region drawing with Escape and restores orbit without saving a box', async () => {
  const save = vi.spyOn(roiApi, 'saveRoi');
  const view = await renderRoiViewer();
  fireEvent.click(within(view.panel).getByRole('button', { name: LANGS.en['roi.draw'] }));
  expect(view.engine.controls.enabled).toBe(false);
  roiPointer(view.canvas, 'pointerdown', 100, 100);
  roiPointer(view.canvas, 'pointermove', 400, 300);
  fireEvent.keyDown(view.canvas, { key: 'Escape', code: 'Escape' });
  expect(view.engine.controls.enabled).toBe(true);
  expect(within(view.panel).queryByRole('button', { name: LANGS.en['roi.cancel'] })).toBeNull();
  roiPointer(view.canvas, 'pointerup', 400, 300);
  expect(view.engine.scene.getObjectByName('roi-box')).toBeUndefined();
  expect(save).not.toHaveBeenCalled();
});

it('saves a direct development viewer region with an explicit development author', async () => {
  expect(sessionStorage.getItem('qarau.auth.session')).toBeNull();
  const save = vi.spyOn(roiApi, 'saveRoi').mockImplementation(async (_siteId, value) => value);
  const view = await renderRoiViewer();
  drawRoi(view);
  const button = within(view.panel).getByRole('button', {
    name: LANGS.en['roi.save'],
  }) as HTMLButtonElement;
  expect(button.disabled).toBe(false);
  fireEvent.click(button);
  await waitFor(() =>
    expect(save).toHaveBeenCalledWith(
      'notre-dame',
      expect.objectContaining({ created_by: 'development', source_run: 'notre-dame' }),
    ),
  );
  await within(view.panel).findByText(LANGS.en['roi.saved']);
  expect(screen.queryByRole('alert')).toBeNull();
});

it('shows load, save and clear failures in plain words and preserves the unsaved box', async () => {
  sessionStorage.setItem('qarau.auth.session', 'Bearer test-roi-credential');
  sessionStorage.setItem('qarau.auth.userId', 'roi-operator');
  vi.mocked(roiApi.loadRoi).mockRejectedValueOnce(new Error('HTTP 503'));
  vi.spyOn(roiApi, 'saveRoi').mockRejectedValue(new Error('HTTP 503'));
  vi.spyOn(roiApi, 'deleteRoi').mockRejectedValue(new Error('HTTP 503'));
  const view = await renderRoiViewer();
  expect((await screen.findByRole('alert')).textContent).toBe(LANGS.en['roi.loadError']);
  fireEvent.click(within(view.panel).getByRole('button', { name: LANGS.en['roi.retry'] }));
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  await waitFor(() =>
    expect(
      (within(view.panel).getByRole('button', { name: LANGS.en['roi.draw'] }) as HTMLButtonElement)
        .disabled,
    ).toBe(false),
  );
  drawRoi(view);
  fireEvent.click(within(view.panel).getByRole('button', { name: LANGS.en['roi.save'] }));
  expect((await screen.findByRole('alert')).textContent).toBe(LANGS.en['roi.saveError']);
  expect(view.engine.scene.getObjectByName('roi-box')).toBeTruthy();
  fireEvent.click(within(view.panel).getByRole('button', { name: LANGS.en['roi.clear'] }));
  expect((await screen.findByRole('alert')).textContent).toBe(LANGS.en['roi.clearError']);
  expect(view.engine.scene.getObjectByName('roi-box')).toBeTruthy();
});

it.each<Lang>(['en', 'ru', 'kk'])(
  'localizes region controls and warns when a saved region comes from another run (%s)',
  async (lang) => {
    setLang(lang);
    vi.mocked(roiApi.resolveRoiSiteId).mockResolvedValue('north-site');
    vi.mocked(roiApi.loadRoi).mockResolvedValue({
      version: 1,
      site_id: 'north-site',
      source_run: 'earlier-survey',
      frame: 'reconstruction',
      up_axis: 'z',
      min: [-1, -1, -1],
      max: [1, 1, 1],
      created_at: '2026-10-17T12:00:00Z',
      created_by: 'roi-operator',
    });
    const view = await renderRoiViewer();
    expect(roiApi.loadRoi).toHaveBeenCalledWith('north-site', expect.any(AbortSignal));
    for (const key of ['roi.draw', 'roi.save', 'roi.clear', 'roi.hideOutside'] as const)
      expect(within(view.panel).getByRole('button', { name: LANGS[lang][key] })).toBeTruthy();
    expect(within(view.panel).getByRole('note').textContent).toContain(
      LANGS[lang]['roi.approximate'],
    );
    expect(within(view.panel).getByRole('note').textContent).toContain('earlier-survey');
    expect(view.engine.scene.getObjectByName('roi-box')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  },
);
