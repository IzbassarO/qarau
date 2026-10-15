import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, expect, it, vi } from 'vitest';
import { createDemo } from '../fixtures/demo';
import * as bundleApi from '../lib/bundle';
import { ViewerPage } from './ViewerPage';

vi.mock('../viewer/ViewerScreen', () => ({
  ViewerScreen: ({ bundle }: { bundle: bundleApi.Bundle }) => (
    <div
      data-testid="viewer"
      data-run-id={bundle.runId}
      data-point-count={bundle.position.length / 3}
    />
  ),
}));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it('loads /viewer/demo from the existing local point cloud without requesting a manifest', async () => {
  const fetch = vi.fn().mockRejectedValue(new Error('Unexpected network request'));
  vi.stubGlobal('fetch', fetch);
  const manifest = vi.spyOn(bundleApi, 'loadManifest');
  const load = vi.spyOn(bundleApi, 'loadBundle');
  render(
    <MemoryRouter initialEntries={['/viewer/demo']}>
      <Routes>
        <Route path="/viewer/:runId" element={<ViewerPage />} />
      </Routes>
    </MemoryRouter>,
  );
  const viewer = await screen.findByTestId('viewer');
  const pointCount = Number(viewer.getAttribute('data-point-count'));
  expect(viewer.getAttribute('data-run-id')).toBe('demo');
  expect(pointCount).toBeGreaterThan(0);
  expect(pointCount).toBe(createDemo('en').bundle.position.length / 3);
  expect(screen.queryByRole('alert')).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
  expect(manifest).not.toHaveBeenCalled();
  expect(load).not.toHaveBeenCalled();
});
