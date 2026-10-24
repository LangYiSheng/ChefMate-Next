import { describe, expect, it } from 'vitest';
import { assistantProgress, conversationStage, recipeSearchCards } from './chat-presentation';
import type { Message } from '../runtime/types';

describe('conversation presentation boundaries', () => {
  it('shows standalone recipe cards only for successful queries in the current turn', () => {
    const recipes = [{ recipe_id: 'a', name: '油麦菜' }, { recipe_id: 'b', name: '生菜' }];
    const queried: Message = { role: 'assistant', content: '', recipes, tool_calls: [{ name: 'search_recipes', result: { status: 'ok' } }] };
    expect(recipeSearchCards(queried)).toEqual(recipes);
    expect(recipeSearchCards({ ...queried, selected_recipe: recipes[1] })).toEqual([recipes[1]]);
    expect(recipeSearchCards({ ...queried, tool_calls: [{ name: 'search_recipes', result: { status: 'error' } }] })).toEqual([]);
    expect(recipeSearchCards({ ...queried, actions: [{ kind: 'show_component', component: { id: 'plan', kind: 'meal_plan', revision: 1, data: { recipes: [recipes[1]] } } }] })).toEqual([]);
    expect(recipeSearchCards({ ...queried, streaming: true })).toEqual([]);
  });

  it('combines phase, active dish and waiting state without leaking unknown phase identifiers', () => {
    expect(conversationStage({ stage: 'prep', recipe_names: ['回锅肉', '西兰花'], active_recipe_name: '回锅肉' })).toBe('备菜 · 回锅肉 · 另1道');
    expect(conversationStage({ stage: 'planning', recipe_names: ['生菜'] })).toBe('已选菜 · 生菜');
    expect(conversationStage({ stage: 'shopping', recipe_names: ['生菜'] })).toBe('备菜 · 生菜');
    expect(conversationStage({ stage: 'unknown_backend_stage' })).toBe('闲聊');
    expect(conversationStage({ stage: 'unknown_backend_stage', recipe_names: ['生菜'] })).toBe('已选菜 · 生菜');
    expect(conversationStage({}, true)).toBe('等你回答');
    expect(conversationStage({ stage: 'cooking', active_recipe_name: '生菜' }, true)).toBe('等你回答 · 生菜');
  });

  it('uses a short public label for cancellation and recovery', () => {
    expect(assistantProgress({ role: 'assistant', content: '', recovering: true })).toContain('恢复');
    expect(assistantProgress({ role: 'assistant', content: '' }, { sessionId: 'a', requestId: 'r', input: '', status: '查询', cancelling: true })).toContain('正在停止');
    expect(assistantProgress({ role: 'assistant', content: '', tool_calls: [{ name: 'get_cooked_meal_intake', running: true }] })).toBe('核对本餐营养…');
    expect(assistantProgress({ role: 'assistant', content: '', tool_calls: [{ name: 'show_prep_checklist', running: true }] }, { sessionId: 'a', requestId: 'r', input: '', status: '正在理解需求…' })).toBe('整理备菜清单…');
  });
});
