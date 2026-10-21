import { WorkspaceController, ConfirmationQueue, visibleMessageViews, recoverRunningTurn } from './workspace.js';
import { renderView } from './ui-components.js';
import { legacyRecipeItems } from './ui-protocol.js';
import { STAGE_NAMES, VIEW_LABELS, TOOL_NAMES, viewSummary, activityRows, latestActivity, conversationLabel, snapshotStatus } from './presentation.js';
import {brandImage,applyBranding} from './branding.js';
import {CookingFocus} from './focus.js';
import {prepareImage} from './image-input.js';
import {imageThumbnail} from './image-preview.js';
import {icon} from './icons.js';
import {initShell} from './shell.js';
import {activityLabel} from './activity.js';
import {renderStarters} from './welcome.js';
import {moveDrafts,releaseImages,imageMetadata,suggestedReplies,matchesDraft,recoverLocalImages} from './conversation-state.js';
import {QuestionState,answerRequestId,matchesAnswer} from './question-state.js';
import {QuestionComposer} from './question.js';
import {DeveloperPanel} from './developer-panel.js';
import {TimerPopover} from './timer-popover.js';
applyBranding();

const $ = (id) => document.getElementById(id);
const ui = Object.fromEntries([
  'app', 'sidebar', 'sessions', 'new-chat', 'chat-title', 'status-button', 'status-dot',
  'status-label', 'connection-banner', 'conversation', 'welcome', 'messages', 'composer',
  'composer-input', 'send-button', 'stop-button', 'turn-status',
  'detail-panel', 'query-tab', 'recipe-tab', 'query-content', 'recipe-content',
  'drawer-backdrop', 'toast', 'announcer', 'open-sidebar', 'toggle-details',
  'workspace-tab', 'workspace-content', 'workspace-context', 'skills-tab', 'skills-content',
  'conversation-stage',
  'attach-image','image-input','image-drafts','image-status','composer-choices','reply-suggestions','welcome-templates','shuffle-templates','resume-draft','question-composer',
].map((id) => [id, $(id)]));

const state = {
  status: null, sessions: [], currentId: null, cache: new Map(), drafts: new Map(),
  runs: new Map(), selectionEpoch: 0, sessionsEpoch: 0, detailEpoch: 0,
  detailAbort: null, detailOpen: false, sidebarOpen: false, tab: 'query',
  selectedRecipe: null, remotePoll: null, renderFrame: null, creating: false, preparing: false, initializing:true, skills: [], skillsEpoch: 0,
  traceMode: readTraceMode(), developer: readDeveloper(), uiPreferences: readUIPreferences(),
  imageDrafts:new Map(),imageUploads:new Map(),confirmationQueue:new ConfirmationQueue(),
  blankId:`draft:${crypto.randomUUID()}`,starterCapability:null,failedDrafts:new Map(),
};
const questions=new QuestionState({
  read:id=>JSON.parse(localStorage.getItem(`chefmate:question:${id}`) || 'null'),
  write:(id,value)=>value ? localStorage.setItem(`chefmate:question:${id}`,JSON.stringify(value)) : localStorage.removeItem(`chefmate:question:${id}`),
});
const answering=new Set(),continuingAnswers=new Set();
function readDeveloper() { try { return JSON.parse(localStorage.getItem('chefmate:developer-settings') || '{}').enabled === true; } catch { return false; } }
function readTraceMode() { try { return JSON.parse(localStorage.getItem('chefmate:developer-settings') || '{}').trace_mode === 'detailed' ? 'detailed' : 'compact'; } catch { return 'compact'; } }
function readUIPreferences() { try { return JSON.parse(localStorage.getItem('chefmate:ui-settings') || '{}'); } catch { return {}; } }
document.body.classList.toggle('compact-density',state.uiPreferences.density==='compact');
function refreshDisplayPreferences() {
  state.developer=readDeveloper();state.traceMode=readTraceMode();state.uiPreferences=readUIPreferences();
  ui['query-tab'].hidden=!state.developer;ui['skills-tab'].hidden=!state.developer;
  if (!state.developer && ['query','skills'].includes(state.tab)) setTab('workspace');
  document.body.classList.toggle('compact-density',state.uiPreferences.density==='compact');
  applyBranding();
  renderConversation();
}
window.addEventListener('pageshow',refreshDisplayPreferences);
window.addEventListener('storage',event=>{
  if(['chefmate:developer-settings','chefmate:ui-settings'].includes(event.key)) refreshDisplayPreferences();
});
function storedSession() { try { return localStorage.getItem('chefmate:active-session'); } catch { return null; } }
const labels = {
  find_ingredients: '查找食材', search_recipes: '查询菜谱', get_recipe: '读取菜谱',
  get_search_options: '读取查询选项', beginner: '新手', normal: '普通', advanced: '进阶',
  update_preferences: '更新偏好', present_preferences: '整理偏好', show_recipe_choices: '展示菜谱选择',
  build_meal_plan: '安排菜单', build_shopping_list: '整理备菜清单', prepare_ingredients: '准备食材',
  get_cooking_guide: '整理烹饪步骤', start_timer: '开始计时', load_skill: '读取厨房技能', list_skills: '查看厨房技能',
  complete: '完整', estimated: '含估算', incomplete: '不完整', unknown: '未知',
};
const methods = { steam: '蒸', boil: '煮', stir_fry: '炒', deep_fry: '炸', pan_fry: '煎', bake: '烘焙', roast: '烤', stew: '炖', braise: '焖烧', cold_mix: '凉拌', other: '其他' };
const tools = { stove: '炉灶', wok: '炒锅', pot: '锅', oven: '烤箱', air_fryer: '空气炸锅', microwave: '微波炉', rice_cooker: '电饭煲', steamer: '蒸锅', pressure_cooker: '高压锅' };
const workspace = new WorkspaceController({
  api, notify: toast, onChange: () => {
    updateComposer(); updateConversationState();
    workspace.tick();
    const editing = ui.messages.contains(document.activeElement) && document.activeElement?.matches('input,textarea,select');
    if (!editing || workspace.entries.get(state.currentId)?.saving) scheduleRender(state.currentId);
  },
  openPanel: (tab) => { setDetails(true); setTab(tab); },
  onConfirm: (sessionId, message) => { state.confirmationQueue.enqueue(sessionId,message); continueConfirmations(); },
});
const cookingFocus=new CookingFocus(workspace,{session:currentSession,onChange:updateComposer,
  activity:id=>({running:busy(id),status:state.runs.get(id)?.status,cancelling:state.runs.get(id)?.cancelling}),
  renderMessage:markdown,retry:message=>sendMessage(message.retryMessage,!message.retryAsNew,message)});
const timerPopover=new TimerPopover(workspace);
workspace.onTimerRender=(timers,entry,id)=>timerPopover.update(timers,entry,id);
workspace.onOpenTimers=timerId=>timerPopover.open(undefined,{timerId});
workspace.onTimerReminder=(timer,id)=>timerPopover.remind(timer,id);
workspace.onTimerStarted=(payload,id)=>timerPopover.started(payload,id);
workspace.onTimerSceneChange=()=>timerPopover.close(false);
const developerPanel=new DeveloperPanel({api,sessionId:()=>state.currentId,session:currentSession,enabled:()=>state.developer});
document.addEventListener('visibilitychange',()=>{ if(!document.hidden)workspace.tick(); });
const questionComposer=new QuestionComposer(ui['question-composer'],{state:questions,onAnswer:submitAnswer,
  onRetry:id=>{const entry=questions.get(id);if(entry)questions.set(id,{...entry,stage:'saved',error:''});updateComposer();}});
workspace.onFocus=(view,id,trigger)=>{
  const source=cookingFocus.captureSource(view,trigger);
  setSidebar(false);setDetails(false);cookingFocus.open(view,id,source);
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function announce(text) { ui.announcer.textContent = text; }
let toastTimer;
function toast(text) {
  clearTimeout(toastTimer);
  ui.toast.textContent = text;
  ui.toast.hidden = false;
  toastTimer = setTimeout(() => { ui.toast.hidden = true; }, 4200);
}

function safeText(value) {
  return String(value ?? '')
    .replace(/(\b(?:postgres(?:ql)?|https?):\/\/)[^\s/@]+:[^\s/@]+@/gi, '$1[已隐藏]@')
    .replace(/\bBearer\s+\S+/gi, 'Bearer [已隐藏]')
    .replace(/\bsk-[a-zA-Z0-9_-]{12,}/g, '[密钥已隐藏]')
    .replace(/((?:api[_ -]?key|password|secret|token|authorization)\s*[:=]\s*)[^\s,;]+/gi, '$1[已隐藏]');
}

function safeValue(value, depth = 0) {
  if (depth > 10) return '[内容过深]';
  if (typeof value === 'string') return safeText(value);
  if (Array.isArray(value)) return value.map((item) => safeValue(item, depth + 1));
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key,
      /^(?:api_?key|password|secret|token|access_token|authorization|database_url|dsn|connection_string)$/i.test(key)
        ? '[已隐藏]' : safeValue(item, depth + 1),
    ]));
  }
  return value;
}

function pretty(value) {
  if (value === undefined) return '无';
  try { return JSON.stringify(safeValue(value), null, 2); } catch { return '无法显示这段记录'; }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
  });
  let payload;
  try { payload = await response.json(); } catch { payload = null; }
  if (!response.ok) {
    const detail = payload?.detail;
    const message = typeof detail === 'string' ? detail : payload?.message || payload?.error?.message;
    const error = new Error(safeText(message || `请求失败（${response.status}），请稍后重试。`));
    error.status = response.status;
    error.data = payload;
    throw error;
  }
  return payload;
}

