import { COMPONENT_KINDS, STAGES, EXECUTION_STAGES, splitTerms } from './ui-protocol.js';
import { pageWindow } from './presentation.js';
import { icon } from './icons.js';
import {durationText, timerPresentation, timerForStep, manualTimerSeconds, timerDraftParts} from './cooking-timer.js';

export function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text != null) item.textContent = String(text);
  return item;
}
function button(text, callback, disabled = false, className = 'ui-button') {
  const item = node('button', className, text); item.type = 'button'; item.disabled = disabled;
  const glyph = ({ '上一页': 'chevron-left', '下一页': 'chevron-right', '上一步': 'chevron-left', '下一步': 'chevron-right',
    '全屏查看': 'maximize', '查看做法': 'book-open', '暂停': 'pause', '继续': 'play', '结束计时': 'square',
    '移除': 'trash-2', '完成这一步': 'check', '取消完成': 'undo-2' })[text];
  if (glyph) item.prepend(icon(glyph));
  item.addEventListener('click', () => Promise.resolve(callback()).catch(error => {
    console.warn('界面操作未完成：', error?.message || '请重试。');
  })); return item;
}
function badge(text, className = '') { return node('span', `ui-badge ${className}`, text); }
function hint(text) { return node('p', 'ui-hint', text); }
function pagedList(items,ctx,key,render,size=4) {
  // The full-screen deck measures the available space itself, including long rows.
  // Keep its complete list available; an inline fixed-size page would hide later items.
  if (ctx.focused) {
    const root = node('div', 'paged-content');
    items.forEach((item, index) => { const row = render(item); row.dataset.focusKey = `${key}:${item.id || item.recipe_id || index}`; root.append(row); });
    return root;
  }
  const root=node('div','paged-content'); const page=pageWindow(items,ctx.viewState?.[key] || 0,size);
  for(const item of page.items) root.append(render(item));
  if(page.pages>1) {
    const controls=node('div','page-controls'); controls.setAttribute('aria-label','内容分页');
    const change=delta=>{ctx.viewState[key]=page.index+delta;ctx.onViewState();};
    controls.append(button('上一页',()=>change(-1),page.index===0,'page-button'),node('span','',`${page.index+1} / ${page.pages} · 共 ${page.total} 项`),button('下一页',()=>change(1),page.index===page.pages-1,'page-button'));
    root.append(controls);
  }
  return root;
}
function disclosure(title,texts) {
  const details=node('details','ui-disclosure');details.append(node('summary','',title));
  for(const text of texts) details.append(hint(text));return details;
}
function sectionTabs(root,ctx,key,sections) {
  const selected=ctx.viewState?.[key] || sections[0][0];
  const nav=node('div','section-tabs');nav.setAttribute('role','tablist');nav.setAttribute('aria-label','菜谱内容');
  for(const [name,label] of sections) {
    const tab=button(label,()=>{ctx.viewState[key]=name;ctx.onViewState();},false,'section-tab');
    tab.setAttribute('role','tab');tab.setAttribute('aria-selected',String(selected===name));nav.append(tab);
  }
  root.append(nav);return selected;
}
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
  const title = node('h3', '', view.title || '任务内容');
  const glyph = ({ preference_form: 'list-checks', recipe_choices: 'utensils', recipe_detail: 'book-open',
    meal_plan: 'chef-hat', prep_checklist: 'list-checks',
    cooking_guide: 'chef-hat', timers: 'timer', task_progress: 'circle-check' })[view.kind];
  if (glyph) title.prepend(icon(glyph));
  heading.append(title);
  if (context.historical) heading.append(badge('历史记录', 'muted'));
  card.append(heading);
  if (!context.historical && !context.focused && (['prep_checklist','cooking_guide'].includes(view.kind)
      || view.kind==='recipe_detail' && context.viewState?.detailSection==='steps')) {
    const focus = button('全屏查看',()=>context.openFocus?.(view,focus),false,'focus-button');
    heading.append(focus);
  }
  const renderer = rendererRegistry.get(view.kind);
  const ctx = { ...context, locked, viewState:context.viewState || {},onViewState:context.onViewState || (()=>{}),
    action: (type, payload) => context.action(type, payload, view) };
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
    feedback.textContent = ctx.dirty(view, values) ? '未保存 · 发送消息时会先同步这些条件' : '已同步';
  };
  const fieldPage=pageWindow(view.data.fields || [],ctx.viewState.formPage || 0,3);
  for (const field of fieldPage.items) {
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
  if(fieldPage.pages>1) {
    const navigation=node('div','page-controls');
    const change=delta=>{if(root.reportValidity()) {ctx.viewState.formPage=fieldPage.index+delta;ctx.onViewState();}};
    navigation.append(button('上一页',()=>change(-1),fieldPage.index===0,'page-button'),node('span','',`条件 ${fieldPage.index+1} / ${fieldPage.pages}`),button('下一页',()=>change(1),fieldPage.index===fieldPage.pages-1,'page-button'));
    root.append(navigation);
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
  const save = async () => {
    if (!root.reportValidity()) return;
    await ctx.savePreferences(view, values);
  };
  root.append(feedback);
  if(ctx.stage && ctx.stage!=='idea') root.append(hint('条件用于后续查询；已选菜的份量请在餐单中调整。'));
  root.addEventListener('submit', (event) => { event.preventDefault(); if(!ctx.locked) save(); });
  return root;
}

function recipeChoices(view, ctx) {
  const root = node('div'); const recipes = view.data.recipes || [];
  if (!recipes.length) { root.append(hint('还没有符合条件的菜谱，继续告诉助手你的偏好。')); return root; }
  const selected = new Set(ctx.selectedRecipeIds || []);
  const selection = new Set(ctx.draft?.selectedIds ?? selected);
  const selectionNote = node('p', 'ui-hint', `已选 ${selection.size} 道，最多 4 道；确认后保存到餐单。`);
  const selectedNames = node('div','selected-recipes');
  const updateSelectedNames = () => selectedNames.replaceChildren(...recipes.filter(recipe=>selection.has(recipe.recipe_id)).map(recipe=>badge(recipe.name,'muted')));
  updateSelectedNames();
  const locked = ctx.locked || EXECUTION_STAGES.includes(ctx.stage);
  const list = node('div', 'ui-recipe-list');
  list.append(pagedList(recipes,ctx,'choicesPage',(recipe)=>{
    const row = node('div', 'ui-recipe-choice'); const label = node('label', 'ui-choice-label');
    const checkbox = node('input'); checkbox.type = 'checkbox'; checkbox.checked = selection.has(recipe.recipe_id); checkbox.disabled = locked;
    checkbox.dataset.controlKey = `choose:${recipe.recipe_id}`;
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) selection.add(recipe.recipe_id); else selection.delete(recipe.recipe_id);
      selectionNote.textContent = selection.size > 4 ? '最多选择 4 道菜，请减少选择。' : `已勾选 ${selection.size} 道，确认后保存到当前菜单。`;
      ctx.onSelection?.(view, [...selection]);
      updateSelectedNames();
    });
    const copy = node('span'); copy.append(node('strong', '', recipe.name), node('small', '', recipeMeta(recipe)));
    label.append(checkbox, copy); row.append(label);
    if (recipe.nutrition?.estimated) copy.append(badge('营养估算'));
    row.append(button('详情', () => ctx.action('open_recipe_detail', { recipe_id: recipe.recipe_id }), locked, 'ui-text-button'));
    return row;
  },3));
  if (ctx.draft?.conflict) root.append(hint('候选或菜单已经变化，请复核当前内容；需要时先放弃旧选择。'));
  root.append(list, selectedNames, selectionNote);
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
  if (recipe.description) root.append(disclosure('关于这道菜',[recipe.description]));
  if (recipe.servings != null) root.append(hint(`${recipe.servings} 人份${recipe.estimated_servings ? '（人数估算）' : ''}`));
  else if (recipe.target_servings != null) root.append(hint(`目标 ${recipe.target_servings} 人份 · 原人数未知，暂不能换算。`));
  const tab=sectionTabs(root,ctx,'detailSection',[['ingredients','食材'],['steps','做法'],['nutrition','营养'],['notes','说明']]);
  if(tab==='ingredients') root.append(pagedList(recipe.ingredients || [],ctx,'ingredientsPage',(item)=>{
    const row = node('div', 'ui-ingredient'); row.append(node('span', '', item.canonical_name), node('span', '', amount(item)));
    if (item.estimated) row.append(badge('估算')); return row;
  },5));
  if(tab==='steps') root.append(pagedList(recipe.steps || [],ctx,'detailSteps',(step)=>node('p','ui-step-instruction',step.instruction || step.description || step),2));
  if(tab==='nutrition') root.append(hint('每份营养 · 未知数值保持未知'),nutritionNode(recipe.nutrition));
  if(tab==='notes') {
    root.append(pagedList(recipe.warnings || [],ctx,'notesPage',warning=>hint(warning),2));
    for(const ref of recipe.references || []) {
      try { const url=new URL(ref.url); if(!['http:','https:'].includes(url.protocol) || url.username || url.password) continue;
        const link=node('a','reference-link',ref.title || '参考来源');link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';root.append(link);
      } catch { /* Invalid references remain unavailable. */ }
    }
    if(recipe.source?.data_snapshot?.data_version) root.append(hint(`来源版本 ${recipe.source.data_snapshot.data_version}`));
  }
  return root;
}

