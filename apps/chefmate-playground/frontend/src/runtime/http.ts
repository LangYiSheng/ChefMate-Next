import type { ApiData, Message, Session } from './types';

let boundAccount: string | null = null;
export function bindHttpAccount(accountId: string | null) { boundAccount = accountId; }
export function accountHeaders(path: string, input?: HeadersInit): Headers {
  const headers = new Headers(input);
  if (boundAccount && path.startsWith('/api/') && !['/api/auth/me', '/api/auth/login', '/api/auth/register'].includes(path)) {
    headers.set('X-ChefMate-Account', boundAccount);
  }
  return headers;
}

export function notifyAccountBoundary(status: number, payload: any, path: string) {
  if (typeof window === 'undefined' || !path.startsWith('/api/')) return;
  if ((status === 401 && !path.startsWith('/api/auth/')) || payload?.detail?.code === 'account_changed') {
    window.dispatchEvent(new CustomEvent('chefmate:session-expired'));
  }
}

export function safeText(value: unknown): string {
  return String(value ?? '')
    .replace(/(\b(?:postgres(?:ql)?|https?):\/\/)[^\s/@]+:[^\s/@]+@/gi, '$1[已隐藏]@')
    .replace(/\bBearer\s+\S+/gi, 'Bearer [已隐藏]')
    .replace(/\bsk-[a-zA-Z0-9_-]{12,}/g, '[密钥已隐藏]')
    .replace(/((?:api[_ -]?key|password|secret|token|authorization)\s*[:=]\s*)[^\s,;]+/gi, '$1[已隐藏]');
}

export function safeValue(value: any, depth = 0): any {
  if (depth > 10) return '[内容过深]';
  if (typeof value === 'string') return safeText(value);
  if (Array.isArray(value)) return value.map(item => safeValue(item, depth + 1));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key,
    /^(?:api_?key|password|secret|token|access_token|authorization|database_url|dsn|connection_string)$/i.test(key)
      ? '[已隐藏]' : safeValue(item, depth + 1),
  ]));
  return value;
}

export function pretty(value: unknown): string {
  if (value === undefined) return '无';
  try { return JSON.stringify(safeValue(value), null, 2); } catch { return '无法显示这段记录'; }
}

export class HttpError extends Error {
  constructor(message: string, public status: number, public data: any) { super(message); }
}

export async function requestJson<T = any>(fetcher: typeof fetch, path: string, options: RequestInit = {}): Promise<T> {
  const headers = accountHeaders(path, options.headers);
  if (!headers.has('Accept')) headers.set('Accept', 'application/json');
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const response = await fetcher(path, { ...options, headers });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    notifyAccountBoundary(response.status, payload, path);
    const message = typeof payload?.detail === 'string' ? payload.detail : payload?.detail?.message || payload?.message || payload?.error?.message;
    throw new HttpError(safeText(message || `请求失败（${response.status}），请稍后重试。`), response.status, payload);
  }
  return payload as T;
}

export function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  return requestJson<T>(fetch, path, options);
}

export function normalizeToolCall(call: ApiData): ApiData {
  return { ...call, running: Boolean(call.running || call.status === 'running') };
}

export function normalizeMessage(message: ApiData): Message {
  const result = message.result || message.metadata?.result || {};
  return {
    ...message, role: message.role, content: message.content ?? message.text ?? result.text ?? '',
    recipes: message.recipes ?? result.recipes ?? [],
    tool_calls: (message.tool_calls ?? result.tool_calls ?? []).map(normalizeToolCall),
    selected_recipe: message.selected_recipe ?? result.selected_recipe,
    snapshot: message.snapshot ?? result.snapshot,
    actions: message.actions ?? result.actions ?? [], skills: message.skills ?? result.skills ?? [],
    process: message.process ?? result.process ?? [], usage: message.usage ?? result.usage,
    context_usage: message.context_usage ?? result.context_usage,
    suggested_replies: message.suggested_replies ?? result.suggested_replies ?? [],
  };
}

export function normalizeSession(session: ApiData): Session {
  const messages: Message[] = (session.messages || []).filter((message: ApiData) => ['user', 'assistant'].includes(message.role)).map(normalizeMessage);
  const running = Boolean(session.running || session.last_status === 'running');
  const lastUser = [...messages].reverse().find(message => message.role === 'user');
  if (running && session.active_turn) {
    messages.push(normalizeMessage({ ...session.active_turn, role: 'assistant', turn_id: session.active_turn.id,
      content: session.active_turn.text || '', streaming: true }));
  } else if (['error', 'cancelled', 'interrupted'].includes(session.last_status) && messages.at(-1)?.role === 'user') {
    const result = session.last_run?.result || {};
    const cancelled = session.last_status === 'cancelled';
    const message = normalizeMessage({ role: 'assistant', ...result, content: result.text || '', cancelled,
      retryMessage: session.last_error?.retryable === false ? null : lastUser?.content,
      retryImages: lastUser?.images || [] });
    if (!cancelled) message.error = safeText(session.last_error?.message
      || (session.last_status === 'interrupted' ? '服务重启中断了这次执行，可以重试。' : '这次执行没有完成，可以重试。'));
    messages.push(message);
  }
  return { ...session, id: session.id, title: session.title || '新对话', running, messages };
}

/** Fetch POST streams use SSE framing, including CRLF, split UTF-8 and multiline data. */
export async function readEvents(response: Response, onEvent: (event: ApiData) => void | Promise<void>): Promise<void> {
  if (!response.body) throw new Error('浏览器没有收到可读取的响应，请重试。');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '', data: string[] = [], namedType = '';
  const line = async (value: string) => {
    if (!value) {
      if (data.length) {
        let payload: ApiData;
        try { payload = JSON.parse(data.join('\n')); } catch { throw new Error('服务返回了无法读取的事件，请重试。'); }
        if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('服务返回了无效事件，请重试。');
        if (!payload.type && namedType) payload.type = namedType;
        await onEvent(payload);
      }
      data = []; namedType = ''; return;
    }
    if (value.startsWith(':')) return;
    const colon = value.indexOf(':');
    const field = colon < 0 ? value : value.slice(0, colon);
    let content = colon < 0 ? '' : value.slice(colon + 1);
    if (content.startsWith(' ')) content = content.slice(1);
    if (field === 'data') data.push(content);
    else if (field === 'event') namedType = content;
  };
  try {
    let done = false;
    while (!done) {
      const chunk = await reader.read(); done = chunk.done;
      buffer += decoder.decode(chunk.value, { stream: !done });
      while (true) {
        const boundary = buffer.search(/[\r\n]/);
        if (boundary < 0 || (!done && boundary === buffer.length - 1 && buffer[boundary] === '\r')) break;
        const width = buffer[boundary] === '\r' && buffer[boundary + 1] === '\n' ? 2 : 1;
        await line(buffer.slice(0, boundary)); buffer = buffer.slice(boundary + width);
      }
    }
    if (buffer) await line(buffer);
    await line('');
  } catch (error) {
    await reader.cancel().catch(() => undefined);
    throw error;
  } finally { reader.releaseLock(); }
}