function currentSession() { return state.cache.get(state.currentId); }
function pendingQuestion(id=state.currentId) {
  const snapshot=workspace.snapshot(id);
  return snapshot ? snapshot.pending_question || null : state.cache.get(id)?.pending_question || null;
}
function savedAnswer(id=state.currentId) {
  const pending=questions.get(id),snapshot=workspace.snapshot(id);
  if(pending?.stage==='uncertain' && !snapshot?.pending_question && matchesAnswer(snapshot?.last_answer,pending.payload))questions.saved(id);
  questions.recovered(id,state.cache.get(id));
  const entry=questions.get(id);
  return entry && ['saved','sending','continue_failed'].includes(entry.stage) ? entry : null;
}
async function submitAnswer(id,question) {
  if(answering.has(id) || busy(id) || !state.status?.ready)return;
  const snapshot=workspace.snapshot(id);
  if(snapshot?.pending_question?.id!==question.id){toast('问题已更新，请先查看最新内容。');return;}
  let entry;
  try{entry=questions.prepare(id,question,snapshot.revision);}catch(error){questions.fail(id,error.message);updateComposer();return;}
  answering.add(id);updateComposer();
  try {
    await workspace.dispatch('answer_question',entry.payload,null,{sessionId:id,request:entry.request});
    questions.saved(id);
    const cached=state.cache.get(id);if(cached){cached.pending_question=null;cached.awaiting_user=false;}
    const summary=state.sessions.find(session=>session.id===id);if(summary)summary.awaiting_user=false;
    renderSessions();
  } catch(error) {
    questions.fail(id,error.message || '答案没有保存，请重试。',{uncertain:!error.knownRejected && (!error.status || error.status>=500)});
  } finally {answering.delete(id);updateComposer();}
}
async function continueAnswers() {
  if(state.initializing || state.creating || state.preparing || !state.status?.ready)return;
  const ids=new Set([...state.sessions.map(session=>session.id),...state.cache.keys()]);
  for(const id of ids) {
    const entry=savedAnswer(id);
    if(!entry || entry.stage==='continue_failed' || busy(id) || continuingAnswers.has(id) || pendingQuestion(id))continue;
    continuingAnswers.add(id);questions.set(id,{...entry,stage:'sending',error:''});
    try {
      await sendMessage(entry.message,false,null,{sessionId:id,fromComposer:false,questionContinuation:true,
        requestId:answerRequestId(entry.questionId),onAccepted:()=>questions.accepted(id)});
      questions.failedContinuation(id,entry.questionId,'答案已保存，下一轮尚未开始。');
    } catch(error) {
      questions.failedContinuation(id,entry.questionId,error.message || '答案已保存，下一轮尚未开始。');
    } finally {continuingAnswers.delete(id);updateComposer();}
    break;
  }
}
function draftKey(id=state.currentId) {return id ?? state.blankId;}
function fillComposer(text,selection=null) {
  if(state.creating || state.preparing)return;
  ui['composer-input'].value=text;resizeInput();persistDraft();updateComposer();ui['composer-input'].focus();
  const start=selection ? text.indexOf(selection) : -1;
  ui['composer-input'].setSelectionRange(start>=0?start:text.length,start>=0?start+selection.length:text.length);
}
function renderWelcomeTemplates(force=false) {
  const capability=Boolean(state.status?.supports_images);
  if(!force && state.starterCapability===capability)return;
  state.starterCapability=capability;
  renderStarters(ui['welcome-templates'],{supportsImages:capability,onUse:(text,selection,images)=>{
    fillComposer(text,selection);
    if(images && !state.imageDrafts.get(draftKey())?.length)toast('可以用输入框旁的图片按钮附上照片。');
  }});
}
function renderReplySuggestions() {
  const replies=suggestedReplies(currentSession()?.messages,state.status?.reply_suggestions!==false);
  const host=ui['reply-suggestions'];host.hidden=!replies.length || busy() || Boolean(pendingQuestion() || savedAnswer() || state.confirmationQueue.get(state.currentId));
  host.replaceChildren();
  for(const reply of replies) {
    const button=element('button','reply-suggestion',reply.title);button.type='button';button.title=reply.text;
    button.disabled=state.creating || state.preparing;button.append(icon('corner-down-left'));
    button.addEventListener('click',()=>fillComposer(reply.text));host.append(button);
  }
}
function recoverableDraft() {return [...state.failedDrafts.keys()].reverse().find(key=>key!==draftKey());}
function resumeDraft() {
  const key=recoverableDraft();if(!key || state.creating || state.preparing)return;
  persistDraft();
  // Keep the current local draft retrievable if restoring a different one.
  if(state.currentId===null && (state.drafts.get(state.blankId)?.trim() || state.imageDrafts.get(state.blankId)?.length))state.failedDrafts.set(state.blankId,true);
  const saved=state.failedDrafts.get(key);
  if(saved?.sessionId){void openSession(saved.sessionId);return;}
  state.blankId=key;cookingFocus.close({immediate:true});selectBlank();setSidebar(false);ui['composer-input'].focus();
}
function hasRetry(message) { return message.retryMessage!=null && (Boolean(message.retryMessage) || Boolean(message.retryImages?.length)); }
function renderComposerActions() {
  const pending=state.confirmationQueue.get(state.currentId),sessionId=state.currentId;
  const actions=pending ? [
    {label:pending.status==='failed'?'重试继续':'准备继续…',tone:'primary',disabled:pending.status!=='failed',
      context:pending.status==='failed' ? '确认已保存，下一轮尚未开始。' : '确认已保存，正在准备下一轮…',
      run:()=>{state.confirmationQueue.retry(sessionId);continueConfirmations();}},
    {label:'暂不继续',tone:'secondary',allowOffline:true,disabled:pending.status==='preparing',
      run:()=>{state.confirmationQueue.delete(sessionId);updateComposer();}},
  ] : workspace.composerActions(sessionId);
  ui['composer-choices'].hidden=!actions.length;ui['composer-choices'].replaceChildren();
  if(actions[0]?.context)ui['composer-choices'].append(element('span','choice-context',actions[0].context));
  for(const action of actions) {
    const button=element('button',`composer-choice ${action.tone || 'primary'}`,action.label);button.type='button';button.disabled=Boolean(action.disabled || busy() || state.preparing || (!state.status?.ready && !action.allowOffline));
    button.addEventListener('click',()=>Promise.resolve(action.run()).catch(error=>toast(error.message || '操作没有保存，请复核。')));
    ui['composer-choices'].append(button);
  }
}
function renderImageDrafts() {
  const id=state.currentId,key=draftKey(),images=state.imageDrafts.get(key) || [],uploading=state.imageUploads.get(key);
  ui['image-drafts'].hidden=!images.length;ui['image-drafts'].replaceChildren();
  ui['image-status'].hidden=!uploading;ui['image-status'].textContent=uploading?'正在整理图片…':'';
  for(const [index,image] of images.entries()) {
    const tile=element('div','attachment-draft'),preview=imageThumbnail(id,image,images,index);
    const remove=element('button');remove.append(icon('x'));remove.type='button';remove.setAttribute('aria-label',`移除图片：${image.name}`);remove.disabled=busy(id) || state.preparing;
    remove.addEventListener('click',async()=>{
      try {
        if(!image.local && id)await api(`/api/sessions/${encodeURIComponent(id)}/images/${encodeURIComponent(image.id)}`,{method:'DELETE'});
        releaseImages([image]);
        state.imageDrafts.set(key,(state.imageDrafts.get(key) || []).filter(item=>item.id!==image.id));
        if(key===draftKey())updateComposer();
      }catch(error){toast(error.message);}
    });
    tile.append(preview,remove,element('small','',`${image.width}×${image.height} · ${Math.round(image.byte_size/1024)} KB`));ui['image-drafts'].append(tile);
  }
}
async function uploadImages(files) {
  if(!files.length || state.initializing || state.preparing || busy() || !state.status?.supports_images)return;
  const key=draftKey();
  if(state.imageUploads.has(key)){toast('图片正在处理，请稍等再添加。');return;}
  const current=state.imageDrafts.get(key) || [];
  if(current.length+files.length>maxImages()){toast(`当前模型每次最多接收 ${maxImages()} 张图片。`);return;}
  const operation={};state.imageUploads.set(key,operation);updateComposer();
  try {
    for(const file of files) {
      const image=await prepareImage(file);
      if(state.imageUploads.get(key)!==operation){releaseImages([image]);break;}
      state.imageDrafts.set(key,[...(state.imageDrafts.get(key) || []),image]);
    }
  }catch(error){if(state.imageUploads.get(key)===operation)toast(error.message || '图片处理失败，请重新选择。');}
  finally{if(state.imageUploads.get(key)===operation)state.imageUploads.delete(key);if(key===draftKey())updateComposer();}
}
function updateConversationState() {
  const snapshot = workspace.snapshot(state.currentId);
  const stage = snapshot?.stage || currentSession()?.conversation_state?.stage;
  ui['conversation-stage'].hidden = !stage || !state.currentId;
  const status=snapshot?snapshotStatus(snapshot):currentSession()?.conversation_state || {};
  ui['conversation-stage'].textContent = conversationLabel(status);
  ui['conversation-stage'].title=conversationLabel(status);
  if (snapshot) {
    const item = state.sessions.find(item => item.id === state.currentId);
    if (item) {item.conversation_state = status;item.awaiting_user=Boolean(snapshot.pending_question) && !busy();}
    const badge = ui.sessions.querySelector(`[data-session-id="${CSS.escape(state.currentId)}"] .session-stage`);
    if (badge) {badge.textContent = snapshot.pending_question && !busy() ? '等你回答' : conversationLabel(status);badge.title=badge.textContent;}
  }
}
function busy(id = state.currentId) { return state.runs.has(id) || Boolean(state.cache.get(id)?.running); }
function maxImages() { return Math.max(1, Math.min(12, Number(state.status?.max_images) || 3)); }
function continueConfirmations() {
  if(state.initializing || state.creating || state.preparing || !state.status?.ready)return;
  for(const [sessionId,pending] of state.confirmationQueue.items) {
    if(busy(sessionId) || pending.status!=='queued')continue;
    // An action can finish while another conversation is preparing a turn.
    // Keep its continuation until that short preparation lock is released.
    void state.confirmationQueue.start(sessionId,(message,onPrepared)=>
      sendMessage(message,false,null,{sessionId,fromComposer:false,onPrepared})).finally(updateComposer);
    break;
  }
}
function blankTitle(title) { return !title || ['新会话', '新对话'].includes(title); }
function resizeInput() {
  ui['composer-input'].style.height = 'auto';
  ui['composer-input'].style.height = `${Math.min(ui['composer-input'].scrollHeight, 165)}px`;
}

