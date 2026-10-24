import { useLayoutEffect, useRef } from 'react';
import { chefStore } from '../../runtime/store';
import type { Message, RunState } from '../../runtime/types';
import { ActivityText } from '../../components/ActivityText';
import { assistantProgress } from '../../components/chat-presentation';
import { Markdown } from '../../components/Markdown';
import { Brand } from '../../components/Icon';
import './focus-conversation.css';

/** Keep the current exchange beside the task without duplicating its cards. */
export function FocusConversation({ messages, run, sessionId, active, expanded = false }: { messages: Message[]; run?: RunState | null; sessionId: string; active: boolean; expanded?: boolean }) {
  const scroll = useRef<HTMLDivElement>(null), stick = useRef(true);
  const lastUser = messages.findLastIndex(message => message.role === 'user');
  const exchange = expanded ? messages : messages.slice(Math.max(0, lastUser));
  useLayoutEffect(() => {
    if (active && stick.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [messages, run?.status, active, expanded]);
  useLayoutEffect(() => {
    const element = scroll.current;
    if (!active || !element || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => { if (stick.current) element.scrollTop = element.scrollHeight; });
    observer.observe(element); return () => observer.disconnect();
  }, [active, expanded]);
  return <div ref={scroll} className={`focus-assistant${expanded ? ' is-expanded' : ''}`} aria-label="专注模式对话" onScroll={event => { const el = event.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60; }}>
    {!exchange.length && <p className="ui-hint">有问题随时问我，做菜进度会留在这里。</p>}
    {exchange.map((message, index) => <div key={message.id || `${index}:${message.role}`} className={`focus-exchange ${message.role}`}>
      {message.role === 'user' ? <p className="focus-user-message">{message.content || '已发送图片'}</p> : <>
        <div className="assistant-heading"><Brand className="assistant-icon" /><span>ChefMate</span></div>
        {message.content && (!message.streaming || message.resultComplete) && !message.error && !message.cancelled && <Markdown text={message.content} />}
        {message.streaming && !message.error && !message.cancelled && <ActivityText text={assistantProgress(message, run)} active={active} />}
        {message.error && <div className="error-box" role="alert">{message.error}{message.retryMessage != null && !run && <button type="button" className="retry-button" onClick={() => void chefStore.retryMessage(message, sessionId)}>重试这次请求</button>}</div>}
        {message.cancelled && <p className="message-meta">本次执行已停止。</p>}
      </>}
    </div>)}
  </div>;
}
