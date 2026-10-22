import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { setLang } from '../i18n';
import { SessionImage } from './SessionImage';

const src = '/api/runs/test-run/files/frame.jpg';
const createObjectURL = vi.fn(() => 'blob:test-frame');
const revokeObjectURL = vi.fn();

beforeEach(() => {
  sessionStorage.clear();
  setLang('en');
  vi.stubGlobal('URL', Object.assign(class extends URL {}, { createObjectURL, revokeObjectURL }));
});
afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

it('loads a protected frame with the session header and releases its blob URL on unmount', async () => {
  sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
  const fetch = vi.fn().mockResolvedValue(new Response('frame'));
  vi.stubGlobal('fetch', fetch);
  const { unmount } = render(<SessionImage src={src} alt="Video frame" className="frame" />);
  await waitFor(() =>
    expect(screen.getByAltText('Video frame').getAttribute('src')).toBe('blob:test-frame'),
  );
  expect(fetch).toHaveBeenCalledWith(src, {
    headers: { Authorization: 'Bearer test-api-value' },
    signal: expect.any(AbortSignal),
    cache: 'no-store',
  });
  unmount();
  expect(revokeObjectURL).toHaveBeenCalledWith('blob:test-frame');
});

it('uses a normal image for the public dev viewer without a session', () => {
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  render(<SessionImage src={src} alt="Video frame" className="frame" />);
  expect(screen.getByAltText('Video frame').getAttribute('src')).toBe(src);
  expect(fetch).not.toHaveBeenCalled();
});

it('does not attach credentials to external or fixture image URLs', () => {
  sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  render(<SessionImage src="https://example.com/frame.jpg" alt="Video frame" className="frame" />);
  expect(screen.getByAltText('Video frame').getAttribute('src')).toBe(
    'https://example.com/frame.jpg',
  );
  expect(fetch).not.toHaveBeenCalled();
});

it('shows a localized error when a protected frame fails', async () => {
  sessionStorage.setItem('qarau.auth.session', 'Bearer test-api-value');
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
  render(<SessionImage src={src} alt="Video frame" className="frame" />);
  expect((await screen.findByRole('alert')).textContent).toContain(
    'Could not load this video frame',
  );
  expect(createObjectURL).not.toHaveBeenCalled();
});
