import {normalizeAppearance,resolveAppearance,appearanceTokens} from './appearance-presets.js';
export {THEME_MODES,resolveTheme} from './appearance-presets.js';

export function readAppearance(storage = globalThis.localStorage) {
  try {
    const saved = JSON.parse(storage?.getItem('chefmate:ui-settings') || '{}');
    return normalizeAppearance(saved);
  } catch { return normalizeAppearance(); }
}

let listening = false;
export function applyAppearance(preferences = readAppearance()) {
  if (typeof document === 'undefined') return;
  const darkSystem = globalThis.matchMedia?.('(prefers-color-scheme: dark)').matches || false;
  const {theme,palette,fonts,preferences:normalized}=resolveAppearance(preferences,darkSystem),root=document.documentElement;
  root.dataset.theme=theme;root.dataset.palette=palette.id;root.dataset.fontPairing=fonts.id;
  root.dataset.showEstimates=String(normalized.show_estimates);
  root.style.colorScheme=theme;
  for(const [token,value]of Object.entries(appearanceTokens(palette,fonts)))root.style.setProperty(token,value);
  const color = document.querySelector('meta[name="theme-color"]');
  if (color) color.content = palette.background;
  window.dispatchEvent(new CustomEvent('chefmate:appearance'));
}

export function initAppearance() {
  applyAppearance();
  if (listening || typeof window === 'undefined') return;
  listening = true;
  window.matchMedia?.('(prefers-color-scheme: dark)').addEventListener('change', () => applyAppearance());
  window.addEventListener('storage', event => {
    if (event.key === 'chefmate:ui-settings' || event.key === null) applyAppearance();
  });
}