function mealPlan(view, ctx) {
  const root = node('div'); const recipes = view.data.recipes || [];
  const servingsLocked = ctx.locked || !['planning', 'prep'].includes(ctx.stage);
  const removalLocked = ctx.locked || !['idea', 'planning', 'completed'].includes(ctx.stage);
  if (!recipes.length) { root.append(hint('先从菜谱候选中确认想做的菜。')); return root; }
  root.append(pagedList(recipes,ctx,'mealPage',(recipe)=>{
    const card = node('div', 'ui-plan-recipe');
    card.append(node('strong', '', recipe.name), hint(recipeMeta(recipe)));
    if(recipe.recipe_id===ctx.activeRecipeId) card.append(badge('当前菜品','muted'));
    const form = node('form', 'ui-serving-form');
    const label = node('label', '', '份量'); const input = node('input', 'ui-input'); input.type = 'number'; input.min = '0.5'; input.step = '0.5'; input.max = '100'; input.value = ctx.draft?.servings?.[recipe.recipe_id] ?? recipe.target_servings ?? recipe.servings ?? ''; input.disabled = servingsLocked; input.required = true;
    input.setAttribute('aria-label', `${recipe.name}的人份`); label.append(input, document.createTextNode(' 人份')); form.append(label);
    input.dataset.controlKey = `servings:${recipe.recipe_id}`;
    input.addEventListener('input',()=>ctx.onServings?.(view,recipe.recipe_id,input.value===''?null:Number(input.value)));
    form.addEventListener('submit',event=>event.preventDefault());card.append(form);
    if (recipe.estimated_servings) card.append(hint('原配方人数含估算，份量调整仍保留这一依据。'));
    if (recipe.original_servings == null) card.append(hint(`原人数未知，暂不能换算${recipe.target_servings == null ? '。' : `；已记录目标 ${recipe.target_servings} 人份。`}`));
    const actions = node('div', 'ui-actions');
    actions.append(button('查看详情', () => ctx.action('open_recipe_detail', { recipe_id: recipe.recipe_id }), ctx.locked, 'ui-text-button'),
      button('查看做法', () => ctx.openRecipeSection(recipe.recipe_id,'steps'), ctx.locked, 'ui-text-button'),
      button('设为当前菜品', () => ctx.action('set_active_recipe', { recipe_id: recipe.recipe_id }), ctx.locked || recipe.recipe_id===ctx.activeRecipeId, 'ui-text-button'),
      button('移除', () => ctx.action('select_recipes', { recipe_ids: recipes.filter((item) => item.recipe_id !== recipe.recipe_id).map((item) => item.recipe_id) }), removalLocked || recipes.length <= 1, 'ui-text-button'));
    card.append(actions); return card;
  },2));
  if(!ctx.historical && ctx.stage==='planning') root.append(hint('选好后告诉助手，它会继续帮你准备食材。'));
  return root;
}