function updateComposer() {
  const running = busy();
  const localRun = state.runs.get(state.currentId);
  const ready = Boolean(state.status?.ready);
  ui['send-button'].hidden = running;
  ui['stop-button'].hidden = !running;
  ui['stop-button'].disabled = Boolean(localRun?.cancelling);
  const imageDraft=state.imageDrafts.get(draftKey()) || [],uploading=state.imageUploads.get(draftKey());
  const question=pendingQuestion(),continuation=savedAnswer(),waiting=Boolean(question && !running);
  ui['send-button'].disabled = state.initializing || !ready || waiting || continuation || state.creating || state.preparing || uploading || Boolean(state.confirmationQueue.get(state.currentId)) ||
    (!ui['composer-input'].value.trim() && !imageDraft.length) || (imageDraft.length && !state.status?.supports_images) || imageDraft.length>maxImages() || !workspace.canSubmit(state.currentId);
  ui['attach-image'].hidden=!state.status?.supports_images;
  ui['attach-image'].disabled=state.initializing || !ready || running || state.creating || state.preparing || uploading || imageDraft.length>=maxImages();
  document.getElementById('kitchen-focus').classList.toggle('awaiting-answer',waiting);
  renderImageDrafts();renderComposerActions();renderReplySuggestions();cookingFocus.refresh();
  questionComposer.render(state.currentId,question,{busy:running,ready:ready && !state.preparing,continuation:!running && !question ? continuation : null});
  ui.composer.hidden=waiting || Boolean(!running && continuation);
  if(waiting){ui['image-drafts'].hidden=true;ui['composer-choices'].hidden=true;ui['resume-draft'].hidden=true;}
  ui['resume-draft'].hidden=waiting || Boolean(continuation) || !recoverableDraft();ui['resume-draft'].disabled=state.creating || state.preparing;
  ui['composer-input'].disabled = state.creating || state.preparing;
  ui['turn-status'].hidden = !running;
  const activity=localRun?.cancelling ? '正在停止，请稍等…'
    : localRun?.status || currentSession()?.recoveryStatus || (running ? '正在执行，可以随时停止。' : '');
  ui['turn-status'].replaceChildren();
  if(activity)ui['turn-status'].append(activityLabel(activity,localRun?.requestId || currentSession()?.active_turn?.id || state.currentId || 'draft'));
  const taskLabel = workspace.syncLabel(state.currentId);
  ui['workspace-context'].hidden = !taskLabel || taskLabel === '当前内容已保存';
  ui['workspace-context'].textContent = taskLabel;
  ui['workspace-context'].classList.toggle('unsaved', workspace.unsaved(state.currentId));
  queueMicrotask(continueConfirmations);
  queueMicrotask(continueAnswers);
}

function renderStatus() {
  const status = state.status;
  ui['status-dot'].className = `status-dot ${status?.ready ? 'ready' : status ? 'issue' : ''}`;
  ui['status-label'].textContent = !status ? '连接中' : status.ready ? '已连接' : '待连接';
  const db = status?.database;
  let message = '';
  if (status && !status.configured) message = '模型尚未配置。请按项目 README 设置模型连接后重启服务，再开始对话。';
  else if (status && !db?.ready) message = '菜谱数据库暂时不可用。请检查数据库连接，页面恢复连接后即可继续。';
  else if (status && !status.ready) message = '服务暂时未就绪，请稍后重试连接。';
  ui['connection-banner'].hidden = !message;
  ui['connection-banner'].textContent = message;
  renderWelcomeTemplates();
  updateComposer();
}

async function loadStatus() {
  try {
    state.status = await api('/api/status');
  } catch (error) {
    state.status = { ready: false, configured: true, database: { ready: false }, connectionError: error.message };
    ui['connection-banner'].hidden = false;
  }
  renderStatus();
  renderQueryPanel();
}

function renderSessions() {
  ui.sessions.replaceChildren();
  if (!state.sessions.length) ui.sessions.append(element('div', 'session-empty', '从一个新会话开始吧。'));
  for (const session of state.sessions) {
    const waiting=Boolean(!session.running && !state.runs.has(session.id) && (session.awaiting_user || (session.id===state.currentId && pendingQuestion())));
    const row = element('div', `session-row${session.id === state.currentId ? ' active' : ''}${waiting?' awaiting':''}`);
    row.dataset.sessionId = session.id;
    const title = session.id === state.currentId ? currentSession()?.title || session.title : session.title;
    const link = element('button', 'session-link', title || '新会话');
    link.type = 'button';
    link.setAttribute('aria-label', title || '新会话');
    const stageLabel=waiting ? '等你回答' : conversationLabel(session.conversation_state || {});
    const badge=element('small','session-stage',stageLabel);badge.title=stageLabel;link.append(badge);
    link.title = title || '新会话';
    link.setAttribute('aria-current', session.id === state.currentId ? 'page' : 'false');
    link.addEventListener('click', () => openSession(session.id));
    row.append(link);
    if(waiting){const marker=element('span','session-awaiting');marker.setAttribute('role','img');marker.setAttribute('aria-label','有问题等你回答');row.append(marker);}
    if (session.running || state.runs.has(session.id)) row.append(element('span', 'session-running'));
    const remove = element('button', 'session-delete');remove.append(icon('trash-2'));
    remove.type = 'button';
    remove.setAttribute('aria-label', `删除会话：${session.title || '新会话'}`);
    remove.title = '删除会话';
    remove.disabled = busy(session.id);
    let armed = false;
    let armTimer;
    remove.addEventListener('click', async () => {
      if (!armed) {
        armed = true;
        remove.textContent = '确认';
        remove.classList.add('confirm-delete');
        remove.setAttribute('aria-label', `确认删除会话：${session.title || '新会话'}`);
        armTimer = setTimeout(() => { armed = false; remove.replaceChildren(icon('trash-2')); remove.classList.remove('confirm-delete'); }, 3500);
        return;
      }
      clearTimeout(armTimer);
      remove.disabled = true;
      try {
        await api(`/api/sessions/${encodeURIComponent(session.id)}`, { method: 'DELETE' });
        state.cache.delete(session.id);
        state.drafts.delete(session.id);
        state.failedDrafts.delete(session.id);
        releaseImages(state.imageDrafts.get(session.id));state.imageDrafts.delete(session.id);state.imageUploads.delete(session.id);
        state.confirmationQueue.delete(session.id);
        questions.accepted(session.id);
        workspace.remove(session.id);
        if (state.currentId === session.id) selectBlank();
        await loadSessions();
      } catch (error) { toast(error.message); remove.disabled = false; }
    });
    row.append(remove);
    ui.sessions.append(row);
  }
  updateConversationState();
}

async function loadSessions() {
  const epoch = ++state.sessionsEpoch;
  const payload = await api('/api/sessions');
  if (epoch !== state.sessionsEpoch) return;
  state.sessions = Array.isArray(payload) ? payload : payload?.items || [];
  renderSessions();
}

function persistDraft() { state.drafts.set(draftKey(), ui['composer-input'].value); }
function resetRecipe() {
  state.detailEpoch++;
  state.detailAbort?.abort();
  state.detailAbort = null;
  state.selectedRecipe = null;
  renderRecipeEmpty();
}

function selectBlank() {
  state.selectionEpoch++;
  state.currentId = null;
  try { localStorage.removeItem('chefmate:active-session'); } catch { /* In-memory selection remains usable. */ }
  workspace.select(null);
  state.skillsEpoch++;
  clearTimeout(state.remotePoll);
  resetRecipe();
  ui['composer-input'].value = state.drafts.get(state.blankId) || '';
  resizeInput();
  renderConversation();
  renderSessions();
  renderQueryPanel();
  updateComposer();
}

function newSession() {
  if(state.creating || state.preparing)return;
  cookingFocus.close({immediate:true});
  persistDraft();
  if(!state.failedDrafts.has(state.blankId)) {
    releaseImages(state.imageDrafts.get(state.blankId));
    state.imageDrafts.delete(state.blankId);state.drafts.delete(state.blankId);
  }
  state.imageUploads.delete(state.blankId);
  state.blankId=`draft:${crypto.randomUUID()}`;
  selectBlank();setSidebar(false);renderWelcomeTemplates(true);ui['composer-input'].focus();
}

