// JSON transport, redacted diagnostics, SSE decoding and persisted conversation normalization.
export function safeText(value) {
  return String(value ?? '')
    .replace(/(\b(?:postgres(?:ql)?|https?):\/\/)[^\s/@]+:[^\s/@]+@/gi, '$1[已隐藏]@')
    .replace(/\bBearer\s+\S+/gi, 'Bearer [已隐藏]')
    .replace(/\bsk-[a-zA-Z0-9_-]{12,}/g, '[密钥已隐藏]')
    .replace(/((?:api[_ -]?key|password|secret|token|authorization)\s*[:=]\s*)[^\s,;]+/gi, '$1[已隐藏]');
}

export function safeValue(value, depth = 0) {
  if (depth > 10) return '[内容过深]';
  if (typeof value === 'string') return safeText(value);
  if (Array.isArray(value)) return value.map((item) => safeValue(item, depth + 1));
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key,
      /^(?:api_?key|password|secret|token|access_token|authorization|database_url|dsn|connection_string)$/i.test(key)
        ? '[已隐藏]' : safeValue(item, depth + 1),
    ]));
  }
  return value;
}

export function pretty(value) {
  if (value === undefined) return '无';
  try { return JSON.stringify(safeValue(value), null, 2); } catch { return '无法显示这段记录'; }
}

export async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
  });
  let payload;
  try { payload = await response.json(); } catch { payload = null; }
  if (!response.ok) {
    const detail = payload?.detail;
    const message = typeof detail === 'string' ? detail : payload?.message || payload?.error?.message;
    const error = new Error(safeText(message || `请求失败（${response.status}），请稍后重试。`));
    error.status = response.status;
    error.data = payload;
    throw error;
  }
  return payload;
}
export function normalizeMessage(message) {
  const result = message.result || message.metadata?.result || {};
  return {
    ...message, content: message.content ?? message.text ?? result.text ?? '',
    recipes: message.recipes ?? result.recipes ?? [],
    tool_calls: (message.tool_calls ?? result.tool_calls ?? []).map(normalizeToolCall),
    selected_recipe: message.selected_recipe ?? result.selected_recipe,
    snapshot: message.snapshot ?? result.snapshot,
    actions: message.actions ?? result.actions ?? [],
    skills: message.skills ?? result.skills ?? [],
    process: message.process ?? result.process ?? [],
    usage: message.usage ?? result.usage,
    context_usage:message.context_usage ?? result.context_usage,
    suggested_replies:message.suggested_replies ?? result.suggested_replies ?? [],
  };
}

export function normalizeToolCall(call) {
  return { ...call, running: Boolean(call.running || call.status === 'running') };
}

export function normalizeSession(session) {
  const messages = (session.messages || []).filter((m) => ['user', 'assistant'].includes(m.role)).map(normalizeMessage);
  const running = Boolean(session.running || session.last_status === 'running');
  const lastUser = [...messages].reverse().find((message) => message.role === 'user');
  if (running && session.active_turn) {
    messages.push({ role: 'assistant',turn_id:session.active_turn.id,content: session.active_turn.text || '',
      tool_calls: (session.active_turn.tool_calls || []).map(normalizeToolCall), actions: session.active_turn.actions || [], process: session.active_turn.process || [], usage:session.active_turn.usage,context_usage:session.active_turn.context_usage, recipes: [], streaming: true });
  } else if (['error', 'cancelled', 'interrupted'].includes(session.last_status) && messages.at(-1)?.role === 'user') {
    const result = session.last_run?.result || {};
    const cancelled = session.last_status === 'cancelled';
    const message = { role: 'assistant', content: result.text || '', tool_calls: (result.tool_calls || []).map(normalizeToolCall),
      recipes: result.recipes || [], snapshot: result.snapshot, actions: result.actions || [], process: result.process || [], usage:result.usage,context_usage:result.context_usage, cancelled,
      retryMessage: session.last_error?.retryable === false ? null : lastUser?.content };
    message.retryImages=lastUser?.images || [];
    if (!cancelled) message.error = session.last_error?.message
      || (session.last_status === 'interrupted' ? '服务重启中断了这次执行，可以重试。' : '这次执行没有完成，可以重试。');
    messages.push(message);
  }
  return { ...session, running, messages };
}
export async function readEvents(response, onEvent) {
  if (!response.body) throw new Error('浏览器没有收到可读取的响应，请重试。');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let data = [];
  let namedType = '';
  const line = (value) => {
    if (!value) {
      if (data.length) {
        let payload;
        try { payload = JSON.parse(data.join('\n')); } catch { throw new Error('服务返回了无法读取的事件，请重试。'); }
        if (!payload.type && namedType) payload.type = namedType;
        onEvent(payload);
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
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let newline;
      while ((newline = buffer.indexOf('\n')) >= 0) {
        line(buffer.slice(0, newline).replace(/\r$/, ''));
        buffer = buffer.slice(newline + 1);
      }
      if (done) {
        if (buffer) line(buffer.replace(/\r$/, ''));
        line('');
        break;
      }
    }
  } finally { reader.releaseLock(); }
}
