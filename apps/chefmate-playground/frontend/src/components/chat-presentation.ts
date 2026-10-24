import type { ApiData, Message, RunState } from '../runtime/types';
import { messageViews } from '../features/workspace/protocol';

export const toolNames: Record<string, string> = { find_ingredients: '识别食材', search_recipes: '筛选菜谱', get_recipe: '读取菜谱', show_preference_form: '整理用餐条件', show_recipe_choices: '整理菜品选择', set_meal_plan: '安排餐单', show_meal_plan: '核对餐单', show_prep_checklist: '整理备菜清单', show_cooking_guide: '整理烹饪步骤', start_timer: '开始计时', load_skill: '读取组件指南', ask_user: '向你确认细节', create_recipe_draft: '整理个人菜谱草稿', create_food_log_draft: '整理饮食记录', maintain_memory_summary: '维护长期记忆', get_cooked_meal_intake: '核对本餐营养', record_cooked_meal_intake: '登记实际食用份额' };
Object.assign(toolNames, {
  set_kitchen_display: '切换厨房显示方式', advance_cooking_stage: '切换做菜阶段',
  navigate_cooking_step: '切换烹饪步骤',
  update_preferences: '保存用餐条件', select_recipes: '确认菜品', show_recipe_details: '读取菜谱详情',
  update_prep_items: '更新备菜进度', update_cooking_guidance: '更新烹饪步骤', set_cooking_timer: '设置厨房计时', show_timers: '查看厨房计时',
  get_user_profile: '读取饮食偏好', update_user_profile: '记住饮食偏好', search_conversation_history: '查找历史对话',
  get_app_preferences: '读取界面偏好', update_app_preferences: '调整界面偏好',
  list_food_logs: '查找饮食记录', get_food_log: '读取饮食记录', get_nutrition_summary: '汇总最近营养',
  get_cooking_workspace: '核对当前做菜进度', get_search_options: '查看可选食材与口味',
  search_custom_recipes: '查找个人菜谱', get_recipe_draft: '读取菜谱草稿', revise_recipe_draft: '修改菜谱草稿',
  request_recipe_publication: '准备菜谱确认', confirm_recipe_publication: '保存个人菜谱',
  request_recipe_deletion: '准备菜谱删除确认', confirm_recipe_deletion: '删除已确认的菜谱',
  read_long_term_memory: '读取饮食习惯', search_personal_history: '查找历史记录',
  search_food_logs: '查找饮食记录', revise_food_log_draft: '修改饮食草稿', confirm_food_log: '保存饮食记录',
  list_skills: '查看可用厨房功能', suggest_replies: '整理下一步建议',
});

/** Aggregate recipe references also carry previous turns; they are not a new recommendation. */
export function recipeSearchCards(message: Message): ApiData[] {
  if (message.streaming || message.recovering) return [];
  const queried = (message.tool_calls || []).some(call => ['search_recipes', 'get_recipe'].includes(call.name || call.tool_name) && call.result?.status === 'ok');
  if (!queried || messageViews(message).some(view => ['recipe_choices', 'recipe_detail', 'meal_plan', 'prep_checklist', 'cooking_guide', 'timers', 'task_progress'].includes(view.kind))) return [];
  return message.selected_recipe ? [message.selected_recipe] : Array.isArray(message.recipes) ? message.recipes : [];
}

export function assistantProgress(message: Message, run?: RunState | null): string {
  if (run?.cancelling) return '正在停止，请稍等…';
  if (message.recovering || run?.recovering) return '连接中断，正在恢复…';
  const call = (message.tool_calls || []).findLast(item => item.running || item.status === 'running');
  if (call) return `${toolNames[call.name || call.tool_name] || '正在处理厨房任务'}…`;
  if (run?.status) return run.status;
  return message.content ? '正在整理回复…' : '正在理解你的需求…';
}

const stages: Record<string, string> = { idea: '闲聊', planning: '正在选菜', prep: '备菜', shopping: '备菜', cooking: '烹饪', completed: '已完成' };
export function conversationStage(status: ApiData = {}, waiting = false): string {
  const names = [...new Set<string>((Array.isArray(status.recipe_names) ? status.recipe_names : [])
    .filter((name: unknown): name is string => typeof name === 'string' && Boolean(name.trim())).map((name: string) => name.trim()))];
  const name = typeof status.active_recipe_name === 'string' && status.active_recipe_name.trim() || names[0];
  const stage = waiting ? '等你回答' : name && status.stage === 'planning' ? '已选菜'
    : stages[status.stage] || (name ? '已选菜' : status.choosing ? '正在选菜' : '闲聊');
  const others = name ? names.filter(item => item !== name).length : 0;
  return name ? `${stage} · ${name}${others ? ` · 另${others}道` : ''}` : stage;
}
