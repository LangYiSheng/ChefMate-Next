import { TOOL_NAMES } from './presentation.js';

export function focusStartIndex(session) {
  const messages = session?.messages || [];
  for (let index = messages.length - 1; index >= 0; index--) {
    if (messages[index].role === 'user') return index;
  }
  return Math.max(0, messages.length - 1);
}

export function focusConversation(session, startIndex, activity = {}) {
  const messages = (session?.messages || []).slice(startIndex).filter(message => ['user', 'assistant'].includes(message.role)).slice(-10);
  const last = messages.at(-1);
  const assistant = last?.role === 'assistant' ? last : null;
  const running = Boolean(activity.running || session?.running || assistant?.streaming || assistant?.recovering);
  const tool = [...(assistant?.tool_calls || [])].reverse().find(call => call.running || call.status === 'running');
  let status = '';
  if (running) status = activity.cancelling ? '正在停止本次回复…'
    : assistant?.recovering ? '连接中断，正在恢复回复…'
    : tool ? `${TOOL_NAMES[tool.name || tool.tool_name] || '正在使用厨房工具'}…`
    : assistant?.content ? '正在回复…' : activity.status || '正在等待助手回复…';
  else if (assistant?.error) status = assistant.error;
  else if (assistant?.cancelled) status = '本次回复已停止，可以继续交流。';
  return { messages, running, status, failed: Boolean(!running && assistant?.error),
    retry: !running && assistant && assistant.retryMessage != null &&
      (assistant.retryMessage || assistant.retryImages?.length) ? assistant : null };
}

export function focusTransform(source, target) {
  if (!source || source.width <= 0 || source.height <= 0 || target.width <= 0 || target.height <= 0) return null;
  if (source.right <= target.left || source.left >= target.right || source.bottom <= target.top || source.top >= target.bottom) return null;
  return `translate(${source.left - target.left}px, ${source.top - target.top}px) scale(${source.width / target.width}, ${source.height / target.height})`;
}

// Cancel old transitions so a late close cannot hide a reopened view.
export class FocusMotion {
  constructor() { this.generation = 0; this.animation = null; }
  cancel() { this.generation++; this.animation?.cancel(); this.animation = null; }
  start(animation, finish) {
    this.cancel();
    const generation = this.generation;
    if (!animation) { finish?.(); return; }
    this.animation = animation;
    Promise.resolve(animation.finished).then(() => {
      if (generation !== this.generation) return;
      this.animation = null; finish?.(); animation.cancel();
    }, () => {});
  }
}
