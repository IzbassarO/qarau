import { act, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createDemo } from '../fixtures/demo';
import { LANGS, setLang } from '../i18n';
import { ViewerScreen } from './ViewerScreen';

const engine = vi.hoisted(() => ({
  controls: undefined as { isReady: boolean; ready: Promise<void> } | undefined,
}));

vi.mock('./engine', () => ({
  ViewerEngine: class {
    controls = engine.controls!;
    fps = 0;
    resize = vi.fn();
    dispose = vi.fn();
    setFrame = vi.fn();
    setOptions = vi.fn();
    setPicked = vi.fn();
    setSelectedFinding = vi.fn();
  },
}));

function preparingControls() {
  let resolve!: () => void;
  let reject!: (error: Error) => void;
  const ready = new Promise<void>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  engine.controls = { isReady: false, ready };
  return { resolve, reject };
}

function viewer(bundle = createDemo('en').bundle) {
  return (
    <MemoryRouter>
      <ViewerScreen bundle={bundle} />
    </MemoryRouter>
  );
}

beforeEach(() => {
  setLang('en');
  engine.controls = { isReady: true, ready: Promise.resolve() };
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe = vi.fn();
      disconnect = vi.fn();
    },
  );
});
afterEach(() => vi.unstubAllGlobals());

it('shows the controls hint immediately when navigation is already ready', async () => {
  await act(async () => {
    render(viewer());
  });
  expect(screen.getByText(LANGS.en['canvas.hint'])).toBeTruthy();
  expect(screen.queryByRole('status')).toBeNull();
  expect(screen.queryByRole('alert')).toBeNull();
});

it('shows preparation status until navigation is ready', async () => {
  const controls = preparingControls();
  render(viewer());
  expect(screen.getByRole('status').textContent).toBe(LANGS.en['canvas.preparingNavigation']);
  expect(screen.queryByText(LANGS.en['canvas.hint'])).toBeNull();
  await act(async () => controls.resolve());
  expect(screen.getByText(LANGS.en['canvas.hint'])).toBeTruthy();
  expect(screen.queryByRole('status')).toBeNull();
});

it('reports a navigation preparation failure', async () => {
  const controls = preparingControls();
  render(viewer());
  await act(async () => controls.reject(new Error('Index build failed')));
  expect(screen.getByRole('alert').textContent).toBe(LANGS.en['canvas.navigationError']);
  expect(screen.queryByText(LANGS.en['canvas.hint'])).toBeNull();
});

it.each(['resolve', 'reject'] as const)(
  'ignores an old viewer readiness %s after the bundle changes',
  async (outcome) => {
    const oldControls = preparingControls();
    const view = render(viewer());
    const newControls = preparingControls();
    view.rerender(viewer({ ...createDemo('en').bundle, runId: 'next-run' }));
    await act(async () => {
      if (outcome === 'resolve') oldControls.resolve();
      else oldControls.reject(new DOMException('Disposed', 'AbortError'));
    });
    expect(screen.getByRole('status').textContent).toBe(LANGS.en['canvas.preparingNavigation']);
    expect(screen.queryByRole('alert')).toBeNull();
    await act(async () => newControls.resolve());
    expect(screen.getByText(LANGS.en['canvas.hint'])).toBeTruthy();
  },
);

it('handles a disposed viewer readiness rejection after unmount', async () => {
  const controls = preparingControls();
  const view = render(viewer());
  view.unmount();
  await act(async () => controls.reject(new DOMException('Disposed', 'AbortError')));
  expect(screen.queryByRole('alert')).toBeNull();
});
