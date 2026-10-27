import { useRef } from 'react';
import type { Message } from '../runtime/types';
import { Markdown } from './Markdown';

/** Optimistic and persisted replies share the user request's identity. */
export function replyMessageKeys(messages: Message[], sessionId: string) {
  let request: string | undefined;
  return messages.map((message, index) => {
    if (message.role === 'user') request = message.request_id || message.turn_id || message.id || `${index}:${message.content}`;
    return `${sessionId}:${message.role === 'assistant' && request ? `reply:${request}` : message.id || `${message.turn_id || index}:${message.role}`}`;
  });
}

/** Only the server-confirmed answer becomes prose; intermediate model text stays progress. */
export function ReplyMarkdown({ message, active = true }: { message: Message; active?: boolean }) {
  const arrivedLive = useRef(Boolean(message.streaming));
  const visible = message.content && (!message.streaming || message.resultComplete) && !message.recovering && !message.cancelled && !message.error;
  return <Markdown text={visible ? message.content : ''} animate={arrivedLive.current && active} />;
}