function normalizeMessage(message) {
  const result = message.result || message.metadata?.result || {};
  return {
    ...message, content: message.content ?? message.text ?? result.text ?? '',
    recipes: message.recipes ?? result.recipes ?? [],
    tool_calls: (message.tool_calls ?? result.tool_calls ?? []).map(normalizeToolCall),
    selected_recipe: message.selected_recipe ?? result.selected_recipe,
    snapshot: message.snapshot ?? result.snapshot,
    actions: message.actions ?? result.actions ?? [],
    skills: message.skills ?? result.skills ?? [],
    process: message.process ?? result.process ?? [],
    usage: message.usage ?? result.usage,
    suggested_replies:message.suggested_replies ?? result.suggested_replies ?? [],
  };
}

function normalizeToolCall(call) {
  return { ...call, running: Boolean(call.running || call.status === 'running') };
}

function normalizeSession(session) {
  const messages = (session.messages || []).filter((m) => ['user', 'assistant'].includes(m.role)).map(normalizeMessage);
  const running = Boolean(session.running || session.last_status === 'running');
  const lastUser = [...messages].reverse().find((message) => message.role === 'user');
  if (running && session.active_turn) {
    messages.push({ role: 'assistant',turn_id:session.active_turn.id,content: session.active_turn.text || '',
      tool_calls: (session.active_turn.tool_calls || []).map(normalizeToolCall), actions: session.active_turn.actions || [], process: session.active_turn.process || [], usage:session.active_turn.usage, recipes: [], streaming: true });
  } else if (['error', 'cancelled', 'interrupted'].includes(session.last_status) && messages.at(-1)?.role === 'user') {
    const result = session.last_run?.result || {};
    const cancelled = session.last_status === 'cancelled';
    const message = { role: 'assistant', content: result.text || '', tool_calls: (result.tool_calls || []).map(normalizeToolCall),
      recipes: result.recipes || [], snapshot: result.snapshot, actions: result.actions || [], process: result.process || [], usage:result.usage, cancelled,
      retryMessage: session.last_error?.retryable === false ? null : lastUser?.content };
    message.retryImages=lastUser?.images || [];
    if (!cancelled) message.error = session.last_error?.message
      || (session.last_status === 'interrupted' ? '服务重启中断了这次执行，可以重试。' : '这次执行没有完成，可以重试。');
    messages.push(message);
  }
  return { ...session, running, messages };
}

async function openSession(id) {
  if(cookingFocus.active?.sessionId!==id)cookingFocus.close({immediate:true});
  persistDraft();
  const epoch = ++state.selectionEpoch;
  state.currentId = id;
  try { localStorage.setItem('chefmate:active-session',id); } catch { /* In-memory selection remains usable. */ }
  workspace.setRunning(id,true);
  workspace.select(id);
  state.skills = [];
  loadSkills(id);
  clearTimeout(state.remotePoll);
  resetRecipe();
  setSidebar(false);
  ui['composer-input'].value = state.drafts.get(id) || '';
  resizeInput();
  renderConversation();
  renderSessions();
  renderQueryPanel();
  updateComposer();
  if (!state.runs.has(id)) {
    try {
      const session = normalizeSession(await api(`/api/sessions/${encodeURIComponent(id)}`));
      if (state.runs.has(id)) return;
      state.cache.set(id, session);
      workspace.setRunning(id,session.running);
      if (epoch !== state.selectionEpoch || id !== state.currentId) return;
      renderConversation(true);
      renderQueryPanel();
      updateComposer();
      if (session.running) pollRemoteSession(id, epoch);
    } catch (error) { if (epoch === state.selectionEpoch) toast(error.message); }
  }
}

function pollRemoteSession(id, epoch) {
  clearTimeout(state.remotePoll);
  state.remotePoll = setTimeout(async () => {
    if (id !== state.currentId || epoch !== state.selectionEpoch || state.runs.has(id)) return;
    try {
      const session = normalizeSession(await api(`/api/sessions/${encodeURIComponent(id)}`));
      if (id !== state.currentId || epoch !== state.selectionEpoch || state.runs.has(id)) return;
      state.cache.set(id, session);
      workspace.setRunning(id,session.running);
      renderConversation();
      renderQueryPanel();
      updateComposer();
      if (session.running) pollRemoteSession(id, epoch);
      else { await loadSessions(); workspace.load(id).catch(() => {}); loadSkills(id); announce('本次执行已结束。'); }
    } catch { if (id === state.currentId && epoch === state.selectionEpoch) pollRemoteSession(id, epoch); }
  }, 2500);
}

function safeLink(raw) {
  try {
    const url = new URL(raw);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

function inlineMarkdown(parent, text) {
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\([^\s)]+\))/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(last, match.index)));
    const token = match[0];
    if (token.startsWith('`')) parent.append(element('code', '', token.slice(1, -1)));
    else if (token.startsWith('**')) parent.append(element('strong', '', token.slice(2, -2)));
    else {
      const parts = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      const href = safeLink(parts[2]);
      const link = element(href ? 'a' : 'span', '', parts[1]);
      if (href) { link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer'; }
      parent.append(link);
    }
    last = match.index + token.length;
  }
  parent.append(document.createTextNode(text.slice(last)));
}

function markdown(text) {
  const fragment = document.createDocumentFragment();
  const lines = String(text || '').replace(/\r\n/g, '\n').split('\n');
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (/^\s*```/.test(line)) {
      const code = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
      if (i < lines.length) i++;
      const pre = element('pre');
      pre.append(element('code', '', code.join('\n')));
      fragment.append(pre);
      continue;
    }
    if (line.includes('|') && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1] || '')) {
      const split = (row) => row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim());
      const table = element('table');
      const head = element('thead');
      const header = element('tr');
      for (const cell of split(line)) { const th = element('th'); inlineMarkdown(th, cell); header.append(th); }
      head.append(header); table.append(head);
      const body = element('tbody');
      i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        const row = element('tr');
        for (const cell of split(lines[i++])) { const td = element('td'); inlineMarkdown(td, cell); row.append(td); }
        body.append(row);
      }
      table.append(body);
      const wrapper = element('div', 'table-scroll'); wrapper.append(table); fragment.append(wrapper);
      continue;
    }
    const heading = line.match(/^\s*#{1,6}\s+(.+)$/);
    if (heading) { const h = element('h3'); inlineMarkdown(h, heading[1]); fragment.append(h); i++; continue; }
    const listMatch = line.match(/^\s*(?:([-*])|(\d+)[.)])\s+(.+)$/);
    if (listMatch) {
      const ordered = Boolean(listMatch[2]);
      const list = element(ordered ? 'ol' : 'ul');
      if (ordered) list.start = Number(listMatch[2]);
      while (i < lines.length) {
        const item = lines[i].match(/^\s*(?:([-*])|(\d+)[.)])\s+(.+)$/);
        if (!item || Boolean(item[2]) !== ordered) break;
        const li = element('li'); inlineMarkdown(li, item[3]); list.append(li); i++;
      }
      fragment.append(list); continue;
    }
    if (/^\s*>/.test(line)) {
      const quote = element('blockquote'); inlineMarkdown(quote, line.replace(/^\s*>\s?/, '')); fragment.append(quote); i++; continue;
    }
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { fragment.append(element('hr')); i++; continue; }
    const paragraph = element('p');
    inlineMarkdown(paragraph, line);
    i++;
    while (i < lines.length && lines[i].trim() && !/^\s*(?:#{1,6}\s|[-*]\s|\d+[.)]\s|```|>)/.test(lines[i])
      && !(lines[i].includes('|') && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1] || ''))) {
      paragraph.append(element('br'));
      inlineMarkdown(paragraph, lines[i++]);
    }
    fragment.append(paragraph);
  }
  return fragment;
}

function toolCallNode(call) {
  const node = element('details', 'tool-call');
  const name = call.name || call.tool_name || call.function?.name || '查询工具';
  const suffix = call.error ? ' · 失败' : call.running ? ' · 执行中' : call.status === 'interrupted' ? ' · 未完成' : '';
  node.append(element('summary', '', `${labels[name] || name}${suffix}`));
  node.append(element('div', 'tool-call-label', '输入条件'));
  node.append(element('pre', '', pretty(call.arguments ?? call.args ?? call.input ?? call.function?.arguments)));
  if (call.result !== undefined || call.output !== undefined) {
    node.append(element('div', 'tool-call-label', '返回数据'));
    node.append(element('pre', '', pretty(call.result ?? call.output)));
  }
  if (call.error) node.append(element('div', 'error-box', safeText(typeof call.error === 'string' ? call.error : call.error.message)));
  return node;
}

