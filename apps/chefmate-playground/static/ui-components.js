import { COMPONENT_KINDS, STAGES, EXECUTION_STAGES, canChangeStage, clockText, remainingSeconds, splitTerms } from './ui-protocol.js';

export function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text != null) item.textContent = String(text);
  return item;
}
function button(text, callback, disabled = false, className = 'ui-button') {
  const item = node('button', className, text); item.type = 'button'; item.disabled = disabled;
  item.addEventListener('click', () => Promise.resolve(callback()).catch(() => {})); return item;
}
function badge(text, className = '') { return node('span', `ui-badge ${className}`, text); }
function hint(text) { return node('p', 'ui-hint', text); }
function amount(item) { return item.amount_text || (item.amount_value == null ? '用量未知' : `${item.amount_value} ${item.unit || ''}`.trim()); }
function difficulty(value) { return ({ beginner: '新手', normal: '普通', advanced: '进阶' })[value] || value || '难度未知'; }
function recipeMeta(recipe) { return `${recipe.total_time_min == null ? '耗时未知' : `${recipe.total_time_min} 分钟`} · ${difficulty(recipe.difficulty)}`; }
const rendererRegistry = new Map();
export function registerRenderer(kind, renderer) {
  if (!COMPONENT_KINDS.includes(kind) || typeof renderer !== 'function') throw new Error('非法的组件注册。');
  rendererRegistry.set(kind, renderer);
}

export function renderView(view, context) {
  const locked = context.historical || !view.editable || context.busy;
  const card = node('section', `ui-component ui-${view.kind}`);
  card.dataset.viewId = view.id; card.dataset.viewKind = view.kind;
  const heading = node('div', 'ui-component-heading');
  heading.append(node('h3', '', view.title || '任务内容'));
  if (context.historical) heading.append(badge('历史记录', 'muted'));
  else heading.append(badge(`第 ${view.revision} 版`, 'muted'));
  card.append(heading);
  const renderer = rendererRegistry.get(view.kind);
  const ctx = { ...context, locked, action: (type, payload) => context.action(type, payload, view) };
  if (renderer) card.append(renderer(view, ctx));
  else card.append(hint('这个组件的版本暂不支持，请使用文字说明或更新页面。'));
  if (context.historical) card.append(button('在当前任务打开', () => context.openCurrent(view.kind), false, 'ui-text-button'));
  return card;
}

