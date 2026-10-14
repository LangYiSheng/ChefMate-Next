// 组件和操作是本地白名单；模型只能提供经服务端验证的领域数据。
export const COMPONENT_KINDS = Object.freeze([
  'preference_form', 'recipe_choices', 'recipe_detail', 'meal_plan', 'shopping_list',
  'prep_checklist', 'cooking_guide', 'timers', 'task_progress',
]);
export const ACTION_TYPES = Object.freeze([
  'update_preferences', 'select_recipes', 'open_recipe_detail', 'change_servings',
  'check_shopping_item', 'check_prep_item', 'set_active_recipe', 'set_active_step',
  'set_checklist_scope',
  'complete_step', 'change_stage', 'start_timer', 'pause_timer', 'resume_timer', 'cancel_timer',
]);
export const STAGES = Object.freeze({ idea: '想吃什么', planning: '选菜与份量', shopping: '采购', prep: '备菜', cooking: '开始烹饪', completed: '完成' });
const TRANSITIONS = Object.freeze({ idea:['planning'], planning:['idea','shopping','prep'], shopping:['planning','prep'],
  prep:['planning','shopping','cooking'], cooking:['prep','completed'], completed:['idea','planning'] });
export const EXECUTION_STAGES = Object.freeze(['prep', 'cooking', 'completed']);

export function canChangeStage(progress, target) {
  if (!(TRANSITIONS[progress.stage] || []).includes(target)) return false;
  if (['shopping','prep','cooking','completed'].includes(target) && !(progress.recipe_count > 0)) return false;
  if (target === 'cooking' && (!(progress.prep_total > 0) || !(progress.steps_total > 0) || !(progress.prep_ready >= progress.prep_total))) return false;
  if (target === 'completed' && (!(progress.steps_total > 0) || !(progress.steps_done >= progress.steps_total))) return false;
  return true;
}
export function visibleWorkspaceViews(views, stage) {
  const order = stage === 'cooking' ? ['task_progress','cooking_guide','timers','meal_plan','prep_checklist','shopping_list','recipe_detail','preference_form']
    : EXECUTION_STAGES.includes(stage) ? ['task_progress','prep_checklist','shopping_list','meal_plan','cooking_guide','timers','recipe_detail','preference_form']
      : ['task_progress','preference_form','recipe_choices','meal_plan','shopping_list','prep_checklist','cooking_guide','timers','recipe_detail'];
  const rank = (view) => order.includes(view.kind) ? order.indexOf(view.kind) : order.length;
  return views.filter((view) => !(EXECUTION_STAGES.includes(stage) && view.kind === 'recipe_choices')).sort((a,b) => rank(a)-rank(b));
}
export function legacyRecipeItems(message, presentedViews = messageViews(message)) {
  const hasWorkflow = presentedViews.some((view) => ['recipe_choices','recipe_detail','meal_plan','shopping_list',
    'prep_checklist','cooking_guide','timers','task_progress'].includes(view.kind));
  const queriedThisTurn = (message.tool_calls || []).some((call) => ['search_recipes','get_recipe'].includes(call.name || call.tool_name)
    && call.result?.status === 'ok');
  return hasWorkflow || !queriedThisTurn ? [] : message.selected_recipe ? [message.selected_recipe] : [...(message.recipes || [])];
}

export function clone(value) { return value == null ? value : JSON.parse(JSON.stringify(value)); }
export function validView(value) {
  return Boolean(value && typeof value === 'object' && typeof value.id === 'string'
    && typeof value.kind === 'string' && Number.isInteger(value.revision)
    && value.data && typeof value.data === 'object' && !Array.isArray(value.data));
}
export function viewsFrom(value) {
  if (!value || typeof value !== 'object') return [];
  const candidates = [value, value.view, value.component,
    ...(Array.isArray(value.views) ? value.views : []), ...(Array.isArray(value.components) ? value.components : [])];
  if (value.presentation) candidates.push(...viewsFrom(value.presentation));
  for (const action of Array.isArray(value.actions) ? value.actions : []) candidates.push(...viewsFrom(action));
  return candidates.filter(validView);
}
export function messageViews(message) {
  const actions = Array.isArray(message.actions) ? [...message.actions] : [];
  for (const call of message.tool_calls || []) {
    if (Array.isArray(call.result?.actions)) actions.push(...call.result.actions);
  }
  const views = actions.filter(action=>action.kind==='show_component' && validView(action.component))
    .map(action=>({...action.component,placement:action.placement || 'inline'}));
  const unique = new Map();
  for (const view of views) unique.set(`${view.id}:${view.revision}`, view);
  return [...unique.values()];
}
export function makeAction(type, payload, revision, viewId, actionId) {
  if (!ACTION_TYPES.includes(type)) throw new Error('这个操作暂不支持。');
  if (!Number.isInteger(revision) || revision < 0) throw new Error('任务版本尚未读取，请刷新任务。');
  return { action_id: actionId, base_revision: revision, ...(viewId ? { view_id: viewId } : {}), type, payload: clone(payload) };
}
export function remainingSeconds(timer, now = Date.now()) {
  if (['finished', 'cancelled'].includes(timer.status)) return 0;
  if (timer.status === 'paused') return Number.isFinite(timer.remaining_seconds) ? Math.max(0, Math.ceil(timer.remaining_seconds)) : null;
  const deadline = Date.parse(timer.deadline_at || '');
  if (timer.status === 'running' && Number.isFinite(deadline)) return Math.max(0, Math.ceil((deadline - now) / 1000));
  return Number.isFinite(timer.remaining_seconds) ? Math.max(0, Math.ceil(timer.remaining_seconds)) : null;
}
export function clockText(seconds) {
  if (seconds == null) return '时间未知';
  const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60), rest = seconds % 60;
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}` : `${minutes}:${String(rest).padStart(2, '0')}`;
}
export function changedValues(baseline, values) {
  return Object.fromEntries(Object.entries(values).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(baseline[key])));
}
export function splitTerms(value) { return String(value || '').split(/[,，、;；\n]/).map((item) => item.trim()).filter(Boolean); }
