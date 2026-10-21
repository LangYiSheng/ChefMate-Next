export const THEME_MODES = Object.freeze(['system', 'light', 'dark']);

export function resolveTheme(mode, darkSystem = false) {
  return mode === 'dark' || mode === 'system' && darkSystem ? 'dark' : 'light';
}

export function readAppearance(storage = globalThis.localStorage) {
  try {
    const saved = JSON.parse(storage?.getItem('chefmate:ui-settings') || '{}');
    return { ...saved, theme: THEME_MODES.includes(saved.theme) ? saved.theme : 'system' };
  } catch { return { theme: 'system' }; }
}

let listening = false;
export function applyAppearance(mode = readAppearance().theme) {
  if (typeof document === 'undefined') return;
  const darkSystem = globalThis.matchMedia?.('(prefers-color-scheme: dark)').matches || false;
  const theme = resolveTheme(mode, darkSystem);
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  const color = document.querySelector('meta[name="theme-color"]');
  if (color) color.content = theme === 'dark' ? '#18211c' : '#f2f4f2';
}

export function initAppearance() {
  applyAppearance();
  if (listening || typeof window === 'undefined') return;
  listening = true;
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => applyAppearance());
  window.addEventListener('storage', event => {
    if (event.key === 'chefmate:ui-settings') applyAppearance();
  });
}