function preferenceForm(view, ctx) {
  const root = node('form', 'ui-form');
  const values = { ...(view.data.values || {}), ...(ctx.draft?.values || {}) };
  const controls = new Map();
  const update = (name, value) => {
    values[name] = value;
    ctx.onDraft(view, values);
    save.disabled = ctx.locked || !ctx.dirty(view, values);
    discard.disabled = ctx.locked || !ctx.dirty(view, values);
    feedback.textContent = ctx.dirty(view, values) ? '未保存 · 发送消息时会先同步这些条件' : '已同步';
  };
  for (const field of view.data.fields || []) {
    const row = node(field.input_type === 'multiselect' && field.options?.length ? 'div' : 'label', 'ui-field'); row.append(node('span', 'ui-field-label', field.label || field.name));
    const current = values[field.name] ?? field.value;
    let control;
    if (field.input_type === 'checkbox') {
      control = node('input'); control.type = 'checkbox'; control.checked = Boolean(current);
      row.classList.add('ui-checkbox-field'); control.addEventListener('change', () => update(field.name, control.checked));
    } else if (field.input_type === 'multiselect' && field.options?.length) {
      control = node('div', 'ui-options');
      const selected = new Set(current || []);
      for (const option of field.options) {
        const value = typeof option === 'string' ? option : option.value ?? option.code;
        const label = typeof option === 'string' ? option : option.label || value;
        const optionLabel = node('label', 'ui-option'); const input = node('input'); input.type = 'checkbox'; input.checked = selected.has(value); input.disabled = ctx.locked;
        input.addEventListener('change', () => { if (input.checked) selected.add(value); else selected.delete(value); update(field.name, [...selected]); });
        optionLabel.append(input, document.createTextNode(label)); control.append(optionLabel);
      }
      if (field.name === 'available_tools') {
        const unknown = node('label', 'ui-option'); const input = node('input'); input.type = 'checkbox'; input.checked = current == null; input.disabled = ctx.locked;
        input.addEventListener('change', () => { update(field.name, input.checked ? null : [...selected]); });
        unknown.append(input, document.createTextNode('不限制厨具')); control.append(unknown);
      }
    } else {
      control = node(field.input_type === 'textarea' ? 'textarea' : 'input', 'ui-input');
      if (control.tagName === 'INPUT') control.type = field.input_type === 'number' ? 'number' : 'text';
      if (field.input_type === 'number') { control.min = field.name === 'servings' ? '1' : '0'; control.step = field.name === 'servings' ? '0.5' : '1'; }
      control.value = Array.isArray(current) ? current.join('、') : current ?? '';
      if (field.input_type === 'multiselect') control.placeholder = field.name === 'available_tools' ? '留空不限制厨具，多个名称用逗号分隔' : '多个名称用逗号分隔';
      control.maxLength = field.input_type === 'textarea' ? 2000 : 500;
      if (control.tagName === 'TEXTAREA') control.rows = 2;
      control.addEventListener('input', () => {
        const next = field.input_type === 'number' ? control.value === '' ? null : Number(control.value)
          : field.input_type === 'multiselect' ? field.name === 'available_tools' && !control.value.trim() ? null : splitTerms(control.value) : control.value;
        update(field.name, next);
      });
    }
    control.setAttribute('aria-label', field.label || field.name);
    control.dataset.controlKey = `preference:${field.name}`;
    if ('disabled' in control) control.disabled = ctx.locked;
    controls.set(field.name, control); row.append(control); root.append(row);
  }
  if (ctx.draft?.conflict) {
    const comparison = node('div', 'ui-conflict-values', '当前已保存的条件：');
    for (const field of view.data.fields || []) {
      const value = view.data.values?.[field.name];
      comparison.append(node('div', '', `${field.label}：${Array.isArray(value) ? value.join('、') || '未设置' : typeof value === 'boolean' ? value ? '是' : '否' : value ?? '未设置'}`));
    }
    root.append(comparison);
  }
  const feedback = node('div', 'ui-sync-label', ctx.historical ? '当时保存的条件' : ctx.draft?.conflict ? '已变化待重试 · 请复核当前任务' : ctx.draft ? '未保存 · 发送消息时会先同步这些条件' : '已同步');
  feedback.dataset.syncLabel = view.id;
  const actions = node('div', 'ui-actions');
  const save = button(ctx.draft?.conflict ? '复核后保存' : '保存条件', async () => {
    if (!root.reportValidity()) return;
    await ctx.savePreferences(view, values);
  }, ctx.locked || !ctx.draft);
  save.dataset.controlKey = 'save-preferences';
  const discard = button('放弃修改', () => ctx.discardDraft(view), ctx.locked || !ctx.draft, 'ui-text-button');
  actions.append(save, discard); root.append(feedback, actions);
  root.addEventListener('submit', (event) => { event.preventDefault(); save.click(); });
  return root;
}

function recipeChoices(view, ctx) {
  const root = node('div'); const recipes = view.data.recipes || [];
  if (!recipes.length) { root.append(hint('还没有符合条件的菜谱，继续告诉助手你的偏好。')); return root; }
  const selected = new Set(ctx.selectedRecipeIds || []);
  const selection = new Set(ctx.draft?.selectedIds ?? selected);
  const selectionNote = node('p', 'ui-hint', '可选择最多 4 道菜，确认后保存到当前菜单。');
  const changed = () => JSON.stringify([...selection].sort()) !== JSON.stringify([...selected].sort());
  const locked = ctx.locked || EXECUTION_STAGES.includes(ctx.stage);
  const confirm = button(ctx.draft?.conflict ? '复核后确认所选' : '确认所选菜谱', () => ctx.saveSelection(view, [...selection]), locked || !selection.size || selection.size > 4 || !changed());
  const discard = button('放弃修改', () => ctx.discardDraft(view), locked || !ctx.draft, 'ui-text-button');
  confirm.dataset.controlKey = 'confirm-recipes';
  const list = node('div', 'ui-recipe-list');
  for (const recipe of recipes) {
    const row = node('div', 'ui-recipe-choice'); const label = node('label', 'ui-choice-label');
    const checkbox = node('input'); checkbox.type = 'checkbox'; checkbox.checked = selection.has(recipe.recipe_id); checkbox.disabled = locked;
    checkbox.dataset.controlKey = `choose:${recipe.recipe_id}`;
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) selection.add(recipe.recipe_id); else selection.delete(recipe.recipe_id);
      confirm.disabled = locked || !selection.size || selection.size > 4 || !changed();
      selectionNote.textContent = selection.size > 4 ? '最多选择 4 道菜，请减少选择。' : `已勾选 ${selection.size} 道，确认后保存到当前菜单。`;
      ctx.onSelection?.(view, [...selection]);
      discard.disabled = locked || !changed();
    });
    const copy = node('span'); copy.append(node('strong', '', recipe.name), node('small', '', recipeMeta(recipe)));
    label.append(checkbox, copy); row.append(label);
    if (recipe.nutrition?.estimated) copy.append(badge('营养估算'));
    row.append(button('详情', () => ctx.action('open_recipe_detail', { recipe_id: recipe.recipe_id }), locked, 'ui-text-button'));
    list.append(row);
  }
  if (ctx.draft?.conflict) root.append(hint('候选或菜单已经变化，请复核当前内容；需要时先放弃旧选择。'));
  const actions = node('div', 'ui-actions'); actions.append(confirm, discard);
  root.append(list, selectionNote, actions);
  return root;
}

