import { api } from '../../runtime/http';
import { chefStore } from '../../runtime/store';
import { type Action, type Json, type View, type Timer, clone, changedValues, compareVersions, makeAction, validView, timerClock, workspaceTimers, timerPresentation, completionText } from './protocol';

export type Draft = Json & { kind: string; baseRevision: number; conflict?: boolean };
type PendingAction = { request: Action; kind?: string; clearDraft?: string; continuation?: string; requestId?: string };
export type Entry = { snapshot: Json | null; views: View[]; drafts: Record<string, Draft>; loading: boolean; saving: boolean; running: boolean; error: string; activeKind: string | null; failed: PendingAction | null; conflict: PendingAction | null; pending: Json | null; acknowledged: string[]; timerStates: Record<string, string>; clock: () => number; version: number; completion: Json | null };
export type TimerEvent = { sequence: number; sessionId: string; timerId?: string; reason: 'started' | 'expired' | 'inspect' };
type State = { entries: Record<string, Entry>; reminders: { sessionId: string; timer: Timer }[]; tick: number; timerEvent: TimerEvent | null };
const storageKey = (id: string) => `chefmate:react-workspace:${id}`;
function readSaved(id: string): Json { try { return JSON.parse(localStorage.getItem(storageKey(id)) || '{}'); } catch { return {}; } }
const defaultEntry = (id: string): Entry => { const saved = readSaved(id); return { snapshot: null, views: [], drafts: saved.drafts || {}, loading: false, saving: false, running: false, error: '', activeKind: null, failed: saved.failed || null, conflict: null, pending: null, acknowledged: saved.acknowledged || [], timerStates: {}, clock: () => Date.now(), version: 0, completion: saved.completion || null }; };
export class WorkspaceStore {
  private state: State = { entries: {}, reminders: [], tick: 0, timerEvent: null };
  private listeners = new Set<() => void>();
  private loads = new Map<string, Promise<Json | null>>();
  private refreshAfterLoad = new Set<string>();
  private removed = new Set<string>();
  private notify: (text: string) => void = () => {};
  private timer: ReturnType<typeof setInterval> | null = null;
  private mounts = 0;
  private audio: AudioContext | null = null;
  constructor(private request = api, private continueTurn = (id: string, message: string, requestId: string) => { void chefStore.enqueueContinuation(id, message, requestId); }) {}
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => this.listeners.delete(listener); };
  entry(id: string) { if (this.removed.has(id)) return defaultEntry(id); if (!this.state.entries[id]) this.state = { ...this.state, entries: { ...this.state.entries, [id]: defaultEntry(id) } }; return this.state.entries[id]; }
  private emit(id?: string) {
    if (id && this.removed.has(id)) return;
    if (id) { const entry = this.entry(id); this.state = { ...this.state, entries: { ...this.state.entries, [id]: { ...entry, drafts: { ...entry.drafts }, version: entry.version + 1 } } }; this.persist(id); }
    else this.state = { ...this.state };
    this.listeners.forEach(listener => listener());
  }
  private persist(id: string) { const entry = this.entry(id); try { localStorage.setItem(storageKey(id), JSON.stringify({ drafts: entry.drafts, failed: entry.failed, acknowledged: entry.acknowledged.slice(-100), completion: entry.completion })); } catch { /* State remains in memory when storage is unavailable. */ } }
  initialize(notify?: (text: string) => void) {
    if (notify) this.notify = notify;
    this.mounts++;
    if (!this.timer) {
      this.timer = setInterval(() => this.tick(), 1000);
      document.addEventListener('pointerdown', this.unlockAudio, { passive: true });
      document.addEventListener('keydown', this.unlockAudio);
      document.addEventListener('visibilitychange', this.onVisible);
      window.addEventListener('focus', this.onVisible);
    }
    return () => {
      this.mounts = Math.max(0, this.mounts - 1);
      if (this.mounts) return;
      if (this.timer) clearInterval(this.timer);
      this.timer = null;
      document.removeEventListener('pointerdown', this.unlockAudio);
      document.removeEventListener('keydown', this.unlockAudio);
      document.removeEventListener('visibilitychange', this.onVisible);
      window.removeEventListener('focus', this.onVisible);
      void this.audio?.close().catch(() => {}); this.audio = null;
    };
  }
  private onVisible = () => { if (document.visibilityState === 'hidden') return; this.tick(); for (const [id, entry] of Object.entries(this.state.entries)) if (workspaceTimers(entry.views).some(timer => ['running', 'paused'].includes(timer.status))) void this.load(id, true).catch(() => {}); };
  private unlockAudio = () => {
    try { if (!globalThis.AudioContext) return; this.audio ||= new AudioContext(); if (this.audio.state === 'suspended') void this.audio.resume().catch(() => {}); } catch { /* Audio is optional. */ }
  };
  private sound() {
    try {
      if (JSON.parse(localStorage.getItem('chefmate:kitchen-preferences') || '{}').timer_sound === false || !this.audio || this.audio.state !== 'running') return;
      const audio = this.audio;
      for (const [offset, frequency] of [[0, 660], [.23, 880], [.95, 660], [1.18, 880]]) {
        const oscillator = audio.createOscillator(), gain = audio.createGain(), at = audio.currentTime + offset + .02;
        oscillator.type = 'sine'; oscillator.frequency.value = frequency; gain.gain.setValueAtTime(0, at); gain.gain.linearRampToValueAtTime(.14, at + .025); gain.gain.exponentialRampToValueAtTime(.001, at + .35);
        oscillator.connect(gain); gain.connect(audio.destination); oscillator.start(at); oscillator.stop(at + .37); oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); };
      }
    } catch { /* A blocked audio device must not stop the reminder. */ }
  }
  tick() {
    let hasTimers = false, chime = false; const reminders = [...this.state.reminders];
    for (const [sessionId, entry] of Object.entries(this.state.entries)) for (const timer of workspaceTimers(entry.views)) {
      hasTimers = true; const display = timerPresentation(timer, entry.clock()), previous = entry.timerStates[timer.id];
      if (display.expired && !entry.acknowledged.includes(timer.id) && previous !== 'expired' && !reminders.some(item => item.sessionId === sessionId && item.timer.id === timer.id)) { reminders.push({ sessionId, timer }); this.notify(`${timer.label}时间到，看看锅里的状态。`); this.timerEvent(sessionId, timer.id, 'expired'); chime = true; }
      entry.timerStates[timer.id] = display.state;
    }
    if (chime) this.sound();
    if (hasTimers || reminders.length !== this.state.reminders.length) { this.state = { ...this.state, reminders, tick: this.state.tick + 1 }; this.emit(); }
  }
  private timerEvent(sessionId: string, timerId: string | undefined, reason: TimerEvent['reason']) { this.state = { ...this.state, timerEvent: { sequence: (this.state.timerEvent?.sequence || 0) + 1, sessionId, timerId, reason } }; }
  openTimers(sessionId: string, timerId?: string) { this.timerEvent(sessionId, timerId, 'inspect'); this.emit(); }
  acknowledge(id: string, timerId: string) { const entry = this.entry(id); entry.acknowledged = [...new Set([...entry.acknowledged, timerId])]; this.state = { ...this.state, reminders: this.state.reminders.filter(item => item.sessionId !== id || item.timer.id !== timerId) }; this.emit(id); }
  setRunning(id: string, running: boolean) { if (this.removed.has(id)) return; const entry = this.entry(id); if (entry.running === running) return; entry.running = running; if (!running && entry.pending) { const response = entry.pending; entry.pending = null; this.receive(id, response); } this.emit(id); }
  pendingQuestion(id: string) { if (this.removed.has(id)) return null; const entry = this.entry(id); return entry.running ? null : entry.snapshot?.pending_question || null; }
  receive(id: string, response: Json, immediate = false, userKind?: string) {
    if (this.removed.has(id)) return false;
    const entry = this.entry(id), snapshot = response.workspace || response.ui_snapshot;
    if (!snapshot || !Number.isInteger(snapshot.revision) || entry.snapshot && compareVersions(snapshot, entry.snapshot) < 0) return false;
    const received = response._received ?? performance.now();
    if (entry.running) {
      const pending = entry.pending?.workspace || entry.pending?.ui_snapshot;
      if (!pending || compareVersions(snapshot, pending) >= 0) entry.pending = { ...clone(response), _received: received };
      if (!immediate) return false;
    }
    const hadSnapshot = Boolean(entry.snapshot), oldTimerIds = new Set(workspaceTimers(entry.views).map(timer => timer.id));
    const incoming = (response.views || []).filter(validView) as View[];
    const serverNow = incoming.map(view => view.data.server_now).filter(Boolean).sort().at(-1);
    if (serverNow) entry.clock = timerClock(serverNow, () => performance.now(), received);
    const visible = entry.running ? incoming.filter(view => entry.views.some(old => old.id === view.id) || view.kind === userKind) : incoming;
    entry.views = entry.running ? [...entry.views.filter(old => !visible.some(view => view.id === old.id || userKind && old.kind === userKind && view.kind === userKind)), ...clone(visible)] : clone(visible);
    entry.snapshot = clone(snapshot);
    if (response.cooking_intake) entry.completion = clone(response.cooking_intake);
    if (snapshot.stage !== 'completed') entry.completion = null;
    const introduced = workspaceTimers(entry.views).filter(timer => !oldTimerIds.has(timer.id) && ['running', 'paused'].includes(timer.status));
    if (hadSnapshot && introduced.length && !entry.saving && !entry.running) this.timerEvent(id, introduced.at(-1)!.id, 'started');
    for (const [oldId, draft] of Object.entries(entry.drafts)) {
      if (draft.kind === 'question') continue;
      if (!entry.views.some(view => view.id === oldId)) { const replacement = entry.views.find(view => view.kind === draft.kind); if (replacement) { entry.drafts[replacement.id] = draft; delete entry.drafts[oldId]; } }
      if (draft.baseRevision !== snapshot.revision) draft.conflict = true;
    }
    if (entry.failed && !entry.saving && entry.failed.request.type !== 'complete_meal' && snapshot.recent_actions?.some((action: Json) => action.action_id === entry.failed!.request.action_id)) {
      const pending = entry.failed; entry.failed = null; entry.error = '';
      if (pending.request.type === 'change_servings') {
        const view = entry.views.find(view => view.kind === 'meal_plan'), draft = view && entry.drafts[view.id];
        const { recipe_id: recipeId, servings } = pending.request.payload;
        if (view && draft && draft.servings && draft.servings[recipeId] === servings) {
          delete draft.servings[recipeId]; draft.baseline = { ...(draft.baseline || {}), [recipeId]: servings };
          draft.baseRevision = snapshot.revision; draft.conflict = false;
          if (!Object.keys(draft.servings).length) delete entry.drafts[view.id];
        }
      }
      if (pending.clearDraft) { delete entry.drafts[pending.clearDraft]; const replacement = entry.views.find(view => view.kind === pending.kind); if (replacement) delete entry.drafts[replacement.id]; }
      if (pending.continuation) queueMicrotask(() => this.continueTurn(id, pending.continuation!, pending.requestId || pending.request.action_id));
    }
    this.emit(id); return true;
  }
  load(id: string, refresh = false): Promise<Json | null> {
    if (this.removed.has(id)) return Promise.resolve(null);
    const current = this.loads.get(id);
    if (current) { if (refresh) this.refreshAfterLoad.add(id); return current; }
    const entry = this.entry(id); entry.loading = true; this.emit(id);
    const task = (async () => {
      try {
        // A final event can arrive while an earlier tool's read is in flight.
        // Keep the shared promise pending until a read started after that event
        // has completed; merely joining the old read could miss the final view.
        do {
          this.refreshAfterLoad.delete(id);
          const response = await this.request(`/api/sessions/${encodeURIComponent(id)}/workspace`);
          this.receive(id, response);
        } while (this.refreshAfterLoad.has(id) && !this.removed.has(id));
        return this.entry(id).snapshot;
      }
      catch (error) { if (!this.removed.has(id)) this.entry(id).error = error instanceof Error ? error.message : '无法读取当前任务。'; throw error; }
      finally { this.loads.delete(id); if (!this.removed.has(id)) { this.entry(id).loading = false; this.emit(id); } }
    })();
    this.loads.set(id, task); return task;
  }
  setActive(id: string, kind: string) { this.entry(id).activeKind = kind; this.emit(id); }
  draft(id: string, view: View, patch: Json) {
    const entry = this.entry(id), old = entry.drafts[view.id];
    const next: Draft = { ...old, kind: view.kind, baseRevision: old?.baseRevision ?? view.revision, conflict: old?.conflict || false, ...patch };
    const clean = view.kind === 'preference_form' && !Object.keys(changedValues(next.baseline || view.data.values || {}, next.values || {})).length;
    if (clean) delete entry.drafts[view.id]; else entry.drafts[view.id] = next;
    this.emit(id);
  }
  questionDraft(id: string, question: Json, patch: Json = {}) { const entry = this.entry(id), key = `question:${question.id}`; entry.drafts[key] = { optionIds: [], text: '', ...entry.drafts[key], kind: 'question', baseRevision: entry.drafts[key]?.baseRevision ?? entry.snapshot?.revision ?? 0, ...patch }; this.emit(id); }
  discard(id: string, viewId: string) { const entry = this.entry(id); delete entry.drafts[viewId]; if (entry.conflict?.clearDraft === viewId) entry.conflict = null; if (!entry.failed && !entry.conflict) entry.error = ''; this.emit(id); }
  discardConflict(id: string) { const entry = this.entry(id); entry.conflict = null; entry.error = ''; this.emit(id); }
  async dispatch(id: string, type: string, payload: Json, view?: View, options: { revision?: number; clearDraft?: string; request?: Action; continuation?: string; requestId?: string } = {}) {
    if (this.removed.has(id)) throw new Error('这个会话已经移除。');
    const entry = this.entry(id); if (entry.saving) throw new Error('任务正在同步，请稍等。');
    if (entry.failed && !options.request) throw new Error('上次操作的保存结果尚未确认，请先重新同步。');
    const request = options.request || makeAction(type, payload, options.revision ?? entry.snapshot?.revision ?? view?.revision, view?.id);
    const pending: PendingAction = { request, kind: view?.kind, clearDraft: options.clearDraft, continuation: options.continuation, requestId: options.requestId };
    // Persist the exact request before starting I/O, so a reload during saving
    // can reconcile or retry the same action rather than applying it twice.
    entry.saving = true; entry.failed = pending; entry.error = ''; entry.conflict = null; this.emit(id);
    let succeeded = false;
    try {
      const response = await this.request(`/api/sessions/${encodeURIComponent(id)}/actions`, { method: 'POST', body: JSON.stringify(request) });
      this.receive(id, response, true, type === 'open_recipe_detail' ? 'recipe_detail' : type === 'start_timer' ? 'timers' : undefined);
      const latest = this.entry(id);
      if (response.status !== 'ok') {
        const stale = ['revision_conflict', 'stale_view'].includes(response.status);
        if (stale) { latest.conflict = pending; if (options.clearDraft && latest.drafts[options.clearDraft]) latest.drafts[options.clearDraft].conflict = true; }
        latest.failed = null; latest.error = stale ? '任务已变化，草稿已保留。请复核当前内容后重试。' : response.message || '操作没有保存，请检查输入。';
        throw Object.assign(new Error(latest.error), { knownRejected: true });
      }
      latest.failed = null;
      if (type === 'complete_meal') this.notify(completionText(response.cooking_intake));
      if (type === 'start_timer') {
        const timer = [...workspaceTimers(latest.views)].reverse().find(timer => ['running', 'paused'].includes(timer.status)
          && (payload.step_id ? timer.recipe_id === payload.recipe_id && timer.step_id === payload.step_id : timer.label === payload.label && timer.seconds === payload.seconds));
        if (timer) this.timerEvent(id, timer.id, 'started');
      }
      if (options.clearDraft) { delete latest.drafts[options.clearDraft]; const migrated = latest.views.find(item => item.kind === view?.kind); if (migrated && migrated.id !== options.clearDraft) delete latest.drafts[migrated.id]; }
      if (type === 'open_recipe_detail') latest.activeKind = 'recipe_detail';
      succeeded = true; return response;
    } catch (error: any) {
      const latest = this.entry(id);
      if (error.status === 409 || error.data?.status === 'revision_conflict') { if (error.data) this.receive(id, error.data, true); const current = this.entry(id); current.conflict = pending; current.failed = null; current.error = '任务已变化，草稿已保留。请复核当前内容后重试。'; if (!error.data?.workspace) await this.load(id).catch(() => {}); }
      else if (!error.knownRejected) { latest.error = error.message || '任务同步失败，请重试。'; latest.failed = !error.status || error.status >= 500 ? pending : null; }
      throw error;
    } finally { if (!this.removed.has(id)) { this.entry(id).saving = false; if (succeeded && pending.continuation) this.continueTurn(id, pending.continuation, pending.requestId || request.action_id); this.emit(id); } }
  }
  retryFailed(id: string) { const pending = this.entry(id).failed; if (!pending) return Promise.resolve(); return this.dispatch(id, pending.request.type, pending.request.payload, undefined, { ...pending, request: pending.request }); }
  retryConflict(id: string) { const entry = this.entry(id), pending = entry.conflict; if (!pending) return Promise.resolve(); const view = entry.views.find(view => view.id === pending.request.view_id) || entry.views.find(view => view.kind === pending.kind); return this.dispatch(id, pending.request.type, pending.request.payload, view, { revision: entry.snapshot?.revision, clearDraft: pending.clearDraft, continuation: pending.continuation, requestId: pending.requestId }); }
  async savePreferences(id: string, view: View, continuation?: string) { const entry = this.entry(id), draft = entry.drafts[view.id]; if (!draft) return; return this.dispatch(id, 'update_preferences', { values: changedValues(draft.baseline || view.data.values || {}, draft.values) }, view, { revision: draft.conflict ? entry.snapshot?.revision : draft.baseRevision, clearDraft: view.id, continuation }); }
  async prepareTurn(id: string) {
    await this.load(id, true); const entry = this.entry(id);
    if (entry.saving || entry.failed || entry.conflict) throw new Error('请先确认当前任务的保存结果，再发送消息。');
    const drafts = Object.entries(entry.drafts).filter(([, draft]) => !['timers', 'question'].includes(draft.kind));
    if (drafts.some(([, draft]) => draft.kind !== 'preference_form' || draft.conflict)) throw new Error('请先保存或放弃工作区中的修改，再发送消息。');
    for (const [viewId] of drafts) { const view = this.entry(id).views.find(view => view.id === viewId); if (!view?.editable) throw new Error('条件表单已变化，请先复核。'); await this.savePreferences(id, view); }
    if (drafts.length) await this.load(id);
    if (!this.entry(id).snapshot) throw new Error('任务快照尚未读取，请刷新后重试。');
    return clone(this.entry(id).snapshot);
  }
  remove(id: string) { this.removed.add(id); this.refreshAfterLoad.delete(id); const entries = { ...this.state.entries }; delete entries[id]; this.state = { ...this.state, entries, reminders: this.state.reminders.filter(item => item.sessionId !== id) }; try { localStorage.removeItem(storageKey(id)); } catch { /* Optional storage. */ } this.emit(); }
}
export const workspaceStore = new WorkspaceStore();
export function initializeWorkspace(options: { notify?: (text: string) => void } = {}) {
  chefStore.setWorkspaceAdapter({ prepareTurn: id => workspaceStore.prepareTurn(id), refresh: async id => { await workspaceStore.load(id, true); }, setRunning: (id, running) => workspaceStore.setRunning(id, running), pendingQuestion: id => workspaceStore.pendingQuestion(id), remove: id => workspaceStore.remove(id) });
  return workspaceStore.initialize(options.notify);
}
