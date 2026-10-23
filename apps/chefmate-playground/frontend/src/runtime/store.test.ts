import { afterEach, describe, expect, it, vi } from 'vitest';
import { ChefStore } from './store';
import type { ApiData, AttachmentPersistence, ImageDraft, StoreOptions } from './types';

const json = (data: any, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
const stream = (events: any[]) => new Response(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(''), { headers: { 'Content-Type': 'text/event-stream' } });
const deferred = <T,>() => { let resolve!: (value: T) => void; let reject!: (error: any) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flush = async () => { for (let index = 0; index < 30; index++) await Promise.resolve(); };
const stores: ChefStore[] = [];
afterEach(() => { stores.forEach(store => store.dispose()); stores.length = 0; vi.useRealTimers(); });

function memoryStorage() {
  const entries = new Map<string, string>();
  return { getItem: (key: string) => entries.get(key) || null, setItem: (key: string, value: string) => { entries.set(key, value); }, removeItem: (key: string) => { entries.delete(key); } };
}
function memoryAttachments(): AttachmentPersistence {
  let images: Record<string, ImageDraft[]> = {};
  return { load: async () => images, save: async value => { images = value; } };
}
function harness(handler: (path: string, options: RequestInit) => Response | Promise<Response> | undefined = () => undefined, options: StoreOptions = {}) {
  const requests: { path: string; options: RequestInit }[] = [];
  const fetcher = (async (path: string, init: RequestInit = {}) => {
    requests.push({ path, options: init });
    const result = handler(path, init); if (result) return result;
    if (path === '/api/status') return json({ ready: true, supports_images: true, max_images: 3 });
    if (path.startsWith('/api/sessions?')) return json({ items: [], next_cursor: null });
    if (path.endsWith('/workspace')) return json({ ui_snapshot: { revision: 1, stage: 'idea' } });
    if (path === '/api/sessions' && init.method === 'POST') return json({ id: 'created', title: '新对话', messages: [] });
    if (init.method === 'DELETE') return json({ deleted: true });
    return json({ id: path.split('/')[3], title: '对话', messages: [], last_status: 'idle' });
  }) as typeof fetch;
  const store = new ChefStore({ storage: memoryStorage(), attachments: memoryAttachments(), fetch: fetcher, ...options });
  stores.push(store);
  return { store, requests };
}
function controlledStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const response = new Response(new ReadableStream<Uint8Array>({ start(value) { controller = value; } }));
  return { response, event: (event: any) => controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`)), close: () => controller.close() };
}

describe('React conversation store lifecycle', () => {
  it('keeps stable snapshots and does not mutate snapshots already rendered by React', async () => {
    const connection = controlledStream(); let requestId = '';
    const { store } = harness((path, options) => {
      if (path.endsWith('/turn')) { requestId = JSON.parse(options.body as string).request_id; return connection.response; }
      if (path === '/api/sessions/a' && requestId) return json({ id: 'a', messages: [{ role: 'user', content: '煮蛋' }, { role: 'assistant', content: '权威结果' }], last_run: { request_id: requestId }, last_status: 'completed' });
    });
    expect(store.getSnapshot()).toBe(store.getSnapshot());
    await store.start(); await store.openSession('a'); store.setDraft('煮蛋');
    const sending = store.sendMessage(); await flush();
    const before = store.getSnapshot(), message = before.session!.messages.at(-1)!;
    connection.event({ type: 'assistant_delta', text: '先烧水' }); await flush();
    expect(message.content).toBe(''); expect(store.getSnapshot().session!.messages.at(-1)!.content).toBe('先烧水');
    expect(store.getSnapshot()).not.toBe(before);
    connection.event({ type: 'result', result: { status: 'completed', text: '流式结果' } }); connection.event({ type: 'done', status: 'completed' }); connection.close(); await sending;
    expect(store.getSnapshot().session!.messages.at(-1)!.content).toBe('权威结果');
  });

  it('switches chats while a stream continues and only explicit Stop calls server cancellation', async () => {
    const connection = controlledStream(); let requestId = '';
    const { store, requests } = harness((path, options) => {
      if (path.endsWith('/turn')) { requestId = JSON.parse(options.body as string).request_id; return connection.response; }
      if (path.endsWith('/cancel')) return json({ cancelled: true });
      if (path === '/api/sessions/a' && requestId) return json({ id: 'a', last_run: { request_id: requestId }, last_status: 'cancelled', messages: [{ role: 'user', content: '煮蛋' }] });
    });
    await store.start(); await store.openSession('a');
    const sending = store.sendMessage('煮蛋'); await flush();
    const turn = requests.find(item => item.path.endsWith('/turn'))!;
    await store.openSession('b');
    expect(turn.options.signal?.aborted).toBe(false); expect(store.getSnapshot().currentId).toBe('b');
    await store.cancelTurn('a'); await store.cancelTurn('a');
    expect(requests.filter(item => item.path.endsWith('/cancel'))).toHaveLength(1);
    expect(turn.options.signal?.aborted).toBe(false);
    connection.event({ type: 'done', status: 'cancelled' }); connection.close(); await sending;
    expect(store.getSnapshot().cache.a.messages.at(-1)?.cancelled).toBe(true);
    expect(store.getSnapshot().currentId).toBe('b');
  });

  it('recovers a dropped stream by polling its own background chat without resubmitting', async () => {
    vi.useFakeTimers(); let submitted = false, finished = false;
    const { store, requests } = harness((path) => {
      if (path.endsWith('/turn')) { submitted = true; return stream([{ type: 'assistant_delta', text: '还在处理' }]); }
      if (path === '/api/sessions/a' && submitted) return json(finished
        ? { id: 'a', last_status: 'completed', messages: [{ role: 'assistant', content: '做完啦' }] }
        : { id: 'a', last_status: 'running', active_turn: { id: 'turn', text: '继续处理' }, messages: [] });
    });
    await store.start(); await store.openSession('a'); await store.sendMessage('煮蛋');
    expect(store.getSnapshot().cache.a.running).toBe(true);
    await store.openSession('b'); finished = true;
    await vi.advanceTimersByTimeAsync(2500);
    expect(store.getSnapshot().cache.a.running).toBe(false); expect(store.getSnapshot().cache.a.messages.at(-1)?.content).toBe('做完啦');
    expect(store.getSnapshot().currentId).toBe('b'); expect(requests.filter(item => item.path.endsWith('/turn'))).toHaveLength(1);
  });

  it('detaches on dispose without cancelling jobs and ignores late events across restart', async () => {
    const connection = controlledStream(); let started = false;
    const { store, requests } = harness((path) => {
      if (path.endsWith('/turn')) { started = true; return connection.response; }
      if (path === '/api/sessions/a' && started) return json({ id: 'a', last_status: 'completed', messages: [{ role: 'assistant', content: '服务端已完成' }] });
    });
    await store.start(); await store.openSession('a');
    const sending = store.sendMessage('煮蛋'); await flush();
    store.dispose();
    expect(requests.find(item => item.path.endsWith('/turn'))!.options.signal?.aborted).toBe(true);
    expect(requests.some(item => item.path.endsWith('/cancel'))).toBe(false);
    await store.start();
    connection.event({ type: 'assistant_delta', text: '过期事件' }); connection.close(); await sending;
    expect(store.getSnapshot().session?.messages.at(-1)?.content).toBe('服务端已完成');
    expect(store.getSnapshot().runs).toEqual({});
  });

  it('isolates late session reads from the newly selected chat', async () => {
    const slow = deferred<Response>();
    const { store } = harness(path => path === '/api/sessions/a' ? slow.promise : undefined);
    await store.start(); const opening = store.openSession('a'); await store.openSession('b');
    slow.resolve(json({ id: 'a', title: '迟到的 A', messages: [] })); await opening;
    expect(store.getSnapshot().currentId).toBe('b'); expect(store.getSnapshot().session?.id).toBe('b');
    expect(store.getSnapshot().cache.a.title).toBe('迟到的 A');
  });

  it('retains text and local images if first-session preparation fails', async () => {
    const blob = new Blob(['photo'], { type: 'image/jpeg' });
    const { store, requests } = harness((path) => path.includes('/images?') ? json({ id: 'uploaded', name: '蛋.jpg' }) : undefined,
      { prepareImage: async () => ({ id: 'local:photo', name: '蛋.jpg', blob, local: true }) });
    await store.start(); store.setDraft('用这张图片做菜'); const key = store.getSnapshot().blankId;
    await store.uploadImages([new File(['photo'], '蛋.jpg', { type: 'image/jpeg' })]);
    store.setWorkspaceAdapter({ prepareTurn: async () => { throw new Error('草稿待确认'); } });
    const result = await store.sendMessage();
    expect(result.error).toContain('草稿待确认'); expect(store.getSnapshot().currentId).toBeNull();
    expect(store.getSnapshot().drafts[key]).toBe('用这张图片做菜');
    expect(store.getSnapshot().imageDrafts[key][0]).toMatchObject({ local: true, blob });
    expect(store.getSnapshot().imageDrafts[key][0].id).toMatch(/^local:/);
    expect(requests.some(item => item.path.endsWith('/turn'))).toBe(false);
    expect(requests.some(item => item.path === '/api/sessions/created' && item.options.method === 'DELETE')).toBe(true);
  });

  it('keeps an in-flight attachment owned by its source chat after a switch', async () => {
    const image = deferred<ImageDraft>();
    const { store } = harness(() => undefined, { prepareImage: () => image.promise });
    await store.start(); await store.openSession('a');
    const uploading = store.uploadImages([new File(['photo'], 'a.jpg', { type: 'image/jpeg' })]);
    await store.openSession('b'); image.resolve({ id: 'local:a', name: 'a.jpg', local: true }); await uploading;
    expect(store.getSnapshot().imageDrafts.a).toHaveLength(1); expect(store.getSnapshot().imageDrafts.b).toBeUndefined();
  });

  it('persists unsent text and image blobs for a replacement store', async () => {
    const storage = memoryStorage(), attachments = memoryAttachments(), blob = new Blob(['photo'], { type: 'image/jpeg' });
    const first = harness(() => undefined, { storage, attachments, prepareImage: async () => ({ id: 'local:persisted', name: 'persisted.jpg', blob, local: true }) }).store;
    await first.start(); first.setDraft('明天做菜'); const key = first.getSnapshot().blankId;
    await first.uploadImages([new File(['photo'], 'persisted.jpg', { type: 'image/jpeg' })]); await flush(); first.dispose();
    const replacement = harness(() => undefined, { storage, attachments }).store;
    await replacement.start();
    expect(replacement.getSnapshot().blankId).toBe(key); expect(replacement.getSnapshot().drafts[key]).toBe('明天做菜');
    expect(replacement.getSnapshot().imageDrafts[key][0].blob).toBe(blob);
  });

  it('continues a saved confirmation once and keeps the original composer draft', async () => {
    let requestId = '';
    const { store, requests } = harness((path, options) => {
      if (path.endsWith('/turn')) { requestId = JSON.parse(options.body as string).request_id; return stream([{ type: 'result', result: { status: 'completed', text: '开始做菜' } }, { type: 'done', status: 'completed' }]); }
      if (path === '/api/sessions/a' && requestId) return json({ id: 'a', messages: [{ role: 'assistant', content: '开始做菜' }], last_run: { request_id: requestId }, last_status: 'completed' });
    });
    await store.start(); await store.openSession('a'); store.setDraft('我的未发送文字');
    store.enqueueContinuation('a', '备菜已确认，继续', 'confirm:action-1');
    store.enqueueContinuation('a', '备菜已确认，继续', 'confirm:action-1');
    await vi.waitFor(() => expect(store.getSnapshot().cache.a?.messages.at(-1)?.content).toBe('开始做菜'));
    expect(requests.filter(item => item.path.endsWith('/turn'))).toHaveLength(1);
    expect(requestId).toBe('confirm:action-1'); expect(store.getSnapshot().continuations.a).toBeUndefined();
    expect(store.getSnapshot().drafts.a).toBe('我的未发送文字');
  });

  it('keeps a rejected continuation reviewable and retries its same request ID without repeating the action', async () => {
    let rejected = true, requestId = '';
    const { store, requests } = harness((path, options) => {
      if (path.endsWith('/turn')) {
        requestId = JSON.parse(options.body as string).request_id;
        return rejected ? json({ detail: '连接暂时不可用' }, 503) : stream([{ type: 'result', result: { status: 'completed', text: '继续完成' } }, { type: 'done', status: 'completed' }]);
      }
      if (path === '/api/sessions/a' && requestId && !rejected) return json({ id: 'a', messages: [{ role: 'assistant', content: '继续完成' }], last_run: { request_id: requestId }, last_status: 'completed' });
    });
    await store.start(); await store.openSession('a');
    store.enqueueContinuation('a', '按确认内容继续', 'answer:question-1');
    await vi.waitFor(() => expect(store.getSnapshot().continuations.a?.stage).toBe('failed'));
    expect(store.getSnapshot().continuations.a.error).toContain('连接暂时不可用');
    rejected = false; store.retryContinuation('a');
    await vi.waitFor(() => expect(store.getSnapshot().cache.a?.messages.at(-1)?.content).toBe('继续完成'));
    const posts = requests.filter(item => item.path.endsWith('/turn'));
    expect(posts).toHaveLength(2); expect(posts.map(item => JSON.parse(item.options.body as string).request_id)).toEqual(['answer:question-1', 'answer:question-1']);
    expect(requests.some(item => item.path.endsWith('/actions'))).toBe(false);
  });

  it('preserves text typed while a request is accepted and clears its already submitted attachment', async () => {
    const response = deferred<Response>(); let requestId = '';
    const { store } = harness((path, options) => {
      if (path.includes('/images?')) return json({ id: 'uploaded', name: 'egg.jpg' });
      if (path.endsWith('/turn')) { requestId = JSON.parse(options.body as string).request_id; return response.promise; }
      if (path === '/api/sessions/a' && requestId) return json({ id: 'a', messages: [{ role: 'assistant', content: '完成' }], last_run: { request_id: requestId }, last_status: 'completed' });
    }, { prepareImage: async () => ({ id: 'local:egg', name: 'egg.jpg', local: true, blob: new Blob(['egg']) }) });
    await store.start(); await store.openSession('a'); store.setDraft('看这张图');
    await store.uploadImages([new File(['egg'], 'egg.jpg', { type: 'image/jpeg' })]);
    const sending = store.sendMessage(); await flush(); store.setDraft('下一条还想问这个');
    response.resolve(stream([{ type: 'result', result: { status: 'completed', text: '完成' } }, { type: 'done', status: 'completed' }])); await sending;
    expect(store.getSnapshot().drafts.a).toBe('下一条还想问这个'); expect(store.getSnapshot().imageDrafts.a).toBeUndefined();
  });

  it('rechecks a newly arrived question after workspace preparation before submitting a turn', async () => {
    const { store, requests } = harness();
    await store.start(); await store.openSession('a');
    store.setWorkspaceAdapter({ prepareTurn: async () => ({ revision: 2, pending_question: { id: 'new-question' } }) });
    expect((await store.sendMessage('继续')).error).toContain('问题已更新');
    expect(requests.some(item => item.path.endsWith('/turn'))).toBe(false);
  });

  it('reconciles a POST accepted before headers after reload without reposting or losing newer draft text', async () => {
    const storage = memoryStorage(), attachments = memoryAttachments(), response = deferred<Response>(); let pendingId = '';
    const first = harness((path, options) => {
      if (path.includes('/images?')) return json({ id: 'uploaded-egg', name: 'egg.jpg' });
      if (path.endsWith('/turn')) { pendingId = JSON.parse(options.body as string).request_id; return response.promise; }
    }, { storage, attachments, prepareImage: async () => ({ id: 'local:egg', name: 'egg.jpg', local: true, blob: new Blob(['egg']) }) });
    await first.store.start(); await first.store.openSession('a'); first.store.setDraft('看这张图片');
    await first.store.uploadImages([new File(['egg'], 'egg.jpg', { type: 'image/jpeg' })]);
    const sending = first.store.sendMessage(); await flush();
    expect(pendingId).toBeTruthy();
    expect(JSON.parse(storage.getItem('chefmate:react-submissions')!).a.requestId).toBe(pendingId);
    first.store.setDraft('后来补写的新问题'); await flush(); first.store.dispose();
    const replacement = harness(path => path === '/api/sessions/a'
      ? json({ id: 'a', last_status: 'completed', last_run: { request_id: pendingId }, messages: [{ role: 'user', content: '看这张图片', request_id: pendingId }, { role: 'assistant', content: '已完成' }] }) : undefined, { storage, attachments });
    await replacement.store.start();
    expect(replacement.store.getSnapshot().drafts.a).toBe('后来补写的新问题');
    expect(replacement.store.getSnapshot().imageDrafts.a).toBeUndefined();
    expect(JSON.parse(storage.getItem('chefmate:react-submissions')!)).toEqual({});
    expect(replacement.requests.some(item => item.path.endsWith('/turn'))).toBe(false);
    response.resolve(stream([])); await sending;
  });

  it('retries a definitively unaccepted submission with the same request ID', async () => {
    let rejected = true, requestId = '';
    const { store, requests } = harness((path, options) => {
      if (path.endsWith('/turn')) {
        requestId = JSON.parse(options.body as string).request_id;
        return rejected ? json({ detail: '稍后重试' }, 503) : stream([{ type: 'result', result: { status: 'completed', text: '已完成' } }, { type: 'done', status: 'completed' }]);
      }
      if (path === '/api/sessions/a' && requestId && !rejected) return json({ id: 'a', last_status: 'completed', last_run: { request_id: requestId }, messages: [{ role: 'assistant', content: '已完成' }] });
    });
    await store.start(); await store.openSession('a'); store.setDraft('做个早餐'); await store.sendMessage();
    const failed = store.getSnapshot().session!.messages.at(-1)!;
    expect(failed.retryAsNew).toBe(true); rejected = false; await store.retryMessage(failed);
    const posts = requests.filter(item => item.path.endsWith('/turn'));
    expect(posts).toHaveLength(2); expect(JSON.parse(posts[0].options.body as string).request_id).toBe(JSON.parse(posts[1].options.body as string).request_id);
    expect(store.getSnapshot().session!.messages.at(-1)?.content).toBe('已完成');
  });

  it('reconciles a saved answer continuation accepted before reload instead of posting twice', async () => {
    const storage = memoryStorage();
    storage.setItem('chefmate:react-continuations', JSON.stringify({ a: { message: '回答继续', requestId: 'answer:q1', stage: 'sending' } }));
    const { store, requests } = harness(path => path === '/api/sessions/a'
      ? json({ id: 'a', last_status: 'completed', last_run: { request_id: 'answer:q1' }, messages: [] }) : undefined, { storage });
    await store.start(); await flush();
    expect(store.getSnapshot().continuations.a).toBeUndefined();
    expect(requests.some(item => item.path.endsWith('/turn'))).toBe(false);
  });

  it('blocks composing over a pending question and preserves drafts when the selected session disappears', async () => {
    const { store } = harness(path => path === '/api/sessions/missing' ? json({ detail: '不存在' }, 404) : undefined);
    await store.start(); await store.openSession('a');
    store.setWorkspaceAdapter({ prepareTurn: async () => ({ revision: 1 }), pendingQuestion: () => ({ id: 'q', question: '几人份？' }) });
    store.setDraft('还没发送'); expect((await store.sendMessage()).error).toContain('先回答');
    store.setDraft('丢失会话的草稿', 'missing'); await store.openSession('missing');
    expect(store.getSnapshot().currentId).toBeNull(); expect(store.getSnapshot().drafts[store.getSnapshot().blankId]).toBe('丢失会话的草稿');
  });
});