function nutritionNode(nutrition) {
  const root = node('div', 'ui-nutrition'); const values = nutrition?.per_serving || {};
  const estimated = new Set(nutrition?.estimated_per_serving_fields || []);
  for (const [key, label, unit] of [['calories_kcal', '能量', 'kcal'], ['protein_g', '蛋白质', 'g'], ['fat_g', '脂肪', 'g'], ['carbohydrate_g', '碳水', 'g'], ['sugar_g', '糖', 'g'], ['sodium_mg', '钠', 'mg']]) {
    const item = node('div'); item.append(node('small', '', label));
    const value = values[key]; const number = node('strong', '', value == null ? '未知' : `${Math.round(value * 10) / 10} ${unit}`);
    if (estimated.has(key)) number.append(badge('估算')); item.append(number); root.append(item);
  }
  return root;
}
function recipeDetail(view, ctx) {
  const root = node('div'); const recipe = view.data.recipe;
  if (!recipe) { root.append(hint('菜谱详情尚未读取。')); return root; }
  root.append(node('h4', 'ui-recipe-name', recipe.name), hint(recipeMeta(recipe)));
  if (recipe.description) root.append(node('p', 'ui-description', recipe.description));
  if (recipe.servings != null) root.append(hint(`${recipe.servings} 人份${recipe.estimated_servings ? '（人数估算）' : ''}`));
  else if (recipe.target_servings != null) root.append(hint(`目标 ${recipe.target_servings} 人份 · 原人数未知，暂不能换算。`));
  const ingredients = node('div', 'ui-ingredient-list');
  for (const item of recipe.ingredients || []) {
    const row = node('div', 'ui-ingredient'); row.append(node('span', '', item.canonical_name), node('span', '', amount(item)));
    if (item.estimated) row.append(badge('估算')); ingredients.append(row);
  }
  root.append(ingredients, node('h4', '', '每份营养'), nutritionNode(recipe.nutrition));
  const steps = node('ol', 'ui-steps');
  for (const step of recipe.steps || []) steps.append(node('li', '', step.instruction || step.description || step));
  root.append(node('h4', '', '做法'), steps);
  if (recipe.warnings?.length) { const warnings = node('div', 'ui-warnings'); for (const warning of recipe.warnings) warnings.append(node('p', '', warning)); root.append(warnings); }
  if (recipe.source?.data_snapshot?.data_version) root.append(hint(`菜谱来源 ${recipe.source.data_snapshot.data_version}`));
  return root;
}

function mealPlan(view, ctx) {
  const root = node('div'); const recipes = view.data.recipes || [];
  const servingsLocked = ctx.locked || !['planning', 'shopping', 'prep'].includes(ctx.stage);
  const removalLocked = ctx.locked || !['idea', 'planning', 'shopping', 'completed'].includes(ctx.stage);
  if (!recipes.length) { root.append(hint('先从菜谱候选中确认想做的菜。')); return root; }
  for (const recipe of recipes) {
    const card = node('div', 'ui-plan-recipe');
    card.append(node('strong', '', recipe.name), hint(recipeMeta(recipe)));
    const form = node('form', 'ui-serving-form');
    const label = node('label', '', '份量'); const input = node('input', 'ui-input'); input.type = 'number'; input.min = '0.5'; input.step = '0.5'; input.max = '100'; input.value = recipe.target_servings ?? recipe.servings ?? ''; input.disabled = servingsLocked; input.required = true;
    input.setAttribute('aria-label', `${recipe.name}的人份`); label.append(input, document.createTextNode(' 人份')); form.append(label);
    input.dataset.controlKey = `servings:${recipe.recipe_id}`;
    const save = button('调整', async () => { if (form.reportValidity()) await ctx.action('change_servings', { recipe_id: recipe.recipe_id, servings: Number(input.value) }); }, servingsLocked, 'ui-text-button');
    form.append(save); form.addEventListener('submit', (event) => { event.preventDefault(); save.click(); }); card.append(form);
    if (recipe.estimated_servings) card.append(hint('原配方人数含估算，份量调整仍保留这一依据。'));
    if (recipe.original_servings == null) card.append(hint(`原人数未知，暂不能换算${recipe.target_servings == null ? '。' : `；已记录目标 ${recipe.target_servings} 人份。`}`));
    const actions = node('div', 'ui-actions');
    actions.append(button('查看详情', () => ctx.action('open_recipe_detail', { recipe_id: recipe.recipe_id }), ctx.locked, 'ui-text-button'),
      button('选择这道菜的步骤', () => ctx.action('set_active_recipe', { recipe_id: recipe.recipe_id }), ctx.locked, 'ui-text-button'),
      button('移除', () => ctx.action('select_recipes', { recipe_ids: recipes.filter((item) => item.recipe_id !== recipe.recipe_id).map((item) => item.recipe_id) }), removalLocked || recipes.length <= 1, 'ui-text-button'));
    card.append(actions); root.append(card);
  }
  return root;
}

