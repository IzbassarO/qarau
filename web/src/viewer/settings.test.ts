import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import {
  defaultObserverBoostKey,
  loadObserverBoostKey,
  saveObserverBoostKey,
  loadSensitivity,
  saveSensitivity,
} from './settings';

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

it('defaults Observer boost to Alt on Mac and Ctrl elsewhere', () => {
  expect(defaultObserverBoostKey('MacIntel')).toBe('alt');
  expect(defaultObserverBoostKey('Linux x86_64')).toBe('control');
});

it('persists the Observer boost key per signed-in user and handles blocked storage', () => {
  saveObserverBoostKey('operator-a', 'alt');
  saveObserverBoostKey('operator-b', 'control');
  expect(loadObserverBoostKey('operator-a')).toBe('alt');
  expect(loadObserverBoostKey('operator-b')).toBe('control');
  const fallback = defaultObserverBoostKey();
  saveObserverBoostKey(null, fallback === 'alt' ? 'control' : 'alt');
  expect(loadObserverBoostKey(null)).toBe(fallback);
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  expect(loadObserverBoostKey('operator-a')).toBe(fallback);
  expect(() => saveObserverBoostKey('operator-a', 'alt')).not.toThrow();
});

it('saves and loads sensitivity separately for each user id', () => {
  expect(loadSensitivity('operator-a')).toBe(1);
  saveSensitivity('operator-a', 0.5);
  saveSensitivity('operator-b', 2.5);
  expect(loadSensitivity('operator-a')).toBe(0.5);
  expect(loadSensitivity('operator-b')).toBe(2.5);
  expect(loadSensitivity('operator-c')).toBe(1);
  expect(localStorage.getItem('qarau.viewer.sensitivity.operator-a')).toBe('0.5');
});

it('keeps guest settings ephemeral and does not reuse another user preference', () => {
  saveSensitivity('operator-a', 3);
  saveSensitivity(null, 2);
  expect(loadSensitivity(null)).toBe(1);
  expect(localStorage.length).toBe(1);
});

it('constrains stored values to the supported range and handles invalid values', () => {
  saveSensitivity('operator-a', 100);
  expect(loadSensitivity('operator-a')).toBe(4);
  saveSensitivity('operator-a', 0);
  expect(loadSensitivity('operator-a')).toBe(0.25);
  localStorage.setItem('qarau.viewer.sensitivity.operator-a', 'invalid');
  expect(loadSensitivity('operator-a')).toBe(1);
});

it('works when the browser blocks local storage', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  expect(loadSensitivity('operator-a')).toBe(1);
  expect(() => saveSensitivity('operator-a', 2)).not.toThrow();
});
