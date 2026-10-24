import { afterEach, describe, expect, it } from 'vitest';
import { ChefStore } from './store';
import type { ApiData } from './types';

const json = (data: unknown) => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
const display = (action_id: string, mode = 'focus') => ({ kind: 'set_kitchen_display', action_id, mode, view_kind: 'prep_checklist' });
const reply = (...actions: ApiData[]) => ({ role: 'assistant', content: '准备好了', actions });
const stores: ChefStore[] = [];
afterEach(() => { stores.forEach(store => store.dispose()); stores.length = 0; });

function harness() {
  const sessions: Record<string, ApiData> = {
    a: { id: 'a', title: '晚餐', messages: [], last_status: 'idle' },
    b: { id: 'b', title: '午餐', messages: [], last_status: 'idle' },
  };
  let controller!: ReadableStreamDefaultController<Uint8Array>, requestId = '';
  const fetcher = (async (path: string, options: RequestInit = {}) => {
    if (path === '/api/status') return json({ ready: true });
    if (path.startsWith('/api/sessions?')) return json({ items: [], next_cursor: null });
    if (path.endsWith('/workspace')) return json({ ui_snapshot: { revision: 1, stage: 'prep' } });
    if (path.endsWith('/turn')) {
      requestId = JSON.parse(options.body as string).request_id;
      return new Response(new ReadableStream<Uint8Array>({ start(value) { controller = value; } }));
    }
    return json(sessions[path.split('/')[3]]);
  }) as typeof fetch;
  const store = new ChefStore({ fetch: fetcher, storage: null, attachments: { load: async () => ({}), save: async () => {} }, pollInterval: 60000 });
  stores.push(store);
  return {
    store, sessions,
    event: (event: ApiData) => controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`)),
    finish: (actions: ApiData[]) => {
      sessions.a = { ...sessions.a, messages: [reply(...actions)], last_status: 'completed', last_run: { request_id: requestId } };
      controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify({ type: 'result', result: { status: 'completed', text: '准备好了', actions } })}\n\n`));
      controller.enqueue(new TextEncoder().encode('data: {"type":"done","status":"completed"}\n\n'));
      controller.close();
    },
  };
}

describe('kitchen display actions across the conversation lifecycle', () => {
  it('never opens focus from initial history or newly discovered historical actions in a revisited idle chat', async () => {
    const { store, sessions } = harness();
    sessions.a.messages = [reply(display('historical-1'))];
    await store.start(); await store.openSession('a');
    expect(store.getSnapshot().kitchenDisplayRequest).toBeFalsy();
    await store.openSession('b');
    sessions.a.messages.push(reply(display('historical-2')));
    await store.openSession('a'); await store.refreshSession('a');
    expect(store.getSnapshot().kitchenDisplayRequest).toBeFalsy();
  });

  it('delivers a live action once and does not replay it from result, authority refresh or reopening', async () => {
    const h = harness(), action = display('live-1');
    await h.store.start(); await h.store.openSession('a');
    const delivered: string[] = [];
    const unsubscribe = h.store.subscribe(() => {
      const request = h.store.getSnapshot().kitchenDisplayRequest;
      if (request) { delivered.push(request.action_id); h.store.acknowledgeKitchenDisplay(request.action_id); }
    });
    const sending = h.store.sendMessage('请进入全屏备菜'); await flush();
    h.event({ type: 'ui_action', action }); await flush();
    expect(delivered).toEqual(['live-1']);
    h.event({ type: 'ui_action', action }); h.finish([action]); await sending;
    await h.store.refreshSession('a'); await h.store.openSession('b'); await h.store.openSession('a');
    expect(delivered).toEqual(['live-1']); expect(h.store.getSnapshot().kitchenDisplayRequest).toBeNull();
    unsubscribe();
  });

  it('consumes background actions without switching the visible chat or replaying when returning', async () => {
    const h = harness(), action = display('background-1');
    await h.store.start(); await h.store.openSession('a');
    const sending = h.store.sendMessage('请进入全屏备菜'); await flush();
    await h.store.openSession('b');
    h.event({ type: 'ui_action', action }); await flush();
    expect(h.store.getSnapshot().currentId).toBe('b');
    expect(h.store.getSnapshot().kitchenDisplayRequest).toBeFalsy();
    h.finish([action]); await sending; await h.store.openSession('a');
    expect(h.store.getSnapshot().kitchenDisplayRequest).toBeFalsy();
  });

  it.each([true, false])('recovers a newly completed action only for the visible owning chat (visible=%s)', async visible => {
    const h = harness();
    h.sessions.a = { ...h.sessions.a, last_status: 'running', running: true, messages: [reply(display('old-action'))] };
    await h.store.start(); await h.store.openSession('a');
    expect(h.store.getSnapshot().kitchenDisplayRequest).toBeFalsy();
    if (!visible) await h.store.openSession('b');
    h.sessions.a = { ...h.sessions.a, running: false, last_status: 'completed', messages: [...h.sessions.a.messages, reply(display('recovered-1'))] };
    await h.store.refreshSession('a');
    expect(h.store.getSnapshot().kitchenDisplayRequest?.action_id).toBe(visible ? 'recovered-1' : undefined);
    if (visible) h.store.acknowledgeKitchenDisplay('recovered-1');
    else await h.store.openSession('a');
    await h.store.refreshSession('a');
    expect(h.store.getSnapshot().kitchenDisplayRequest).toBeFalsy();
  });

  it('acknowledges only the matching pending action', async () => {
    const h = harness(), action = display('ack-1', 'conversation');
    await h.store.start(); await h.store.openSession('a');
    const sending = h.store.sendMessage('返回聊天'); await flush();
    h.event({ type: 'ui_action', action }); await flush();
    expect(h.store.getSnapshot().kitchenDisplayRequest).toEqual({ ...action, sessionId: 'a' });
    h.store.acknowledgeKitchenDisplay('some-other-action');
    expect(h.store.getSnapshot().kitchenDisplayRequest?.action_id).toBe('ack-1');
    h.store.acknowledgeKitchenDisplay('ack-1');
    expect(h.store.getSnapshot().kitchenDisplayRequest).toBeNull();
    h.finish([action]); await sending;
    expect(h.store.getSnapshot().kitchenDisplayRequest).toBeNull();
  });

  it('discards a pending display request immediately when leaving its session', async () => {
    const h = harness(), action = display('pending-1');
    await h.store.start(); await h.store.openSession('a');
    const sending = h.store.sendMessage('打开全屏'); await flush();
    h.event({ type: 'ui_action', action }); await flush();
    expect(h.store.getSnapshot().kitchenDisplayRequest?.action_id).toBe('pending-1');
    await h.store.openSession('b');
    expect(h.store.getSnapshot().kitchenDisplayRequest).toBeFalsy();
    await h.store.openSession('a');
    expect(h.store.getSnapshot().kitchenDisplayRequest).toBeFalsy();
    h.finish([action]); await sending;
  });
});