function shoppingList(view, ctx) {
  const root = node('div');
  const locked = ctx.locked || !['planning', 'shopping', 'prep'].includes(ctx.stage);
  for (const item of view.data.items || []) {
    const label = node('label', `ui-check-row${item.checked ? ' checked' : ''}`); const input = node('input'); input.type = 'checkbox'; input.checked = Boolean(item.checked); input.disabled = locked;
    input.dataset.controlKey = `shopping:${item.id}`;
    input.addEventListener('change', () => ctx.action('check_shopping_item', { item_id: item.id, checked: input.checked }).catch(() => { input.checked = Boolean(item.checked); }));
    const copy = node('span'); copy.append(node('strong', '', item.canonical_name), node('small', '', amount(item)));
    if (item.estimated) copy.append(badge('估算')); label.append(input, copy); root.append(label);
  }
  if (!view.data.items?.length) root.append(hint('当前没有待准备项。'));
  for (const note of view.data.notes || []) root.append(hint(note));
  return root;
}

function prepChecklist(view, ctx) {
  const root = node('div');
  const locked = ctx.locked || !['shopping', 'prep'].includes(ctx.stage);
  for (const item of view.data.items || []) {
    const row = node('div', `ui-prep-row ${item.status === 'ready' ? 'checked' : ''}`); const label = node('label', 'ui-check-row'); const input = node('input'); input.type = 'checkbox'; input.checked = item.status === 'ready'; input.disabled = locked;
    input.dataset.controlKey = `prep:${item.id}`;
    input.addEventListener('change', () => ctx.action('check_prep_item', { item_id: item.id, status: input.checked ? 'ready' : 'pending' }).catch(() => { input.checked = item.status === 'ready'; }));
    const copy = node('span'); copy.append(node('strong', '', item.canonical_name), node('small', '', amount(item)));
    if (item.raw_text) copy.append(node('small', 'ui-evidence', item.raw_text));
    if (item.estimated) copy.append(badge('估算')); label.append(input, copy); row.append(label);
    row.append(button(item.status === 'skipped' ? '恢复' : '跳过', () => ctx.action('check_prep_item', { item_id: item.id, status: item.status === 'skipped' ? 'pending' : 'skipped' }), locked, 'ui-text-button'));
    if (item.status === 'skipped') row.append(badge('已跳过', 'muted')); root.append(row);
  }
  for (const note of view.data.notes || []) root.append(hint(note));
  if (!ctx.historical && view.data.items?.length && !['shopping', 'prep'].includes(ctx.stage)) root.append(hint('切到采购或备菜阶段后，可以逐项确认。'));
  if (!view.data.items?.length) root.append(hint('选定菜谱后可以开始逐项备菜。'));
  return root;
}

function cookingGuide(view, ctx) {
  const root = node('div'); const data = view.data;
  if (!data.recipe_id) { root.append(hint('在菜单中选择一道菜，开始查看步骤。')); return root; }
  root.append(node('h4', 'ui-recipe-name', data.recipe_name));
  for (const step of data.steps || []) {
    const card = node('div', `ui-cooking-step ${step.status}`);
    const heading = node('div', 'ui-step-heading'); heading.append(badge(String(Number(step.ordinal) + 1)), node('span', '', step.status === 'done' ? '已完成' : step.id === data.active_step_id ? '当前步骤' : '待进行'));
    card.append(heading, node('p', 'ui-step-instruction', step.instruction));
    const actions = node('div', 'ui-actions');
    actions.append(button(step.status === 'done' ? '取消完成' : '完成这一步', () => ctx.action('complete_step', { recipe_id: data.recipe_id, step_id: step.id, completed: step.status !== 'done' }), ctx.locked || ctx.stage !== 'cooking'));
    if (step.status !== 'done' && step.id !== data.active_step_id) actions.append(button('切到这一步', () => ctx.action('set_active_step', { recipe_id: data.recipe_id, step_id: step.id }), ctx.locked || ctx.stage !== 'cooking', 'ui-text-button'));
    if (step.timer_seconds > 0) actions.append(button(`计时 ${Math.round(step.timer_seconds / 60 * 10) / 10} 分钟`, () => ctx.action('start_timer', { recipe_id: data.recipe_id, step_id: step.id, seconds: step.timer_seconds, label: `${data.recipe_name} · 第 ${Number(step.ordinal) + 1} 步` }), ctx.locked, 'ui-text-button'));
    card.append(actions); root.append(card);
  }
  return root;
}