function checklistScope(view, ctx) {
  const navigation = node('nav','checklist-scope'); navigation.setAttribute('aria-label','食材清单范围');
  const selected = view.data.recipe_ids;
  for (const [ids,label] of [[null,'整餐合并'], ...(ctx.mealRecipes || []).map(recipe => [[recipe.recipe_id],recipe.name])]) {
    const active = ids === null ? selected == null : selected?.length === 1 && selected[0] === ids[0];
    const control = button(label, async () => {
      await ctx.action('set_checklist_scope',{recipe_ids:ids});
      ctx.viewState.prepPage=0;ctx.onViewState();
    },ctx.locked || active,'ui-text-button');
    control.setAttribute('aria-pressed',String(active));navigation.append(control);
  }
  return navigation;
}

function prepChecklist(view, ctx) {
  const root = node('div');
  root.append(checklistScope(view,ctx));
  for(const text of view.data.guidance || []) root.append(node('div','ui-adaptation',text));
  const locked = ctx.locked || ctx.stage!=='prep';
  const prepared=(view.data.items || []).filter(item=>['ready','skipped'].includes(item.status)).length;
  root.append(hint(`已确认 ${prepared} / ${view.data.items?.length || 0} 项`));
  root.append(pagedList(view.data.items || [],ctx,'prepPage',(item)=>{
    const row = node('div', `ui-prep-row ${item.status === 'ready' ? 'checked' : ''}`); const label = node('label', 'ui-check-row'); const input = node('input'); input.type = 'checkbox'; input.checked = item.status === 'ready'; input.disabled = locked;
    input.dataset.controlKey = `prep:${item.id}`;
    input.addEventListener('change', () => ctx.action('check_prep_item', { item_id: item.id, status: input.checked ? 'ready' : 'pending' }).catch(() => { input.checked = item.status === 'ready'; }));
    const copy = node('span'); copy.append(node('strong', '', item.canonical_name), node('small', '', amount(item)));
    if (item.raw_text) copy.title=`原文：${item.raw_text}`;
    if (item.estimated) copy.append(badge('估算')); label.append(input, copy); row.append(label);
    row.append(button(item.status === 'skipped' ? '恢复' : '跳过', () => ctx.action('check_prep_item', { item_id: item.id, status: item.status === 'skipped' ? 'pending' : 'skipped' }), locked, 'ui-text-button'));
    if (item.status === 'skipped') row.append(badge('已跳过', 'muted')); return row;
  },ctx.focused?8:4));
  if(view.data.notes?.length) root.append(disclosure('备菜与原文说明',[...view.data.notes,...(view.data.items || []).map(item=>`${item.canonical_name}：${item.raw_text || '未记录原文'}`)]));
  if (!ctx.historical && view.data.items?.length && ctx.stage!=='prep') root.append(hint('进入备菜阶段后，可以逐项确认。'));
  if (!view.data.items?.length) root.append(hint('选定菜谱后可以开始逐项备菜。'));
  return root;
}

