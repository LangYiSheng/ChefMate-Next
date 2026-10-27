import { useId, useLayoutEffect, useRef, useState } from 'react';
import { chefStore } from '../../runtime/store';
import type { Message, RunState } from '../../runtime/types';
import { ActivityText } from '../../components/ActivityText';
import { assistantProgress } from '../../components/chat-presentation';
import { ReplyMarkdown, replyMessageKeys } from '../../components/ReplyMarkdown';
import { Icon } from '../../components/Icon';
import './focus-conversation.css';

/** Keep the current exchange beside the task without duplicating its cards. */
export function FocusConversation({ messages, run, sessionId, active, expanded = false }: { messages: Message[]; run?: RunState | null; sessionId: string; active: boolean; expanded?: boolean }) {
  const scroll = useRef<HTMLDivElement>(null), stick = useRef(true);
  const [collapsed, setCollapsed] = useState(false), bodyId = useId();
  const lastUser = messages.findLastIndex(message => message.role === 'user');
  const exchange = expanded ? messages : messages.slice(Math.max(0, lastUser));
  const user = messages[lastUser];
  const answer = messages.slice(lastUser + 1).findLast(message => message.role === 'assistant');
  const turnKey = user?.request_id || user?.turn_id || user?.id || `${lastUser}:${user?.content || ''}`;
  const finished = answer && (!answer.streaming || answer.resultComplete) && !answer.recovering && !run?.recovering && !answer.error && !answer.cancelled;
  useLayoutEffect(() => { setCollapsed(false); stick.current = true; }, [turnKey]);
  useLayoutEffect(() => {
    if (active && stick.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [messages, run?.status, active, expanded, collapsed]);
  useLayoutEffect(() => {
    const element = scroll.current;
    if (!active || !element || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => { if (stick.current) element.scrollTop = element.scrollHeight; });
    observer.observe(element); return () => observer.disconnect();
  }, [active, expanded]);
  const visible = expanded ? exchange : finished ? [answer] : [user, answer].filter((message): message is Message => Boolean(message));
  const keys = replyMessageKeys(messages, sessionId);
  return <section className={`focus-reply${collapsed ? ' is-collapsed' : ''}${expanded ? ' is-expanded' : ''}`} aria-label="专注模式对话">
    {!expanded && <button type="button" className="focus-reply-toggle" aria-label={`${collapsed ? '展开' : '收起'}助手回复`} aria-expanded={!collapsed} aria-controls={bodyId} onClick={() => setCollapsed(value => !value)}><Icon name="message-circle" /><span>助手回复</span><Icon name={collapsed ? 'chevron-down' : 'chevron-up'} /></button>}
    <div id={bodyId} ref={scroll} hidden={collapsed} className={`focus-assistant${expanded ? ' is-expanded' : ''}`} onScroll={event => { const el = event.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60; }}>
    {!visible.length && <p className="ui-hint">有问题随时问我。</p>}
    {visible.map(message => <div key={keys[messages.indexOf(message)]} className={`focus-exchange ${message.role}`}>
      {message.role === 'user' ? <p className="focus-user-message">{message.content || '已发送图片'}</p> : <>
        <ReplyMarkdown message={message} active={active && !collapsed} />
        {(message.streaming || message.recovering || run?.recovering) && !message.error && !message.cancelled && <ActivityText text={assistantProgress(message, run)} active={active} />}
      </>}
    </div>)}
    {!answer && user && <ActivityText text={run?.status || '正在理解你的需求…'} active={active} />}
    </div>
    {answer?.error && <div className="error-box" role="alert">{answer.error}{answer.retryMessage != null && !run && <button type="button" className="retry-button" onClick={() => void chefStore.retryMessage(answer, sessionId)}>重试这次请求</button>}</div>}
    {answer?.cancelled && <p className="message-meta">本次执行已停止。</p>}
  </section>;
}