function timers(view, ctx) {
  const root = node('div');
  const newTimerLocked = ctx.locked || ctx.stage !== 'cooking';
  for (const timer of view.data.timers || []) {
    const card = node('div', 'ui-timer'); card.append(node('strong', '', timer.label));
    const time = node('time', 'ui-timer-clock', clockText(remainingSeconds(timer))); time.dataset.timerId = timer.id;
    const remaining = remainingSeconds(timer); const status = remaining === 0 && timer.status === 'running' ? '时间到' : ({ running: '计时中', paused: '已暂停', finished: '已结束', cancelled: '已取消' })[timer.status] || '状态未知';
    card.append(time, badge(status, 'muted'));
    const actions = node('div', 'ui-actions');
    if (timer.status === 'running') actions.append(button('暂停', () => ctx.action('pause_timer', { timer_id: timer.id }), ctx.locked, 'ui-text-button'));
    if (timer.status === 'paused') actions.append(button('继续', () => ctx.action('resume_timer', { timer_id: timer.id }), ctx.locked, 'ui-text-button'));
    if (['running', 'paused'].includes(timer.status)) actions.append(button('结束计时', () => ctx.action('cancel_timer', { timer_id: timer.id }), ctx.locked, 'ui-text-button'));
    card.append(actions); root.append(card);
  }
  if (!view.data.timers?.length) root.append(hint('计时器随任务保存，关闭页面后仍可按截止时间恢复。'));
  const form = node('form', 'ui-timer-form'); const label = node('input', 'ui-input'); label.placeholder = '计时名称'; label.maxLength = 100; label.setAttribute('aria-label', '计时名称'); label.disabled = newTimerLocked;
  const minutes = node('input', 'ui-input'); minutes.type = 'number'; minutes.min = '0.1'; minutes.max = '1440'; minutes.step = '0.1'; minutes.required = true; minutes.placeholder = '分钟'; minutes.setAttribute('aria-label', '计时分钟数'); minutes.disabled = newTimerLocked;
  const start = button('开始计时', async () => { if (form.reportValidity()) await ctx.action('start_timer', { seconds: Math.round(Number(minutes.value) * 60), label: label.value.trim() || '厨房计时' }); }, newTimerLocked);
  form.append(label, minutes, start); form.addEventListener('submit', (event) => { event.preventDefault(); start.click(); }); root.append(form);
  return root;
}

function taskProgress(view, ctx) {
  const root = node('div'); const data = view.data; const stages = node('div', 'ui-stages');
  for (const [stage, label] of Object.entries(STAGES)) {
    const item = button(label, () => ctx.action('change_stage', { stage }), ctx.locked || !canChangeStage(data, stage), `ui-stage${data.stage === stage ? ' active' : ''}`);
    item.setAttribute('aria-pressed', String(data.stage === stage)); stages.append(item);
  }
  root.append(stages, hint(`${data.recipe_count ?? 0} 道菜 · 备菜 ${data.prep_ready ?? 0}/${data.prep_total ?? 0} · 步骤 ${data.steps_done ?? 0}/${data.steps_total ?? 0}`));
  if (!ctx.historical && data.stage === 'prep' && data.prep_ready < data.prep_total) root.append(hint('食材尚未全部确认，标记备齐或明确跳过后再开始烹饪。'));
  if (!ctx.historical && data.stage === 'cooking' && data.steps_done < data.steps_total) root.append(hint('完成全部步骤后，再将这次烹饪标为完成。'));
  return root;
}

for (const [kind, renderer] of Object.entries({ preference_form: preferenceForm, recipe_choices: recipeChoices,
  recipe_detail: recipeDetail, meal_plan: mealPlan, shopping_list: shoppingList, prep_checklist: prepChecklist,
  cooking_guide: cookingGuide, timers, task_progress: taskProgress })) registerRenderer(kind, renderer);
