import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { HttpError } from '../../runtime/http';
import { initializeUiPreferences, readUiPreferences, resetUiPreferenceCache, saveUiPreferences } from './client';

const fixture = vi.hoisted(() => ({ api: vi.fn(), appearance: vi.fn(), listener: null as null | (() => void), listeners: new Set<() => void>(), state: { runs: {} as Record<string, any>, cache: {} as Record<string, any> }, loadStatus: vi.fn(async () => {}) }));
vi.mock('../../runtime/http', async () => ({ ...await vi.importActual('../../runtime/http'), api: fixture.api }));
vi.mock('../../runtime/store', () => ({ chefStore: { subscribe: (listener: () => void) => { fixture.listeners.add(listener); fixture.listener = () => { for (const notify of fixture.listeners) notify(); }; return () => { fixture.listeners.delete(listener); }; }, getSnapshot: () => fixture.state, loadStatus: fixture.loadStatus } }));
vi.mock('../settings/appearance', () => ({ applyAppearance: fixture.appearance, readAppearance: () => ({ theme: 'dark', light_palette: 'blue', dark_palette: 'night', font_pairing: 'mixed', font_size: 'small', density: 'compact', inline_limit: 1, show_estimates: false, api_key: 'must-not-leave-browser' }) }));
const prefs = (revision: string, extra: Record<string, unknown> = {}) => ({ revision, initialized: true, values: { theme: 'light', light_palette: 'ink', dark_palette: 'graphite', font_pairing: 'mixed', font_size: 'medium', density: 'comfortable', inline_limit: 2, show_estimates: true, reply_suggestions: true, reply_suggestion_count: 3, ...extra } });
async function settled() { for (let i = 0; i < 30; i++) await Promise.resolve(); }
const cleanups: Array<() => void> = [];
function start() { const cleanup = initializeUiPreferences(); cleanups.push(cleanup); return cleanup; }
beforeEach(() => { resetUiPreferenceCache(); fixture.api.mockReset(); fixture.appearance.mockClear(); fixture.loadStatus.mockClear(); fixture.listeners.clear(); fixture.state = { runs: {}, cache: {} }; localStorage.clear(); });
afterEach(async () => { cleanups.splice(0).forEach(cleanup => cleanup()); await settled(); });

