import { clone, changedValues, makeAction, EXECUTION_STAGES, messageViews } from './ui-protocol.js';
import { viewChoices, VIEW_LABELS, STAGE_NAMES } from './presentation.js';
import { node, renderView } from './ui-components.js';
import { icon } from './icons.js';
import { timerPresentation } from './cooking-timer.js';
import { timerClock, workspaceTimers } from './timer-runtime.js';

const keyFor = (id) => `chefmate:workspace-drafts:${id}`;
const compareSnapshots = (left, right) => (left?.revision ?? -1) - (right?.revision ?? -1)
  || (left?.presentation_revision ?? 0) - (right?.presentation_revision ?? 0);
function readDrafts(id) { try { return JSON.parse(localStorage.getItem(keyFor(id)) || '{}'); } catch { return {}; } }
function writeDrafts(id, value) { try { localStorage.setItem(keyFor(id), JSON.stringify(value)); } catch { /* 无存储权限时仍保留内存草稿。 */ } }

// Confirmation is already persisted when queued. Retrying its continuation
// must never reapply the action or silently drop the intended user message.
export class ConfirmationQueue {
  constructor() { this.items=new Map(); }
  get(id) { return this.items.get(id); }
  delete(id) { return this.items.delete(id); }
  enqueue(id,message) { this.items.set(id,{message,status:'queued',error:''}); }
  retry(id) { const item=this.get(id);if(item?.status==='failed'){item.status='queued';item.error='';} }
  async start(id,send) {
    const item=this.get(id);
    if(!item || item.status!=='queued')return;
    item.status='preparing';
    const prepared=()=>{if(this.get(id)===item)this.delete(id);};
    try {
      const result=await send(item.message,prepared);
      if(this.get(id)===item){item.status='failed';item.error=result?.error || '暂时无法开始下一轮，请重试。';}
    } catch(error) {
      if(this.get(id)===item){item.status='failed';item.error=error.message || '暂时无法开始下一轮，请重试。';}
    }
  }
}

export function visibleMessageViews(message) {
  return message.streaming || message.recovering ? [] : messageViews(message);
}
export function recoverRunningTurn(session,message) {
  session.running=true;
  session.recoveryStatus='连接中断，正在恢复执行状态…';
  message.streaming=true;message.recovering=true;
  delete message.error;
}