function cookingGuide(view, ctx) {
  const root = node('div', 'ui-cooking-guide'); const data = view.data;
  if (!data.recipe_id) { root.append(hint('在菜单中选择一道菜，开始查看步骤。')); return root; }
  root.append(node('h4', 'ui-recipe-name', data.recipe_name));
  for(const text of data.guidance || []) root.append(node('div','ui-adaptation',text));
  const allSteps=data.steps || [];
  if (!allSteps.length) { root.append(hint('这道菜暂时没有可用步骤。')); return root; }
  if(ctx.viewState.recipeId!==data.recipe_id) { ctx.viewState.recipeId=data.recipe_id;delete ctx.viewState.stepIndex; }
  const activeIndex=Math.max(0,allSteps.findIndex(step=>step.id===data.active_step_id));
  const stepIndex=Math.max(0,Math.min(allSteps.length-1,ctx.viewState?.stepIndex ?? activeIndex));
  const completed=allSteps.filter(step=>step.status==='done').length;
  const overview=node('div','cooking-overview');
  overview.append(node('span','cooking-progress-label',`已完成 ${completed} / ${allSteps.length} 步`));
  const progress=node('progress','cooking-progress');progress.max=allSteps.length;progress.value=completed;
  progress.setAttribute('aria-label','烹饪步骤完成进度');overview.append(progress);root.append(overview);
  const navigation=node('div','step-navigator cooking-step-nav');navigation.setAttribute('aria-label','浏览烹饪步骤');
  navigation.append(button('上一步',()=>{ctx.viewState.stepIndex=Math.max(0,stepIndex-1);ctx.onViewState();},stepIndex<=0,'page-button'),
    node('strong','',`第 ${stepIndex+1} / ${allSteps.length} 步`),button('下一步',()=>{ctx.viewState.stepIndex=Math.min(allSteps.length-1,stepIndex+1);ctx.onViewState();},stepIndex>=allSteps.length-1,'page-button'));
  root.append(navigation);
  for (const step of allSteps[stepIndex] ? [allSteps[stepIndex]] : []) {
    const state=step.status==='done'?'done':step.id===data.active_step_id?'current':'browsing';
    const card = node('div', `ui-cooking-step ${state}`);card.dataset.stepId=step.id;
    const heading = node('div', 'ui-step-heading');
    heading.append(node('span','cooking-step-number',String(Number(step.ordinal) + 1).padStart(2,'0')),
      node('span',`cooking-step-status ${state}`,({done:'这一步已完成',current:'正在做这一步',browsing:'正在浏览'})[state]));
    card.append(heading, node('p', 'ui-step-instruction', step.adapted_instruction || step.instruction));
    if(step.adapted_instruction) {
      card.append(hint(`按你的条件调整：${step.adaptation_reason || ''}`));
      card.append(disclosure('查看原菜谱步骤',[step.instruction]));
    }
    const actions = node('div', 'ui-actions cooking-step-actions');
    const complete=button(step.status === 'done' ? '取消完成' : '完成这一步', () => ctx.action('complete_step', { recipe_id: data.recipe_id, step_id: step.id, completed: step.status !== 'done' }), ctx.locked || ctx.stage !== 'cooking',step.status==='done'?'ui-button cooking-secondary':'ui-button cooking-primary');
    actions.append(complete);
    if (step.id!==data.active_step_id && data.active_step_id) {
      const back=button('回到正在做的步骤',()=>{delete ctx.viewState.stepIndex;ctx.onViewState();},false,'ui-button cooking-secondary');
      back.prepend(icon('arrow-left'));actions.append(back);
      if(step.status!=='done') actions.append(button('从这一步继续',()=>ctx.action('set_active_step',{recipe_id:data.recipe_id,step_id:step.id}),ctx.locked || ctx.stage!=='cooking','ui-button cooking-secondary'));
    }
    card.append(actions);
    const savedTimer=timerForStep(data.timers,data.recipe_id,step.id);
    if(savedTimer) card.append(timerRow(savedTimer,ctx,true));
    else if (step.timer_seconds > 0 && !step.adapted_instruction && step.status!=='done') {
      const timing=node('div','cooking-step-timing');
      timing.append(node('span','cooking-timing-note',`这一步可计时 ${durationText(step.timer_seconds)}`));
      const start=button(`开始 ${durationText(step.timer_seconds)}计时`,()=>ctx.action('start_timer',{recipe_id:data.recipe_id,step_id:step.id,seconds:step.timer_seconds,label:`${data.recipe_name} · 第 ${Number(step.ordinal)+1} 步`}),ctx.locked || ctx.stage!=='cooking','ui-button cooking-secondary timer-start-button');
      start.prepend(icon('play'));timing.append(start);card.append(timing);
    }
    root.append(card);
  }
  const note=hint(ctx.stage==='cooking'?'做完后点“完成这一步”，计时结束不会代你确认步骤。':'当前可以浏览做法，进入烹饪后再确认步骤和计时。');
  note.classList.add('cooking-step-note');if(!ctx.focused)root.append(note);
  return root;
}

