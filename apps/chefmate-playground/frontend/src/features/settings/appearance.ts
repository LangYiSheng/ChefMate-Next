import {normalizeAppearance, resolveAppearance, appearanceTokens} from './appearance-presets';
export {PALETTES, FONT_PAIRINGS, FONT_SIZES, THEME_MODES, resolveTheme} from './appearance-presets';

let faviconSource: Promise<string> | null = null;
let faviconGeneration = 0;
async function updateFavicon(accent: string) {
  const generation = ++faviconGeneration;
  try {
    faviconSource ||= fetch('/static/brand/chef-buddy.svg').then(response => {if (!response.ok) throw new Error('Brand unavailable'); return response.text();});
    const source = await faviconSource;
    if (generation !== faviconGeneration) return;
    const tinted = source.replace(/color=(["'])#343d38\1/gi, `color="${accent}"`);
    document.querySelector<HTMLLinkElement>('link[rel="icon"]')?.setAttribute('href', `data:image/svg+xml,${encodeURIComponent(tinted)}`);
  } catch {faviconSource = null;}
}

export function readAppearance(storage: Pick<Storage, 'getItem'> | undefined = globalThis.localStorage) {
  try { return normalizeAppearance(JSON.parse(storage?.getItem('chefmate:ui-settings') || '{}')); }
  catch { return normalizeAppearance(); }
}

export function applyAppearance(preferences = readAppearance()) {
  if (typeof document === 'undefined') return;
  const resolved = resolveAppearance(preferences, globalThis.matchMedia?.('(prefers-color-scheme: dark)').matches || false);
  const {theme, palette, fonts, fontSize, preferences: normalized} = resolved;
  const root = document.documentElement;
  Object.assign(root.dataset, {theme, palette: palette.id, fontPairing: fonts.id, fontSize: fontSize.id, showEstimates: String(normalized.show_estimates), density: normalized.density || 'comfortable'});
  root.style.colorScheme = theme;
  for (const [token, value] of Object.entries(appearanceTokens(palette, fonts, fontSize))) root.style.setProperty(token, value);
  root.style.fontSize = 'var(--font-size-base, 15px)';
  document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.setAttribute('content', palette.background);
  void updateFavicon(palette.accent);
  window.dispatchEvent(new CustomEvent('chefmate:appearance'));
}

export function initAppearance() {
  applyAppearance();
  const media = window.matchMedia?.('(prefers-color-scheme: dark)');
  const refresh = () => applyAppearance();
  const onStorage = (event: StorageEvent) => { if (!event.key || event.key === 'chefmate:ui-settings') refresh(); };
  media?.addEventListener('change', refresh);
  window.addEventListener('storage', onStorage);
  return () => { media?.removeEventListener('change', refresh); window.removeEventListener('storage', onStorage); };
}
