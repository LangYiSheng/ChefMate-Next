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

export function focusSceneFrames(distance, closing = false) {
  const focus = [{ transform: `translateX(${distance}px)` }, { transform: 'translateX(0)' }];
  const main = [{ transform: 'translateX(0)' }, { transform: `translateX(${-distance}px)` }];
  return { focus: closing ? [...focus].reverse() : focus, main: closing ? [...main].reverse() : main };
}

// Completion always follows the complete meal snapshot, including a scoped checklist.
export function focusCompletion(snapshot = {}, kind) {
  snapshot ||= {};
  const progress = snapshot.components?.find(item => item.kind === 'task_progress')?.data || {};
  if (snapshot.stage === 'completed' && ['prep_checklist', 'cooking_guide'].includes(kind)) return 'completed';
  if (kind === 'prep_checklist' && ['planning', 'prep'].includes(snapshot.stage) &&
      progress.prep_total > 0 && progress.prep_ready >= progress.prep_total) return 'prep';
  if (kind === 'cooking_guide' && snapshot.stage === 'cooking' &&
      (snapshot.allowed_transitions?.includes('completed') || progress.steps_total > 0 && progress.steps_done >= progress.steps_total)) return 'meal';
  return null;
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
