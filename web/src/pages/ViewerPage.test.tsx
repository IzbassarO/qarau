import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PerspectiveCamera, Vector3 } from 'three';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createDemo } from '../fixtures/demo';
import { login, logout } from '../auth/auth';
import { LANGS, setLang, type Lang } from '../i18n';
import * as bundleApi from '../lib/bundle';
import { ViewerEngine } from '../viewer/engine';
import type { Axis } from '../viewer/gizmo';
import { saveSensitivity } from '../viewer/settings';
import { ViewerPage } from './ViewerPage';
import { mockReportApi } from '../test/reportApi';

const { renderScene } = vi.hoisted(() => ({ renderScene: vi.fn() }));

// Keep real navigation, input handlers, camera math and UI; jsdom only lacks WebGL.
vi.mock('three', async (importOriginal) => {
  const actual = await importOriginal<typeof import('three')>();
  return {
    ...actual,
    WebGLRenderer: class {
      getContext() {
        return {
          SYNC_GPU_COMMANDS_COMPLETE: 0x9117,
          TIMEOUT_EXPIRED: 0x911b,
          fenceSync: () => ({}),
          clientWaitSync: () => 0x911a,
          deleteSync() {},
          flush() {},
        };
      }
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
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}
    >
      <MemoryRouter initialEntries={[`/viewer/${runId}${query}`]}>
        <Routes>
          <Route path="/viewer/:runId" element={<ViewerPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderDemo() {
  return renderViewer('demo');
}

it.each(['notre-dame', 'notre-dame-p3e'])(
  'links viewer %s to the API inspection report even without findings',
  async (runId) => {
    const fetch = mockReportApi();
    const original = fetch.getMockImplementation()!;
    fetch.mockImplementation(async (input) =>
      String(input) === '/api/inspections/37/findings' ? Response.json([]) : original(input),
    );
    const bundle = { ...createDemo('en').bundle, runId };
    vi.spyOn(bundleApi, 'loadManifest').mockResolvedValue(bundle.manifest);
    vi.spyOn(bundleApi, 'loadBundle').mockResolvedValue(bundle);
    const view = renderViewer(runId);
    expect((await screen.findByRole('link', { name: 'Report' })).getAttribute('href')).toBe(
      '/inspections/37/report',
    );
    view.unmount();
  },
);

it('links /viewer/demo to the seeded demo report route', () => {
  renderDemo();
  expect(screen.getByRole('link', { name: 'Report' }).getAttribute('href')).toBe(
    '/inspections/demo/report',
  );
});

it.each([
  ['missing', 'The findings file listed in the bundle is missing. Re-run the projection stage.'],
  [
    'broken',
    'The findings file could not be read (damaged or inconsistent with the map). Re-run the projection stage.',
  ],
] as const)(
  'shows a %s findings warning outside the folded panels',
  async (findingsError, message) => {
    const bundle = { ...createDemo('en').bundle, runId: 'findings-failure', findingsError };
    vi.spyOn(bundleApi, 'loadManifest').mockResolvedValue(bundle.manifest);
    vi.spyOn(bundleApi, 'loadBundle').mockResolvedValue(bundle);
    renderViewer('findings-failure');
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toBe(message);
    expect(alert.className).toBe('qv-notice viewer-notice viewer-error');
    expect(alert.closest('details')).toBeNull();
    expect(document.querySelector('.viewer-panel-drawer')?.hasAttribute('open')).toBe(false);
    expect(screen.getAllByRole('alert')).toHaveLength(1);
    expect(
      screen.getByLabelText(
        'Interactive 3D map with point cloud and camera path. Two-finger drag or mouse drag: orbit. Shift: pan. Pinch or mouse wheel: zoom. Alt/Option: precision. 1: Orbit. F: Observer.',
      ),
    ).toBeTruthy();
  },
);

it('opens in Orbit by default without an observer minimap or human figure', () => {
  const setSensitivity = vi.spyOn(ViewerEngine.prototype, 'setSensitivity');
  renderDemo();
  const engine = setSensitivity.mock.contexts.at(-1) as ViewerEngine;
  expect(engine.observerMode).toBe(false);
  expect(screen.queryByTestId('observer-minimap')).toBeNull();
  expect(
    screen
      .getByRole('button', { name: LANGS.en['viewer.mode.orbit'] })
      .getAttribute('aria-pressed'),
  ).toBe('true');
  expect(engine.scene.getObjectByName('observer-human')?.visible).not.toBe(true);
  expect(screen.queryByRole('alert')).toBeNull();
});

it.each(['', '&point=0'])(
  'opens /viewer/aegviidu?mode=observer%s with a minimap, Observer indicator and controls hint',
  async (pointQuery) => {
    const bundle = { ...createDemo('en').bundle, runId: 'aegviidu' };
    vi.spyOn(bundleApi, 'loadManifest').mockResolvedValue(bundle.manifest);
    vi.spyOn(bundleApi, 'loadBundle').mockResolvedValue(bundle);
    const setSensitivity = vi.spyOn(ViewerEngine.prototype, 'setSensitivity');
    renderViewer('aegviidu', `?mode=observer${pointQuery}`);
    const minimap = await screen.findByTestId('observer-minimap');
    expect(minimap.getAttribute('aria-label')).toBe(LANGS.en['viewer.observer.minimap']);
    await waitFor(() => expect(setSensitivity).toHaveBeenCalled());
    const engine = setSensitivity.mock.contexts.at(-1) as ViewerEngine;
    expect(engine.observerMode).toBe(true);
    expect(engine.controls.enabled).toBe(false);
    expect(
      screen
        .getByRole('button', { name: LANGS.en['viewer.mode.observer'] })
        .getAttribute('aria-pressed'),
    ).toBe('true');
    expect(screen.getByRole('status').textContent).toBe(LANGS.en['canvas.observerHint']);
    expect(engine.scene.getObjectByName('observer-human')?.visible).not.toBe(true);
    expect(screen.queryByRole('alert')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: LANGS.en['viewer.mode.orbit'] }));
    expect(engine.observerMode).toBe(false);
    expect(screen.queryByTestId('observer-minimap')).toBeNull();
  },
);

it('switches /viewer/aegviidu from Orbit to Observer with its mode button', async () => {
  const bundle = { ...createDemo('en').bundle, runId: 'aegviidu' };
  vi.spyOn(bundleApi, 'loadManifest').mockResolvedValue(bundle.manifest);
  vi.spyOn(bundleApi, 'loadBundle').mockResolvedValue(bundle);
  const setSensitivity = vi.spyOn(ViewerEngine.prototype, 'setSensitivity');
  renderViewer('aegviidu');
  const observer = await screen.findByRole('button', { name: LANGS.en['viewer.mode.observer'] });
  await waitFor(() => expect(setSensitivity).toHaveBeenCalled());
  const engine = setSensitivity.mock.contexts.at(-1) as ViewerEngine;
  expect(engine.observerMode).toBe(false);
  fireEvent.click(observer);
  expect(engine.observerMode).toBe(true);
  expect(engine.controls.enabled).toBe(false);
  expect(observer.getAttribute('aria-pressed')).toBe('true');
  expect(
    screen
      .getByRole('button', { name: LANGS.en['viewer.mode.orbit'] })
      .getAttribute('aria-pressed'),
  ).toBe('false');
  expect(screen.getByTestId('observer-minimap')).toBeTruthy();
  expect(screen.getByRole('status').textContent).toBe(LANGS.en['canvas.observerHint']);
  expect(screen.queryByRole('alert')).toBeNull();
});

it('moves immediately after clicking third-person view and lets V return to first person', () => {
  const setSensitivity = vi.spyOn(ViewerEngine.prototype, 'setSensitivity');
  renderViewer('demo', '?mode=observer');
  const engine = setSensitivity.mock.contexts.at(-1) as ViewerEngine;
  const thirdPerson = screen.getByRole('button', { name: LANGS.en['viewer.observer.thirdPerson'] });
  act(() => thirdPerson.focus());
  fireEvent.click(thirdPerson);
  expect(engine.thirdPerson).toBe(true);
  const position = engine.observerView!.position.clone();
  fireEvent.keyDown(document.activeElement!, { key: 'w', code: 'KeyW' });
  advanceFrames(10);
  fireEvent.keyUp(document.activeElement!, { key: 'w', code: 'KeyW' });
  expect(engine.observerView!.position.distanceTo(position)).toBeGreaterThan(0);
  fireEvent.keyDown(document.activeElement!, { key: 'v', code: 'KeyV' });
  expect(engine.thirdPerson).toBe(false);
  expect(screen.getByTestId('observer-minimap')).toBeTruthy();
});

it('keeps Observer and its minimap while changing the speed boost setting', () => {
  const setSensitivity = vi.spyOn(ViewerEngine.prototype, 'setSensitivity');
  renderViewer('demo', '?mode=observer');
  const engine = setSensitivity.mock.contexts.at(-1) as ViewerEngine;
  fireEvent.click(screen.getByText(LANGS.en['viewer.settings.title']));
  const boost = screen.getByLabelText(LANGS.en['viewer.settings.observerBoost']);
  act(() => boost.focus());
  fireEvent.change(boost, { target: { value: 'alt' } });
  fireEvent.keyDown(boost, { key: 'f', code: 'KeyF' });
  expect(engine.observerMode).toBe(true);
  expect(screen.getByTestId('observer-minimap')).toBeTruthy();
  expect(screen.getByRole('status').textContent).toBe(LANGS.en['canvas.observerHint']);
});

it('opens ?point=0 with the info tool, evidence panel and selected-point marker, and seeks its exact frame', async () => {
  const bundle = createDemo('en').bundle;
  const setPicked = vi.spyOn(ViewerEngine.prototype, 'setPicked');
  const viewThroughFrame = vi.spyOn(ViewerEngine.prototype, 'viewThroughFrame');
  renderViewer('demo', '?point=0');
  const tool = screen.getByRole('button', { name: LANGS.en['pointInfo.tool'] });
  expect(tool.getAttribute('aria-pressed')).toBe('true');
  const tools = tool.closest('details') as HTMLDetailsElement;
  expect(tools.open).toBe(false);
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
  expect(tools.open).toBe(true);
  expect(document.activeElement).toBe(tool);
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
  advanceFrames(1);

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
      await waitFor(() => {
        advanceFrames(1);
        expect(renderScene).toHaveBeenCalled();
      });
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
  advanceFrames(1);
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

it('shows Observer controls and the minimap after F, keeps them after Esc and exits with F', () => {
  renderDemo();
  const canvas = screen.getByLabelText(/Interactive 3D map/);
  const hint = LANGS.en['canvas.observerHint'];
  canvas.focus();
  expect(screen.queryByText(hint)).toBeNull();
  expect(screen.queryByTestId('observer-minimap')).toBeNull();
  fireEvent.keyDown(canvas, { key: 'f', code: 'KeyF' });
  expect(screen.getByRole('status').textContent).toBe(hint);
  expect(screen.getByTestId('observer-minimap')).toBeTruthy();
  fireEvent.keyDown(canvas, { key: 'f', code: 'KeyF' });
  expect(screen.queryByText(hint)).toBeNull();
  expect(screen.queryByTestId('observer-minimap')).toBeNull();
  fireEvent.keyDown(canvas, { key: 'f', code: 'KeyF' });
  expect(screen.getByText(hint)).toBeTruthy();
  fireEvent.keyDown(canvas, { key: 'Escape', code: 'Escape' });
  expect(screen.getByRole('status').textContent).toBe(hint);
  expect(screen.getByTestId('observer-minimap')).toBeTruthy();
  fireEvent.keyDown(canvas, { key: 'f', code: 'KeyF' });
  expect(screen.queryByText(hint)).toBeNull();
  expect(screen.queryByTestId('observer-minimap')).toBeNull();
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
    const walk = within(modes).getByRole('button', {
      name: locale['viewer.mode.walk'],
    }) as HTMLButtonElement;
    expect(walk.disabled).toBe(true);
    expect(walk.title).toBe(locale['viewer.mode.comingSoon']);
    const observer = within(modes).getByRole('button', {
      name: locale['viewer.mode.observer'],
    }) as HTMLButtonElement;
    expect(observer.disabled).toBe(false);
    fireEvent.click(observer);
    expect(observer.getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('status').textContent).toBe(locale['canvas.observerHint']);
    expect(screen.getByTestId('observer-minimap').getAttribute('aria-label')).toBe(
      locale['viewer.observer.minimap'],
    );
    fireEvent.click(within(modes).getByRole('button', { name: locale['viewer.mode.orbit'] }));
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

it.each<Lang>(['en', 'ru', 'kk'])('localizes axis controls and the Observer hint in %s', (lang) => {
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
  expect(screen.getByRole('status').textContent).toBe(locale['canvas.observerHint']);
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
