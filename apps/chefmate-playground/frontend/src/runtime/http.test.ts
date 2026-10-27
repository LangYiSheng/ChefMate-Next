import { afterEach, describe, expect, it, vi } from 'vitest';
import { accountHeaders, bindHttpAccount, HttpError, normalizeSession, readEvents, requestJson, safeText } from './http';

afterEach(() => {bindHttpAccount(null); vi.restoreAllMocks();});

function chunks(values: Uint8Array[]) {
  return new Response(new ReadableStream<Uint8Array>({ start(controller) { values.forEach(value => controller.enqueue(value)); controller.close(); } }));
}

describe('HTTP and SSE protocol', () => {
  it('pins private request headers to the accepted account rather than cross-tab browser storage', async () => {
    bindHttpAccount('account-a');
    localStorage.setItem('chefmate:browser-account', 'account-b');
    const captured = accountHeaders('/api/sessions');
    expect(captured.get('X-ChefMate-Account')).toBe('account-a');
    expect(accountHeaders('/api/auth/me').has('X-ChefMate-Account')).toBe(false);
    expect(accountHeaders('/api/auth/login').has('X-ChefMate-Account')).toBe(false);
    bindHttpAccount('account-b');
    expect(captured.get('X-ChefMate-Account')).toBe('account-a');
    expect(accountHeaders('/api/sessions').get('X-ChefMate-Account')).toBe('account-b');
  });

  it('refreshes the identity boundary on a stale-account conflict without losing its message', async () => {
    const changed = vi.fn(); window.addEventListener('chefmate:session-expired', changed);
    const fetcher = (async () => new Response(JSON.stringify({detail: {code: 'account_changed', message: '当前账号已经切换'}}), {status: 409})) as typeof fetch;
    await expect(requestJson(fetcher, '/api/ui-preferences', {method: 'PATCH'})).rejects.toMatchObject({status: 409, message: '当前账号已经切换'});
    expect(changed).toHaveBeenCalledOnce(); window.removeEventListener('chefmate:session-expired', changed);
  });
  it('decodes split UTF-8, CRLF, comments, named events and multiline data in order', async () => {
    const bytes = new TextEncoder().encode(': heartbeat\r\nevent: assistant_delta\r\ndata: {"text":\r\ndata: "番茄炒蛋"}\r\n\r\ndata: {"type":"done","status":"completed"}\r\n\r\n');
    const events: any[] = [];
    await readEvents(chunks([...bytes].map(byte => new Uint8Array([byte]))), async event => { await Promise.resolve(); events.push(event); });
    expect(events).toEqual([{ text: '番茄炒蛋', type: 'assistant_delta' }, { type: 'done', status: 'completed' }]);
  });

  it('accepts a final event without a trailing blank line and CR-only frames', async () => {
    const events: any[] = [];
    await readEvents(chunks([new TextEncoder().encode('data: {"type":"status","text":"开始"}\r\rdata: {"type":"done"}')]), event => { events.push(event); });
    expect(events.map(event => event.type)).toEqual(['status', 'done']);
  });

  it('rejects malformed event JSON rather than silently losing the terminal event', async () => {
    await expect(readEvents(chunks([new TextEncoder().encode('data: not-json\n\n')]), () => {})).rejects.toThrow('无法读取的事件');
    await expect(readEvents(new Response(null), () => {})).rejects.toThrow('没有收到可读取的响应');
  });

  it('redacts server errors and preserves custom transport headers', async () => {
    let headers: Headers | undefined;
    const fetcher = (async (_path: any, options: RequestInit) => {
      headers = new Headers(options.headers);
      return new Response(JSON.stringify({ detail: 'password=private Bearer abcdef' }), { status: 409 });
    }) as typeof fetch;
    const error = await requestJson(fetcher, '/api/example', { method: 'POST', body: 'bytes', headers: { 'Content-Type': 'image/jpeg' } }).catch(error => error);
    expect(error).toBeInstanceOf(HttpError); expect(error.status).toBe(409);
    expect(error.message).not.toContain('private'); expect(error.message).not.toContain('abcdef');
    expect(headers?.get('Content-Type')).toBe('image/jpeg');
    expect(safeText('postgresql://chef:secret@localhost/db')).not.toContain('chef:secret');
  });

  it('reconstructs interrupted retries and active turns from saved sessions', () => {
    const interrupted = normalizeSession({ id: 'a', last_status: 'interrupted', messages: [{ role: 'system', content: 'hidden' }, { role: 'user', content: '煮蛋', images: [{ id: 'photo' }] }] });
    expect(interrupted.messages).toHaveLength(2);
    expect(interrupted.messages[1]).toMatchObject({ role: 'assistant', retryMessage: '煮蛋', retryImages: [{ id: 'photo' }] });
    expect(interrupted.messages[1].error).toContain('服务重启');
    const running = normalizeSession({ id: 'a', last_status: 'running', messages: [], active_turn: { id: 'turn', text: '正在查询', tool_calls: [{ status: 'running' }] } });
    expect(running.running).toBe(true); expect(running.messages[0]).toMatchObject({ turn_id: 'turn', content: '正在查询', streaming: true });
    expect(running.messages[0].tool_calls?.[0].running).toBe(true);
  });
});
