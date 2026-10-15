import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { PerspectiveCamera, Vector3 } from 'three';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createDemo } from '../fixtures/demo';
import { setLang } from '../i18n';
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

function renderDemo() {
  return render(
    <MemoryRouter initialEntries={['/viewer/demo']}>
      <Routes>
        <Route path="/viewer/:runId" element={<ViewerPage />} />
      </Routes>
    </MemoryRouter>,
  );
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

it('localizes axis controls and the fly hint in Russian', () => {
  setLang('ru');
  renderDemo();
  const gizmo = screen.getByRole('group', { name: 'Виды по осям' });
  expect(within(gizmo).getAllByRole('button')).toHaveLength(6);
  expect(within(gizmo).getByRole('button', { name: 'Вид сверху (+Z)' })).toBeTruthy();
  const canvas = screen.getByLabelText(/Интерактивная 3D-карта/);
  canvas.focus();
  fireEvent.keyDown(canvas, { key: 'f', code: 'KeyF' });
  expect(screen.getByRole('status').textContent).toBe(
    'Режим полёта: WASD — движение, мышь — обзор, Shift — быстрее, F/Esc — выход',
  );
});
