import { api, HttpError } from '../../runtime/http';
import { chefStore } from '../../runtime/store';
import { applyAppearance, readAppearance } from '../settings/appearance';

export type UiPreferencesSnapshot = { revision: string; initialized: boolean; values: Record<string, unknown> };
const appearanceKeys = ['theme', 'light_palette', 'dark_palette', 'font_pairing', 'font_size', 'density', 'inline_limit', 'show_estimates'] as const;
const preferenceKeys = [...appearanceKeys, 'reply_suggestions', 'reply_suggestion_count'];
let lastApplied = '';
let writeGeneration = 0, writes = 0;
let writeTail: Promise<void> = Promise.resolve();
const refreshers = new Set<() => void>();
const readGenerations = new WeakMap<UiPreferencesSnapshot, number>();

function checkAbort(signal?: AbortSignal) {
  if (signal?.aborted) throw new DOMException('Preference synchronization cancelled', 'AbortError');
}

async function waitForWrites(signal?: AbortSignal) {
  while (writes) {
    checkAbort(signal);
    const tail = writeTail;
    if (!signal) { await tail; continue; }
    await new Promise<void>((resolve, reject) => {
      const abort = () => reject(new DOMException('Preference synchronization cancelled', 'AbortError'));
      signal.addEventListener('abort', abort, { once: true });
      tail.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
      if (signal.aborted) abort();
    });
  }
  checkAbort(signal);
}

function writePreferences(changes: Record<string, unknown>, revision: string, signal?: AbortSignal) {
  if (Object.keys(changes).some(key => !preferenceKeys.includes(key))) return Promise.reject(new Error('只能修改普通外观与对话偏好。'));
  checkAbort(signal);
  const body = JSON.stringify({ revision, changes });
  // Invalidate reads as soon as a write is queued, not only after it reaches HTTP.
  writeGeneration++; writes++;
  const task = writeTail.then(async () => {
    checkAbort(signal);
    const result = snapshot(await api('/api/ui-preferences', { method: 'PATCH', signal, body }));
    checkAbort(signal);
    apply(result); return result;
  }).finally(() => {
    writes--; writeGeneration++;
    // Also reconcile uncertain/aborted writes. A response can be lost after commit.
    if (!writes) for (const refresh of refreshers) refresh();
  });
  writeTail = task.then(() => undefined, () => undefined);
  return task;
}

function snapshot(data: unknown): UiPreferencesSnapshot {
  const value = data as UiPreferencesSnapshot;
  if (!value || typeof value.revision !== 'string' || typeof value.initialized !== 'boolean' || !value.values || typeof value.values !== 'object' || Array.isArray(value.values)) throw new Error('外观偏好返回格式不正确。');
  return { revision: value.revision, initialized: value.initialized, values: Object.fromEntries(preferenceKeys.filter(key => Object.hasOwn(value.values, key)).map(key => [key, value.values[key]])) };
}

function apply(data: UiPreferencesSnapshot) {
  const signature = JSON.stringify(data);
  if (signature === lastApplied) return;
  lastApplied = signature;
  const appearance = Object.fromEntries(appearanceKeys.filter(key => Object.hasOwn(data.values, key)).map(key => [key, data.values[key]]));
  try { localStorage.setItem('chefmate:ui-settings', JSON.stringify(appearance)); } catch { /* Current page still reflects server preferences. */ }
  applyAppearance(appearance);
  window.dispatchEvent(new CustomEvent('chefmate:ui-preferences', { detail: data }));
  void chefStore.loadStatus().catch(() => {});
}

export async function readUiPreferences(options: { signal?: AbortSignal } = {}) {
  for (;;) {
    await waitForWrites(options.signal);
    const generation = writeGeneration;
    const result = snapshot(await api('/api/ui-preferences', options));
    checkAbort(options.signal);
    if (writes || generation !== writeGeneration) continue;
    readGenerations.set(result, generation);
    return result;
  }
}

export async function saveUiPreferences(changes: Record<string, unknown>, revision: string) {
  return writePreferences(changes, revision);
}

/** Only current live tool completions trigger reads; historical tool snapshots never replace preferences. */
export function initializeUiPreferences() {
  const abort = new AbortController(), seen = new Set<string>();
  const observedRuns = new Map<string, string>();
  let pending: Promise<void> | null = null, queued = false, seedAttempted = false;
  const refresh = () => {
    if (abort.signal.aborted || document.visibilityState === 'hidden') return Promise.resolve();
    if (writes) { queued = true; return Promise.resolve(); }
    if (pending) { queued = true; return pending; }
    queued = false;
    pending = (async () => {
      try {
        const current = await readUiPreferences({ signal: abort.signal });
        if (abort.signal.aborted) return;
        if (!current.initialized) {
          // A failed seed may have committed or the service may still be offline.
          // Reconcile by GET, but do not turn that reconciliation into another write.
          if (seedAttempted) return;
          seedAttempted = true;
          const local = readAppearance(), seed = Object.fromEntries(appearanceKeys.map(key => [key, local[key]]));
          try { await writePreferences(seed, current.revision, abort.signal); }
          catch (error) { if (!(error instanceof HttpError) || error.status !== 409) throw error; queued = true; }
          return;
        }
        if (writes || readGenerations.get(current) !== writeGeneration) { queued = true; return; }
        apply(current);
      } catch { /* Existing local preferences remain usable while the service reconnects. */ }
    })().finally(() => { pending = null; if (queued) { queued = false; void refresh(); } });
    return pending;
  };
  refreshers.add(refresh);
  const observe = () => {
    const state = chefStore.getSnapshot();
    const running = new Set([...Object.keys(state.runs), ...Object.entries(state.cache).filter(([, session]) => session.running).map(([id]) => id)]);
    let completed = false;
    for (const id of running) {
      const identity = state.runs[id]?.requestId || state.cache[id]?.active_turn?.request_id || state.cache[id]?.active_turn?.id || '';
      const previous = observedRuns.get(id);
      if (previous && identity && previous !== identity) completed = true;
      observedRuns.set(id, identity);
      const message = state.cache[id]?.messages.at(-1);
      if (!message?.streaming) continue;
      for (const call of message.tool_calls || []) {
        if (!['get_app_preferences', 'update_app_preferences'].includes(call.name) || call.running || call.result?.status !== 'ok') continue;
        const key = `${id}:${identity}:${call.call_id || call.result.preferences?.revision || ''}`;
        if (seen.has(key)) continue;
        seen.add(key); void refresh();
      }
    }
    for (const id of observedRuns.keys()) if (!running.has(id)) { observedRuns.delete(id); completed = true; }
    // Canonical recovery can finish without delivering tool_end or result over SSE.
    // Only a lifecycle seen running here may trigger this read; history cannot replay values.
    if (completed) void refresh();
  };
  const unsubscribe = chefStore.subscribe(observe);
  observe();
  const visible = () => {
    if (document.visibilityState === 'hidden') return;
    if (!pending && !writes) seedAttempted = false;
    void refresh();
  };
  window.addEventListener('focus', visible); document.addEventListener('visibilitychange', visible);
  void refresh();
  return () => { abort.abort(); refreshers.delete(refresh); unsubscribe(); window.removeEventListener('focus', visible); document.removeEventListener('visibilitychange', visible); };
}
