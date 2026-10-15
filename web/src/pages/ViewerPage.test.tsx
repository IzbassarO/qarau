import { act, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createDemo } from '../fixtures/demo';
import { setLang } from '../i18n';
import * as bundleApi from '../lib/bundle';
import { ViewerScreen } from '../viewer/ViewerScreen';
import { ViewerPage } from './ViewerPage';

vi.mock('../viewer/ViewerScreen', () => ({
  ViewerScreen: vi.fn(({ bundle }: { bundle: bundleApi.Bundle }) => (
    <div data-testid="viewer" data-run-id={bundle.runId} />
  )),
}));

function renderRoute(runId = 'demo') {
  return render(
    <MemoryRouter initialEntries={[`/viewer/${runId}`]}>
      <Routes>
        <Route path="/viewer/:runId" element={<ViewerPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  setLang('en');
  vi.mocked(ViewerScreen).mockClear();
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it('opens the demo alias with the local point cloud without requesting the backend', () => {
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  const manifest = vi.spyOn(bundleApi, 'loadManifest');
  const load = vi.spyOn(bundleApi, 'loadBundle');
  renderRoute();
  expect(screen.getByTestId('viewer').getAttribute('data-run-id')).toBe('demo');
  const bundle = vi.mocked(ViewerScreen).mock.calls.at(-1)![0].bundle;
  expect(bundle.position.length).toBeGreaterThan(0);
  expect(bundle).toBe(createDemo('en').bundle);
  expect(manifest).not.toHaveBeenCalled();
  expect(load).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
});

it('retains the demo bundle and viewer when the locale changes', () => {
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  renderRoute();
  const viewer = screen.getByTestId('viewer');
  const bundle = vi.mocked(ViewerScreen).mock.calls.at(-1)![0].bundle;
  for (const lang of ['ru', 'kk'] as const) {
    act(() => setLang(lang));
    expect(screen.getByTestId('viewer')).toBe(viewer);
    expect(vi.mocked(ViewerScreen).mock.calls.at(-1)![0].bundle).toBe(bundle);
  }
  expect(fetch).not.toHaveBeenCalled();
});

it('still loads real runs through the manifest and bundle APIs', async () => {
  const bundle = { ...createDemo('en').bundle, runId: 'real-run' };
  const manifest = vi.spyOn(bundleApi, 'loadManifest').mockResolvedValue(bundle.manifest);
  const load = vi.spyOn(bundleApi, 'loadBundle').mockResolvedValue(bundle);
  renderRoute('real-run');
  expect((await screen.findByTestId('viewer')).getAttribute('data-run-id')).toBe('real-run');
  expect(manifest).toHaveBeenCalledWith('real-run');
  expect(load).toHaveBeenCalledWith('real-run', bundle.manifest, expect.any(Function));
});
