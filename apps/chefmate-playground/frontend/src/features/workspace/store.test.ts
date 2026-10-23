import { beforeEach, describe, expect, it, vi } from 'vitest';
import { WorkspaceStore } from './store';
import { answerPayload, makeAction, messageViews, safeReference, timerClock, timerPresentation, manualTimerSeconds, type View } from './protocol';

const preference = (revision = 1, id = 'preferences'): View => ({ id, kind: 'preference_form', revision, editable: true, data: { values: { servings: 2 }, fields: [{ name: 'servings', input_type: 'number', label: '用餐人数' }] } });
const response = (revision: number, views: View[] = [preference(revision)], extra = {}) => ({ status: 'ok', workspace: { revision, stage: 'planning', selected_recipe_ids: [], recent_actions: [], ...extra }, views });
describe('workspace transactions', () => {
  beforeEach(() => localStorage.clear());
  it('keeps draft values when a newer server revision replaces the form and requires review', async () => {
    const request = vi.fn().mockResolvedValue(response(2, [preference(2, 'new-form')])), store = new WorkspaceStore(request);
    store.receive('s', response(1)); store.draft('s', preference(), { baseline: { servings: 2 }, values: { servings: 4 } });
    await store.load('s');
    expect(store.entry('s').drafts['new-form']).toMatchObject({ values: { servings: 4 }, baseRevision: 1, conflict: true });
    await expect(store.prepareTurn('s')).rejects.toThrow('保存或放弃');
    expect(request.mock.calls.every(call => call[0].endsWith('/workspace'))).toBe(true);
  });
  it('does not expose new model forms until the turn settles, including reconnect reads', () => {
    const store = new WorkspaceStore(); store.receive('s', response(1)); store.setRunning('s', true);
    store.receive('s', response(2, [preference(2), { id: 'new-choice', kind: 'recipe_choices', revision: 2, editable: true, data: { recipes: [] } }]));
    expect(store.entry('s').snapshot?.revision).toBe(1); expect(store.entry('s').views).toHaveLength(1);
    store.setRunning('s', false); expect(store.entry('s').snapshot?.revision).toBe(2); expect(store.entry('s').views).toHaveLength(2);
  });
  it('refetches after a final event when a tool read is still in flight', async () => {
    let completeToolRead!: (value: any) => void;
    const request = vi.fn().mockImplementationOnce(() => new Promise(resolve => { completeToolRead = resolve; })).mockResolvedValueOnce(response(3, [preference(3), { id: 'final', kind: 'recipe_choices', revision: 3, data: { recipes: [] } }]));
    const store = new WorkspaceStore(request); store.receive('s', response(1)); store.setRunning('s', true);
    const toolRead = store.load('s', true); store.setRunning('s', false);
    const finalRead = store.load('s', true); completeToolRead(response(2)); await Promise.all([toolRead, finalRead]);
    expect(request).toHaveBeenCalledTimes(2); expect(store.entry('s').snapshot?.revision).toBe(3); expect(store.entry('s').views.some(view => view.id === 'final')).toBe(true);
  });
  it('reveals only an explicitly requested detail while a model turn is running', async () => {
    const detail = { id: 'detail', kind: 'recipe_detail', revision: 2, editable: false, data: { recipe: { name: '鸡蛋' } } };
    const request = vi.fn().mockResolvedValue(response(2, [preference(2), detail, { id: 'secret', kind: 'recipe_choices', revision: 2, data: {} }]));
    const store = new WorkspaceStore(request); store.receive('s', response(1)); store.setRunning('s', true);
    await store.dispatch('s', 'open_recipe_detail', { recipe_id: 'egg' }, preference());
    expect(store.entry('s').views.map(view => view.id)).toEqual(['preferences', 'detail']);
    store.setRunning('s', false); expect(store.entry('s').views).toHaveLength(3);
  });
  it('retries an uncertain answer with the identical action and continues only after saving', async () => {
    const continued = vi.fn(), request = vi.fn().mockRejectedValueOnce(new TypeError('network interrupted')).mockResolvedValueOnce(response(2, [], { pending_question: null }));
    const store = new WorkspaceStore(request, continued); store.receive('s', response(1)); store.questionDraft('s', { id: 'q' }, { optionIds: ['yes'] });
    await expect(store.dispatch('s', 'answer_question', { question_id: 'q', option_ids: ['yes'], text: '' }, undefined, { clearDraft: 'question:q', continuation: '继续', requestId: 'answer:q' })).rejects.toThrow('network interrupted');
    expect(continued).not.toHaveBeenCalled(); expect(store.entry('s').drafts['question:q']).toBeDefined();
    const first = JSON.parse(request.mock.calls[0][1].body); await store.retryFailed('s');
    expect(JSON.parse(request.mock.calls[1][1].body)).toEqual(first);
    expect(continued).toHaveBeenCalledExactlyOnceWith('s', '继续', 'answer:q'); expect(store.entry('s').drafts['question:q']).toBeUndefined();
  });
  it('reconciles an accepted action after a network failure without applying it again', async () => {
    const continued = vi.fn(), request = vi.fn().mockRejectedValueOnce(new Error('lost response')); const store = new WorkspaceStore(request, continued);
    store.receive('s', response(1));
    await expect(store.dispatch('s', 'select_recipes', { recipe_ids: ['egg'] }, preference(), { continuation: '已选鸡蛋' })).rejects.toThrow();
    const actionId = store.entry('s').failed!.request.action_id;
    store.receive('s', response(2, [], { recent_actions: [{ action_id: actionId }] })); await Promise.resolve();
    expect(request).toHaveBeenCalledTimes(1); expect(continued).toHaveBeenCalledExactlyOnceWith('s', '已选鸡蛋', actionId);
    store.receive('s', response(2, [], { recent_actions: [{ action_id: actionId }] })); await Promise.resolve(); expect(continued).toHaveBeenCalledTimes(1);
  });
  it('uses a new action after explicit conflict review and keeps the original draft', async () => {
    const request = vi.fn().mockResolvedValueOnce({ ...response(3), status: 'revision_conflict' }).mockResolvedValueOnce(response(4));
    const store = new WorkspaceStore(request); store.receive('s', response(1)); store.draft('s', preference(), { baseline: { servings: 2 }, values: { servings: 5 } });
    await expect(store.savePreferences('s', preference())).rejects.toThrow('任务已变化');
    expect(store.entry('s').drafts.preferences.values.servings).toBe(5);
    const original = JSON.parse(request.mock.calls[0][1].body); await store.retryConflict('s'); const reviewed = JSON.parse(request.mock.calls[1][1].body);
    expect(reviewed.base_revision).toBe(3); expect(reviewed.action_id).not.toBe(original.action_id); expect(store.entry('s').drafts.preferences).toBeUndefined();
  });
  it('saves a clean preference draft before returning the authoritative snapshot for a user message', async () => {
    const request = vi.fn().mockResolvedValueOnce(response(1)).mockResolvedValueOnce(response(2, [preference(2)])).mockResolvedValueOnce(response(2, [preference(2)]));
    const store = new WorkspaceStore(request); store.receive('s', response(1)); store.draft('s', preference(), { baseline: { servings: 2 }, values: { servings: 3 } });
    const snapshot = await store.prepareTurn('s'); expect(snapshot?.revision).toBe(2); expect(JSON.parse(request.mock.calls[1][1].body).payload).toEqual({ values: { servings: 3 } }); expect(store.entry('s').drafts.preferences).toBeUndefined();
  });
  it('rejects stale snapshots and remembers timer acknowledgements across a remount', () => {
    const store = new WorkspaceStore(); store.receive('s', response(5)); store.receive('s', response(2)); expect(store.entry('s').snapshot?.revision).toBe(5);
    store.acknowledge('s', 'timer'); const next = new WorkspaceStore(); expect(next.entry('s').acknowledged).toContain('timer');
  });
  it('reminds once for an expired background timer and does not repeat after acknowledgement', () => {
    const store = new WorkspaceStore(); const timer = { id: 'timer', label: '汤', status: 'running', seconds: 30, deadline_at: '2026-01-01T00:00:30Z' }; let now = Date.parse('2026-01-01T00:00:29Z');
    store.receive('background', response(1, [{ id: 'timers', kind: 'timers', revision: 1, data: { timers: [timer] } }])); store.entry('background').clock = () => now;
    store.tick(); expect(store.getSnapshot().reminders).toHaveLength(0); now += 2000; store.tick(); store.tick(); expect(store.getSnapshot().reminders).toHaveLength(1);
    expect(store.getSnapshot().reminders[0].sessionId).toBe('background'); store.acknowledge('background', 'timer'); store.tick(); expect(store.getSnapshot().reminders).toHaveLength(0);
  });
  it('persists the idempotent request before the network resolves so a reload can recover it', async () => {
    let resolve!: (value: any) => void; const request = vi.fn().mockImplementation(() => new Promise(done => { resolve = done; })), store = new WorkspaceStore(request);
    store.receive('s', response(1)); const saving = store.dispatch('s', 'start_timer', { seconds: 30, label: '厨房计时' });
    const reloaded = new WorkspaceStore(); expect(reloaded.entry('s').failed?.request).toEqual(JSON.parse(request.mock.calls[0][1].body));
    resolve(response(2)); await saving;
  });
  it('does not resurrect a removed session from an outstanding workspace read', async () => {
    let resolve!: (value: any) => void; const request = vi.fn().mockImplementation(() => new Promise(done => { resolve = done; })), store = new WorkspaceStore(request);
    const loading = store.load('removed'); store.remove('removed'); resolve(response(2)); await loading;
    expect(store.getSnapshot().entries.removed).toBeUndefined(); expect(localStorage.getItem('chefmate:react-workspace:removed')).toBeNull();
  });
  it('deduplicates the global timer interval and cleans it only after the final owner unmounts', () => {
    vi.useFakeTimers(); const store = new WorkspaceStore(), before = vi.getTimerCount(), first = store.initialize(), second = store.initialize();
    expect(vi.getTimerCount()).toBe(before + 1); first(); expect(vi.getTimerCount()).toBe(before + 1); second(); expect(vi.getTimerCount()).toBe(before); vi.useRealTimers();
  });
});
describe('workspace protocol boundaries', () => {
  it('uses monotonic elapsed time after a server clock sync and respects paused timers', () => {
    let elapsed = 120; const clock = timerClock('2026-01-01T00:00:00Z', () => elapsed); elapsed = 3120;
    expect(clock()).toBe(Date.parse('2026-01-01T00:00:03Z'));
    expect(timerPresentation({ id: 'p', label: '暂停', status: 'paused', seconds: 10, remaining_seconds: 5, deadline_at: '2000-01-01T00:00:00Z' }, clock())).toMatchObject({ remaining: 5, expired: false, clock: '0:05' });
    expect(manualTimerSeconds(0, 30)).toBe(30); expect(manualTimerSeconds(0, 60)).toBeNull(); expect(manualTimerSeconds(1440, 1)).toBeNull();
  });
  it('filters unsafe links and unknown protocol actions rather than executing them', () => {
    expect(safeReference('javascript:alert(1)')).toBeNull(); expect(safeReference('https://a:b@example.org')).toBeNull(); expect(safeReference('https://example.org/path')).toBe('https://example.org/path');
    expect(() => makeAction('change_stage', { stage: 'completed' }, 1)).toThrow();
  });
  it('keeps model forms hidden for streaming/recovering messages and deduplicates revisions', () => {
    const actions = [1, 3, 2].map(revision => ({ kind: 'show_component', component: preference(revision) }));
    expect(messageViews({ actions, streaming: true })).toEqual([]); expect(messageViews({ actions, recovering: true })).toEqual([]); expect(messageViews({ actions })).toHaveLength(1); expect(messageViews({ actions })[0].revision).toBe(3);
  });
  it('validates answers with allowed options and custom text rules', () => {
    const question = { id: 'q', options: [{ id: 'yes' }], multiple: false, allow_custom: false };
    expect(() => answerPayload(question, { optionIds: ['forged'], text: 'ignored' })).toThrow();
    expect(answerPayload(question, { optionIds: ['yes', 'yes'], text: 'ignored' })).toEqual({ question_id: 'q', option_ids: ['yes'], text: '' });
  });
});
