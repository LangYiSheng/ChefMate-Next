import { useRef, useState, type ReactNode } from 'react';
import { Icon } from '../../components/Icon';
import { type Json, type View, type Timer, labels, stages, safeReference, durationText, manualTimerSeconds, timerPresentation, timerCapacity, completionText } from './protocol';
import { workspaceStore, type Entry } from './store';
import { ReadingPages } from './ReadingPages';

type Context = { sessionId: string; entry: Entry; historical?: boolean; focused?: boolean; onFocus?: (kind: string) => void; onRecipe?: (id: string) => void; onContinue?: (message: string) => void };
export function Button({ children, onClick, disabled, className = 'ui-button', ...rest }: { children: ReactNode; onClick?: () => unknown; disabled?: boolean; className?: string; [key: string]: any }) {
  return <button {...rest} className={className} type="button" disabled={disabled} onClick={() => { Promise.resolve(onClick?.()).catch(() => {}); }}>{children}</button>;
}
export const Hint = ({ children }: { children: ReactNode }) => <p className="ui-hint">{children}</p>;
export const CompletionNotice = ({ entry }: { entry: Entry }) => entry.snapshot?.stage === 'completed' && entry.completion ? <p className="workspace-completion" role="status"><Icon name="circle-check" /><span>{completionText(entry.completion)}</span></p> : null;
const Estimate = () => <span className="estimate-marker" title="这一项含估算值">估算</span>;
function Paged({ items, render, size = 4, label = '内容' }: { items: any[]; render: (item: any, index: number) => ReactNode; size?: number; label?: string }) {
  const [page, setPage] = useState(0), pages = Math.max(1, Math.ceil(items.length / size)), index = Math.min(page, pages - 1);
  return <div className="paged-content">{items.slice(index * size, (index + 1) * size).map((item, offset) => <div key={item.id || item.recipe_id || index * size + offset}>{render(item, index * size + offset)}</div>)}{pages > 1 && <nav className="page-controls" aria-label={`${label}分页`}><Button className="page-button" disabled={index === 0} onClick={() => setPage(index - 1)}>上一页</Button><span>{label} {index + 1} / {pages}</span><Button className="page-button" disabled={index === pages - 1} onClick={() => setPage(index + 1)}>下一页</Button></nav>}</div>;
}
const amount = (item: Json) => item.amount_text || (item.amount_value == null ? '用量未知' : `${item.amount_value} ${item.unit || ''}`.trim());
const recipeMeta = (recipe: Json) => `${recipe.total_time_min == null ? '耗时未知' : `${recipe.total_time_min} 分钟`} · ${({ beginner: '新手', normal: '普通', advanced: '进阶' } as Json)[recipe.difficulty] || recipe.difficulty || '难度未知'}`;
const locked = (view: View, ctx: Context) => !!ctx.historical || !view.editable || ctx.entry.saving || !!ctx.entry.failed;
const action = async (view: View, ctx: Context, type: string, payload: Json) => {
  const response = await workspaceStore.dispatch(ctx.sessionId, type, payload, view);
  if (type === 'open_recipe_detail') {
    if (ctx.onFocus) ctx.onFocus('recipe_detail');
    else ctx.onRecipe?.(payload.recipe_id);
  }
  return response;
};
export function WorkspaceError({ sessionId, entry }: { sessionId: string; entry: Entry }) {
  if (entry.saving && !entry.error || !entry.error && !entry.failed && !entry.conflict) return null;
  return <div className="error-box" role="alert"><p>{entry.error || '上次操作的结果尚未确认。'}</p>{entry.failed && <Button className="retry-button" disabled={entry.saving} onClick={() => workspaceStore.retryFailed(sessionId)}>重新同步这次操作</Button>}{entry.conflict && <><Button className="retry-button" disabled={entry.saving} onClick={() => workspaceStore.retryConflict(sessionId)}>复核后重试这次操作</Button><Button className="ui-text-button" disabled={entry.saving} onClick={() => workspaceStore.discardConflict(sessionId)}>放弃这次操作</Button></>}</div>;
}
function DraftActions({ view, ctx, onSave, label = '确认并继续', disabled = false }: { view: View; ctx: Context; onSave: () => unknown; label?: string; disabled?: boolean }) {
  const draft = ctx.entry.drafts[view.id]; if (!draft || ctx.historical) return null;
  return <div className="ui-actions workspace-draft-actions">{draft.conflict && <p className="ui-hint">任务已经变化，已保留你的修改；请对照当前内容复核后保存。</p>}<Button className="ui-button cooking-primary" disabled={locked(view, ctx) || disabled || ctx.entry.running} onClick={onSave}>{ctx.entry.saving ? '正在保存…' : draft.conflict ? `复核后${label}` : label}</Button><Button className="ui-text-button" disabled={ctx.entry.saving} onClick={() => workspaceStore.discard(ctx.sessionId, view.id)}>放弃修改</Button></div>;
}
function Preferences({ view, ctx }: { view: View; ctx: Context }) {
  const draft = ctx.entry.drafts[view.id], values = { ...(view.data.values || {}), ...(draft?.values || {}) }, disabled = locked(view, ctx), form = useRef<HTMLFormElement>(null);
  const update = (name: string, value: any) => workspaceStore.draft(ctx.sessionId, view, { baseline: draft?.baseline || view.data.values || {}, values: { ...values, [name]: value } });
  const [page, setPage] = useState(0), fields = view.data.fields || [], pages = Math.max(1, Math.ceil(fields.length / 3)), index = Math.min(page, pages - 1);
  const save = () => { if (form.current?.reportValidity()) return workspaceStore.savePreferences(ctx.sessionId, view, '我已确认这些偏好，请按保存的条件继续帮我选菜。'); };
  return <form ref={form} className="ui-form" onSubmit={event => { event.preventDefault(); if (!disabled && !ctx.entry.running) void save()?.catch(() => {}); }}>
    {fields.slice(index * 3, (index + 1) * 3).map((field: Json) => {
      const value = Object.hasOwn(values, field.name) ? values[field.name] : field.value, label = field.label || field.name;
      if (field.input_type === 'multiselect' && field.options?.length) {
        const selected: string[] = Array.isArray(value) ? value : [];
        return <fieldset className="ui-field" key={field.name} disabled={disabled}><legend className="ui-field-label">{label}</legend><div className="ui-options">{field.options.map((option: Json | string) => { const code = typeof option === 'string' ? option : option.value ?? option.code; return <label className="ui-option" key={code}><input type="checkbox" checked={selected.includes(code)} onChange={event => update(field.name, event.target.checked ? [...selected, code] : selected.filter(item => item !== code))} />{typeof option === 'string' ? option : option.label || code}</label>; })}{field.name === 'available_tools' && <label className="ui-option"><input type="checkbox" checked={value == null} onChange={event => update(field.name, event.target.checked ? null : selected)} />不限制厨具</label>}</div></fieldset>;
      }
      if (field.input_type === 'checkbox') return <label className="ui-field ui-checkbox-field" key={field.name}><input type="checkbox" aria-label={label} checked={!!value} disabled={disabled} onChange={event => update(field.name, event.target.checked)} />{label}</label>;
      const text = Array.isArray(value) ? value.join('、') : value ?? '';
      const change = (text: string) => update(field.name, field.input_type === 'number' ? text === '' ? null : Number(text) : field.input_type === 'multiselect' ? field.name === 'available_tools' && !text.trim() ? null : text.split(/[,，、;；\n]/).map(item => item.trim()).filter(Boolean) : text);
      return <label className="ui-field" key={field.name}><span className="ui-field-label">{label}</span>{field.input_type === 'textarea' ? <textarea className="ui-input" aria-label={label} value={text} rows={2} maxLength={2000} disabled={disabled} onChange={event => change(event.target.value)} /> : <input className="ui-input" aria-label={label} type={field.input_type === 'number' ? 'number' : 'text'} value={text} min={field.name === 'servings' ? 1 : 0} step={field.name === 'servings' ? .5 : 1} maxLength={500} disabled={disabled} placeholder={field.input_type === 'multiselect' ? '多个名称用逗号分隔' : undefined} onChange={event => change(event.target.value)} />}</label>;
    })}
    {pages > 1 && <nav className="page-controls" aria-label="条件分页"><Button className="page-button" disabled={index === 0} onClick={() => setPage(index - 1)}>上一页</Button><span>条件 {index + 1} / {pages}</span><Button className="page-button" disabled={index === pages - 1} onClick={() => setPage(index + 1)}>下一页</Button></nav>}
    {draft?.conflict && <details className="ui-conflict-values"><summary>查看当前已保存的条件</summary>{fields.map((field: Json) => <p key={field.name}>{field.label}：{Array.isArray(view.data.values?.[field.name]) ? view.data.values[field.name].join('、') : String(view.data.values?.[field.name] ?? '未设置')}</p>)}</details>}
    <p className="ui-sync-label">{ctx.historical ? '当时保存的条件' : draft ? '未保存 · 发送消息时会先同步这些条件' : '已同步'}</p>
    <DraftActions view={view} ctx={ctx} label="保存条件并继续" onSave={save} />{ctx.entry.snapshot?.stage !== 'idea' && <Hint>条件用于后续查询；已选菜的份量请在餐单中调整。</Hint>}
  </form>;
}
function Choices({ view, ctx }: { view: View; ctx: Context }) {
  const recipes: Json[] = view.data.recipes || [], draft = ctx.entry.drafts[view.id], selected: string[] = draft?.selectedIds ?? ctx.entry.snapshot?.selected_recipe_ids ?? [], disabled = locked(view, ctx) || ['prep', 'cooking', 'completed'].includes(ctx.entry.snapshot?.stage);
  const select = (id: string, checked: boolean) => workspaceStore.draft(ctx.sessionId, view, { selectedIds: checked ? [...new Set([...selected, id])] : selected.filter(item => item !== id) });
  const save = () => workspaceStore.dispatch(ctx.sessionId, 'select_recipes', { recipe_ids: selected }, view, { revision: draft?.conflict ? ctx.entry.snapshot?.revision : draft?.baseRevision, clearDraft: view.id, continuation: `我已确认菜品：${recipes.filter(recipe => selected.includes(recipe.recipe_id)).map(recipe => recipe.name).join('、')}。请按当前餐单继续安排。` });
  return <div>{!recipes.length && <Hint>还没有符合条件的菜谱，继续告诉助手你的偏好。</Hint>}<Paged label="候选菜品" items={recipes} size={3} render={recipe => <div className="ui-recipe-choice"><label className="ui-choice-label"><input type="checkbox" checked={selected.includes(recipe.recipe_id)} disabled={disabled} onChange={event => select(recipe.recipe_id, event.target.checked)} /><span><strong>{recipe.name}</strong><small>{recipeMeta(recipe)}</small>{recipe.nutrition?.estimated && <Estimate />}</span></label><Button className="ui-text-button" disabled={locked(view, ctx)} onClick={() => action(view, ctx, 'open_recipe_detail', { recipe_id: recipe.recipe_id })}>详情</Button></div>} /><div className="selected-recipes">{recipes.filter(recipe => selected.includes(recipe.recipe_id)).map(recipe => <span className="ui-badge muted" key={recipe.recipe_id}>{recipe.name}</span>)}</div><Hint>已选 {selected.length} 道，最多 4 道；确认后保存到餐单。</Hint><DraftActions view={view} ctx={ctx} label="确认菜品并继续" disabled={disabled || !selected.length || selected.length > 4} onSave={save} /></div>;
}
function Nutrition({ data }: { data: Json }) {
  return <div className="ui-nutrition">{[['calories_kcal', '能量', 'kcal'], ['protein_g', '蛋白质', 'g'], ['fat_g', '脂肪', 'g'], ['carbohydrate_g', '碳水', 'g'], ['sugar_g', '糖', 'g'], ['sodium_mg', '钠', 'mg']].map(([key, label, unit]) => <div key={key}><small>{label}</small><strong>{data.per_serving?.[key] == null ? '未知' : `${Math.round(data.per_serving[key] * 10) / 10} ${unit}`}{data.estimated_per_serving_fields?.includes(key) && <Estimate />}</strong></div>)}</div>;
}
function RecipeDetail({ view, ctx }: { view: View; ctx: Context }) {
  const recipe = view.data.recipe, [section, setSection] = useState('ingredients'); if (!recipe) return <Hint>菜谱详情尚未读取。</Hint>;
  return <div><h4 className="ui-recipe-name">{recipe.name}</h4><Hint>{recipeMeta(recipe)}</Hint>{recipe.description && <p>{recipe.description}</p>}{recipe.target_servings != null ? <Hint>这盘用量 {recipe.target_servings} 配方等效份{recipe.original_servings == null ? ' · 原配方份数未知，食材保留原量' : ''}{recipe.estimated_servings && <Estimate />}</Hint> : recipe.servings != null && <Hint>原配方 {recipe.servings} 份{recipe.estimated_servings && <Estimate />}</Hint>}
    <nav className="section-tabs" aria-label="菜谱详情">{[['ingredients', '食材'], ['steps', '做法'], ['nutrition', '营养'], ['notes', '说明']].map(([key, name]) => <Button key={key} className={`section-tab${section === key ? ' active' : ''}`} aria-pressed={section === key} onClick={() => setSection(key)}>{name}</Button>)}</nav>
    {section === 'ingredients' && <Paged items={recipe.ingredients || []} label="食材" size={5} render={item => <div className="ui-ingredient"><span>{item.canonical_name}</span><span>{amount(item)}{item.estimated && <Estimate />}</span></div>} />}
    {section === 'steps' && <Paged items={recipe.steps || []} size={ctx.focused ? 1 : 2} label="做法" render={step => <p className="ui-step-instruction">{step.instruction || step.description || (typeof step === 'string' ? step : '')}</p>} />}
    {section === 'nutrition' && <><Hint>每配方等效份营养 · 整餐每人摄入请在食光手记查看</Hint><Nutrition data={recipe.nutrition || {}} /></>}
    {section === 'notes' && <>{(recipe.warnings || []).map((text: string, index: number) => <Hint key={index}>{text}</Hint>)}{(recipe.references || []).map((ref: Json, index: number) => { const url = safeReference(ref.url); return url && <a className="reference-link" key={index} href={url} target="_blank" rel="noopener noreferrer">{ref.title || '参考来源'}</a>; })}{recipe.source?.data_snapshot?.data_version && <Hint>来源版本 {recipe.source.data_snapshot.data_version}</Hint>}</>}
    {ctx.onRecipe && <Button className="ui-text-button" onClick={() => ctx.onRecipe!(recipe.recipe_id)}>在菜谱中心查看</Button>}
  </div>;
}
function MealPlan({ view, ctx }: { view: View; ctx: Context }) {
  const recipes: Json[] = view.data.recipes || [], draft = ctx.entry.drafts[view.id], stage = ctx.entry.snapshot?.stage;
  const [error, setError] = useState('');
  const changeServings = (recipeId: string, text: string) => {
    setError('');
    const baseline = draft?.baseline || Object.fromEntries(recipes.map(recipe => [recipe.recipe_id, recipe.target_servings ?? recipe.servings ?? null]));
    const servings = { ...(draft?.servings || {}) }, value = text === '' ? null : Number(text);
    if (value === baseline[recipeId]) delete servings[recipeId]; else servings[recipeId] = value;
    if (Object.keys(servings).length) workspaceStore.draft(ctx.sessionId, view, { baseline, servings });
    else workspaceStore.discard(ctx.sessionId, view.id);
  };
  const save = async () => {
    const pending = Object.entries(draft?.servings || {});
    if (pending.some(([, value]) => typeof value !== 'number' || value < .1 || value > 100)) { setError('这盘用量请填写 0.1 到 100 配方等效份。'); return; }
    setError('');
    for (const [recipeId, servings] of pending) {
      const current = workspaceStore.entry(ctx.sessionId).views.find(item => item.kind === 'meal_plan') || view;
      await workspaceStore.dispatch(ctx.sessionId, 'change_servings', { recipe_id: recipeId, servings }, current);
      const live = workspaceStore.entry(ctx.sessionId), remaining = { ...(live.drafts[current.id]?.servings || draft?.servings) };
      delete remaining[recipeId];
      if (Object.keys(remaining).length) workspaceStore.draft(ctx.sessionId, current, { servings: remaining, baseline: { ...(draft?.baseline || {}), [recipeId]: servings }, baseRevision: live.snapshot?.revision, conflict: false });
      else workspaceStore.discard(ctx.sessionId, current.id);
    }
  };
  return <div>
    {!recipes.length && <Hint>先从菜谱候选中确认想做的菜。</Hint>}
    <div className="meal-summary"><strong>{view.data.meal_diners == null ? `这餐安排 ${recipes.length} 道菜` : `${view.data.meal_diners} 人共享 · ${recipes.length} 道菜`}</strong>{view.data.portion_note && <Hint>{view.data.portion_note}</Hint>}</div>
    <Paged items={recipes} label="餐单" size={2} render={recipe => <article className="ui-plan-recipe">
      <strong>{recipe.name}</strong><Hint>{recipeMeta(recipe)}</Hint>
      {recipe.recipe_id === ctx.entry.snapshot?.active_recipe_id && <span className="ui-badge muted">当前菜品</span>}
      <label className="ui-serving-form">这盘用量 <input className="ui-input" aria-label={`${recipe.name}的配方等效份量`} aria-invalid={Boolean(error)} type="number" min="0.1" max="100" step="any" required disabled={locked(view, ctx) || !['planning', 'prep'].includes(stage)} value={draft?.servings && Object.hasOwn(draft.servings, recipe.recipe_id) ? draft.servings[recipe.recipe_id] ?? '' : recipe.target_servings ?? recipe.servings ?? ''} onChange={event => changeServings(recipe.recipe_id, event.target.value)} /> 配方等效份</label>
      <Hint>{recipe.original_servings == null ? '原配方份数未知，食材暂保留原量。' : `原配方 ${recipe.original_servings} 份，食材按这盘用量换算。`}{recipe.estimated_servings && <Estimate />}</Hint>
      <div className="ui-actions"><Button className="ui-text-button" disabled={locked(view, ctx)} onClick={() => action(view, ctx, 'open_recipe_detail', { recipe_id: recipe.recipe_id })}>查看详情与做法</Button><Button className="ui-text-button" disabled={locked(view, ctx) || recipe.recipe_id === ctx.entry.snapshot?.active_recipe_id} onClick={() => action(view, ctx, 'set_active_recipe', { recipe_id: recipe.recipe_id })}>设为当前菜品</Button><Button className="ui-text-button" disabled={locked(view, ctx) || !['idea', 'planning', 'completed'].includes(stage) || recipes.length <= 1} onClick={() => action(view, ctx, 'select_recipes', { recipe_ids: recipes.filter(item => item.recipe_id !== recipe.recipe_id).map(item => item.recipe_id) })}>移除</Button></div>
    </article>} />
    {error && <p className="question-error" role="alert">{error}</p>}<DraftActions view={view} ctx={ctx} label="保存份量" onSave={save} />
    {stage === 'planning' && <Hint>选好后告诉助手，它会继续帮你准备食材。</Hint>}
  </div>;
}
function DisplayModes({ mode, setMode }: { mode: string; setMode: (mode: string) => void }) {
  return <div className="workflow-mode-bar" role="tablist" aria-label="步骤显示方式" onKeyDown={event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault(); const next = event.key === 'Home' ? 'detail' : event.key === 'End' ? 'list' : mode === 'detail' ? 'list' : 'detail';
    setMode(next); (event.currentTarget.querySelectorAll<HTMLButtonElement>('[role=tab]')[next === 'detail' ? 0 : 1])?.focus();
  }}>{[['detail', '逐步阅读'], ['list', '完整步骤']].map(([value, label]) => <Button key={value} className="workflow-mode-tab" role="tab" tabIndex={mode === value ? 0 : -1} aria-selected={mode === value} onClick={() => setMode(value)}>{label}</Button>)}</div>;
}
function PrepChecklist({ view, ctx }: { view: View; ctx: Context }) {
  const items: Json[] = view.data.items || [], recipes: Json[] = ctx.entry.views.find(item => item.kind === 'meal_plan')?.data.recipes || [];
  const disabled = locked(view, ctx) || !['planning', 'prep'].includes(ctx.entry.snapshot?.stage);
  const resolved = (item: Json) => ['ready', 'skipped'].includes(item.status);
  const sorted = [...items].sort((left, right) => Number(resolved(left)) - Number(resolved(right)));
  return <div className="workspace-preparation">
    {recipes.length > 1 && <nav className="checklist-scope" aria-label="食材清单范围"><Button className="ui-text-button" aria-pressed={view.data.recipe_ids == null} disabled={locked(view, ctx)} onClick={() => action(view, ctx, 'set_checklist_scope', { recipe_ids: null })}>整餐合并</Button>{recipes.map(recipe => <Button className="ui-text-button" key={recipe.recipe_id} aria-pressed={view.data.recipe_ids?.length === 1 && view.data.recipe_ids[0] === recipe.recipe_id} disabled={locked(view, ctx)} onClick={() => action(view, ctx, 'set_checklist_scope', { recipe_ids: [recipe.recipe_id] })}>{recipe.name}</Button>)}</nav>}
    {(view.data.guidance || []).map((text: string, index: number) => <div className="ui-adaptation" key={index}>{text}</div>)}
    <p className="preparation-progress" role="status">已确认 {items.filter(resolved).length} / {items.length} 项<span>勾选已备齐的食材，不需要的可明确跳过</span></p>
    <ul className="workspace-prep-list" aria-label="完整备料清单">{sorted.map(item => <li key={item.id} data-prep-id={item.id} className={`ui-prep-row${resolved(item) ? ' prep-resolved' : ''}`}>
      <label className="ui-check-row"><input type="checkbox" aria-label={`${item.canonical_name}已备齐`} checked={item.status === 'ready'} disabled={disabled} onChange={event => { void action(view, ctx, 'check_prep_item', { item_id: item.id, status: event.target.checked ? 'ready' : 'pending' }).catch(() => {}); }} />
        <span title={item.raw_text ? `原文：${item.raw_text}` : undefined}><strong>{item.canonical_name}</strong><small className="prep-amount">{amount(item)}{item.estimated && <Estimate />}</small></span>
      </label>
      {item.status === 'skipped' && <span className="ui-badge muted">已跳过</span>}
      {item.status !== 'ready' && <Button className="ui-text-button" disabled={disabled} onClick={() => action(view, ctx, 'check_prep_item', { item_id: item.id, status: item.status === 'skipped' ? 'pending' : 'skipped' })}>{item.status === 'skipped' ? '恢复' : '跳过'}</Button>}
    </li>)}</ul>
    {!items.length && <Hint>选定菜谱后可以开始逐项备菜。</Hint>}
    {!!view.data.notes?.length && <details className="workspace-disclosure"><summary>备菜与原文说明</summary>{view.data.notes.map((text: string, index: number) => <p key={index}>{text}</p>)}{items.map(item => <p key={item.id}>{item.canonical_name}：{item.raw_text || '未记录原文'}</p>)}</details>}
    {!ctx.historical && ctx.entry.snapshot?.stage === 'prep' && items.length > 0 && items.every(resolved) && <div className="preparation-finish"><Button className="ui-button cooking-primary" disabled={ctx.entry.running || ctx.entry.saving} onClick={() => ctx.onContinue?.('食材已经全部备齐了，开始做菜吧。')}>食材备齐，继续做菜</Button></div>}
  </div>;
}
export function CompleteMeal({ ctx }: { ctx: Context }) {
  const [confirming, setConfirming] = useState(false);
  const allowed = ctx.entry.snapshot?.allowed_transitions?.includes('completed') || ctx.entry.views.find(view => view.kind === 'task_progress')?.data.steps_total > 0 && ctx.entry.views.find(view => view.kind === 'task_progress')?.data.steps_done >= ctx.entry.views.find(view => view.kind === 'task_progress')?.data.steps_total;
  if (ctx.historical || ctx.entry.snapshot?.stage !== 'cooking') return null;
  return <div className="complete-meal-control">{confirming ? <div className="complete-meal-confirm"><p>确认这餐烹饪已经完成？完成后会保存烹饪记录。</p><Button className="ui-button cooking-primary" disabled={ctx.entry.saving || ctx.entry.running || !allowed} onClick={async () => { await workspaceStore.dispatch(ctx.sessionId, 'complete_meal', {}, undefined, { continuation: '这顿饭所有菜都做好啦，我已点击完成本次菜肴。请查看本次营养登记状态；如果是多人用餐或人数不明确，先问我自己吃了哪些、吃了多少，再登记个人营养。' }); setConfirming(false); }}>确认完成本餐</Button><Button className="ui-text-button" onClick={() => setConfirming(false)}>继续烹饪</Button></div> : <Button className="ui-button cooking-primary complete-meal-button" disabled={!allowed || ctx.entry.saving || ctx.entry.running} onClick={() => setConfirming(true)}>完成本餐</Button>}{!allowed && <Hint>完成全部步骤后，再将这次烹饪标为完成。</Hint>}</div>;
}
function CookingGuide({ view, ctx }: { view: View; ctx: Context }) {
  const data = view.data, steps: Json[] = data.steps || [];
  const [mode, setMode] = useState('detail');
  const [browsing, setBrowsing] = useState<{ recipeId: string; stepId: string } | null>(null);
  const activeIndex = Math.max(0, steps.findIndex(step => step.id === data.active_step_id));
  const browseIndex = browsing?.recipeId === data.recipe_id ? steps.findIndex(step => step.id === browsing?.stepId) : -1;
  const index = browseIndex >= 0 ? browseIndex : activeIndex;
  const recipes: Json[] = ctx.entry.views.find(item => item.kind === 'meal_plan')?.data.recipes || [];
  const disabled = locked(view, ctx) || ctx.entry.snapshot?.stage !== 'cooking';
  const jump = (next: number) => setBrowsing({ recipeId: data.recipe_id, stepId: steps[next].id });
  const renderStep = (step: Json) => {
    const state = step.status === 'done' ? 'done' : step.id === data.active_step_id ? 'current' : 'browsing';
    const matches = (data.timers || []).filter((timer: Timer) => timer.recipe_id === data.recipe_id && timer.step_id === step.id);
    const timer = matches.find((timer: Timer) => ['running', 'paused'].includes(timer.status)) || matches.at(-1);
    const display = timer && timerPresentation(timer, ctx.entry.clock());
    const instruction = step.adapted_instruction || step.instruction || '';
    return <article className={`ui-cooking-step ${state}`} key={step.id} data-step-id={step.id}>
      <div className="ui-step-heading"><span className="cooking-step-number">{String(Number(step.ordinal) + 1).padStart(2, '0')}</span><span className={`cooking-step-status ${state}`}>{({ done: '这一步已完成', current: '正在做这一步', browsing: '正在浏览' } as Json)[state]}</span></div>
      <div className="cooking-step-main">
        {ctx.focused && mode === 'detail' ? <ReadingPages key={`${data.recipe_id}:${step.id}`} text={instruction} label="步骤说明" /> : <p className="ui-step-instruction">{instruction}</p>}
        {step.adapted_instruction && <div className="step-adaptation"><Hint>按你的条件调整：{step.adaptation_reason || ''}</Hint><details className="workspace-disclosure"><summary>查看原菜谱步骤</summary><p>{step.instruction}</p></details></div>}
      </div>
      <div className="ui-actions cooking-step-actions">
        <Button className={`ui-button ${step.status === 'done' ? 'cooking-secondary' : 'cooking-primary'}`} disabled={disabled} onClick={async () => { await action(view, ctx, 'complete_step', { recipe_id: data.recipe_id, step_id: step.id, completed: step.status !== 'done' }); setBrowsing(null); }}>{step.status === 'done' ? '取消完成' : '完成这一步'}</Button>
        {step.id !== data.active_step_id && data.active_step_id && <><Button className="ui-button cooking-secondary" onClick={() => setBrowsing(null)}>回到正在做的步骤</Button>{step.status !== 'done' && <Button className="ui-button cooking-secondary" disabled={disabled} onClick={async () => { await action(view, ctx, 'set_active_step', { recipe_id: data.recipe_id, step_id: step.id }); setBrowsing(null); }}>从这一步继续</Button>}</>}
        {timer && ['running', 'paused'].includes(timer.status) ? <Button className="ui-button cooking-secondary step-timer-shortcut" aria-label={`查看这一步的计时：${timer.label}`} onClick={() => workspaceStore.openTimers(ctx.sessionId, timer.id)}><Icon name="timer" /><time>{display.clock}</time><span>{display.status}</span></Button> : step.timer_seconds > 0 && !step.adapted_instruction && step.status !== 'done' && <Button className="ui-button cooking-secondary timer-start-button" disabled={disabled} onClick={() => action(view, ctx, 'start_timer', { recipe_id: data.recipe_id, step_id: step.id, seconds: step.timer_seconds, label: `${data.recipe_name} · 第 ${Number(step.ordinal) + 1} 步` })}><Icon name="timer" />开始 {durationText(step.timer_seconds)}计时</Button>}
      </div>
    </article>;
  };
  if (!data.recipe_id) return <Hint>在餐单中选择一道菜，开始查看步骤。</Hint>;
  return <div className={`ui-cooking-guide cooking-mode-${mode}`}>
    <div className="cooking-guide-tools"><DisplayModes mode={mode} setMode={setMode} />
      {recipes.length > 1 && <nav className="cooking-recipe-scope" aria-label="切换本餐菜品">{recipes.map(recipe => <Button className="ui-text-button" key={recipe.recipe_id} aria-pressed={recipe.recipe_id === data.recipe_id} disabled={locked(view, ctx)} onClick={() => action(view, ctx, 'set_active_recipe', { recipe_id: recipe.recipe_id })}>{recipe.name}</Button>)}</nav>}
      <h4 className="ui-recipe-name">{data.recipe_name}</h4>
      {(data.guidance || []).map((text: string, index: number) => <div className="ui-adaptation" key={index}>{text}</div>)}
      <div className="cooking-overview"><span className="cooking-progress-label">已完成 {steps.filter(step => step.status === 'done').length} / {steps.length} 步</span><progress className="cooking-progress" aria-label="烹饪步骤完成进度" max={Math.max(steps.length, 1)} value={steps.filter(step => step.status === 'done').length} /></div>
      {mode === 'detail' && !!steps.length && <nav className="step-navigator cooking-step-nav" aria-label="浏览烹饪步骤"><Button className="page-button" disabled={index === 0} onClick={() => jump(index - 1)}>上一步</Button><strong>第 {index + 1} / {steps.length} 步</strong><Button className="page-button" disabled={index >= steps.length - 1} onClick={() => jump(index + 1)}>下一步</Button></nav>}
    </div>
    {!steps.length && <Hint>这道菜暂时没有可用步骤。</Hint>}
    {mode === 'list' ? <div className="workflow-list cooking-flat-list">{steps.map(renderStep)}</div> : steps[index] && renderStep(steps[index])}
    {ctx.focused && mode === 'detail' && <details className="workflow-overview"><summary>全部步骤大纲</summary><ol>{steps.map((step, index) => <li key={step.id}><Button className="workflow-step-jump" aria-current={index === (browseIndex >= 0 ? browseIndex : activeIndex) ? 'step' : undefined} onClick={() => jump(index)}><span className="workflow-step-index">{index + 1}</span><span>{step.status === 'done' ? '已完成 · ' : ''}{step.adapted_instruction || step.instruction}</span></Button></li>)}</ol></details>}
    <CompleteMeal ctx={ctx} />
  </div>;
}
export function TimerRow({ timer, ctx, view }: { timer: Timer; ctx: Context; view: View }) {
  const display = timerPresentation(timer, ctx.entry.clock()), disabled = locked(view, ctx), cookingDisabled = disabled || ctx.entry.snapshot?.stage !== 'cooking';
  const run = (type: string, payload: Json) => action(view, ctx, type, payload);
  const restart = async () => {
    await run('start_timer', { seconds: timer.seconds, label: timer.label, ...(timer.recipe_id ? { recipe_id: timer.recipe_id } : {}), ...(timer.step_id ? { step_id: timer.step_id } : {}) });
    workspaceStore.acknowledge(ctx.sessionId, timer.id);
  };
  return <article className="timer-popup-row" data-state={display.state} data-timer-row={timer.id}>
    <div className="timer-popup-copy"><strong>{timer.label}</strong><div className="timer-popup-readout"><time className="timer-popup-clock" aria-label={display.remaining == null ? '剩余时间未知' : `剩余 ${display.remaining} 秒`}>{display.clock}</time><span>{display.status}</span></div><progress aria-label={`${timer.label}剩余时间比例`} max="1" value={display.progress} /></div>
    {!ctx.historical && <div className="timer-popup-actions">
      {display.expired && <Button className="timer-popup-button" aria-label={`确认${timer.label}时间到`} onClick={() => workspaceStore.acknowledge(ctx.sessionId, timer.id)}>知道了</Button>}
      {(display.expired || timer.status === 'cancelled') && <Button className="timer-popup-button" disabled={cookingDisabled} onClick={restart}>重新计时</Button>}
      {!display.expired && ['running', 'paused'].includes(timer.status) && <><Button className="timer-popup-button" aria-label={`${timer.status === 'paused' ? '继续' : '暂停'}${timer.label}`} disabled={cookingDisabled} onClick={() => run(timer.status === 'paused' ? 'resume_timer' : 'pause_timer', { timer_id: timer.id })}>{timer.status === 'paused' ? '继续' : '暂停'}</Button><Button className="timer-popup-button" aria-label={`结束${timer.label}`} disabled={disabled} onClick={() => run('cancel_timer', { timer_id: timer.id })}>结束</Button></>}
    </div>}
  </article>;
}
export function TimerForm({ view, ctx, onStarted }: { view: View; ctx: Context; onStarted?: () => void }) {
  const draft = ctx.entry.drafts[view.id], label = draft?.label || '', minutes = draft?.minutes ?? '3', seconds = draft?.seconds ?? '0', [error, setError] = useState('');
  const { limit, active } = timerCapacity(ctx.entry.snapshot, ctx.entry.views, ctx.entry.clock()), disabled = locked(view, ctx) || ctx.entry.snapshot?.stage !== 'cooking' || active >= limit;
  const update = (patch: Json) => { setError(''); workspaceStore.draft(ctx.sessionId, view, { label, minutes, seconds, ...patch }); };
  return <form className="timer-create-form" onSubmit={async event => { event.preventDefault(); const total = manualTimerSeconds(minutes, seconds); if (!total) { setError('请填写 1 秒到 24 小时；秒数为 0 到 59。'); return; } if (disabled) return; try { await workspaceStore.dispatch(ctx.sessionId, 'start_timer', { seconds: total, label: label.trim() || '厨房计时' }, view, { clearDraft: view.id }); onStarted?.(); } catch { /* Store displays the failed transaction. */ } }}><label className="ui-field"><span className="ui-field-label">计时名称</span><input className="ui-input" aria-label="计时名称" maxLength={100} value={label} disabled={disabled} placeholder="厨房计时" onChange={event => update({ label: event.target.value })} /></label><div className="timer-duration-fields"><label>分钟<input className="ui-input" type="number" aria-label="计时分钟" min="0" max="1440" step="1" value={minutes} disabled={disabled} onChange={event => update({ minutes: event.target.value })} /></label><label>秒<input className="ui-input" type="number" aria-label="计时秒" min="0" max="59" step="1" value={seconds} disabled={disabled} onChange={event => update({ seconds: event.target.value })} /></label></div>{error && <p className="question-error" role="alert">{error}</p>}<button className="ui-button cooking-primary" type="submit" disabled={disabled}>开始计时</button><Hint>{ctx.entry.snapshot?.stage !== 'cooking' ? '进入烹饪阶段后可以启动计时。' : active >= limit ? `已有 ${active} / ${limit} 个计时，请先结束已有计时。` : `最多 ${limit} 个同时计时`}</Hint></form>;
}
function Timers({ view, ctx }: { view: View; ctx: Context }) { return <div><Paged label="计时器" size={2} items={view.data.timers || []} render={timer => <TimerRow timer={timer} view={view} ctx={ctx} />} />{!view.data.timers?.length && <Hint>还没有计时器，设好时间就能开始。</Hint>}{!ctx.historical && <TimerForm view={view} ctx={ctx} />}</div>; }
function TaskProgress({ view, ctx }: { view: View; ctx: Context }) { const data = view.data; return <div><span className="ui-badge">{stages[data.stage] || '厨房任务'}</span><Hint>{data.recipe_count ?? 0} 道菜 · 备菜 {data.prep_ready ?? 0}/{data.prep_total ?? 0} · 步骤 {data.steps_done ?? 0}/{data.steps_total ?? 0}</Hint><CompleteMeal ctx={ctx} /></div>; }
const renderers: Record<string, (props: { view: View; ctx: Context }) => ReactNode> = { preference_form: Preferences, recipe_choices: Choices, recipe_detail: RecipeDetail, meal_plan: MealPlan, prep_checklist: PrepChecklist, cooking_guide: CookingGuide, timers: Timers, task_progress: TaskProgress };
export function ViewCard({ view, ...ctx }: Context & { view: View }) {
  const Renderer = renderers[view.kind], glyph = ({ preference_form: 'list-checks', recipe_choices: 'utensils', recipe_detail: 'book-open', meal_plan: 'chef-hat', prep_checklist: 'list-checks', cooking_guide: 'chef-hat', timers: 'timer', task_progress: 'circle-check' } as Json)[view.kind];
  return <section className={`ui-component ui-${view.kind}`} data-view-id={view.id} data-view-kind={view.kind}><div className="ui-component-heading"><h3>{glyph && <Icon name={glyph} />}{view.title || labels[view.kind] || '任务内容'}</h3>{ctx.historical && <span className="ui-badge muted">历史记录</span>}{!ctx.historical && !ctx.focused && ctx.onFocus && ['prep_checklist', 'cooking_guide', 'recipe_detail'].includes(view.kind) && <Button className="focus-button" onClick={() => ctx.onFocus?.(view.kind)}>全屏查看</Button>}</div>{Renderer ? <Renderer key={view.id} view={view} ctx={ctx} /> : <Hint>这个组件暂不支持，请通过对话继续。</Hint>}{ctx.historical && <Button className="ui-text-button" onClick={() => workspaceStore.setActive(ctx.sessionId, view.kind)}>在当前任务打开</Button>}</section>;
}