function executionTrace(message) {
  if (message.streaming && !state.developer) {
    const line = element('div','execution-trace trace-summary');
    line.append(activityLabel(safeText(latestActivity(message)),message.turn_id || message.id || message));
    return line;
  }
  const rows = activityRows(message);
  const trace = element(message.streaming || state.traceMode==='detailed' ? 'section' : 'details', `execution-trace ${state.traceMode}`);
  trace.setAttribute('aria-label','执行过程');
  const summary = element(trace.tagName==='DETAILS' ? 'summary' : 'div','trace-summary');
  if(message.streaming)summary.append(activityLabel(safeText(latestActivity(message)),message.turn_id || message.id || message));
  else summary.append(icon(message.error ? 'circle-alert' : message.cancelled ? 'square' : 'circle-check','trace-orbit'),element('span','trace-current','执行过程'));
  trace.append(summary);
  if (message.usage?.model_calls) {
    const usage = message.usage;
    const count = value => value == null ? '未报告' : Number(value).toLocaleString();
    trace.append(element('p','usage-summary',`${usage.model_calls} 次模型请求 · 输入 ${count(usage.input_tokens)} · 输出 ${count(usage.output_tokens)} · 缓存读取 ${count(usage.cached_tokens)} tokens`));
  }
  {
    const timeline = element('ol','trace-timeline');
    for (const row of rows) {
      const item = element('li',`trace-row ${row.state || 'note'}`);
      item.append(icon(row.state==='running' ? 'loader-circle' : row.state==='failed' ? 'circle-alert' : 'check','trace-marker'));
      const copy = element('div','trace-copy');
      if (row.kind === 'note') {
        const note = element('details','trace-note');
        note.append(element('summary','',safeText(row.text.split('\n')[0].replace(/\*\*/g,'').slice(0,120))),markdown(safeText(row.text)));
        copy.append(note);
      } else copy.append(element('p','',safeText(row.text)));
      if (row.kind === 'tool') copy.append(element('small','',({running:'使用中',failed:'未完成，正在调整',interrupted:'已中断',done:'已完成'})[row.state]));
      item.append(copy); timeline.append(item);
    }
    trace.append(timeline);
    if (message.tool_calls?.length) {
      const technical = element('details','trace-technical'); technical.append(element('summary','','查看工具记录'));
      for (const call of message.tool_calls) technical.append(toolCallNode(call));
      trace.append(technical);
    }
  }
  return trace;
}

function recipeId(recipe) { return recipe.recipe_id || recipe.id; }
function matchedNames(recipe) {
  return [...new Set((recipe.matched_ingredients || []).map((item) => typeof item === 'string' ? item : item.canonical_name || item.name || item.ingredient_id).filter(Boolean))];
}
function timeLabel(value) { return value == null ? '耗时未知' : `${Number(value)} 分钟`; }

function recipeButton(recipe, snapshot) {
  const button = element('button', 'recipe-result');
  button.type = 'button';
  button.setAttribute('aria-label', `查看${recipe.name || '菜谱'}详情`);
  const copy = element('span', 'recipe-result-copy');
  const title = element('span', 'recipe-result-name', recipe.name || '未命名菜谱');
  if (recipe.nutrition_status === 'estimated' || recipe.nutrition?.estimated || recipe.nutrition_estimated) title.append(element('span', 'tag', '营养估算'));
  copy.append(title);
  const names = matchedNames(recipe);
  const meta = [timeLabel(recipe.total_time_min), labels[recipe.difficulty] || recipe.difficulty, names.length ? `匹配 ${names.join('、')}` : ''].filter(Boolean);
  copy.append(element('span', 'recipe-result-meta', meta.join(' · ')));
  button.append(copy, icon('chevron-right','recipe-result-arrow'));
  button.disabled = !recipeId(recipe);
  const expectedSnapshot = snapshot ? { ...snapshot } : null;
  button.addEventListener('click', () => openRecipe(recipeId(recipe), expectedSnapshot, true));
  return button;
}

function renderConversation(forceScroll = false) {
  const session = currentSession();
  const messages = session?.messages || [];
  const nearBottom = ui.conversation.scrollHeight - ui.conversation.scrollTop - ui.conversation.clientHeight < 100;
  const openedLogs = new Set([...ui.messages.querySelectorAll('details[open][data-log-key]')].map((node) => node.dataset.logKey));
  ui['chat-title'].textContent = !blankTitle(session?.title) ? session.title : '菜谱助手';
  ui.welcome.hidden = messages.length > 0;
  ui.messages.hidden = !messages.length;
  ui.messages.replaceChildren();
  const lastViewMessage = new Map();
  const shownViews = visibleMessageViews;
  messages.forEach((message,index) => shownViews(message).forEach(view => lastViewMessage.set(view.id,index)));
  for (let index = 0; index < messages.length; index++) {
    const message = messages[index];
    if (message.role === 'user') {
      const node = element('article', 'message user');
      node.setAttribute('aria-label', '你的消息');
      if(message.images?.length) {
        const images=element('div','message-images');
        for(const [imageIndex,image] of message.images.entries()) images.append(imageThumbnail(state.currentId,image,message.images,imageIndex));
        node.append(images);
      }
      if(message.content)node.append(element('div', 'user-bubble', message.content));
      ui.messages.append(node);
      continue;
    }
    const node = element('article', 'message assistant');
    node.setAttribute('aria-label', 'ChefMate 的回复');
    const heading = element('div', 'assistant-heading');
    heading.append(brandImage('assistant-icon'), element('span', '', 'ChefMate'));
    node.append(heading);
    const body = element('div', 'assistant-body');
    const processOnly = message.streaming && !message.resultComplete || Boolean(message.error || message.cancelled);
    if (message.content && !processOnly) body.append(markdown(message.content));
    node.append(body);
    const typedViews = shownViews(message);
    if (typedViews.length) {
      const components = element('div', 'message-components');
      const relevant = typedViews.filter(view=>view.kind!=='task_progress');
      const inlineIds = new Set(relevant.filter(view=>view.placement==='inline').slice(-(Number(state.uiPreferences.inline_limit)===1?1:2)).map(view=>view.id));
      for (const view of relevant) {
        if (lastViewMessage.get(view.id) !== index) continue;
        const live = inlineIds.has(view.id) && workspace.renderInline(view,state.currentId,()=>scheduleRender(state.currentId));
        if (live) { live.classList.add('inline-component'); components.append(live); continue; }
        const receipt = element('button','view-receipt'); receipt.type='button';
        const glyph=icon(({recipe_choices:'layout-grid',preference_form:'list-filter',cooking_guide:'cooking-pot',timers:'timer'})[view.kind] || 'list');glyph.classList.add('view-receipt-icon');
        receipt.append(glyph,element('span','view-receipt-copy',view.title || VIEW_LABELS[view.kind]),element('small','',viewSummary(view)),icon('arrow-up-right'));
        receipt.addEventListener('click',()=>workspace.openCurrent(view.kind)); components.append(receipt);
      }
      node.append(components);
    }
    const recipeItems = message.streaming ? [] : legacyRecipeItems(message, typedViews);
    if (recipeItems.length) {
      const recipes = element('div', 'recipe-results');
      for (const recipe of recipeItems) recipes.append(recipeButton(recipe, message.snapshot));
      node.append(recipes);
    } else if (message.resultTotal === 0) node.append(element('div', 'empty-results', '没有找到同时满足这些条件的菜谱，可以继续调整条件。'));
    if ((message.streaming || state.developer) && (message.tool_calls?.length || message.process?.length || message.streaming || message.usage?.model_calls)) {
      const log = executionTrace(message);
      log.dataset.logKey = `${state.currentId}:${index}`;
      for (const detail of log.querySelectorAll('details')) {
        detail.dataset.logKey = `${log.dataset.logKey}:${detail.querySelector('summary')?.textContent}`;
        detail.open = openedLogs.has(detail.dataset.logKey);
      }
      node.append(log);
    }
    if (message.error) {
      const error = element('div', 'error-box', safeText(message.error));
      if (hasRetry(message) && !busy()) {
        const retry = element('button', 'retry-button', '重试这次请求');
        retry.type = 'button';
        retry.addEventListener('click', () => sendMessage(message.retryMessage, !message.retryAsNew, message));
        error.append(element('br'), retry);
      }
      node.append(error);
    }
    if (message.cancelled) {
      const note = element('div', 'message-meta', '本次执行已停止。');
      if (hasRetry(message) && !busy()) {
        const retry = element('button', 'retry-button', '重新执行');
        retry.type = 'button'; retry.addEventListener('click', () => sendMessage(message.retryMessage, !message.retryAsNew, message));
        note.append(document.createTextNode(' '), retry);
      }
      node.append(note);
    }
    ui.messages.append(node);
  }
  if (nearBottom || forceScroll) ui.conversation.scrollTop = ui.conversation.scrollHeight;
  updateConversationState();
  cookingFocus.refresh();
}

function scheduleRender(id) {
  if (id !== state.currentId || state.renderFrame) return;
  state.renderFrame = requestAnimationFrame(() => {
    state.renderFrame = null;
    if (id !== state.currentId) return;
    renderConversation(); renderQueryPanel(); updateComposer();
  });
}

async function readEvents(response, onEvent) {
  if (!response.body) throw new Error('浏览器没有收到可读取的响应，请重试。');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let data = [];
  let namedType = '';
  const line = (value) => {
    if (!value) {
      if (data.length) {
        let payload;
        try { payload = JSON.parse(data.join('\n')); } catch { throw new Error('服务返回了无法读取的事件，请重试。'); }
        if (!payload.type && namedType) payload.type = namedType;
        onEvent(payload);
      }
      data = []; namedType = ''; return;
    }
    if (value.startsWith(':')) return;
    const colon = value.indexOf(':');
    const field = colon < 0 ? value : value.slice(0, colon);
    let content = colon < 0 ? '' : value.slice(colon + 1);
    if (content.startsWith(' ')) content = content.slice(1);
    if (field === 'data') data.push(content);
    else if (field === 'event') namedType = content;
  };
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let newline;
      while ((newline = buffer.indexOf('\n')) >= 0) {
        line(buffer.slice(0, newline).replace(/\r$/, ''));
        buffer = buffer.slice(newline + 1);
      }
      if (done) {
        if (buffer) line(buffer.replace(/\r$/, ''));
        line('');
        break;
      }
    }
  } finally { reader.releaseLock(); }
}

