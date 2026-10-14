import { clone, changedValues, makeAction, remainingSeconds, clockText, EXECUTION_STAGES } from './ui-protocol.js';
import { viewChoices, VIEW_LABELS, STAGE_NAMES } from './presentation.js';
import { node, renderView } from './ui-components.js';

const keyFor = (id) => `chefmate:workspace-drafts:${id}`;
function readDrafts(id) { try { return JSON.parse(localStorage.getItem(keyFor(id)) || '{}'); } catch { return {}; } }
function writeDrafts(id, value) { try { localStorage.setItem(keyFor(id), JSON.stringify(value)); } catch { /* 无存储权限时仍保留内存草稿。 */ } }

export class WorkspaceController {
  constructor({ api, notify, onChange, openPanel }) {
    this.api = api; this.notify = notify; this.onChange = onChange; this.openPanel = openPanel;
    this.entries = new Map(); this.activeId = null; this.container = null;
    this.timer = setInterval(() => this.tick(), 1000);
  }
  entry(id) {
    if (!this.entries.has(id)) this.entries.set(id, { snapshot: null, views: [], drafts: id ? readDrafts(id) : {},
      generation: 0, loading: false, saving: false, error: '', conflict: null, failedRequest: null,
      activeKind: null, viewState: {} });
    return this.entries.get(id);
  }
  select(id) { this.activeId = id; this.render(); if (id) this.load(id).catch(() => {}); this.onChange(); }
  remove(id) {
    this.entries.get(id)?.controller?.abort(); this.entries.delete(id);
    try { localStorage.removeItem(keyFor(id)); } catch { /* 可继续删除服务端会话。 */ }
  }
  async load(id = this.activeId) {
    if (!id) return null;
    const entry = this.entry(id); const generation = ++entry.generation;
    entry.controller?.abort(); const controller = new AbortController(); entry.controller = controller;
    entry.loading = true; entry.error = ''; this.refresh(id);
    try {
      const response = await this.api(`/api/sessions/${encodeURIComponent(id)}/workspace`, { signal: controller.signal });
      if (generation !== entry.generation || controller.signal.aborted) return entry.snapshot;
      this.receive(id, response);
      return entry.snapshot;
    } catch (error) {
      if (error.name !== 'AbortError' && generation === entry.generation) entry.error = error.message || '无法读取当前任务。';
      throw error;
    } finally {
      if (generation === entry.generation) { entry.loading = false; this.refresh(id); }
    }
  }
  receive(id, response) {
    const entry = this.entry(id); const snapshot = response?.workspace || response?.ui_snapshot;
    if (!snapshot || !Number.isInteger(snapshot.revision)) return false;
    if (entry.snapshot && snapshot.revision < entry.snapshot.revision) return false;
    const previousStage = entry.snapshot?.stage;
    const oldIds = new Set(entry.views.map(view=>view.id));
    entry.snapshot = clone(snapshot);
    // 完整呈现与模型短快照分开；短快照不能被当作食材、菜谱详情。
    entry.views = Array.isArray(response.views) ? clone(response.views) : entry.views;
    const choices = viewChoices(entry.views,snapshot.stage);
    const introduced = choices.filter(view=>!oldIds.has(view.id));
    if (previousStage !== snapshot.stage || !choices.some(view=>view.kind === entry.activeKind)) entry.activeKind = choices[0]?.kind;
    else if (introduced.length) entry.activeKind = introduced.at(-1).kind;
    for (const [oldId, draft] of Object.entries(entry.drafts)) {
      if (!entry.views.some((view) => view.id === oldId)) {
        const replacement = entry.views.find((view) => view.kind === draft.kind);
        if (replacement) { entry.drafts[replacement.id] = { ...draft, conflict: true }; delete entry.drafts[oldId]; }
      }
      if (draft.baseRevision !== snapshot.revision) draft.conflict = true;
    }
    if (entry.failedRequest && snapshot.recent_actions?.some((item) => item.action_id === entry.failedRequest.action_id)) {
      const draft = entry.drafts[entry.failedDraft];
      if (draft?.baseline && JSON.stringify(changedValues(draft.baseline, draft.values)) === JSON.stringify(entry.failedRequest.payload?.values)) delete entry.drafts[entry.failedDraft];
      if (draft?.selectedIds && JSON.stringify(draft.selectedIds) === JSON.stringify(entry.failedRequest.payload?.recipe_ids)) delete entry.drafts[entry.failedDraft];
      entry.failedRequest = null; entry.autoSaveError = false; entry.error = '';
    }
    writeDrafts(id, entry.drafts); this.refresh(id); return true;
  }
  refresh(id) { if (id === this.activeId) this.render(); this.onChange(); }
  snapshot(id = this.activeId) { return clone(this.entries.get(id)?.snapshot); }
  canSubmit(id = this.activeId) {
    const entry = this.entries.get(id);
    return !entry || (!entry.saving && !entry.failedRequest && !entry.conflict && !entry.autoSaveError
      && !Object.values(entry.drafts).some((draft) => draft.kind !== 'preference_form' || draft.conflict));
  }
  unsaved(id = this.activeId) {
    const entry = this.entries.get(id);
    return Boolean(entry && (entry.saving || entry.failedRequest || entry.conflict || Object.keys(entry.drafts).length));
  }
  syncLabel(id = this.activeId) {
    const entry = this.entries.get(id);
    if (!id) return '';
    if (entry?.saving) return '正在同步任务…';
    if (entry?.failedRequest) return '同步状态未知，请先检查当前任务';
    if (entry?.conflict) return '任务已变化，操作待复核';
    if (Object.values(entry?.drafts || {}).some((draft) => draft.conflict)) return '任务已变化，表单待复核';
    if (Object.values(entry?.drafts || {}).some((draft) => draft.kind === 'recipe_choices')) return '菜谱选择尚未确认';
    if (Object.keys(entry?.drafts || {}).length) return '条件尚未保存';
    if (entry?.snapshot) return '当前内容已保存';
    return entry?.error ? '任务暂时不可用' : '正在读取任务…';
  }
  async prepareTurn(id) {
    const entry = this.entry(id);
    // 每次发消息都核对服务端版本；已编辑草稿不会被这次读取覆盖。
    await this.load(id);
    if (!this.canSubmit(id)) throw new Error('请先复核未保存的修改或确认菜谱选择，再发送消息。');
    for (const [viewId, draft] of Object.entries(entry.drafts)) {
      if (draft.kind !== 'preference_form') continue;
      const view = entry.views.find((item) => item.id === viewId && item.kind === 'preference_form');
      if (!view || !view.editable || draft.conflict) throw new Error('条件表单已变化，请先在当前任务中复核。');
      try {
        await this.dispatch('update_preferences', { values: changedValues(draft.baseline, draft.values) }, view,
          { sessionId: id, revision: draft.baseRevision, clearDraft: viewId });
      } catch (error) {
        entry.autoSaveError = true; this.onChange();
        throw new Error(`条件尚未保存，消息没有发送。${error.message || '请稍后重试。'}`);
      }
    }
    // 自动保存完成后再读取真实短快照，不用本地草稿替代服务端状态。
    await this.load(id);
    if (!this.canSubmit(id)) throw new Error('任务在保存期间发生变化，请复核后再发送消息。');
    if (!entry.snapshot) throw new Error('任务快照尚未读取，请刷新任务后重试。');
    return this.snapshot(id);
  }
  setDraft(view, values) {
    const entry = this.entry(this.activeId); const previous = entry.drafts[view.id];
    if (entry.autoSaveError) { entry.autoSaveError = false; if (!entry.conflict && !entry.failedRequest) entry.error = ''; }
    const baseline = previous?.baseline || clone(view.data.values || {});
    if (!Object.keys(changedValues(baseline, values)).length) delete entry.drafts[view.id];
    else entry.drafts[view.id] = { kind: view.kind, baseline, values: clone(values), baseRevision: previous?.baseRevision ?? view.revision,
      conflict: Boolean(previous?.conflict || previous && previous.baseRevision !== entry.snapshot?.revision) };
    writeDrafts(this.activeId, entry.drafts); this.onChange();
  }
  setSelectionDraft(view, selectedIds) {
    const entry = this.entry(this.activeId);
    const selected = entry.views.find((item) => item.kind === 'meal_plan')?.data.recipes?.map((recipe) => recipe.recipe_id) || [];
    if (JSON.stringify([...selectedIds].sort()) === JSON.stringify([...selected].sort())) delete entry.drafts[view.id];
    else entry.drafts[view.id] = { kind: view.kind, selectedIds: [...selectedIds],
      baseRevision: entry.drafts[view.id]?.baseRevision ?? view.revision,
      conflict: Boolean(entry.drafts[view.id]?.conflict) };
    writeDrafts(this.activeId, entry.drafts); this.onChange();
  }
  async saveSelection(view, selectedIds) {
    const entry = this.entry(this.activeId); const draft = entry.drafts[view.id];
    await this.dispatch('select_recipes', { recipe_ids: selectedIds }, view,
      { revision: draft?.conflict ? entry.snapshot.revision : draft?.baseRevision ?? view.revision, clearDraft: view.id });
  }
  discardDraft(view) {
    const entry = this.entry(this.activeId); delete entry.drafts[view.id];
    entry.autoSaveError = false;
    if (entry.conflict?.clearDraft === view.id || entry.conflict?.kind === view.kind) { entry.conflict = null; entry.error = ''; }
    writeDrafts(this.activeId, entry.drafts); this.refresh(this.activeId);
  }
  async savePreferences(view, values) {
    const entry = this.entry(this.activeId); const draft = entry.drafts[view.id];
    if (!draft) return;
    const current = entry.views.find((item) => item.kind === 'preference_form');
    const revision = draft.conflict ? entry.snapshot.revision : draft.baseRevision;
    await this.dispatch('update_preferences', { values: changedValues(draft.baseline, values) }, current || view,
      { revision, clearDraft: view.id });
  }
  async dispatch(type, payload, view, options = {}) {
    const id = options.sessionId || this.activeId;
    if (!id) { this.notify('请先创建会话。'); throw new Error('没有当前任务'); }
    const entry = this.entry(id);
    if (entry.saving) { this.notify('任务正在同步，请稍等。'); throw new Error('任务正在同步'); }
    const request = options.request || makeAction(type, payload, options.revision ?? view?.revision ?? entry.snapshot?.revision,
      view?.id, crypto.randomUUID());
    entry.saving = true; entry.error = ''; entry.conflict = null; this.refresh(id);
    try {
      const response = await this.api(`/api/sessions/${encodeURIComponent(id)}/actions`, { method: 'POST', body: JSON.stringify(request) });
      this.receive(id, response);
      if (response.status !== 'ok') {
        const stale = ['revision_conflict', 'stale_view'].includes(response.status);
        if (stale) {
          entry.conflict = { request, kind: view?.kind, clearDraft: options.clearDraft };
          entry.failedRequest = null;
          if (options.clearDraft && entry.drafts[options.clearDraft]) entry.drafts[options.clearDraft].conflict = true;
        }
        entry.error = stale ? '任务已变化，请复核当前内容后再操作。' : response.message || '这次操作未完成。';
        this.notify(entry.error); const error = new Error(entry.error); error.knownRejected = true; throw error;
      }
      entry.failedRequest = null;
      entry.autoSaveError = false;
      if (options.clearDraft) delete entry.drafts[options.clearDraft];
      writeDrafts(id, entry.drafts); this.notify('已同步到当前任务。'); return response;
    } catch (error) {
      if (error.status === 409 || error.data?.status === 'revision_conflict') {
        this.receive(id, error.data);
        entry.conflict = { request, kind: view?.kind, clearDraft: options.clearDraft };
        entry.failedRequest = null;
        entry.error = '任务已变化，请复核当前内容后再操作。';
        if (options.clearDraft && entry.drafts[options.clearDraft]) entry.drafts[options.clearDraft].conflict = true;
        if (!error.data?.workspace) await this.load(id).catch(() => {});
      } else if (!entry.conflict && !error.knownRejected) {
        entry.error = error.message || '任务同步失败，请重试。';
        if (!error.status || error.status >= 500) { entry.failedRequest = request; entry.failedDraft = options.clearDraft; }
        this.notify(entry.error);
      }
      throw error;
    } finally { entry.saving = false; writeDrafts(id, entry.drafts); this.refresh(id); }
  }
  async retryConflict() {
    const entry = this.entry(this.activeId); const conflict = entry.conflict;
    if (!conflict || !entry.snapshot) return;
    const currentView = entry.views.find((view) => view.id === conflict.request.view_id) || entry.views.find((view) => view.kind === conflict.kind);
    await this.dispatch(conflict.request.type, conflict.request.payload, currentView,
      { revision: entry.snapshot.revision, clearDraft: conflict.clearDraft });
  }
  async retryFailed() {
    const entry = this.entry(this.activeId); const request = entry.failedRequest;
    if (!request) return;
    await this.dispatch(request.type, request.payload, null, { request, clearDraft: entry.failedDraft });
  }
  openCurrent(kind) {
    this.openPanel('workspace');
    if (!this.activeId) return;
    const entry = this.entry(this.activeId);
    if (viewChoices(entry.views,entry.snapshot?.stage).some(view=>view.kind===kind)) {
      entry.activeKind=kind; this.render();
    }
    else if (kind === 'recipe_choices' && EXECUTION_STAGES.includes(this.entry(this.activeId).snapshot?.stage)) this.notify('当前正在执行已选餐单。需要改选菜谱时，请先返回选菜阶段。');
    else this.notify('当前任务还没有这项内容，可以继续向助手提出需求。');
  }
  renderInto(container) { this.container = container; this.render(); }
  render() {
    if (!this.container) return;
    const scrollTop = this.container.scrollTop;
    const focusKey = this.container.contains(document.activeElement) ? document.activeElement?.dataset?.controlKey : null;
    if (focusKey && this.activeId) this.entry(this.activeId).focusKey = focusKey;
    this.container.replaceChildren();
    const id = this.activeId;
    if (!id) { this.container.append(node('div', 'panel-empty', '从一个会话开始。选菜、购物、备菜、步骤和计时都会随当前任务保存。')); return; }
    const entry = this.entry(id); const header = node('div', 'workspace-heading');
    header.append(node('span', 'workspace-stage', STAGE_NAMES[entry.snapshot?.stage] || '厨房任务'));
    const refresh = node('button', 'ui-text-button', entry.loading ? '读取中…' : '刷新'); refresh.type = 'button'; refresh.disabled = entry.loading || entry.saving;
    refresh.addEventListener('click', () => this.load(id).catch(() => {})); header.append(refresh); this.container.append(header);
    const sync = node('p', 'workspace-sync', entry.saving ? '正在保存…' : this.unsaved(id) ? '有修改待确认' : '操作会自动保存，下一条消息带上当前状态'); sync.setAttribute('role', 'status'); this.container.append(sync);
    if (entry.error) {
      const error = node('div', 'error-box', entry.error);
      if (entry.conflict && !entry.conflict.clearDraft) {
        const retry = node('button', 'retry-button', '复核当前任务后重试'); retry.type = 'button'; retry.disabled = entry.saving;
        retry.addEventListener('click', () => this.retryConflict().catch(() => {})); error.append(node('br'), retry);
      }
      if (entry.conflict) {
        const discard = node('button', 'retry-button', '放弃这次操作'); discard.type = 'button'; discard.disabled = entry.saving;
        discard.addEventListener('click', () => { entry.conflict = null; entry.error = ''; this.refresh(id); }); error.append(node('br'), discard);
      }
      if (entry.failedRequest) {
        const retry = node('button', 'retry-button', '重新同步这次操作'); retry.type = 'button'; retry.disabled = entry.saving;
        retry.addEventListener('click', () => this.retryFailed().catch(() => {})); error.append(node('br'), retry);
      }
      this.container.append(error);
    }
    if (!entry.views.length && entry.loading) this.container.append(node('div', 'panel-loading', '正在读取任务…'));
    const selected = entry.snapshot?.selected_recipe_ids || entry.views.find((view) => view.kind === 'meal_plan')?.data.recipes?.map((recipe) => recipe.recipe_id) || [];
    const execution = EXECUTION_STAGES.includes(entry.snapshot?.stage);
    const hiddenChoiceDrafts = Object.entries(entry.drafts).filter(([,draft]) => draft.kind === 'recipe_choices');
    if (execution && hiddenChoiceDrafts.length) {
      const warning = node('div', 'error-box', '还有未确认的菜谱选择。可以放弃这份选择继续当前餐单，或返回选菜阶段复核。');
      const discard = node('button', 'retry-button', '放弃未确认的选择'); discard.type = 'button';
      discard.addEventListener('click', () => {
        for (const [draftId] of hiddenChoiceDrafts) delete entry.drafts[draftId];
        if (entry.conflict?.kind === 'recipe_choices') { entry.conflict=null; entry.error=''; }
        writeDrafts(id,entry.drafts); this.refresh(id);
      });
      warning.append(node('br'),discard); this.container.append(warning);
    }
    const choices = viewChoices(entry.views,entry.snapshot?.stage);
    const navigation = node('nav','workspace-navigation'); navigation.setAttribute('aria-label','工作区内容');
    for (const view of choices) {
      const control = node('button',`workspace-nav${entry.activeKind===view.kind ? ' active' : ''}`,VIEW_LABELS[view.kind]);
      control.type='button'; control.setAttribute('aria-pressed',String(entry.activeKind===view.kind));
      control.addEventListener('click',()=>{entry.activeKind=view.kind;this.render();}); navigation.append(control);
    }
    this.container.append(navigation);
    const activeView = choices.find(view=>view.kind===entry.activeKind) || choices[0];
    if (!activeView && !entry.loading) this.container.append(node('div','panel-empty','告诉助手你的想法，合适的表单、菜品和清单会在这里打开。'));
    for (const view of activeView ? [activeView] : []) {
      const draft = entry.drafts[view.id];
      const content = renderView(view, { historical: false, busy: entry.saving, draft, selectedRecipeIds: selected,
        stage: entry.snapshot?.stage,
        activeRecipeId: entry.snapshot?.active_recipe_id,
        viewState: entry.viewState[view.id] ||= {},
        onViewState: () => this.render(),
        openRecipeSection: async (recipeId,section) => {
          await this.dispatch('open_recipe_detail',{recipe_id:recipeId},view,{sessionId:id});
          const detail=entry.views.find(item=>item.kind==='recipe_detail' && item.data.recipe?.recipe_id===recipeId);
          if(detail) { (entry.viewState[detail.id] ||= {}).detailSection=section;entry.activeKind='recipe_detail';this.refresh(id); }
        },
        action: (type, payload, origin) => this.dispatch(type, payload, origin),
        openCurrent: (kind) => this.openCurrent(kind), onDraft: (origin, values) => this.setDraft(origin, values),
        dirty: (origin, values) => Object.keys(changedValues(entry.drafts[origin.id]?.baseline || origin.data.values || {}, values)).length > 0,
        savePreferences: (origin, values) => this.savePreferences(origin, values), discardDraft: (origin) => this.discardDraft(origin),
        onSelection: (origin, ids) => this.setSelectionDraft(origin, ids), saveSelection: (origin, ids) => this.saveSelection(origin, ids) });
      this.container.append(content);
    }
    this.tick();
    this.container.scrollTop = scrollTop;
    if (entry.focusKey) {
      const target = this.container.querySelector(`[data-control-key="${CSS.escape(entry.focusKey)}"]`);
      if (target && !target.disabled) target.focus({ preventScroll: true });
    }
  }
  tick() {
    const views = this.entries.get(this.activeId)?.views || [];
    const timers = views.find((view) => view.kind === 'timers')?.data.timers || [];
    for (const timer of timers) {
      const target = this.container?.querySelector(`[data-timer-id="${CSS.escape(timer.id)}"]`);
      if (target) target.textContent = clockText(remainingSeconds(timer));
      if (target && timer.status === 'running' && remainingSeconds(timer) === 0) {
        const status = target.parentElement?.querySelector('.ui-badge');
        if (status) status.textContent = '时间到';
      }
    }
  }
}