function timerRow(timer,ctx,attached=false) {
  const display=timerPresentation(timer,ctx.timerNow?.() ?? Date.now());
  const row=node('section',`ui-timer ui-timer-panel${attached?' step-attached-timer':''}`);
  row.dataset.timerRow=timer.id;row.dataset.timerState=display.state;
  const title=node('div','timer-heading');title.append(icon('timer'),node('strong','timer-label',timer.label || '厨房计时'));
  const status=node('span','timer-status',display.status);status.dataset.timerStatus=timer.id;status.setAttribute('aria-live','polite');
  title.append(status);row.append(title);
  const readout=node('div','timer-readout');
  const clock=node('time','ui-timer-clock',display.clock);clock.dataset.timerId=timer.id;
  clock.dataset.timerState=display.state;
  clock.setAttribute('aria-label',`剩余 ${display.remaining==null?'时间未知':durationText(display.remaining)}`);
  readout.append(clock,node('span','timer-duration',`共 ${durationText(timer.seconds)}`));row.append(readout);
  const progress=node('progress','timer-progress');progress.max=1;progress.value=display.progress;
  progress.dataset.timerProgress=timer.id;progress.setAttribute('aria-label','计时剩余进度');row.append(progress);
  const controls=node('div','ui-actions timer-controls');controls.dataset.timerControls=timer.id;controls.hidden=display.expired;
  const cookingLocked=ctx.locked || ctx.stage!=='cooking';
  if(timer.status==='running') controls.append(button('暂停',()=>ctx.action('pause_timer',{timer_id:timer.id}),cookingLocked,'ui-button cooking-secondary'));
  if(timer.status==='paused') controls.append(button('继续',()=>ctx.action('resume_timer',{timer_id:timer.id}),cookingLocked,'ui-button cooking-primary'));
  if(['running','paused'].includes(timer.status)) controls.append(button('结束计时',()=>ctx.action('cancel_timer',{timer_id:timer.id}),ctx.locked,'ui-button cooking-secondary'));
  const restart=()=>ctx.action('start_timer',{seconds:timer.seconds,label:timer.label || '厨房计时',...(timer.recipe_id?{recipe_id:timer.recipe_id}:{}),...(timer.step_id?{step_id:timer.step_id}:{})});
  if(timer.status==='cancelled') {const retry=button('重新计时',restart,cookingLocked,'ui-button cooking-secondary');retry.prepend(icon('refresh-cw'));controls.append(retry);}
  const expired=node('div','timer-expired');expired.dataset.timerExpired=timer.id;expired.hidden=!display.expired;
  const message=node('p','timer-expired-message','时间到，看看锅里的状态。');message.prepend(icon('timer'));
  const retry=button('重新计时',restart,cookingLocked,'ui-button cooking-secondary');retry.prepend(icon('refresh-cw'));
  expired.append(message,retry);row.append(controls,expired);return row;
}