function applyEvent(run, event) {
  const message = run.message;
  if (event.type === 'model_usage') message.usage = event.usage;
  if (event.type === 'status') run.status = safeText(event.message || event.text || '正在执行…');
  if (event.type === 'assistant_delta') message.content += String(event.text ?? event.delta ?? '');
  if (event.type === 'tool_start') {
    if (message.content) { message.process ||= []; message.process.push(message.content); message.content = ''; }
    message.tool_calls.push({ ...event, running: true });
    run.status = `${TOOL_NAMES[event.name] || '使用厨房工具'}…`;
  }
  if (event.type === 'tool_end') {
    const call = message.tool_calls.find((item) => item.call_id === event.call_id);
    if (call) Object.assign(call, event, { running: false });
    else message.tool_calls.push({ ...event, running: false });
    if (event.name === 'load_skill' && run.sessionId === state.currentId) renderSkillsPanel();
  }
  if (event.type === 'ui_action') {
    message.actions ||= [];
    if (event.action) message.actions.push(event.action);
    workspace.load(run.sessionId).catch(() => {});
  }
  if (event.type === 'skill_loaded') {
    message.skills ||= [];
    message.skills.push(event.skill || event);
    if (run.sessionId === state.currentId) renderSkillsPanel();
  }
  if (event.type === 'result') {
    const result = event.result || event;
    message.content = result.text ?? message.content;
    message.recipes = result.status === 'completed' ? result.recipes || [] : [];
    message.selected_recipe = result.status === 'completed' ? result.selected_recipe : null;
    message.snapshot = result.snapshot;
    message.usage = result.usage;
    message.actions = result.actions || message.actions || [];
    message.skills = result.skills || message.skills || [];
    message.process = result.process || message.process || [];
    message.suggested_replies=result.status==='completed' ? result.suggested_replies || [] : [];
    run.terminalStatus = result.status;
    if (result.tool_calls?.length) message.tool_calls = result.tool_calls.map(normalizeToolCall);
    message.resultTotal = result.total ?? result.query_result?.total;
    run.resultReceived = result.status === 'completed';
    message.resultComplete = run.resultReceived;
    if (result.status === 'cancelled') { message.cancelled = true; message.retryMessage = run.input; }
    else if (result.status !== 'completed') {
      run.failed = true;
      run.retryable = result.error?.retryable !== false;
      message.error = safeText(result.error?.message || '这次执行没有完成，请重试。');
      message.retryMessage = run.retryable ? run.input : null;
    }
  }
  if (event.type === 'error') {
    message.error = safeText(event.message || event.error?.message || '这次执行没有完成，请重试。');
    run.retryable = event.retryable !== false;
    message.retryMessage = run.retryable ? run.input : null;
    run.failed = true;
  }
  if (event.type === 'done') {
    run.done = true;
    run.terminalStatus = event.status || run.terminalStatus;
    message.cancelled = run.terminalStatus === 'cancelled' || Boolean(event.cancelled);
    if (message.cancelled) message.retryMessage = run.input;
    else if (run.terminalStatus !== 'completed') {
      run.failed = true;
      message.error ||= '这次执行没有完成，请重试。';
      if (run.retryable !== false) message.retryMessage ??= run.input;
    }
  }
  scheduleRender(run.sessionId);
}

async function sendMessage(text, retry = false, replacedMessage = null, options = {}) {
  const input = text.trim();
  const targetId=options.sessionId ?? state.currentId,fromComposer=options.fromComposer !== false;
  if(pendingQuestion(targetId)){toast('先回答助手的问题，确认后就会接着处理。');return {error:'需要先回答当前问题。'};}
  if(fromComposer && savedAnswer(targetId)){toast('答案已经保存，请先重试继续。');return {error:'答案等待继续处理。'};}
  if(fromComposer && state.confirmationQueue.get(targetId)) {toast('确认已保存，请先重试继续，或选择“暂不继续”后再发送。');return;}
  const sourceKey=draftKey(targetId),selectionEpoch=state.selectionEpoch;
  let draftImages=replacedMessage?.retryImages || (fromComposer ? state.imageDrafts.get(sourceKey) : []) || [];
  if (state.initializing || (!input && !draftImages.length) || busy(targetId) || state.creating || state.preparing || (fromComposer && state.imageUploads.get(sourceKey))) return;
  if(draftImages.length && !state.status?.supports_images){toast('当前模型没有开启图片能力。');return;}
  if(draftImages.length>maxImages()){toast(`当前模型每次最多接收 ${maxImages()} 张图片，请减少图片后发送。`);return;}
  if (!state.status?.ready) { toast('请先完成模型与数据库连接。'); return; }
  if (input.length > 8000) { toast('消息最多 8000 个字符，请缩短后发送。'); return; }
  let id = targetId;
  let uiSnapshot;
  // Hold one lock through creation, attachment upload and UI snapshot preparation.
  // Draft maps keep ownership even when the user switches conversations meanwhile.
  state.preparing=true;
  if(fromComposer && sourceKey===draftKey())persistDraft();
  updateComposer();
  try {
    if(!id) {
      state.creating=true;updateComposer();
      const session = normalizeSession(await api('/api/sessions', { method: 'POST', body: '{}' }));
      id = session.id;
      state.cache.set(id, session);
      moveDrafts(sourceKey,id,state.drafts,state.imageDrafts);
      if (selectionEpoch === state.selectionEpoch && state.currentId===null && sourceKey===state.blankId) {
        state.currentId = id;
        state.selectionEpoch++;
        try{localStorage.setItem('chefmate:active-session',id);}catch{/* Selection is available in memory. */}
        workspace.select(id);
        loadSkills(id);
      }
      if(state.blankId===sourceKey)state.blankId=`draft:${crypto.randomUUID()}`;
      state.creating=false;updateComposer();
    }
    for(let index=0;index<draftImages.length;index++) {
      const pending=draftImages[index];
      if(!pending.local)continue;
      const uploaded=await api(`/api/sessions/${encodeURIComponent(id)}/images?name=${encodeURIComponent(pending.name)}`,{
        method:'POST',headers:{'Content-Type':'image/jpeg'},body:pending.blob});
      const replacement={...pending,...uploaded,local:false};
      draftImages=draftImages.map((image,i)=>i===index?replacement:image);
      if(fromComposer && !replacedMessage)state.imageDrafts.set(id,draftImages);
    }
    uiSnapshot=await workspace.prepareTurn(id);
  }catch(error){
    if(!targetId)state.failedDrafts.set(sourceKey,true);
    if(!targetId && id) {
      moveDrafts(id,sourceKey,state.drafts,state.imageDrafts);
      state.imageDrafts.set(sourceKey,recoverLocalImages(state.imageDrafts.get(sourceKey) || []));
      state.failedDrafts.set(sourceKey,true);state.cache.delete(id);workspace.remove(id);
      // This newly-created session has no submitted turn. Retain local files even if cleanup fails.
      void api(`/api/sessions/${encodeURIComponent(id)}`,{method:'DELETE'}).catch(()=>{});
      if(state.currentId===id){
        state.currentId=null;state.blankId=sourceKey;workspace.select(null);
        try{localStorage.removeItem('chefmate:active-session');}catch{/* Selection remains in memory. */}
        renderConversation();renderSessions();renderQueryPanel();
      }
    }
    toast(error.message);return {error:error.message};
  }
  finally{state.creating=false;state.preparing=false;updateComposer();}
  draftImages=draftImages.map(imageMetadata);
  const session = state.cache.get(id) || { id, title: '新会话', messages: [] };
  options.onPrepared?.();
  state.cache.set(id, session);
  if (replacedMessage && session.messages.at(-1) === replacedMessage) {
    session.messages.pop();
    if (replacedMessage.retryAsNew && session.messages.at(-1)?.role === 'user' && session.messages.at(-1)?.content === input) session.messages.pop();
  }
  if (retry) {
    const last = session.messages.at(-1);
    if (last?.role === 'assistant' && (last.error || last.cancelled) && last.retryMessage === input) session.messages.pop();
  }
  if (!(retry && session.messages.at(-1)?.role === 'user' && session.messages.at(-1)?.content === input)) session.messages.push({ role: 'user', content: input,images:draftImages });
  const assistant = { role: 'assistant', content: '', recipes: [], tool_calls: [], actions: [], skills: [], process: [], streaming: true };
  assistant.retryImages=draftImages;
  session.messages.push(assistant);
  session.running = true;
  workspace.setRunning(id,true);
  if (blankTitle(session.title)) session.title = input.slice(0, 26) || '图片对话';
  const controller = new AbortController();
  const run = { sessionId: id, controller, message: assistant, input, requestId: crypto.randomUUID(),
    status: '正在理解需求…', done: false, resultReceived: false, httpRejected: false };
  if(options.requestId)run.requestId=options.requestId;
  state.runs.set(id, run);
  if (id === state.currentId) {
    resizeInput();
    renderConversation(true); renderQueryPanel(); updateComposer();
  }
  renderSessions();
  announce('开始查询。');
  try {
    const response = await fetch(`/api/sessions/${encodeURIComponent(id)}/turn`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ message: input, request_id: run.requestId, retry, ui_snapshot: uiSnapshot,images:draftImages.map(image=>image.id) }), signal: controller.signal,
    });
    if (!response.ok) {
      run.httpRejected = true;
      const payload = await response.json().catch(() => null);
      throw new Error(safeText(typeof payload?.detail === 'string' ? payload.detail : payload?.message || `请求失败（${response.status}）。`));
    }
    options.onAccepted?.();
    const retainedImages=state.imageDrafts.get(id) || [];
    if(fromComposer && (!replacedMessage || matchesDraft(state.drafts.get(id),retainedImages,input,draftImages))) {
      if((state.drafts.get(id) || '').trim()===input)state.drafts.delete(id);
      state.imageDrafts.delete(id);releaseImages(retainedImages);
      if(id===state.currentId && ui['composer-input'].value.trim()===input)ui['composer-input'].value='';
    }
    state.failedDrafts.delete(sourceKey);
    resizeInput();updateComposer();
    void loadSessions().catch(()=>{});
    await readEvents(response, (event) => applyEvent(run, event));
    if (!run.resultReceived && !run.failed && !assistant.cancelled && (!run.done || run.terminalStatus === 'completed')) throw new Error('连接在结果返回前中断，正在恢复执行状态。');
    if (assistant.cancelled) assistant.retryMessage = input;
  } catch (error) {
    if(run.httpRejected && !targetId && fromComposer)state.failedDrafts.set(id,{sessionId:id});
    if (error.name === 'AbortError' && run.cancelling) { assistant.cancelled = true; assistant.retryMessage = input; }
    else { assistant.error = safeText(error.message || '连接中断，请重试。'); assistant.retryMessage = input; }
  } finally {
    assistant.streaming = false;
    assistant.tool_calls.forEach((call) => { call.running = false; if (call.status === 'running') call.status = 'interrupted'; });
    session.running = false;
    let recovered;
    try {
      const saved = normalizeSession(await api(`/api/sessions/${encodeURIComponent(id)}`));
      if (saved.running || saved.last_run?.request_id === run.requestId) {
        state.cache.set(id, saved);
        recovered = saved;
      } else if (assistant.error) {
        // A rejected HTTP request never added this user message to the server history.
        assistant.retryAsNew = true;
      }
    } catch {
      if (!run.httpRejected && !run.done && !run.resultReceived) {
        recoverRunningTurn(session,assistant);
        recovered = session;
      } else if (run.httpRejected) assistant.retryAsNew = true;
    }
    state.runs.delete(id);
    workspace.setRunning(id,Boolean(recovered?.running));
    updateComposer();
    if (id === state.currentId) {
      renderConversation(); renderQueryPanel(); updateComposer();
      if (recovered?.running) pollRemoteSession(id, state.selectionEpoch);
    }
    await loadSessions().catch(() => {});
    workspace.load(id).catch(() => {});
    if (id === state.currentId) loadSkills(id);
    if (recovered?.running) announce('连接中断，正在恢复本次执行。');
    else if (recovered?.last_status === 'completed' || run.resultReceived) announce('查询完成。');
    else announce(assistant.cancelled || recovered?.last_status === 'cancelled' ? '本次执行已停止。' : '执行未完成，可以重试。');
  }
}

