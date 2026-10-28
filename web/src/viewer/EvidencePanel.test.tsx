import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { setLang } from '../i18n';
import type { Finding } from '../lib/findings';
import fixture from '../test/fixtures/finding.json';
import { EvidencePanel } from './EvidencePanel';

const finding = fixture.finding as Finding;
const base = {
  selection: 'first',
  loading: false,
  error: false,
  onClose: vi.fn(),
};

beforeEach(() => setLang('en'));

it('shows Loading during the initial request', () => {
  render(<EvidencePanel {...base} findings={undefined} loading />);
  expect(screen.getByRole('status').textContent).toBe('Loading');
});

it('shows the request error in plain words', () => {
  render(<EvidencePanel {...base} findings={undefined} error />);
  expect(screen.getByRole('alert').textContent).toBe(
    'Could not load findings. Check your connection and try again.',
  );
  expect(screen.queryByText('Loading')).toBeNull();
});

it('shows No findings for an empty response or an unknown id', () => {
  const view = render(<EvidencePanel {...base} findings={[]} />);
  expect(screen.getByRole('status').textContent).toBe('No findings');
  view.rerender(<EvidencePanel {...base} selection="999" findings={[finding]} />);
  expect(screen.getByRole('status').textContent).toBe('No findings');
});

it('shows API class, severity, status, area and three source frames with SVG outlines', () => {
  const view = render(<EvidencePanel {...base} selection="701" findings={[finding]} />);
  expect(screen.getByRole('heading', { name: 'Corrosion' })).toBeTruthy();
  for (const text of ['Critical', 'Confirmed', '1.2 m²', 'reference: measured sheet'])
    expect(screen.getByText(text)).toBeTruthy();
  const frames = screen.getAllByTestId('evidence-frame');
  expect(frames).toHaveLength(3);
  expect(frames.map((frame) => within(frame).getByRole('img').getAttribute('src'))).toEqual([
    '/api/runs/notre-dame-p3e/files/frames/000000.jpg',
    '/api/runs/notre-dame-p3e/files/frames/000004.jpg',
    '/api/runs/notre-dame-p3e/files/frames/000007.jpg',
  ]);
  for (const frame of frames) {
    expect(frame.querySelectorAll('svg polygon')).toHaveLength(1);
    expect(frame.querySelector('svg')?.getAttribute('viewBox')).toBe('0 0 400 250');
  }
  expect(view.container.textContent).not.toMatch(/confidence|%|0\.987654|98\.7/i);
  expect(screen.queryByRole('button', { name: /confirm|reject/i })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Close finding' }));
  expect(base.onClose).toHaveBeenCalledOnce();
});

it('uses natural image dimensions for the contain-fit outline, including letterboxing', () => {
  render(<EvidencePanel {...base} findings={[finding]} />);
  const image = screen.getByAltText('Frame 0');
  Object.defineProperties(image, {
    naturalWidth: { value: 1920 },
    naturalHeight: { value: 1080 },
  });
  fireEvent.load(image);
  expect(
    screen.getAllByTestId('evidence-frame')[0].querySelector('polygon')?.getAttribute('points'),
  ).toBe(
    '52.083333333333336,33.333333333333336 66.66666666666667,35.41666666666667 68.75,62.5 54.16666666666667,60.41666666666667',
  );
});

it('states that an unscaled area is in units', () => {
  render(<EvidencePanel {...base} findings={[{ ...finding, area_m2: null, scale: 'not set' }]} />);
  expect(screen.getByText('3.5 units · scale not set')).toBeTruthy();
});

it.each(['error', 'empty'] as const)(
  'never returns to Loading after a loaded response becomes %s',
  (next) => {
    const view = render(<EvidencePanel {...base} findings={[finding]} />);
    const panel = screen.getByRole('complementary', { name: 'Finding' });
    expect(screen.getByText('1.2 m²')).toBeTruthy();
    view.rerender(
      <EvidencePanel
        {...base}
        findings={next === 'empty' ? [] : undefined}
        error={next === 'error'}
      />,
    );
    expect(screen.queryByText('Loading')).toBeNull();
    expect(screen.getByRole('complementary', { name: 'Finding' })).toBe(panel);
    expect(screen.getByRole(next === 'error' ? 'alert' : 'status').textContent).toBe(
      next === 'error'
        ? 'Could not load findings. Check your connection and try again.'
        : 'No findings',
    );
    // Even a subsequent pending background request must not restore the initial state.
    view.rerender(<EvidencePanel {...base} findings={undefined} loading />);
    expect(screen.queryByText('Loading')).toBeNull();
  },
);
