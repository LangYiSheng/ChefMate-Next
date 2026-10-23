export const componentExamples: Record<string, string> = {
  'meal-planning': '5 个人，帮我搭配 4 道家常菜。', 'pantry-preparation': '这些菜要先准备什么？',
  'cooking-guide': '这一步要计时多久？', 'recipe-authoring': '把这张菜谱截图整理成我的菜谱。',
  'personal-memory': '记住我不吃香菜。', 'food-journal': '这是今天的午饭，帮我记一下。',
};
export const toolLabels: Record<string, string> = {
  create_food_log_draft: '整理饮食记录', search_food_logs: '查看个人饮食', get_food_log: '读取饮食记录',
  revise_food_log_draft: '调整饮食草稿', confirm_food_log: '确认饮食记录', get_cooked_meal_intake: '查看本餐营养登记',
  record_cooked_meal_intake: '登记本餐食用份额', find_ingredients: '识别食材', search_recipes: '筛选菜谱', get_recipe: '读取菜谱',
  get_search_options: '核对查询条件', show_preference_form: '整理用餐条件', show_recipe_choices: '整理菜品选择',
  show_recipe_details: '展示菜品详情', set_meal_plan: '安排餐单', show_meal_plan: '核对餐单', show_shopping_list: '整理备菜清单',
  show_prep_checklist: '整理备菜清单', show_cooking_guide: '整理烹饪步骤', set_cooking_timer: '设置计时', show_timers: '查看计时',
  get_cooking_workspace: '核对当前进度', advance_cooking_stage: '更新会话阶段', update_cooking_guidance: '调整当前做法',
  request_recipe_deletion: '确认删除个人菜谱', confirm_recipe_deletion: '处理菜谱删除确认', create_recipe_draft: '整理个人菜谱草稿',
  search_custom_recipes: '搜索个人菜谱', suggest_replies: '提供对话建议', read_long_term_memory: '读取长期记忆',
  maintain_memory_summary: '维护助手记忆', search_personal_history: '查找个人历史', update_preferences: '保存用餐条件',
  select_recipes: '更新菜品选择', update_prep_items: '确认备菜状态', ask_user: '向你确认细节', load_skill: '读取组件指南', list_skills: '查看组件指南',
};
export const interfaceLabels: Record<string, string> = {
  journal: '食光手记', recipes: '菜谱中心', memory: '长期记忆设置', preference_form: '用餐条件', recipe_choices: '菜品选择',
  recipe_detail: '菜谱详情', meal_plan: '餐单与份量', prep_checklist: '备菜清单', cooking_guide: '烹饪步骤', timers: '计时器',
  task_progress: '任务进度', food_log: '饮食记录', nutrition_summary: '营养统计',
};
export const dependencyLabels: Record<string, string> = {retrieval: '菜谱检索', cookbook: '个人菜谱存储', memory: '长期记忆存储', journal: '饮食记录存储'};