async function cancelTurn() {
  const id = state.currentId;
  if (!id || !busy(id)) return;
  const run = state.runs.get(id);
  if (run?.cancelling) return;
  if (run) run.cancelling = true;
  updateComposer();
  try {
    await api(`/api/sessions/${encodeURIComponent(id)}/cancel`, { method: 'POST', body: '{}' });
    if (!run) { toast('已请求停止执行。'); pollRemoteSession(id, state.selectionEpoch); }
  } catch (error) { if (run) run.cancelling = false; toast(error.message); updateComposer(); }
}

function backdrop() {
  const mobile = matchMedia('(max-width: 760px)').matches;
  const overlayDetail = matchMedia('(max-width: 1180px)').matches;
  ui.sidebar.inert = mobile && !state.sidebarOpen;
  if (mobile && !state.sidebarOpen) ui.sidebar.setAttribute('aria-hidden', 'true');
  else ui.sidebar.removeAttribute('aria-hidden');
  ui['drawer-backdrop'].hidden = !(mobile && state.sidebarOpen || overlayDetail && state.detailOpen);
}
function setSidebar(open) {
  state.sidebarOpen = open;
  ui.app.classList.toggle('sidebar-open', open);
  ui['open-sidebar'].setAttribute('aria-expanded', String(open));
  backdrop();
  if (open) ui['new-chat'].focus();
}
function setDetails(open) {
  state.detailOpen = open;
  ui.app.classList.toggle('panel-open', open);
  ui['detail-panel'].hidden = !open;
  ui['toggle-details'].setAttribute('aria-expanded', String(open));
  ui['toggle-details'].setAttribute('aria-label', open ? '收起查询与菜谱详情' : '展开查询与菜谱详情');
  backdrop();
}
function setTab(tab, focus = false) {
  if (!state.developer && ['query','skills'].includes(tab)) tab='workspace';
  state.tab = tab;
  for (const name of ['workspace', 'query', 'recipe', 'skills']) {
    ui[`${name}-tab`].classList.toggle('active', name === tab);
    ui[`${name}-tab`].setAttribute('aria-selected', String(name === tab));
    ui[`${name}-tab`].tabIndex = name === tab ? 0 : -1;
    ui[`${name}-content`].hidden = name !== tab;
  }
  if (focus) ui[`${tab}-tab`].focus();
  if (tab === 'skills') renderSkillsPanel();
  if (tab === 'query') renderQueryPanel();
}

function panelSection(title) {
  const section = element('section', 'panel-section');
  section.append(element('h3', '', title));
  return section;
}

function renderQueryPanel() {
  if(state.developer){developerPanel.render();return;}
  const opened = new Set([...ui['query-content'].querySelectorAll('details[open][data-call-index]')].map((node) => node.dataset.callIndex));
  ui['query-content'].replaceChildren();
  const status = state.status;
  const connection = panelSection('当前连接');
  const db = status?.database;
  const rows = [
    ['模型', status?.configured ? status.model || '已配置' : '未配置'],
    ['数据库', db?.ready ? '已连接' : '未连接'],
    ['菜谱数量', db?.recipe_count ?? db?.recipes_count ?? '未知'],
    ['数据版本', db?.data_version || '未知'],
  ];
  for (const [key, value] of rows) {
    const row = element('div', 'connection-line'); row.append(element('span', '', key), element('span', '', safeText(value))); connection.append(row);
  }
  if (!status?.ready) {
    const retry = element('button', 'panel-retry', '重新检查连接');
    retry.type = 'button'; retry.addEventListener('click', loadStatus); connection.append(retry);
  }
  ui['query-content'].append(connection);
  const messages = currentSession()?.messages || [];
  const latest = [...messages].reverse().find((message) => message.role === 'assistant');
  const lastSearch = [...messages].reverse().flatMap((message) => [...(message.tool_calls || [])].reverse())
    .find((call) => (call.name || call.tool_name) === 'search_recipes' && call.result);
  if (lastSearch && !(latest?.tool_calls || []).includes(lastSearch)) {
    const evidence = panelSection('最近一次菜谱查询'); evidence.append(toolCallNode(lastSearch)); ui['query-content'].append(evidence);
  }
  if (!latest?.tool_calls?.length) {
    const empty = element('div', 'panel-empty');
    empty.append(icon('search','panel-empty-symbol'), element('div', '', '查询开始后，这里会保留食材候选、筛选条件和返回依据。'));
    ui['query-content'].append(empty); return;
  }
  const section = panelSection('本轮查询');
  if (latest.snapshot?.data_version) section.append(element('p', 'nutrition-note', `本轮数据版本 ${latest.snapshot.data_version}`));
  latest.tool_calls.forEach((call, index) => {
    const node = toolCallNode(call); node.dataset.callIndex = String(index); node.open = opened.has(String(index)); section.append(node);
  });
  ui['query-content'].append(section);
}

function renderRecipeEmpty() {
  ui['recipe-content'].replaceChildren();
  const empty = element('div', 'panel-empty');
  empty.append(icon('book-open','panel-empty-symbol'), element('div', '', '点击回复中的菜谱，查看食材用量、完整步骤和营养信息。'));
  ui['recipe-content'].append(empty);
}

async function loadSkills(id = state.currentId) {
  const epoch = ++state.skillsEpoch;
  try {
    const payload = await api(`/api/skills${id ? `?session_id=${encodeURIComponent(id)}` : ''}`);
    if (epoch !== state.skillsEpoch || id !== state.currentId) return;
    state.skills = Array.isArray(payload) ? payload : payload?.skills || payload?.items || [];
    renderSkillsPanel();
  } catch (error) {
    if (epoch === state.skillsEpoch && id === state.currentId) {
      ui['skills-content'].replaceChildren(element('div', 'error-box', error.message));
    }
  }
}

