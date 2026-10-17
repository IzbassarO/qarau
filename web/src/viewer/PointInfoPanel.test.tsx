import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { LANGS, setLang, type Lang } from '../i18n';
import { PointInfoPanel } from './PointInfoPanel';
import type { PointInfo } from './pointInfo';

const info: PointInfo = {
  index: 4,
  position: { x: 1.23456, y: -2, z: 3.4 },
  unit: 'model',
  views: 2,
  visibilitySource: 'track',
  frames: [
    { frameId: 7, thumbnailUrl: '/api/runs/test/files/thumb7.jpg', timeSec: 1.625 },
    { frameId: 8, thumbnailUrl: '/api/runs/test/files/thumb8.jpg', timeSec: null },
  ],
  height: { available: false, reason: 'needs_scale' },
  distance: { available: false, reason: 'needs_scale' },
  confidence: { available: false, reason: 'needs_confidence' },
};

beforeEach(() => setLang('en'));

it('renders three-decimal model coordinates, views and unavailable measurements', () => {
  render(<PointInfoPanel info={info} onSeek={vi.fn()} onClose={vi.fn()} />);
  expect(screen.getByRole('region', { name: 'Point info' })).toBeTruthy();
  for (const text of ['1.235 model units', '-2.000 model units', '3.400 model units', 'Views', '2'])
    expect(screen.getByText(text)).toBeTruthy();
  expect(screen.getAllByText('Not available: needs metric scale (P5a)')).toHaveLength(2);
  expect(screen.getByText('Not available: needs point confidence (P6)')).toBeTruthy();
  expect(screen.queryByText('estimated')).toBeNull();
});

it('seeks to the exact frame timestamp on thumbnail click and disables untimed thumbnails', () => {
  const onSeek = vi.fn();
  render(<PointInfoPanel info={info} onSeek={onSeek} onClose={vi.fn()} />);
  expect(screen.getByAltText('Frame 7').getAttribute('src')).toBe(info.frames[0].thumbnailUrl);
  fireEvent.click(screen.getByRole('button', { name: 'Jump to frame 7' }));
  expect(onSeek).toHaveBeenCalledExactlyOnceWith(1.625, 7);
  const untimed = screen.getByRole('button', { name: 'Jump to frame 8' }) as HTMLButtonElement;
  expect(untimed.disabled).toBe(true);
  expect(untimed.title).toBe('Timestamp unavailable');
  fireEvent.click(untimed);
  expect(onSeek).toHaveBeenCalledTimes(1);
});

it('closes the panel and explicitly shows empty estimated visibility', () => {
  const onClose = vi.fn();
  render(
    <PointInfoPanel
      info={{ ...info, frames: [], views: 0, visibilitySource: 'estimated' }}
      onSeek={vi.fn()}
      onClose={onClose}
    />,
  );
  expect(screen.getByText('No frames see this point.')).toBeTruthy();
  expect(screen.getByText('estimated')).toBeTruthy();
  expect(screen.getByText('Projected into camera bounds; occlusion is not checked.')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Close point info' }));
  expect(onClose).toHaveBeenCalledOnce();
});

it('shows metric values and supplied confidence when available', () => {
  render(
    <PointInfoPanel
      info={{
        ...info,
        unit: 'm',
        height: { available: true, value: 3.4 },
        distance: { available: true, value: 4.2 },
        confidence: { available: true, value: 0.8 },
      }}
      onSeek={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  expect(screen.getAllByText('3.400 m')).toHaveLength(2);
  expect(screen.getByText('4.200 m')).toBeTruthy();
  expect(screen.getByText('0.800')).toBeTruthy();
});

it.each<Lang>(['en', 'ru', 'kk'])('localizes panel labels and unavailability in %s', (lang) => {
  setLang(lang);
  const locale = LANGS[lang];
  render(<PointInfoPanel info={info} onSeek={vi.fn()} onClose={vi.fn()} />);
  expect(screen.getByRole('region', { name: locale['pointInfo.title'] })).toBeTruthy();
  expect(screen.getByRole('button', { name: locale['pointInfo.close'] })).toBeTruthy();
  expect(screen.getByText(locale['pointInfo.views'])).toBeTruthy();
  expect(screen.getAllByText(locale['pointInfo.needsScale'])).toHaveLength(2);
  expect(screen.getByText(locale['pointInfo.needsConfidence'])).toBeTruthy();
});
