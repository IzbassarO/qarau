import { hasRgb, type Bundle } from '../lib/bundle';
import type { ColorMode } from './engine';

interface DefaultViewState {
  colorMode: ColorMode;
  buildUp: boolean;
  frustums: boolean;
  showFarLayer: boolean;
}

/** Only explicit user choices should be passed as saved; missing storage is no preference. */
export function defaultViewState(bundle: Bundle, saved?: ColorMode): DefaultViewState {
  return {
    colorMode: hasRgb(bundle) ? (saved ?? 'rgb') : 'height',
    buildUp: false,
    frustums: false,
    showFarLayer: false,
  };
}
