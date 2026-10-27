export type KeyboardPreferences = Readonly<{send_mode: 'enter' | 'mod-enter'}>;

const storageKey = 'chefmate:keyboard-preferences';
const changedEvent = 'chefmate:keyboard-preferences';
let snapshot: KeyboardPreferences = Object.freeze({send_mode: 'enter'});

/** Stable snapshots let the composer subscribe without changing while a settings draft is edited. */
export function readKeyboardPreferences(): KeyboardPreferences {
  let mode: KeyboardPreferences['send_mode'] = 'enter';
  try {
    if (JSON.parse(localStorage.getItem(storageKey) || '{}')?.send_mode === 'mod-enter') mode = 'mod-enter';
  } catch { /* A missing or damaged browser preference uses the normal Enter shortcut. */ }
  if (snapshot.send_mode !== mode) snapshot = Object.freeze({send_mode: mode});
  return snapshot;
}

export function subscribeKeyboardPreferences(listener: () => void) {
  const changed = () => listener();
  const stored = (event: StorageEvent) => {if (event.key === storageKey || event.key === null) listener();};
  window.addEventListener(changedEvent, changed);
  window.addEventListener('storage', stored);
  return () => {window.removeEventListener(changedEvent, changed); window.removeEventListener('storage', stored);};
}

export function saveKeyboardPreferences(value: KeyboardPreferences): KeyboardPreferences {
  if (!['enter', 'mod-enter'].includes(value.send_mode)) throw new Error('请选择有效的发送快捷键。');
  localStorage.setItem(storageKey, JSON.stringify({send_mode: value.send_mode}));
  const accepted = readKeyboardPreferences();
  window.dispatchEvent(new Event(changedEvent));
  return accepted;
}

type SendKey = {
  key: string; shiftKey?: boolean; altKey?: boolean; ctrlKey?: boolean; metaKey?: boolean;
  isComposing?: boolean; keyCode?: number; nativeEvent?: {isComposing?: boolean; keyCode?: number};
};

export function shouldSendMessage(event: SendKey, preferences = readKeyboardPreferences()): boolean {
  if (event.key !== 'Enter' || event.shiftKey || event.altKey || event.isComposing || event.nativeEvent?.isComposing || event.keyCode === 229 || event.nativeEvent?.keyCode === 229) return false;
  const modified = Boolean(event.ctrlKey || event.metaKey);
  return preferences.send_mode === 'mod-enter' ? modified : !modified;
}