export class WorkspaceController {
  constructor({ api, notify, onChange, openPanel, onConfirm = async () => {} }) {
    this.api = api; this.notify = notify; this.onChange = onChange; this.openPanel = openPanel;
    this.onConfirm = onConfirm;
    this.entries = new Map(); this.activeId = null; this.container = null;
    this.timer = setInterval(() => this.tick(), 1000);
  }
  entry(id) {
    if (!this.entries.has(id)) this.entries.set(id, { snapshot: null, views: [], drafts: id ? readDrafts(id) : {},
      generation: 0, loading: false, saving: false, error: '', conflict: null, failedRequest: null,
      activeKind: null, viewState: {}, presentationPending: false, pendingResponse: null, confirming: false,
      timerNow: () => Date.now(), timerAcknowledged: this.readTimerAcknowledged(id), timerStates:new Map() });
    return this.entries.get(id);
  }
  select(id) { this.activeId = id; this.render(); if (id) this.load(id).catch(() => {}); this.onChange(); }
  remove(id) {
    this.entries.get(id)?.controller?.abort(); this.entries.delete(id);
    try { localStorage.removeItem(keyFor(id)); } catch { /* 可继续删除服务端会话。 */ }
    try { localStorage.removeItem(`chefmate:timer-ack:${id}`); } catch { /* No local storage available. */ }
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
  setRunning(id, running) {
    const entry = this.entry(id);
    entry.presentationPending = Boolean(running);
    if (!running && entry.pendingResponse) {
      const response = entry.pendingResponse;
      entry.pendingResponse = null;
      this.receive(id, response);
    }
  }
  receive(id, response, { immediate = false, userViewKind = null } = {}) {
    const entry = this.entry(id); const snapshot = response?.workspace || response?.ui_snapshot;
    if (!snapshot || !Number.isInteger(snapshot.revision)) return false;
    if (entry.snapshot && compareSnapshots(snapshot, entry.snapshot) < 0) return false;
    const receivedAt = response._timerReceivedAt ?? performance.now();
    const serverNow = (response.views || []).map(view => view.data?.server_now).filter(Boolean).sort().at(-1);
    // Model tools save their work before the final answer. Keep those new views
    // out of the UI until the turn ends, including workspace reads on reconnect.
    // Direct user actions still receive their own confirmed state immediately.
    if (entry.presentationPending) {
      const pendingSnapshot = entry.pendingResponse?.workspace || entry.pendingResponse?.ui_snapshot;
      if (!pendingSnapshot || compareSnapshots(snapshot, pendingSnapshot) >= 0) entry.pendingResponse = {...clone(response), _timerReceivedAt:receivedAt};
      if(!immediate)return false;
      // An action response also includes every model-created view. Only expose
      // updates to views already on screen and a detail explicitly opened by
      // the user; the complete response stays queued until the turn settles.
      const visibleIds=new Set(entry.views.map(view=>view.id));
      const updates=(response.views || []).filter(view=>visibleIds.has(view.id) || view.kind===userViewKind);
      response={...response,views:[...entry.views.filter(view=>!updates.some(update=>update.id===view.id
        || userViewKind && update.kind===userViewKind && view.kind===userViewKind)),...updates]};
    }
    const previousStage = entry.snapshot?.stage;
    const oldIds = new Set(entry.views.map(view=>view.id));
    entry.snapshot = clone(snapshot);
    // 完整呈现与模型短快照分开；短快照不能被当作食材、菜谱详情。
    entry.views = Array.isArray(response.views) ? clone(response.views) : entry.views;
    if (serverNow) entry.timerNow = timerClock(serverNow,undefined,receivedAt);
    const choices = viewChoices(entry.views,snapshot.stage);
    const introduced = choices.filter(view=>!oldIds.has(view.id));
    if (previousStage !== snapshot.stage || !choices.some(view=>view.kind === entry.activeKind)) entry.activeKind = choices[0]?.kind;
    else if (introduced.length) entry.activeKind = introduced.at(-1).kind;
    for (const [oldId, draft] of Object.entries(entry.drafts)) {
      if (!entry.views.some((view) => view.id === oldId)) {
        const replacement = entry.views.find((view) => view.kind === draft.kind);
        if (replacement) {
          entry.drafts[replacement.id] = { ...draft, conflict: Boolean(draft.conflict || draft.baseRevision !== snapshot.revision) };
          delete entry.drafts[oldId];
        }
      }
      if (draft.baseRevision !== snapshot.revision) draft.conflict = true;
    }
    if (entry.failedRequest && snapshot.recent_actions?.some((item) => item.action_id === entry.failedRequest.action_id)) {
      const draft = entry.drafts[entry.failedDraft];
      if (draft?.baseline && JSON.stringify(changedValues(draft.baseline, draft.values)) === JSON.stringify(entry.failedRequest.payload?.values)) delete entry.drafts[entry.failedDraft];
      if (draft?.selectedIds && JSON.stringify(draft.selectedIds) === JSON.stringify(entry.failedRequest.payload?.recipe_ids)) delete entry.drafts[entry.failedDraft];
      if (entry.failedRequest.type === 'start_timer') {
        for (const [key,value] of Object.entries(entry.drafts)) {
          if (value.kind === 'timers' && Math.round(value.minutes * 60) === entry.failedRequest.payload.seconds
              && (value.label?.trim() || '厨房计时') === entry.failedRequest.payload.label) delete entry.drafts[key];
        }
      }
      entry.failedRequest = null; entry.autoSaveError = false; entry.error = '';
    }
    writeDrafts(id, entry.drafts); this.refresh(id); return true;
  }
  refresh(id) { if (id === this.activeId) this.render(); this.onChange(); }
  snapshot(id = this.activeId) { return clone(this.entries.get(id)?.snapshot); }
  canSubmit(id = this.activeId) {
    const entry = this.entries.get(id);
    return !entry || (!entry.saving && !entry.failedRequest && !entry.conflict && !entry.autoSaveError
      && !Object.values(entry.drafts).some((draft) => draft.kind !== 'timers' && (draft.kind !== 'preference_form' || draft.conflict)));
  }
  unsaved(id = this.activeId) {
    const entry = this.entries.get(id);
    return Boolean(entry && (entry.saving || entry.failedRequest || entry.conflict || Object.keys(entry.drafts).length));
  }
  composerActions(id = this.activeId) {
    if(!id)return [];
    const entry=this.entry(id),actions=[];
    for(const [viewId,draft] of Object.entries(entry.drafts)) {
      const view=entry.views.find(item=>item.id===viewId);
      if(!view || !view.editable)continue;
      if(draft.kind==='preference_form') {
        actions.push({label:draft.conflict?'复核后保存条件':'保存条件',context:'条件已修改',tone:'primary',disabled:entry.saving || entry.confirming,
          run:async()=>{
            for(const form of document.querySelectorAll(`[data-view-id="${view.id}"] form`)) if(!form.reportValidity())return;
            return this.confirmAndContinue(id,()=>this.savePreferences(view,draft.values,id),'我已确认这些偏好，请按保存的条件继续帮我选菜。');
          }});
      } else if(draft.kind==='recipe_choices') {
        const locked=['prep','cooking','completed'].includes(entry.snapshot?.stage);
        actions.push({label:draft.conflict?'复核后确认菜品':'确认菜品',context:`已选${draft.selectedIds?.length || 0}道菜`,
          tone:'primary',disabled:entry.saving || entry.confirming || locked || !draft.selectedIds?.length || draft.selectedIds.length>4,
          run:()=>{
            const names=(view.data.recipes || []).filter(recipe=>draft.selectedIds.includes(recipe.recipe_id)).map(recipe=>recipe.name);
            const message=names.length ? `我已确认菜品：${names.join('、')}。请按当前餐单继续安排。` : '我已确认选好的菜品，请按当前餐单继续安排。';
            return this.confirmAndContinue(id,()=>this.saveSelection(view,draft.selectedIds,id),message);
          }});
      } else if(draft.kind==='meal_plan') {
        actions.push({label:'更新份量',context:'份量已修改',disabled:entry.saving || !['planning','prep'].includes(entry.snapshot?.stage),
          run:async()=>{
            for(const form of document.querySelectorAll(`[data-view-id="${view.id}"] form`))if(!form.reportValidity())return;
            for(const [recipeId,servings] of Object.entries({...draft.servings})) {
              const current=entry.views.find(item=>item.kind==='meal_plan');
              await this.dispatch('change_servings',{recipe_id:recipeId,servings},current,{sessionId:id,
                revision:draft.conflict?entry.snapshot.revision:draft.baseRevision});
              delete draft.servings[recipeId];draft.baseline[recipeId]=servings;
              draft.baseRevision=entry.snapshot.revision;draft.conflict=false;
              if(!Object.keys(draft.servings).length)delete entry.drafts[viewId];
              writeDrafts(id,entry.drafts);this.refresh(id);
            }
          }});
      } else if(draft.kind==='timers') {
        // Timer fields have their own submit button; they do not block asking
        // the assistant or duplicate confirmation controls above the composer.
        continue;
      } else continue;
      actions.push({label:'放弃修改',tone:'secondary',disabled:entry.saving || entry.confirming,run:()=>this.discardDraft(view,id)});
    }
    return actions;
  }
  async confirmAndContinue(id, save, message) {
    const entry=this.entry(id);
    if(entry.confirming || entry.saving)return;
    entry.confirming=true;this.refresh(id);
    try {
      await save();
      await this.onConfirm(id,message);
    } finally { entry.confirming=false;this.refresh(id); }
  }
  setServingsDraft(view,recipeId,value,id=this.activeId) {
    const entry=this.entry(id),draft=entry.drafts[view.id] ||= {kind:'meal_plan',servings:{},
      baseline:Object.fromEntries(view.data.recipes.map(recipe=>[recipe.recipe_id,recipe.target_servings ?? recipe.servings])),baseRevision:view.revision,conflict:false};
    if(value===draft.baseline[recipeId])delete draft.servings[recipeId];else draft.servings[recipeId]=value;
    if(!Object.keys(draft.servings).length)delete entry.drafts[view.id];
    writeDrafts(id,entry.drafts);this.onChange();
  }
  setTimerDraft(view,label,minutes,id=this.activeId,duration) {
    const entry=this.entry(id),prior=entry.drafts[view.id];
    if(!label.trim() && minutes==null && !duration)delete entry.drafts[view.id];
    else entry.drafts[view.id]={kind:'timers',label,minutes,baseRevision:prior?.baseRevision ?? view.revision,
      conflict:Boolean(prior?.conflict),nonce:prior?.nonce || crypto.randomUUID(),...(duration?{duration:clone(duration)}:{})};
    writeDrafts(id,entry.drafts);this.onChange();
  }
  async startTimer(view, {seconds, label}, id = this.activeId) {
    if (!Number.isInteger(seconds) || seconds < 1 || seconds > 86400) throw new Error('计时请填写 1 秒到 24 小时。');
    const entry = this.entry(id), draft = entry.drafts[view.id];
    if (draft && !draft.nonce) draft.nonce = crypto.randomUUID();
    await this.dispatch('start_timer', {seconds, label:label?.trim() || '厨房计时'}, view,
      {sessionId:id, revision:draft?.conflict ? entry.snapshot.revision : draft?.baseRevision ?? view.revision, clearDraft:view.id});
    // A successful start may replace the timer view. Clear its migrated draft,
    // while preserving unrelated edits and any input retained after failure.
    if (draft?.nonce) for (const [key,value] of Object.entries(entry.drafts)) if (value.nonce === draft.nonce) delete entry.drafts[key];
    writeDrafts(id,entry.drafts); this.refresh(id);
  }
  syncLabel(id = this.activeId) {
    const entry = this.entries.get(id);
    if (!id) return '';
    if (entry?.saving) return '正在同步任务…';
    if (entry?.failedRequest) return '同步状态未知，请先检查当前任务';
    if (entry?.conflict) return '任务已变化，操作待复核';
    if (Object.values(entry?.drafts || {}).some((draft) => draft.conflict)) return '任务已变化，表单待复核';
    if (Object.keys(entry?.drafts || {}).length) return '';
    if (entry?.snapshot) return '当前内容已保存';
    return entry?.error ? '任务暂时不可用' : '正在读取任务…';
  }
  async prepareTurn(id) {
    const entry = this.entry(id);
    // 每次发消息都核对服务端版本；已编辑草稿不会被这次读取覆盖。
    await this.load(id);
    if (!this.canSubmit(id)) throw new Error('请先用输入框上方的候选按钮保存或放弃修改，再发送消息。');
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
  setDraft(view, values, id = this.activeId) {
    const entry = this.entry(id); const previous = entry.drafts[view.id];
    if (entry.autoSaveError) { entry.autoSaveError = false; if (!entry.conflict && !entry.failedRequest) entry.error = ''; }
    const baseline = previous?.baseline || clone(view.data.values || {});
    if (!Object.keys(changedValues(baseline, values)).length) delete entry.drafts[view.id];
    else entry.drafts[view.id] = { kind: view.kind, baseline, values: clone(values), baseRevision: previous?.baseRevision ?? view.revision,
      conflict: Boolean(previous?.conflict || previous && previous.baseRevision !== entry.snapshot?.revision) };
    writeDrafts(id, entry.drafts); this.onChange();
  }
  setSelectionDraft(view, selectedIds, id = this.activeId) {
    const entry = this.entry(id);
    const selected = entry.views.find((item) => item.kind === 'meal_plan')?.data.recipes?.map((recipe) => recipe.recipe_id) || [];
    if (JSON.stringify([...selectedIds].sort()) === JSON.stringify([...selected].sort())) delete entry.drafts[view.id];
    else entry.drafts[view.id] = { kind: view.kind, selectedIds: [...selectedIds],
      baseRevision: entry.drafts[view.id]?.baseRevision ?? view.revision,
      conflict: Boolean(entry.drafts[view.id]?.conflict) };
    writeDrafts(id, entry.drafts); this.onChange();
  }
  async saveSelection(view, selectedIds, id = this.activeId) {
    const entry = this.entry(id); const draft = entry.drafts[view.id];
    await this.dispatch('select_recipes', { recipe_ids: selectedIds }, view,
      { sessionId:id, revision: draft?.conflict ? entry.snapshot.revision : draft?.baseRevision ?? view.revision, clearDraft: view.id });
  }
  discardDraft(view, id = this.activeId) {
    const entry = this.entry(id); delete entry.drafts[view.id];
    entry.autoSaveError = false;
    entry.viewState[view.id] = {...entry.viewState[view.id], draftEpoch:(entry.viewState[view.id]?.draftEpoch || 0)+1};
    if (entry.conflict?.clearDraft === view.id || entry.conflict?.kind === view.kind) { entry.conflict = null; entry.error = ''; }
    writeDrafts(id, entry.drafts); this.refresh(id);
  }
  async savePreferences(view, values, id = this.activeId) {
    const entry = this.entry(id); const draft = entry.drafts[view.id];
    if (!draft) return;
    const current = entry.views.find((item) => item.kind === 'preference_form');
    const revision = draft.conflict ? entry.snapshot.revision : draft.baseRevision;
    await this.dispatch('update_preferences', { values: changedValues(draft.baseline, values) }, current || view,
      { sessionId:id, revision, clearDraft: view.id });
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
      this.receive(id, response, { immediate: true, userViewKind: type==='open_recipe_detail' ? 'recipe_detail' : type==='start_timer' ? 'timers' : null });
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
      if (['complete_step', 'set_active_step'].includes(type)) {
        for (const guide of entry.views.filter(item => item.kind === 'cooking_guide')) delete (entry.viewState[guide.id] ||= {}).stepIndex;
      }
      if (options.clearDraft) delete entry.drafts[options.clearDraft];
      if(type==='start_timer')this.onTimerStarted?.(payload,id);
      writeDrafts(id, entry.drafts); this.notify('已同步到当前任务。'); return response;
    } catch (error) {
      if (error.status === 409 || error.data?.status === 'revision_conflict') {
        this.receive(id, error.data, { immediate: true });
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
  context(view, id = this.activeId) {
    const entry = this.entry(id); const draft = entry.drafts[view.id];
    return { historical: false, busy: entry.saving, draft, selectedRecipeIds: entry.snapshot?.selected_recipe_ids || [],
        stage: entry.snapshot?.stage,
        activeRecipeId: entry.snapshot?.active_recipe_id,
        onServings:(origin,recipeId,value)=>this.setServingsDraft(origin,recipeId,value,id),
        onTimer:(origin,label,minutes,duration)=>this.setTimerDraft(origin,label,minutes,id,duration),
        onStartTimer:(origin,payload)=>this.startTimer(origin,payload,id),
        timerNow:()=>entry.timerNow(),
        openTimers:timerId=>this.onOpenTimers?.(timerId),
        openFocus: (origin,trigger) => this.onFocus?.(origin,id,trigger),
        mealRecipes: entry.views.find(item => item.kind === 'meal_plan')?.data.recipes ||
          entry.snapshot?.components?.find(item => item.kind === 'meal_plan')?.data.recipes || [],
        viewState: entry.viewState[view.id] ||= {},
        onViewState: () => { this.render(); this.onInlineChange?.(); },
        openRecipeSection: async (recipeId,section) => {
          await this.dispatch('open_recipe_detail',{recipe_id:recipeId},view,{sessionId:id});
          const detail=entry.views.find(item=>item.kind==='recipe_detail' && item.data.recipe?.recipe_id===recipeId);
          if(detail) { (entry.viewState[detail.id] ||= {}).detailSection=section;entry.activeKind='recipe_detail';this.refresh(id); }
        },
        action: (type, payload, origin) => this.dispatch(type, payload, origin, {sessionId:id}),
        openCurrent: (kind) => this.openCurrent(kind), onDraft: (origin, values) => this.setDraft(origin, values, id),
        dirty: (origin, values) => Object.keys(changedValues(entry.drafts[origin.id]?.baseline || origin.data.values || {}, values)).length > 0,
        savePreferences: (origin, values) => this.savePreferences(origin, values, id), discardDraft: (origin) => this.discardDraft(origin, id),
        onSelection: (origin, ids) => this.setSelectionDraft(origin, ids, id), saveSelection: (origin, ids) => this.saveSelection(origin, ids, id) };
  }
  renderInline(view, sessionId, onChange) {
    this.onInlineChange = onChange;
    const entry = this.entries.get(sessionId);
    const current = entry?.views.find(item => item.id === view.id && item.kind === view.kind);
    if (!current || sessionId !== this.activeId) return null;
    const context = this.context(current, sessionId);
    const openRecipeSection = context.openRecipeSection;
    const showDetail = async (recipeId, section) => {
      await openRecipeSection(recipeId, section);
      const detail = entry.views.find(item=>item.kind==='recipe_detail' && item.data.recipe?.recipe_id===recipeId);
      if (detail) { entry.inlineDetail={parentId:current.id,viewId:detail.id}; onChange(); }
    };
    const originalAction=context.action;
    context.openRecipeSection=showDetail;
    context.action=(type,payload,origin)=>type==='open_recipe_detail'
      ? showDetail(payload.recipe_id,'ingredients') : originalAction(type,payload,origin);
    // 内联详情替换候选内容，返回时保留选择和分页，不在旁边再堆一张卡片。
    const detail=entry.inlineDetail?.parentId===current.id && entry.views.find(item=>item.id===entry.inlineDetail.viewId);
    let content;
    if(detail) {
      content=renderView(detail,this.context(detail,sessionId));
      const back=node('button','ui-text-button');back.append(icon('arrow-left'),document.createTextNode('返回选择'));back.type='button';
      back.addEventListener('click',()=>{entry.inlineDetail=null;onChange();});content.prepend(back);
    } else content=renderView(current,context);
    if(entry.error) {
      const error=node('div','error-box',entry.error);
      const review=node('button','ui-text-button','查看保存状态');review.type='button';
      review.addEventListener('click',()=>this.openCurrent(current.kind));error.append(review);content.append(error);
    }
    return content;
  }
  renderInto(container) { this.container = container; this.render(); }
  render() {
    if (!this.container) return;
    const scrollTop = this.container.scrollTop;
    const focusKey = this.container.contains(document.activeElement) ? document.activeElement?.dataset?.controlKey : null;
    if (focusKey && this.activeId) this.entry(this.activeId).focusKey = focusKey;
    this.container.replaceChildren();
    const id = this.activeId;
    if (!id) { this.container.append(node('div', 'panel-empty', '从一个会话开始。选菜、备菜、步骤和计时都会随当前任务保存。')); return; }
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
      const content = renderView(view, this.context(view, id));
      this.container.append(content);
    }
    this.tick();
    this.container.scrollTop = scrollTop;
    if (entry.focusKey) {
      const target = this.container.querySelector(`[data-control-key="${CSS.escape(entry.focusKey)}"]`);
      if (target && !target.disabled) target.focus({ preventScroll: true });
    }
  }
  readTimerAcknowledged(id) {
    try { return new Set(JSON.parse(localStorage.getItem(`chefmate:timer-ack:${id}`) || '[]')); } catch { return new Set(); }
  }
  acknowledgeTimer(id, timerId) {
    const entry = this.entry(id); entry.timerAcknowledged.add(timerId);
    try { localStorage.setItem(`chefmate:timer-ack:${id}`, JSON.stringify([...entry.timerAcknowledged].slice(-100))); } catch { /* Keep the acknowledgement in this tab. */ }
    this.tick();
  }
  tick() {
    if (typeof document === 'undefined') return;
    const entry = this.entries.get(this.activeId), timers = workspaceTimers(entry?.views || []);
    let stateChanged = false; const reminders=[];
    for (const timer of timers) {
      const presentation = timerPresentation(timer,entry.timerNow());
      const prior = entry.timerStates.get(timer.id);
      if (prior && prior !== presentation.state) stateChanged = true;
      if (prior === 'running' && presentation.expired) reminders.push(timer);
      entry.timerStates.set(timer.id,presentation.state);
      const targets = document.querySelectorAll(`[data-timer-id="${CSS.escape(timer.id)}"]`);
      for (const target of targets) {
        target.textContent = presentation.clock;
        target.dataset.timerState = presentation.state;
        target.setAttribute('aria-label',presentation.remaining == null ? '剩余时间未知' : `剩余 ${presentation.remaining} 秒`);
      }
      const selector = CSS.escape(timer.id);
      for (const row of document.querySelectorAll(`[data-timer-row="${selector}"]`)) row.dataset.timerState = presentation.state;
      for (const status of document.querySelectorAll(`[data-timer-status="${selector}"]`)) status.textContent = presentation.status;
      for (const progress of document.querySelectorAll(`[data-timer-progress="${selector}"]`)) progress.value = presentation.progress;
      for (const controls of document.querySelectorAll(`[data-timer-controls="${selector}"]`)) controls.hidden = presentation.expired;
      for (const expired of document.querySelectorAll(`[data-timer-expired="${selector}"]`)) expired.hidden = !presentation.expired;
    }
    this.onTimerRender?.(timers,entry,this.activeId);
    for (const timer of reminders) {
      this.notify(`${timer.label}时间到，看看锅里的状态。`);
      this.onTimerReminder?.(timer,this.activeId);
    }
    if (stateChanged) this.onTimerStateChange?.();
  }
}
