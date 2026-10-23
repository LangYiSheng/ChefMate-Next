import type { ApiData, Message, RunState } from '../runtime/types';
import { messageViews } from '../features/workspace/protocol';

export const toolNames: Record<string, string> = { find_ingredients: '识别食材', search_recipes: '筛选菜谱', get_recipe: '读取菜谱', show_preference_form: '整理用餐条件', show_recipe_choices: '整理菜品选择', set_meal_plan: '安排餐单', show_meal_plan: '核对餐单', show_prep_checklist: '整理备菜清单', show_cooking_guide: '整理烹饪步骤', start_timer: '开始计时', load_skill: '读取组件指南', ask_user: '向你确认细节', create_recipe_draft: '整理个人菜谱草稿', create_food_log_draft: '整理饮食记录', maintain_memory_summary: '维护长期记忆', get_cooked_meal_intake: '核对本餐营养', record_cooked_meal_intake: '登记实际食用份额' };

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
  if (run?.status) return run.status;
  const call = (message.tool_calls || []).findLast(item => item.running || item.status === 'running');
  if (call) return `${toolNames[call.name || call.tool_name] || '正在处理厨房任务'}…`;
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
