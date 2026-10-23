export type Json = Record<string, any>;
export type View = { id: string; kind: string; revision: number; presentation_revision?: number; title?: string; editable?: boolean; data: Json; placement?: string };
export type Action = { action_id: string; base_revision: number; view_id?: string; type: string; payload: Json };
export type Timer = { id: string; label: string; status: string; seconds: number; deadline_at?: string | null; remaining_seconds?: number; recipe_id?: string; step_id?: string };
export const labels: Record<string, string> = { preference_form: '条件', recipe_choices: '选菜', recipe_detail: '详情', meal_plan: '餐单', prep_checklist: '备菜', cooking_guide: '做法', timers: '计时', task_progress: '进度' };
export const stages: Record<string, string> = { idea: '想吃什么', planning: '正在选菜', prep: '备菜', cooking: '烹饪', completed: '已完成' };
const actions = new Set(['update_preferences', 'select_recipes', 'open_recipe_detail', 'change_servings', 'check_prep_item', 'set_active_recipe', 'set_active_step', 'answer_question', 'set_checklist_scope', 'complete_step', 'complete_meal', 'start_timer', 'pause_timer', 'resume_timer', 'cancel_timer']);
export const clone = <T,>(value: T): T => value == null ? value : JSON.parse(JSON.stringify(value));
export function changedValues(baseline: Json, values: Json): Json { return Object.fromEntries(Object.entries(values).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(baseline[key]))); }
export function makeAction(type: string, payload: Json, revision: number, viewId?: string, actionId = crypto.randomUUID()): Action {
  if (!actions.has(type)) throw new Error('这个操作暂不支持。');
  if (!Number.isInteger(revision) || revision < 0) throw new Error('任务版本尚未读取，请刷新任务。');
  return { action_id: actionId, base_revision: revision, ...(viewId ? { view_id: viewId } : {}), type, payload: clone(payload) };
}
export function validView(view: any): view is View { return !!view && typeof view.id === 'string' && labels[view.kind] != null && Number.isInteger(view.revision) && !!view.data && typeof view.data === 'object' && !Array.isArray(view.data); }
export function messageViews(message: Json): View[] {
  if (message.streaming || message.recovering) return [];
  const actions = [...(Array.isArray(message.actions) ? message.actions : []), ...(message.tool_calls || []).flatMap((call: Json) => Array.isArray(call.result?.actions) ? call.result.actions : [])];
  const unique = new Map<string, View>();
  for (const action of actions) {
    if (action.kind !== 'show_component' || !validView(action.component)) continue;
    const next = { ...action.component, placement: action.placement || 'inline' };
    const previous = unique.get(next.id);
    if (!previous || compareVersions(next, previous) >= 0) unique.set(next.id, next);
  }
  return [...unique.values()];
}
export function compareVersions(a: Json, b: Json) { return (a.revision ?? -1) - (b.revision ?? -1) || (a.presentation_revision ?? 0) - (b.presentation_revision ?? 0); }
export function viewChoices(views: View[], stage: string): View[] {
  const preferred = ({ idea: 'preference_form', planning: 'meal_plan', prep: 'prep_checklist', cooking: 'cooking_guide', completed: 'meal_plan' } as Record<string, string>)[stage];
  return views.filter(view => view.kind !== 'task_progress' && !(view.kind === 'recipe_choices' && ['prep', 'cooking', 'completed'].includes(stage))
    && !(['cooking', 'completed'].includes(stage) && ['preference_form', 'prep_checklist'].includes(view.kind))).sort((a, b) => Number(b.kind === preferred) - Number(a.kind === preferred));
}
export function clockText(seconds: number | null): string {
  if (seconds == null) return '--:--';
  const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60), rest = seconds % 60;
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}` : `${minutes}:${String(rest).padStart(2, '0')}`;
}
export function timerPresentation(timer: Timer, now = Date.now()) {
  const deadline = Date.parse(timer.deadline_at || '');
  const remaining = ['finished', 'cancelled'].includes(timer.status) ? 0 : timer.status === 'running' && Number.isFinite(deadline)
    ? Math.max(0, Math.ceil((deadline - now) / 1000)) : Number.isFinite(timer.remaining_seconds) ? Math.max(0, Math.ceil(timer.remaining_seconds!)) : null;
  const expired = timer.status === 'finished' || timer.status === 'running' && remaining === 0;
  const state = expired ? 'expired' : ['running', 'paused', 'cancelled'].includes(timer.status) ? timer.status : 'unknown';
  return { remaining, expired, state, clock: clockText(remaining), status: ({ expired: '时间到', running: '正在计时', paused: '已暂停', cancelled: '已结束', unknown: '等待同步' } as Record<string, string>)[state], progress: timer.seconds > 0 && remaining != null ? Math.min(1, remaining / timer.seconds) : 0 };
}
export function timerClock(serverNow: string, elapsedNow = () => performance.now(), received = elapsedNow()) {
  const server = Date.parse(serverNow);
  return Number.isFinite(server) ? () => server + Math.max(0, elapsedNow() - received) : () => Date.now();
}
export function workspaceTimers(views: View[]): Timer[] {
  const unique = new Map<string, Timer>();
  for (const view of [...views.filter(v => v.kind === 'cooking_guide'), ...views.filter(v => v.kind === 'timers')]) for (const timer of view.data.timers || []) unique.set(timer.id, timer);
  return [...unique.values()];
}
export function timerCapacity(snapshot: Json | null | undefined, views: View[], now: number) {
  const timers = workspaceTimers(views), server = snapshot?.timer_capacity;
  const limit = server?.limit ?? snapshot?.cooking_settings?.timer_limit ?? 3;
  const active = Math.max(0, (server?.active_count ?? timers.filter(timer => ['running', 'paused'].includes(timer.status)).length)
    - timers.filter(timer => timer.status === 'running' && timerPresentation(timer, now).expired).length);
  return { limit, active, available: Math.max(0, limit - active) };
}
export function manualTimerSeconds(minutes: string | number, seconds: string | number): number | null {
  if (minutes === '' && seconds === '') return null;
  const m = Number(minutes || 0), s = Number(seconds || 0), total = m * 60 + s;
  return Number.isInteger(m) && Number.isInteger(s) && m >= 0 && s >= 0 && s <= 59 && total >= 1 && total <= 86400 ? total : null;
}
export function durationText(seconds: number): string { return `${Math.floor(seconds / 60) ? `${Math.floor(seconds / 60)} 分钟` : ''}${seconds % 60 ? ` ${seconds % 60} 秒` : ''}`.trim() || '0 秒'; }
export function answerPayload(question: Json, draft: Json) {
  const allowed = new Set((question.options || []).map((option: Json) => option.id));
  const ids = [...new Set<string>(draft.optionIds || [])].filter(id => allowed.has(id));
  const text = question.allow_custom ? String(draft.text || '').trim() : '';
  if (!question.multiple && ids.length > 1) throw new Error('请选择一个答案。');
  if (!ids.length && !text) throw new Error('选一个答案，或者写下你的想法。');
  return { question_id: question.id, option_ids: ids, text };
}
export function answerMessage(question: Json, payload: Json) {
  const names = (question.options || []).filter((option: Json) => payload.option_ids.includes(option.id)).map((option: Json) => option.label);
  return `关于“${question.question}”，我的回答是：${[...names, payload.text].filter(Boolean).join('；')}。请按这个答案继续。`;
}
export function safeReference(value: string): string | null { try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch { return null; } }
export function completionText(receipt: Json | null | undefined) {
  return ({ saved: '制作记录已保存，本餐已按记录份额计入个人营养。', awaiting_portions: '制作记录已保存，个人营养等待你补充本人吃了哪些、吃了多少。', not_eaten: '制作记录已保存，本次未食用，未计入个人摄入。', deleted: '制作记录已保存，个人摄入记录已移除，未自动恢复。' } as Record<string, string>)[receipt?.intake_status] || '制作记录已保存，个人营养登记状态还需核对。';
}