function timers(view, ctx) {
  const root = node('div','ui-timers-guide');
  const newTimerLocked = ctx.locked || ctx.stage !== 'cooking';
  root.append(pagedList(view.data.timers || [],ctx,'timerPage',timer=>timerRow(timer,ctx),2));
  if (!view.data.timers?.length) root.append(node('p','timer-empty','还没有计时器，设好时间就能开始。'));
  if(ctx.historical) return root;
  const form=node('form','ui-timer-form timer-create-form');form.setAttribute('aria-label','新建厨房计时器');
  form.append(node('h4','timer-form-title','新建计时'));
  const label=node('input','ui-input');label.placeholder='例如：焖煮、静置';label.maxLength=100;label.setAttribute('aria-label','计时名称');label.disabled=newTimerLocked;
  label.value=ctx.draft?.label || '';label.dataset.controlKey='timer-label';
  const parts=ctx.draft?.duration || timerDraftParts(ctx.draft?.minutes),duration=node('div','timer-duration-fields');
  const field=(caption,key,max,value)=>{const wrap=node('label','timer-duration-field');const input=node('input','ui-input');
    input.type='number';input.inputMode='numeric';input.min='0';input.max=String(max);input.step='1';input.value=value;input.disabled=newTimerLocked;
    input.setAttribute('aria-label',`计时${caption}数`);input.dataset.controlKey=key;wrap.append(input,node('span','',caption));duration.append(wrap);return input;};
  const minutes=field('分钟','timer-minutes',1440,parts.minutes),seconds=field('秒','timer-seconds',59,parts.seconds);
  const start=node('button','ui-button cooking-primary timer-submit','开始计时');start.type='submit';start.prepend(icon('play'));
  const feedback=node('p','timer-form-feedback');feedback.setAttribute('aria-live','polite');
  const lockedMessage=ctx.stage!=='cooking'?'进入烹饪阶段后可以启动计时。':'正在同步计时状态，请稍等。';
  const changed=()=>{const total=manualTimerSeconds(minutes.value,seconds.value);start.disabled=newTimerLocked || total==null;
    feedback.textContent=newTimerLocked?lockedMessage:total==null?'设置 1 秒到 24 小时之间的时长。':`启动后计时 ${durationText(total)}`;
    ctx.onTimer?.(view,label.value,total==null?null:total/60,{minutes:minutes.value,seconds:seconds.value});};
  for(const input of [label,minutes,seconds])input.addEventListener('input',changed);
  const initial=manualTimerSeconds(minutes.value,seconds.value);start.disabled=newTimerLocked || initial==null;
  feedback.textContent=newTimerLocked?lockedMessage:initial==null?'设置 1 秒到 24 小时之间的时长。':`启动后计时 ${durationText(initial)}`;
  form.append(label,duration,start,feedback);
  const startTimer=async event=>{event.preventDefault();const total=manualTimerSeconds(minutes.value,seconds.value);
    if(newTimerLocked || start.disabled || total==null || ![minutes,seconds,label].every(control=>control.reportValidity()))return;
    start.disabled=true;feedback.textContent='正在启动计时…';
    try {ctx.onTimer?.(view,label.value,total/60);
      if(ctx.onStartTimer) await ctx.onStartTimer(view,{seconds:total,label:label.value.trim() || '厨房计时'});
      else {await ctx.action('start_timer',{seconds:total,label:label.value.trim() || '厨房计时'});ctx.onTimer?.(view,'',null);}
      feedback.textContent='计时已开始。';
    } catch(error) {feedback.textContent=error.message || '计时未开始，请重试。';}
    finally {start.disabled=newTimerLocked || manualTimerSeconds(minutes.value,seconds.value)==null;}
  };
  form.addEventListener('submit',startTimer);
  start.addEventListener('click',event=>{if(!start.form)startTimer(event);});
  for(const input of [label,minutes,seconds])input.addEventListener('keydown',event=>{
    if(event.key==='Enter' && !input.form){event.preventDefault();startTimer(event);}
  });
  root.append(form);
  return root;
}

function taskProgress(view, ctx) {
  const root = node('div'); const data = view.data;
  root.append(badge(STAGES[data.stage] || '厨房会话'),hint(`${data.recipe_count ?? 0} 道菜 · 备菜 ${data.prep_ready ?? 0}/${data.prep_total ?? 0} · 步骤 ${data.steps_done ?? 0}/${data.steps_total ?? 0}`));
  if (!ctx.historical && data.stage === 'prep' && data.prep_ready < data.prep_total) root.append(hint('食材尚未全部确认，标记备齐或明确跳过后再开始烹饪。'));
  if (!ctx.historical && data.stage === 'cooking' && data.steps_done < data.steps_total) root.append(hint('完成全部步骤后，再将这次烹饪标为完成。'));
  return root;
}

for (const [kind, renderer] of Object.entries({ preference_form: preferenceForm, recipe_choices: recipeChoices,
  recipe_detail: recipeDetail, meal_plan: mealPlan, prep_checklist: prepChecklist,
  cooking_guide: cookingGuide, timers, task_progress: taskProgress })) registerRenderer(kind, renderer);
