import { safeValue } from '../../runtime/http';

export type Data = Record<string, any>;
type ToolInfo = { label: string; icon: string; group: string };
const definitions: [string, string, string, string][] = [
  ['find_ingredients', '识别食材', 'search', '选菜与餐单'], ['search_recipes', '搜索菜谱', 'search', '选菜与餐单'],
  ['get_recipe', '读取菜谱', 'book-open', '选菜与餐单'], ['get_search_options', '读取筛选条件', 'list-filter', '选菜与餐单'],
  ['show_preference_form', '整理用餐条件', 'sliders-horizontal', '选菜与餐单'], ['update_preferences', '保存用餐条件', 'sliders-horizontal', '选菜与餐单'],
  ['show_recipe_choices', '展示候选菜品', 'layout-grid', '选菜与餐单'], ['show_recipe_details', '展示菜谱详情', 'book-open', '选菜与餐单'],
  ['select_recipes', '确认菜品选择', 'check-check', '选菜与餐单'], ['set_meal_plan', '安排餐单', 'utensils', '选菜与餐单'], ['show_meal_plan', '展示餐单', 'utensils', '选菜与餐单'],
  ['show_prep_checklist', '整理备菜清单', 'list-checks', '备菜与烹饪'], ['update_prep_items', '更新备菜进度', 'list-checks', '备菜与烹饪'],
  ['show_cooking_guide', '展示烹饪步骤', 'cooking-pot', '备菜与烹饪'], ['update_cooking_guidance', '更新烹饪引导', 'cooking-pot', '备菜与烹饪'],
  ['set_cooking_timer', '设置烹饪计时', 'timer', '备菜与烹饪'], ['show_timers', '展示计时器', 'timer', '备菜与烹饪'],
  ['start_timer', '开始计时', 'timer', '备菜与烹饪'], ['pause_timer', '暂停计时', 'pause', '备菜与烹饪'], ['resume_timer', '继续计时', 'play', '备菜与烹饪'],
  ['advance_cooking_stage', '推进烹饪阶段', 'arrow-up-right', '备菜与烹饪'], ['get_cooking_workspace', '读取厨房状态', 'layers', '工作区与交互'],
  ['ask_user', '向你确认', 'circle-help', '工作区与交互'], ['suggest_replies', '准备快捷回复', 'message-circle', '工作区与交互'],
  ['list_skills', '查看组件指南', 'layers', '工作区与交互'], ['load_skill', '读取组件指南', 'book-open', '工作区与交互'],
  ['create_recipe_draft', '整理菜谱草稿', 'book-open', '个人菜谱'], ['search_custom_recipes', '搜索个人菜谱', 'search', '个人菜谱'],
  ['get_recipe_draft', '读取菜谱草稿', 'book-open', '个人菜谱'], ['revise_recipe_draft', '修改菜谱草稿', 'book-open', '个人菜谱'],
  ['request_recipe_publication', '请求确认保存菜谱', 'circle-help', '个人菜谱'], ['confirm_recipe_publication', '核对菜谱保存确认', 'check-check', '个人菜谱'],
  ['request_recipe_deletion', '请求删除菜谱', 'trash-2', '个人菜谱'], ['confirm_recipe_deletion', '确认删除菜谱', 'trash-2', '个人菜谱'],
  ['read_long_term_memory', '读取长期记忆', 'layers', '长期记忆'], ['maintain_memory_summary', '维护长期记忆', 'layers', '长期记忆'], ['search_personal_history', '查询个人历史', 'search', '长期记忆'],
  ['create_food_log_draft', '整理饮食草稿', 'utensils', '饮食记录'], ['search_food_logs', '查询饮食记录', 'search', '饮食记录'],
  ['get_food_log', '读取饮食记录', 'utensils', '饮食记录'], ['revise_food_log_draft', '修改饮食草稿', 'utensils', '饮食记录'], ['confirm_food_log', '确认饮食记录', 'check-check', '饮食记录'],
  ['get_cooked_meal_intake', '核对本餐营养', 'utensils', '饮食记录'], ['record_cooked_meal_intake', '登记本餐摄入', 'check-check', '饮食记录'],
  ['get_app_preferences', '读取应用偏好', 'sliders-horizontal', '应用偏好'], ['update_app_preferences', '修改应用偏好', 'sliders-horizontal', '应用偏好'],
];
const toolMap = new Map(definitions.map(([name, label, icon, group]) => [name, { label, icon, group }]));
export function toolInfo(name = ''): ToolInfo { return toolMap.get(name) || { label: '扩展工具', icon: 'plug', group: '其他工具' }; }
export const stageLabels: Record<string, string> = { idea: '交流需求', planning: '选菜', shopping: '备菜', prep: '备菜', cooking: '烹饪', completed: '已完成' };
export const componentIcons: Record<string, string> = { 'meal-planning': 'utensils', 'pantry-preparation': 'shopping-bag', 'cooking-guide': 'cooking-pot', 'recipe-authoring': 'book-open', 'personal-memory': 'layers', 'food-journal': 'utensils', 'app-preferences': 'sliders-horizontal' };
export const dependencyLabels: Record<string, string> = { retrieval: '菜谱检索服务', cookbook: '个人菜谱库', memory: '长期记忆库', journal: '饮食记录库' };
export const kindLabels: Record<string, string> = { preference_form: '用餐条件', recipe_choices: '候选菜品', recipe_detail: '菜谱详情', meal_plan: '餐单', prep_checklist: '备菜清单', cooking_guide: '烹饪步骤', timers: '计时器', task_progress: '任务进度', food_log: '饮食记录', nutrition_summary: '营养概要' };
export const statusLabels: Record<string, string> = { ok: '成功', completed: '已完成', running: '执行中', cancelled: '已停止', interrupted: '已中断', error: '失败', verified: '已核验', invalid_arguments: '参数不符', not_found: '未找到', pending: '待确认', saved: '已保存' };
const privateKeys = new Set(['reasoning', 'reasoning_content', 'reasoning_details', 'chain_of_thought', 'thoughts', 'image_contents']);
/** The server owns redaction. This extra presentation guard never renders hidden reasoning fields. */
export function displayValue(value: any, depth = 0): any {
  if (depth > 16) return '[内容过深]';
  if (typeof value === 'string') {
    if (/^\s*[\[{]/.test(value)) { try { return displayValue(JSON.parse(value), depth + 1); } catch { /* Plain text. */ } }
    return safeValue(value);
  }
  if (Array.isArray(value)) return value.map(item => displayValue(item, depth + 1));
  if (value && typeof value === 'object') return safeValue(Object.fromEntries(Object.entries(value).filter(([key]) => !privateKeys.has(key.toLowerCase())).map(([key, item]) => [key, displayValue(item, depth + 1)])));
  return value;
}
export function serialized(value: any): string {
  const clean = displayValue(value);
  if (typeof clean === 'string') return clean;
  return clean === undefined ? '未留存' : JSON.stringify(clean, null, 2);
}
export function summary(value: any): string {
  const clean = displayValue(value);
  if (clean == null) return '未留存';
  if (typeof clean === 'string') return clean.trim().slice(0, 180) || '空文本';
  if (typeof clean !== 'object') return String(clean);
  if (Array.isArray(clean)) return `${clean.length} 项`;
  const parts: string[] = [];
  if (clean.code === 'skill_disabled') parts.push('所需组件未启用');
  if (typeof clean.status === 'string') parts.push(statusLabels[clean.status] || clean.status);
  for (const key of ['message', 'name', 'title', 'question', 'query', 'text', 'recipe_name']) if (typeof clean[key] === 'string' && clean[key]) { parts.push(clean[key].slice(0, 100)); break; }
  if (typeof clean.recipe?.name === 'string') parts.push(clean.recipe.name);
  const workspaceStage = clean.workspace?.stage || clean.ui_snapshot?.stage;
  if (typeof workspaceStage === 'string') parts.push(`当前${stageLabels[workspaceStage] || workspaceStage}`);
  for (const key of ['recipes', 'items', 'steps', 'actions', 'errors']) if (Array.isArray(clean[key])) {
    const names = clean[key].slice(0, 2).map((item: any) => item && typeof item === 'object' ? item.name || item.recipe?.name : '').filter((item: any) => typeof item === 'string' && item.trim());
    parts.push(`${({ recipes: '菜谱', items: '项目', steps: '步骤', actions: '界面动作', errors: '问题' } as Data)[key]} ${clean[key].length} 项${names.length ? `（${names.join('、')}）` : ''}`);
  }
  if (!parts.length) {
    const fieldNames: Data = { recipe_id: '菜谱', seconds: '秒数', label: '名称', servings: '份量', ingredients: '食材', ingredient_names: '食材', available_ingredients: '现有食材', max_total_time_min: '用时上限', option_ids: '选择', stage: '阶段', query: '查询', limit: '数量', text: '文字' };
    for (const [key, value] of Object.entries(clean).slice(0, 3)) {
      if (value == null) continue;
      const brief = Array.isArray(value) ? value.every(item => typeof item === 'string') ? value.slice(0, 3).join('、') : `${value.length} 项` : typeof value === 'object' ? `${Object.keys(value).length} 个字段` : String(value).slice(0, 70);
      parts.push(`${fieldNames[key] || key}：${fieldNames[key] ? brief : Array.isArray(value) ? `${value.length} 项` : typeof value === 'object' ? `${Object.keys(value).length} 个字段` : '已留存'}`);
    }
  }
  return parts.join(' · ') || '空对象';
}
export function callStatus(call: Data): { text: string; tone: string } {
  if (call.running || call.status === 'running') return { text: '执行中', tone: 'running' };
  const result = displayValue(call.result), status = result?.status || call.status;
  if (['ok', 'completed', 'saved'].includes(status)) return { text: statusLabels[status], tone: 'success' };
  if (status) return { text: statusLabels[status] || status, tone: ['cancelled', 'interrupted'].includes(status) ? 'muted' : 'warning' };
  return { text: call.result == null ? '结果未留存' : '已返回', tone: 'muted' };
}
export function formatDate(value?: string): string { if (!value) return ''; const date = new Date(value); return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }
export function numberText(value: unknown): string { return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('zh-CN') : '未报告'; }