function renderSkillsPanel() {
  ui['skills-content'].replaceChildren();
  const heading = element('div', 'workspace-heading'); heading.append(element('span', 'workspace-stage', '厨房技能'));
  const refresh = element('button', 'ui-text-button', '刷新'); refresh.type = 'button'; refresh.addEventListener('click', () => loadSkills()); heading.append(refresh);
  ui['skills-content'].append(heading, element('p', 'ui-hint', '技能指导助手的处理流程。启用和本轮实际加载会分别标明。'));
  const latest = [...(currentSession()?.messages || [])].reverse().find((message) => message.role === 'assistant');
  const loaded = new Set();
  for (const skill of latest?.skills || []) loaded.add(skill.id || skill.name || skill.skill_id);
  for (const action of latest?.actions || []) if (action.kind === 'skill_loaded') loaded.add(action.skill_id || action.skill?.id || action.skill?.name);
  for (const call of latest?.tool_calls || []) if (call.name === 'load_skill' && call.result?.status === 'ok') loaded.add(call.arguments?.name || call.arguments?.skill_id || call.result?.skill?.id || call.result?.skill?.name);
  if (!state.skills.length) ui['skills-content'].append(element('div', 'panel-empty', '目前没有可用技能。'));
  for (const skill of state.skills) {
    const card = element('details', 'skill-card'); const summary = element('summary');
    const title = skill.name || skill.id || '厨房技能';
    const isLoaded = loaded.has(skill.id) || loaded.has(skill.name) || Boolean(skill.loaded);
    summary.append(element('strong', '', title), element('span', 'ui-badge muted', isLoaded ? '本轮已加载' : skill.enabled === false ? '已禁用' : '可用'));
    card.append(summary, element('p', '', skill.description || '暂无说明'));
    const details = [skill.scope ? `来源：${skill.scope}` : '', skill.version ? `版本：${skill.version}` : '', skill.digest ? `内容版本：${skill.digest.slice(0, 12)}` : ''].filter(Boolean);
    if (details.length) card.append(element('p', 'ui-hint', details.join(' · ')));
    const loadedCall = latest?.tool_calls?.find((call) => call.name === 'load_skill' && call.arguments?.name === title && call.result?.status === 'ok');
    const content = skill.body || skill.content || loadedCall?.result?.content;
    if (content) card.append(element('pre', '', content));
    ui['skills-content'].append(card);
  }
}

function detailRequest(id, snapshot, fromReference) {
  if (fromReference && !snapshot) throw new Error('这条菜谱引用缺少数据版本，请重新查询。');
  if (snapshot) return {
    path: '/api/tools/get_recipe',
    options: { method: 'POST', body: JSON.stringify({ recipe_id: id, expected_snapshot: snapshot }) },
  };
  return { path: `/api/recipes/${encodeURIComponent(id)}`, options: {} };
}

async function openRecipe(id, expectedSnapshot = null, fromReference = false) {
  const epoch = ++state.detailEpoch;
  const sessionId = state.currentId;
  state.detailAbort?.abort();
  const controller = new AbortController();
  state.detailAbort = controller;
  state.selectedRecipe = id;
  setDetails(true); setTab('recipe');
  ui['recipe-content'].replaceChildren(element('div', 'panel-loading', '正在读取菜谱…'));
  try {
    const request = detailRequest(id, expectedSnapshot, fromReference);
    const payload = await api(request.path, { ...request.options, signal: controller.signal });
    if (epoch !== state.detailEpoch || sessionId !== state.currentId || id !== state.selectedRecipe) return;
    if (payload?.status === 'snapshot_changed') {
      ui['recipe-content'].replaceChildren(element('div', 'error-box', '数据已更新，请重新查询。'));
      announce('菜谱数据已更新，请重新查询。');
      return;
    }
    if (payload?.status && payload.status !== 'ok') throw new Error('这道菜谱暂时不可用，请重新查询。');
    const recipe = payload?.recipe || payload;
    if (!recipe || !recipe.name) throw new Error('这道菜谱暂时不可用。');
    renderRecipe(recipe, payload.snapshot);
    announce(`已打开${recipe.name}的详情。`);
  } catch (error) {
    if (error.name === 'AbortError' || epoch !== state.detailEpoch || sessionId !== state.currentId) return;
    const errorNode = element('div', 'error-box', safeText(error.message));
    const retry = element('button', 'retry-button', '重试读取');
    retry.type = 'button'; retry.addEventListener('click', () => openRecipe(id, expectedSnapshot, fromReference));
    if (!fromReference || expectedSnapshot) errorNode.append(element('br'), retry);
    ui['recipe-content'].replaceChildren(errorNode);
  }
}

function amountLabel(item) {
  if (item.raw_amount || item.raw_unit) return [item.raw_amount, item.raw_unit].filter(Boolean).join(' ');
  if (item.grams != null) return `${Number(item.grams)} g`;
  return '用量未知';
}
function renderRecipe(recipe, snapshot, viewState = {}) {
  const shown = {name:recipe.name,description:recipe.description,servings:recipe.servings,
    original_servings:recipe.servings,estimated_servings:recipe.estimated_servings,
    total_time_min:recipe.total_time_min,difficulty:recipe.difficulty,
    ingredients:(recipe.ingredients || []).map(item=>({canonical_name:item.canonical_name || item.raw_name || item.ingredient_id,
      amount_text:amountLabel(item),estimated:item.amount_evidence==='estimated' || item.estimated_grams})),
    steps:(recipe.steps || []).map(step=>({instruction:typeof step==='string' ? step : step.description || step.text})),
    nutrition:recipe.nutrition || {per_serving:recipe.nutrition_per_serving},
    references:recipe.references || [],
    warnings:[...(recipe.warnings || []),...(recipe.blocking_reasons || []),
      ...(recipe.required_tools?.length ? [`所需厨具：${recipe.required_tools.map(v=>tools[v] || v).join('、')}`] : []),
      ...(recipe.cooking_methods?.length ? [`烹饪方式：${recipe.cooking_methods.map(v=>methods[v] || v).join('、')}`] : [])],
    source:{data_snapshot:snapshot}};
  ui['recipe-content'].replaceChildren(renderView({id:'source-detail',kind:'recipe_detail',title:'菜谱详情',revision:0,editable:false,data:{recipe:shown}},
    {historical:false,busy:false,viewState,onViewState:()=>renderRecipe(recipe,snapshot,viewState),action:async()=>{}}));
}

ui.composer.addEventListener('submit', (event) => { event.preventDefault(); sendMessage(ui['composer-input'].value); });
ui['composer-input'].addEventListener('input', () => { resizeInput(); persistDraft(); updateComposer(); });
ui['composer-input'].addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault(); sendMessage(ui['composer-input'].value);
  }
});
ui['new-chat'].addEventListener('click', newSession);
ui['attach-image'].addEventListener('click',()=>ui['image-input'].click());
ui['image-input'].addEventListener('change',()=>{
  const files=[...ui['image-input'].files];ui['image-input'].value='';uploadImages(files);
});
ui['composer-input'].addEventListener('paste',event=>{
  const files=[...(event.clipboardData?.files || [])];
  if(files.length && state.status?.supports_images){event.preventDefault();uploadImages(files);}
});
ui['stop-button'].addEventListener('click', cancelTurn);
ui['open-sidebar'].addEventListener('click', () => setSidebar(true));
$('close-sidebar').addEventListener('click', () => { setSidebar(false); ui['open-sidebar'].focus(); });
ui['toggle-details'].addEventListener('click', () => setDetails(!state.detailOpen));
$('close-details').addEventListener('click', () => { setDetails(false); ui['toggle-details'].focus(); });
ui['status-button'].addEventListener('click', () => { setDetails(true); setTab('query'); });
ui['workspace-context'].addEventListener('click', () => { setDetails(true); setTab('workspace'); });
const panelTabs = ['workspace', 'query', 'recipe', 'skills'];
for (const name of panelTabs) {
  ui[`${name}-tab`].addEventListener('click', () => setTab(name));
  ui[`${name}-tab`].addEventListener('keydown', (event) => {
    const visibleTabs=panelTabs.filter(tab=>!ui[`${tab}-tab`].hidden);
    const index = visibleTabs.indexOf(name);
    const next = event.key === 'ArrowLeft' ? (index + visibleTabs.length - 1) % visibleTabs.length
      : event.key === 'ArrowRight' ? (index + 1) % visibleTabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? visibleTabs.length - 1 : null;
    if (next != null) { event.preventDefault(); setTab(visibleTabs[next], true); }
  });
}
ui['drawer-backdrop'].addEventListener('click', () => { setSidebar(false); setDetails(false); ui['composer-input'].focus(); });
document.addEventListener('keydown', (event) => {
  if(document.querySelector('dialog[open]'))return;
  if (event.key === 'Escape') { setSidebar(false); setDetails(false); ui['composer-input'].focus(); }
  // Overlay drawers keep keyboard focus in the open surface.
  if (event.key === 'Tab') {
    const overlay = state.detailOpen && matchMedia('(max-width: 1180px)').matches ? ui['detail-panel']
      : state.sidebarOpen && matchMedia('(max-width: 760px)').matches ? ui.sidebar : null;
    if (!overlay) return;
    const focusable = [...overlay.querySelectorAll('button:not([disabled]), a[href], summary, [tabindex="0"]')].filter((node) => !node.hidden && node.getClientRects().length);
    const first = focusable[0], last = focusable.at(-1);
    if (!first) return;
    if (event.shiftKey && (document.activeElement === first || !overlay.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && (document.activeElement === last || !overlay.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
  }
});
window.addEventListener('resize', backdrop);
ui['shuffle-templates'].addEventListener('click',()=>renderWelcomeTemplates(true));
ui['resume-draft'].addEventListener('click',resumeDraft);

async function initialize() {
  const epoch = state.selectionEpoch;
  workspace.renderInto(ui['workspace-content']);
  setTab('workspace'); renderRecipeEmpty(); renderConversation(); backdrop();
  const results = await Promise.allSettled([loadStatus(), loadSessions(), loadSkills()]);
  if (results[1].status === 'rejected') toast('会话列表暂时无法读取，请刷新页面重试。');
  const initial = state.sessions.find(session=>session.id===storedSession()) || state.sessions[0];
  if (initial && epoch === state.selectionEpoch && !state.creating && !state.currentId) await openSession(initial.id);
  state.initializing=false;
  updateComposer();
}
initShell({onSettingsClose:()=>{
  refreshDisplayPreferences();loadStatus();
  if(state.currentId)workspace.load(state.currentId).catch(()=>{});
}});
initialize();
