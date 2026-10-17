export const DEFAULT_SENSITIVITY = 1;
export const MIN_SENSITIVITY = 0.25;
export const MAX_SENSITIVITY = 4;

export type ObserverBoostKey = 'control' | 'alt';

export function defaultObserverBoostKey(platform = navigator.platform): ObserverBoostKey {
  return /Mac|iPhone|iPad/i.test(platform) ? 'alt' : 'control';
}

export function loadObserverBoostKey(userId: string | null): ObserverBoostKey {
  const fallback = defaultObserverBoostKey();
  if (!userId) return fallback;
  try {
    const value = localStorage.getItem(`qarau.viewer.observerBoost.${encodeURIComponent(userId)}`);
    return value === 'control' || value === 'alt' ? value : fallback;
  } catch {
    return fallback;
  }
}

export function saveObserverBoostKey(userId: string | null, key: ObserverBoostKey): void {
  if (!userId) return;
  try {
    localStorage.setItem(`qarau.viewer.observerBoost.${encodeURIComponent(userId)}`, key);
  } catch {
    // Keep the current session preference when storage is unavailable.
  }
}

export function clampSensitivity(value: number): number {
  return Number.isFinite(value)
    ? Math.max(MIN_SENSITIVITY, Math.min(MAX_SENSITIVITY, value))
    : DEFAULT_SENSITIVITY;
}

const keyFor = (userId: string) => `qarau.viewer.sensitivity.${encodeURIComponent(userId)}`;

/** Guest/demo changes are ephemeral; no setting is shared between anonymous users. */
export function loadSensitivity(userId: string | null): number {
  if (!userId) return DEFAULT_SENSITIVITY;
  try {
    const value = localStorage.getItem(keyFor(userId));
    return value === null ? DEFAULT_SENSITIVITY : clampSensitivity(Number(value));
  } catch {
    return DEFAULT_SENSITIVITY;
  }
}

/** Local per-user preference. Server persistence follows the account API. */
export function saveSensitivity(userId: string | null, sensitivity: number): void {
  if (!userId) return;
  try {
    localStorage.setItem(keyFor(userId), String(clampSensitivity(sensitivity)));
  } catch {
    // Navigation still uses the new value when browser storage is unavailable.
  }
}
