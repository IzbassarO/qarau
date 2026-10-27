import { afterEach, describe, expect, it, vi } from 'vitest';
import { followSystemTheme } from './theme';

let stopFollowing: (() => void) | undefined;

afterEach(() => {
  stopFollowing?.();
  stopFollowing = undefined;
  delete document.documentElement.dataset.theme;
  vi.unstubAllGlobals();
});

function mockSystemTheme(initialDark: boolean) {
  let dark = initialDark;
  const preference = new EventTarget();
  Object.defineProperty(preference, 'matches', { get: () => dark });
  const matchMedia = vi.fn().mockReturnValue(preference);
  vi.stubGlobal('matchMedia', matchMedia);
  return {
    matchMedia,
    change(darkPreference: boolean) {
      dark = darkPreference;
      preference.dispatchEvent(new Event('change'));
    },
  };
}

describe('system theme', () => {
  it('sets data-theme to dark immediately for a dark preference', () => {
    const system = mockSystemTheme(true);
    stopFollowing = followSystemTheme();

    expect(system.matchMedia).toHaveBeenCalledWith('(prefers-color-scheme: dark)');
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('sets data-theme to light immediately for a light preference', () => {
    mockSystemTheme(false);
    stopFollowing = followSystemTheme();

    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('switches data-theme when the system preference changes', () => {
    const system = mockSystemTheme(false);
    stopFollowing = followSystemTheme();

    system.change(true);
    expect(document.documentElement.dataset.theme).toBe('dark');
    system.change(false);
    expect(document.documentElement.dataset.theme).toBe('light');
  });

  it('removes its change subscription when disposed', () => {
    const system = mockSystemTheme(false);
    stopFollowing = followSystemTheme();

    stopFollowing();
    system.change(true);
    expect(document.documentElement.dataset.theme).toBe('light');
  });
});
