import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { setLang } from '../i18n';
import type { FrameInfo } from '../lib/bundle';
import fixture from '../test/fixtures/finding.json';
import { EvidencePanel } from './EvidencePanel';

const frames = [0, 4, 7].map((i) => ({ i, camera: { width: 640, height: 480 } })) as FrameInfo[];
let resize: () => void;
beforeEach(() => {
  setLang('en');
  sessionStorage.clear();
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(callback: () => void) {
        resize = callback;
      }
      observe() {}
      disconnect() {}
    },
  );
});
afterEach(() => vi.unstubAllGlobals());

function openPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const onClose = vi.fn();
  const result = render(
    <QueryClientProvider client={client}>
      <EvidencePanel findingId={701} frames={frames} onClose={onClose} />
    </QueryClientProvider>,
  );
  return { ...result, onClose };
}

it('shows loading until finding detail arrives', () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => new Promise(() => {})),
  );
  openPanel();
  expect(screen.getByRole('status').textContent).toBe('Loading finding evidence…');
  expect(screen.queryByRole('alert')).toBeNull();
});

it('shows a plain-language error and retries the detail request', async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(new Response(null, { status: 503 }))
    .mockResolvedValueOnce(Response.json(fixture.finding));
  vi.stubGlobal('fetch', fetch);
  openPanel();
  expect((await screen.findByRole('alert')).textContent).toContain(
    'Could not load this finding. Try again.',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  expect(await screen.findByText('Corrosion')).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
});

it('shows class, severity, status, measured area and three outlined frames, without confidence or decisions UI', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(fixture.finding)));
  const { container, onClose } = openPanel();
  expect(await screen.findByText('Corrosion')).toBeTruthy();
  for (const text of ['Critical', 'Confirmed', '1.2 m²', 'Scale source: reference: measured sheet'])
    expect(screen.getByText(text)).toBeTruthy();
  const thumbnails = container.querySelector('.evidence-thumbnails')! as HTMLElement;
  expect(within(thumbnails).getAllByRole('img')).toHaveLength(3);
  expect(thumbnails.querySelectorAll('svg polygon')).toHaveLength(3);
  expect(
    within(thumbnails)
      .getAllByRole('img')
      .map((image) => image.getAttribute('src')),
  ).toEqual([
    '/api/runs/notre-dame-p3e/files/frames/000000.jpg',
    '/api/runs/notre-dame-p3e/files/frames/000004.jpg',
    '/api/runs/notre-dame-p3e/files/frames/000007.jpg',
  ]);
  expect(screen.queryByText(/0\.987654|98\.7654|confidence/i)).toBeNull();
  for (const name of [/confirm/i, /reject/i, /reclass/i, /edit/i, /delete/i])
    expect(screen.queryByRole('button', { name })).toBeNull();
  fireEvent.click(within(thumbnails).getByRole('button', { name: 'Frame 7' }));
  expect(container.querySelector('.evidence-main-frame img')?.getAttribute('src')).toBe(
    '/api/runs/notre-dame-p3e/files/frames/000007.jpg',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Close evidence' }));
  expect(onClose).toHaveBeenCalledOnce();
});

it('shows an explicit empty-evidence state and unscaled area', async () => {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValue(
        Response.json({ ...fixture.finding, area_m2: null, scale: 'not set', evidence: [] }),
      ),
  );
  const { container } = openPanel();
  expect(await screen.findByText('No evidence frames are attached to this finding.')).toBeTruthy();
  expect(screen.getByText('3.5 units · scale not set')).toBeTruthy();
  expect(container.querySelector('polygon')).toBeNull();
});

it('uses natural image dimensions and recalculates letterboxing after resize', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      Response.json({
        ...fixture.finding,
        evidence: [
          {
            ...fixture.finding.evidence[0],
            outline: [
              [0, 0],
              [1920, 1080],
              [960, 540],
            ],
          },
        ],
      }),
    ),
  );
  const { container } = openPanel();
  await screen.findByText('Corrosion');
  const box = container.querySelector('.evidence-thumbnails .evidence-frame')!;
  vi.spyOn(box, 'getBoundingClientRect').mockReturnValue({ width: 400, height: 400 } as DOMRect);
  act(() => resize());
  const image = box.querySelector('img')!;
  Object.defineProperties(image, { naturalWidth: { value: 1920 }, naturalHeight: { value: 1080 } });
  fireEvent.load(image);
  expect(box.querySelector('polygon')?.getAttribute('points')).toBe('0,87.5 400,312.5 200,200');
});

it('reports a failed evidence image instead of leaving a silent blank', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(fixture.finding)));
  openPanel();
  await screen.findByText('Corrosion');
  fireEvent.error(screen.getAllByRole('img')[0]);
  expect((await screen.findByRole('alert')).textContent).toBe(
    'Could not load this video frame. Check your access and connection.',
  );
});