describe('safe application preference synchronization', () => {
  it('lets the next account read and save before a hung old account write finishes', async () => {
    let finishOld!: (data: unknown) => void;
    fixture.api.mockImplementationOnce(() => new Promise(resolve => {finishOld = resolve;}))
      .mockResolvedValue(prefs('new-account-ready', {theme: 'light'}));
    const old = saveUiPreferences({theme: 'dark'}, 'shared-base');
    const queuedOld = saveUiPreferences({font_pairing: 'serif'}, 'shared-base');
    const oldRejected = expect(old).rejects.toMatchObject({name: 'AbortError'});
    const queuedRejected = expect(queuedOld).rejects.toMatchObject({name: 'AbortError'});
    await settled(); resetUiPreferenceCache();
    expect((await readUiPreferences()).revision).toBe('new-account-ready');
    await saveUiPreferences({theme: 'light'}, 'new-base');
    expect(fixture.api).toHaveBeenCalledTimes(3);
    expect(fixture.appearance).toHaveBeenCalledOnce();
    finishOld(prefs('old-account-late', {theme: 'dark'}));
    await oldRejected; await queuedRejected;
    expect(fixture.api).toHaveBeenCalledTimes(3);
    expect(fixture.appearance).toHaveBeenLastCalledWith(expect.objectContaining({theme: 'light'}));
  });
  it('discards an old account response and never dispatches its queued write under the next cookie', async () => {
    let finishOld!: (data: unknown) => void;
    fixture.api.mockImplementationOnce(() => new Promise(resolve => {finishOld = resolve;}))
      .mockResolvedValue(prefs('new-account-save', {theme: 'light'}));
    const inFlight = saveUiPreferences({theme: 'dark'}, 'same-default-revision');
    const queued = saveUiPreferences({font_pairing: 'serif'}, 'same-default-revision');
    const oldRejected = expect(inFlight).rejects.toMatchObject({name: 'AbortError'});
    const queuedRejected = expect(queued).rejects.toMatchObject({name: 'AbortError'});
    await settled(); expect(fixture.api).toHaveBeenCalledOnce();
    resetUiPreferenceCache();
    const newAccount = saveUiPreferences({theme: 'light'}, 'new-account-base');
    finishOld(prefs('old-account-dark', {theme: 'dark'}));
    await oldRejected; await queuedRejected; await newAccount;
    expect(fixture.api).toHaveBeenCalledTimes(2);
    expect(fixture.api.mock.calls.map(([, options]) => JSON.parse(options.body).changes)).toEqual([
      {theme: 'dark'}, {theme: 'light'},
    ]);
    expect(fixture.appearance).toHaveBeenCalledOnce();
    expect(fixture.appearance).toHaveBeenLastCalledWith(expect.objectContaining({theme: 'light'}));
    expect(JSON.parse(localStorage.getItem('chefmate:ui-settings')!).theme).toBe('light');
  });
  it('does not let an old account read waiting for writes fetch preferences for the next account', async () => {
    let finishOld!: (data: unknown) => void;
    fixture.api.mockImplementationOnce(() => new Promise(resolve => {finishOld = resolve;}));
    const writing = saveUiPreferences({theme: 'dark'}, 'old-base');
    const reading = readUiPreferences();
    const writeRejected = expect(writing).rejects.toMatchObject({name: 'AbortError'});
    const readRejected = expect(reading).rejects.toMatchObject({name: 'AbortError'});
    await settled(); resetUiPreferenceCache(); finishOld(prefs('old-result'));
    await writeRejected; await readRejected;
    expect(fixture.api).toHaveBeenCalledOnce(); expect(fixture.appearance).not.toHaveBeenCalled();
  });
  it('does not seed or apply a disposed account initializer even before React cleanup runs', async () => {
    let finishOldRead!: (data: unknown) => void;
    fixture.api.mockImplementationOnce(() => new Promise(resolve => {finishOldRead = resolve;}));
    start(); await settled(); resetUiPreferenceCache();
    finishOldRead({...prefs('old-uninitialized'), initialized: false}); await settled();
    window.dispatchEvent(new Event('focus')); await settled();
    expect(fixture.api).toHaveBeenCalledOnce(); expect(fixture.appearance).not.toHaveBeenCalled();
    expect(localStorage.getItem('chefmate:ui-settings')).toBeNull();
  });
  it('seeds only appearance on first use and retains existing interaction preferences', async () => {
    fixture.api.mockResolvedValueOnce({ ...prefs('uninitialized'), initialized: false }).mockResolvedValue(prefs('seeded'));
    const cleanup = start(); await settled();
    const request = JSON.parse(fixture.api.mock.calls[1][1].body);
    expect(request.revision).toBe('uninitialized'); expect(request.changes.theme).toBe('dark');
    expect(request.changes.font_size).toBe('small');
    expect(request.changes).not.toHaveProperty('reply_suggestions'); expect(request.changes).not.toHaveProperty('api_key');
    expect(fixture.appearance).toHaveBeenCalledWith(expect.objectContaining({ theme: 'light' })); cleanup();
  });
  it('uses current server values after a competing initialization rather than overwriting them', async () => {
    fixture.api.mockResolvedValueOnce({ ...prefs('old'), initialized: false }).mockRejectedValueOnce(new HttpError('changed', 409, {})).mockResolvedValueOnce(prefs('winner', { theme: 'dark' }));
    const cleanup = start(); await settled();
    expect(fixture.api).toHaveBeenCalledTimes(3); expect(fixture.appearance).toHaveBeenLastCalledWith(expect.objectContaining({ theme: 'dark' })); cleanup();
  });
  it('saves only requested fields with a dedicated revision and rejects unsafe keys locally', async () => {
    fixture.api.mockResolvedValue(prefs('save')); await saveUiPreferences({ theme: 'dark' }, 'base');
    expect(JSON.parse(fixture.api.mock.calls[0][1].body)).toEqual({ revision: 'base', changes: { theme: 'dark' } });
    await expect(saveUiPreferences({ model: 'forbidden' }, 'base')).rejects.toThrow('只能修改'); expect(fixture.api).toHaveBeenCalledOnce();
  });
  it('persists and applies font size through the same server revision and local cache as other appearance values', async () => {
    fixture.api.mockResolvedValue(prefs('font-size-saved', { font_size: 'large' }));
    await saveUiPreferences({ font_size: 'large' }, 'font-size-base');
    expect(JSON.parse(fixture.api.mock.calls[0][1].body)).toEqual({ revision: 'font-size-base', changes: { font_size: 'large' } });
    expect(fixture.appearance).toHaveBeenLastCalledWith(expect.objectContaining({ font_size: 'large' }));
    expect(JSON.parse(localStorage.getItem('chefmate:ui-settings')!).font_size).toBe('large');
  });
  it('never applies historical tool snapshots and refreshes authority on a live preference tool completion', async () => {
    fixture.api.mockResolvedValueOnce(prefs('start')).mockResolvedValueOnce(prefs('live', { theme: 'dark' }));
    fixture.state.cache.a = { messages: [{ role: 'assistant', tool_calls: [{ name: 'update_app_preferences', call_id: 'history', result: { status: 'ok', preferences: prefs('stale') } }] }] };
    const cleanup = start(); await settled(); fixture.listener?.(); await settled(); expect(fixture.api).toHaveBeenCalledOnce();
    fixture.state.runs.a = {}; fixture.state.cache.a.messages.push({ streaming: true, tool_calls: [{ name: 'update_app_preferences', call_id: 'live', result: { status: 'ok', preferences: prefs('untrusted-old') } }] });
    fixture.listener?.(); await settled(); expect(fixture.api).toHaveBeenCalledTimes(2); expect(fixture.appearance).toHaveBeenLastCalledWith(expect.objectContaining({ theme: 'dark' }));
    fixture.listener?.(); await settled(); expect(fixture.api).toHaveBeenCalledTimes(2); cleanup();
  });
  it('queues a live change during an earlier read and ignores results after disposal', async () => {
    let finish!: (data: unknown) => void;
    fixture.api.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; })).mockResolvedValueOnce(prefs('queued'));
    const cleanup = start(); await settled(); fixture.state.runs.a = {}; fixture.state.cache.a = { messages: [{ streaming: true, tool_calls: [{ name: 'update_app_preferences', call_id: 'during-read', result: { status: 'ok' } }] }] };
    fixture.listener?.(); finish(prefs('before')); await settled(); expect(fixture.api).toHaveBeenCalledTimes(2); cleanup();
    fixture.api.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; })); const dispose = start(); await settled(); dispose(); fixture.appearance.mockClear(); finish(prefs('after-disposal')); await settled(); expect(fixture.appearance).not.toHaveBeenCalled();
  });
  it('strips unexpected server fields and rejects malformed snapshots', async () => {
    fixture.api.mockResolvedValueOnce(prefs('safe', { model: { api_key: 'must-not-apply' }, developer: true }));
    const data = await readUiPreferences(); expect(data.values).not.toHaveProperty('model'); expect(data.values).not.toHaveProperty('developer');
    fixture.api.mockResolvedValueOnce({ values: [] }); await expect(readUiPreferences()).rejects.toThrow('格式不正确');
  });
  it('discards a late pre-save GET and retains the saved appearance and revision', async () => {
    let finishOldRead!: (data: unknown) => void;
    const events: string[] = [], receive = (event: Event) => events.push((event as CustomEvent).detail.revision);
    window.addEventListener('chefmate:ui-preferences', receive);
    fixture.api.mockImplementationOnce(() => new Promise(resolve => { finishOldRead = resolve; })).mockResolvedValue(prefs('race-saved', { theme: 'dark' }));
    const cleanup = start(); await settled();
    await saveUiPreferences({ theme: 'dark' }, 'race-base');
    finishOldRead(prefs('race-old', { theme: 'light' })); await settled();
    expect(fixture.appearance).toHaveBeenLastCalledWith(expect.objectContaining({ theme: 'dark' }));
    expect(JSON.parse(localStorage.getItem('chefmate:ui-settings')!).theme).toBe('dark'); expect(events).not.toContain('race-old');
    const patches = fixture.api.mock.calls.filter(([, options]) => options?.method === 'PATCH');
    expect(patches).toHaveLength(1); expect(JSON.parse(patches[0][1].body).revision).toBe('race-base');
    cleanup(); window.removeEventListener('chefmate:ui-preferences', receive);
  });
  it('serializes writes without rebasing a second caller past a CAS conflict', async () => {
    let finishFirst!: (data: unknown) => void;
    fixture.api.mockImplementationOnce(() => new Promise(resolve => { finishFirst = resolve; })).mockRejectedValueOnce(new HttpError('changed', 409, {}));
    const first = saveUiPreferences({ theme: 'dark' }, 'shared-base');
    const second = saveUiPreferences({ font_pairing: 'serif' }, 'shared-base');
    const rejected = expect(second).rejects.toBeInstanceOf(HttpError);
    await settled(); expect(fixture.api).toHaveBeenCalledOnce();
    finishFirst(prefs('first-write', { theme: 'dark' })); await first; await rejected;
    expect(fixture.api).toHaveBeenCalledTimes(2);
    expect(fixture.api.mock.calls.map(([, options]) => JSON.parse(options.body))).toEqual([
      { revision: 'shared-base', changes: { theme: 'dark' } }, { revision: 'shared-base', changes: { font_pairing: 'serif' } },
    ]);
    expect(fixture.appearance).toHaveBeenLastCalledWith(expect.objectContaining({ theme: 'dark', font_pairing: 'mixed' }));
  });
  it('queues initialization patches with writes and never dispatches a disposed queued seed', async () => {
    let finishSeed!: (data: unknown) => void;
    fixture.api.mockResolvedValueOnce({ ...prefs('seed-base'), initialized: false }).mockResolvedValueOnce({ ...prefs('seed-base'), initialized: false })
      .mockImplementationOnce(() => new Promise(resolve => { finishSeed = resolve; })).mockResolvedValue(prefs('seed-result'));
    const keep = start(), dispose = start(); await settled();
    expect(fixture.api.mock.calls.filter(([, options]) => options?.method === 'PATCH')).toHaveLength(1);
    dispose(); finishSeed(prefs('seed-result')); await settled();
    expect(fixture.api.mock.calls.filter(([, options]) => options?.method === 'PATCH')).toHaveLength(1);
    expect(fixture.appearance).toHaveBeenLastCalledWith(expect.objectContaining({ theme: 'light' })); keep();
  });
  it('aborts an in-flight seed and does not apply its late response after disposal', async () => {
    let finishSeed!: (data: unknown) => void;
    fixture.api.mockResolvedValueOnce({ ...prefs('aborted-seed-base'), initialized: false })
      .mockImplementationOnce(() => new Promise(resolve => { finishSeed = resolve; }));
    const cleanup = start(); await settled();
    const signal = fixture.api.mock.calls[1][1].signal as AbortSignal;
    cleanup(); expect(signal.aborted).toBe(true);
    finishSeed(prefs('aborted-seed-result')); await settled();
    expect(fixture.appearance).not.toHaveBeenCalled(); expect(localStorage.getItem('chefmate:ui-settings')).toBeNull();
  });
  it.each([new Error('offline'), new HttpError('storage unavailable', 503, {})])('does not automatically reseed after initialization fails: %s', async error => {
    fixture.api.mockImplementation((_path, options) => options?.method === 'PATCH' ? Promise.reject(error)
      : Promise.resolve({ ...prefs('failed-seed-base'), initialized: false }));
    const cleanup = start(); await settled(); await settled();
    expect(fixture.api.mock.calls.filter(([, options]) => options?.method === 'PATCH')).toHaveLength(1);
    expect(fixture.api).toHaveBeenCalledTimes(3); expect(fixture.appearance).not.toHaveBeenCalled();
    window.dispatchEvent(new Event('focus')); await settled(); await settled();
    expect(fixture.api.mock.calls.filter(([, options]) => options?.method === 'PATCH')).toHaveLength(2);
    expect(fixture.api).toHaveBeenCalledTimes(6); cleanup();
  });
  it('refreshes after an observed run recovers to completion even without live tool events', async () => {
    fixture.api.mockResolvedValueOnce(prefs('run-start')).mockResolvedValue(prefs('run-finished', { theme: 'dark' }));
    const cleanup = start(); await settled();
    fixture.state.runs.a = { requestId: 'active-turn' }; fixture.state.cache.a = { running: true, messages: [{ streaming: true, tool_calls: [] }] };
    fixture.listener?.(); fixture.state.runs = {}; fixture.listener?.(); await settled(); expect(fixture.api).toHaveBeenCalledOnce();
    fixture.state.cache.a = { running: false, messages: [{ tool_calls: [{ name: 'update_app_preferences', result: { status: 'ok', preferences: prefs('never-apply-history') } }] }] };
    fixture.listener?.(); await settled(); expect(fixture.api).toHaveBeenCalledTimes(2);
    expect(fixture.appearance).toHaveBeenLastCalledWith(expect.objectContaining({ theme: 'dark' }));
    fixture.state.cache.old = { running: false, messages: [{ tool_calls: [{ name: 'update_app_preferences', result: { status: 'ok' } }] }] };
    fixture.listener?.(); await settled(); expect(fixture.api).toHaveBeenCalledTimes(2); cleanup();
  });
});
