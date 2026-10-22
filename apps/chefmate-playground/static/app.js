import { WorkspaceController, ConfirmationQueue } from './workspace.js';
import { applyBranding } from './branding.js';
import { CookingFocus } from './focus.js';
import { initShell } from './shell.js';
import { DeveloperPanel } from './developer-panel.js';
import { TimerPopover } from './timer-popover.js';
import { KitchenCenters } from './centers.js';
import { ContextMeter } from './context-meter.js';
import { ChatFood } from './chat-food.js';
import { api } from './chat-http.js';
import { markdown } from './chat-markdown.js';
import { createChatRenderer } from './chat-renderer.js';
import { createComposerController } from './chat-composer.js';
import { createSessionController } from './chat-sessions.js';
import { createTurnController } from './chat-turns.js';
import { createDetailController } from './chat-details.js';

// Composition root: controllers own behavior, this module wires their surfaces together.
applyBranding();

const $ = (id) => document.getElementById(id);
const ui = Object.fromEntries([
  'app', 'sidebar', 'sessions', 'new-chat', 'chat-title', 'status-button', 'status-dot',
  'status-label', 'connection-banner', 'conversation', 'welcome', 'messages', 'composer',
  'composer-input', 'send-button', 'stop-button', 'turn-status',
  'detail-panel', 'query-tab', 'recipe-tab', 'query-content', 'recipe-content',
  'drawer-backdrop', 'toast', 'announcer', 'open-sidebar', 'toggle-details',
  'workspace-tab', 'workspace-content', 'workspace-context', 'components-tab', 'components-content',
  'conversation-stage',
  'attach-image','image-input','image-drafts','image-status','composer-choices','reply-suggestions','welcome-templates','shuffle-templates','resume-draft','question-composer',
].map((id) => [id, $(id)]));

const state = {
  status: null, sessions: [], currentId: null, cache: new Map(), drafts: new Map(),
  runs: new Map(), selectionEpoch: 0, detailEpoch: 0,
  detailAbort: null, detailOpen: false, sidebarOpen: false, tab: 'query',
  selectedRecipe: null, remotePoll: null, renderFrame: null, creating: false, preparing: false, initializing:true, components: [], componentsEpoch: 0,
  traceMode: readTraceMode(), developer: readDeveloper(), uiPreferences: readUIPreferences(),
  imageDrafts:new Map(),imageUploads:new Map(),confirmationQueue:new ConfirmationQueue(),
  blankId:`draft:${crypto.randomUUID()}`,starterCapability:null,failedDrafts:new Map(),
};
const contextMeter=new ContextMeter(ui.sidebar);
const chatFood=new ChatFood({api,sessionId:()=>state.currentId,notify:toast,canAct:()=>!state.creating&&!state.preparing&&!busy(state.currentId),onChange:id=>scheduleRender(id),ask:(text,mode)=>{
  if(state.creating || state.preparing || busy(state.currentId)){toast('等这一条回复结束，就可以继续。');return;}
  const existing=ui['composer-input'].value.trim();
  if(mode==='send' && !existing)sendMessage(text);
  else{fillComposer(existing?`${existing}\n${text}`:text);if(existing)toast('已把这项操作补到输入框，原来的文字已保留。');}
}});
function readDeveloper() { try { return JSON.parse(localStorage.getItem('chefmate:developer-settings') || '{}').enabled === true; } catch { return false; } }
function readTraceMode() { try { return JSON.parse(localStorage.getItem('chefmate:developer-settings') || '{}').trace_mode === 'detailed' ? 'detailed' : 'compact'; } catch { return 'compact'; } }
function readUIPreferences() { try { return JSON.parse(localStorage.getItem('chefmate:ui-settings') || '{}'); } catch { return {}; } }
document.body.classList.toggle('compact-density',state.uiPreferences.density==='compact');
function refreshDisplayPreferences() {
  state.developer=readDeveloper();state.traceMode=readTraceMode();state.uiPreferences=readUIPreferences();
  ui['query-tab'].hidden=!state.developer;ui['components-tab'].hidden=!state.developer;
  if (!state.developer && ['query','components'].includes(state.tab)) setTab('workspace');
  document.body.classList.toggle('compact-density',state.uiPreferences.density==='compact');
  applyBranding();
  renderConversation();
}
window.addEventListener('pageshow',refreshDisplayPreferences);
window.addEventListener('storage',event=>{
  if(['chefmate:developer-settings','chefmate:ui-settings'].includes(event.key)) refreshDisplayPreferences();
});
function storedSession() { try { return localStorage.getItem('chefmate:active-session'); } catch { return null; } }
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
workspace.onFocus=(view,id,trigger)=>{
  const source=cookingFocus.captureSource(view,trigger);
  setSidebar(false);setDetails(false);cookingFocus.open(view,id,source);
};
function announce(text) { ui.announcer.textContent = text; }
let toastTimer;
function toast(text) {
  clearTimeout(toastTimer);
  ui.toast.textContent = text;
  ui.toast.hidden = false;
  toastTimer = setTimeout(() => { ui.toast.hidden = true; }, 4200);
}
function currentSession() { return state.cache.get(state.currentId); }


const composer = createComposerController({state, ui, workspace, cookingFocus, currentSession, busy, toast,
  renderSessions, sendMessage, openSession, selectBlank, setSidebar});
const details = createDetailController({state, ui, currentSession, developerPanel, loadStatus, announce, toolCallNode});
const sessions = createSessionController({state, ui, workspace, cookingFocus, currentSession, busy, pendingQuestion,
  toast, announce, clearQuestion:id=>composer.questions.accepted(id), draftKey, persistDraft, resizeInput,
  renderConversation, renderQueryPanel, updateComposer, updateConversationState, resetRecipe, loadComponents,
  setSidebar, renderWelcomeTemplates});
const turns = createTurnController({state, ui, workspace, busy, pendingQuestion, savedAnswer, draftKey, maxImages,
  toast, announce, persistDraft, updateComposer, resizeInput, blankTitle, scheduleRender, renderConversation,
  renderSessions, renderQueryPanel, renderComponentsPanel, loadSessions, loadComponents, pollRemoteSession});
const renderer = createChatRenderer({state, ui, workspace, currentSession, cookingFocus, contextMeter, chatFood,
  busy, blankTitle, hasRetry, sendMessage, openRecipe, openDrafts:()=>{centers.source='drafts';shell.setPage('recipes');},
  updateComposer, updateConversationState, renderQueryPanel});

// Lazy forwarding lets controllers call each other without importing the composition root.
function pendingQuestion(...args) { return composer.pendingQuestion(...args); }
function savedAnswer(...args) { return composer.savedAnswer(...args); }
function draftKey(...args) { return composer.draftKey(...args); }
function fillComposer(...args) { return composer.fillComposer(...args); }
function renderWelcomeTemplates(...args) { return composer.renderWelcomeTemplates(...args); }
function resumeDraft(...args) { return composer.resumeDraft(...args); }
function uploadImages(...args) { return composer.uploadImages(...args); }
function updateConversationState(...args) { return composer.updateConversationState(...args); }
function maxImages(...args) { return composer.maxImages(...args); }
function continueConfirmations(...args) { return composer.continueConfirmations(...args); }
function resizeInput(...args) { return composer.resizeInput(...args); }
function updateComposer(...args) { return composer.updateComposer(...args); }
function persistDraft(...args) { return composer.persistDraft(...args); }
function resetRecipe(...args) { return details.resetRecipe(...args); }
function backdrop(...args) { return details.backdrop(...args); }
function setSidebar(...args) { return details.setSidebar(...args); }
function setDetails(...args) { return details.setDetails(...args); }
function setTab(...args) { return details.setTab(...args); }
function renderQueryPanel(...args) { return details.renderQueryPanel(...args); }
function renderRecipeEmpty(...args) { return details.renderRecipeEmpty(...args); }
function loadComponents(...args) { return details.loadComponents(...args); }
function renderComponentsPanel(...args) { return details.renderComponentsPanel(...args); }
function openRecipe(...args) { return details.openRecipe(...args); }
function renderSessions(...args) { return sessions.renderSessions(...args); }
function loadSessions(...args) { return sessions.loadSessions(...args); }
function selectBlank(...args) { return sessions.selectBlank(...args); }
function newSession(...args) { return sessions.newSession(...args); }
function openSession(...args) { return sessions.openSession(...args); }
function pollRemoteSession(...args) { return sessions.pollRemoteSession(...args); }
function sendMessage(...args) { return turns.sendMessage(...args); }
function cancelTurn(...args) { return turns.cancelTurn(...args); }
function renderConversation(...args) { return renderer.renderConversation(...args); }
function scheduleRender(...args) { return renderer.scheduleRender(...args); }
function toolCallNode(...args) { return renderer.toolCallNode(...args); }
function busy(id = state.currentId) { return state.runs.has(id) || Boolean(state.cache.get(id)?.running); }
function blankTitle(title) { return !title || ['新会话', '新对话'].includes(title); }
function hasRetry(message) { return message.retryMessage!=null && (Boolean(message.retryMessage) || Boolean(message.retryImages?.length)); }

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

ui.composer.addEventListener('submit', (event) => { event.preventDefault(); sendMessage(ui['composer-input'].value); });
ui['composer-input'].addEventListener('input', () => { resizeInput(); persistDraft(); updateComposer(); });
ui['composer-input'].addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault(); sendMessage(ui['composer-input'].value);
  }
});
ui['new-chat'].addEventListener('click', newSession);
document.querySelector('.brand').addEventListener('click',event=>{event.preventDefault();newSession({retainDraft:true});});
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
window.addEventListener('chefmate:inspect-context',()=>{if(!state.developer)return;developerPanel.mode='context';setDetails(true);setTab('query');});
$('close-details').addEventListener('click', () => { setDetails(false); ui['toggle-details'].focus(); });
ui['status-button'].addEventListener('click', () => { setDetails(true); setTab('query'); });
ui['workspace-context'].addEventListener('click', () => { setDetails(true); setTab('workspace'); });
const panelTabs = ['workspace', 'query', 'recipe', 'components'];
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
  const results = await Promise.allSettled([loadStatus(), loadSessions(), loadComponents()]);
  if (results[1].status === 'rejected') toast('会话列表暂时无法读取，请刷新页面重试。');
  const initial = storedSession()?{id:storedSession()}:state.sessions[0];
  if (initial && epoch === state.selectionEpoch && !state.creating && !state.currentId) await openSession(initial.id);
  try {
    const timers=await api('/api/timer-sessions');
    await Promise.allSettled(timers.items.filter(item=>item.id!==state.currentId).map(item=>workspace.load(item.id)));
  } catch { /* The active conversation remains usable during a discovery outage. */ }
  state.initializing=false;
  updateComposer();
}
const shell=initShell({onSettingsClose:()=>{
  refreshDisplayPreferences();loadStatus();
  if(state.currentId)workspace.load(state.currentId).catch(()=>{});
}});
function startCenterConversation(text,{record=false}={}) {
  if(state.initializing || state.creating || state.preparing){toast('这轮正在准备，请稍后再试。');return;}
  if(!shell.setPage('chat') || !newSession({retainDraft:true}))return;
  fillComposer(text);
  if(record) {
    if(state.status?.supports_images && !ui['attach-image'].disabled)ui['image-input'].click();
    return;
  }
  return sendMessage(text);
}
const centers=new KitchenCenters({api,onReturn:()=>shell.setPage('chat'),onNavigate:page=>shell.setPage(page),
  onRecord:()=>startCenterConversation('帮我记录这顿饭：识别我接下来提供的照片或文字，整理我自己吃的份量和营养；估算请写清假设，留待我核对后保存。',{record:true}),
  onChat:text=>startCenterConversation(text)});
window.addEventListener('chefmate:page-change',event=>{centers.show(event.detail.page);setDetails(false);renderConversation();});
const finishingMeals=new Set();
window.addEventListener('chefmate:finish-meal',async event=>{
  const id=event.detail?.sessionId || state.currentId;
  if(!id || busy(id) || finishingMeals.has(id))return;
  finishingMeals.add(id);
  try {
    const result=await workspace.dispatch('complete_meal',{},null,{sessionId:id});
    if(result?.status && result.status!=='ok')return;
    if(cookingFocus.active?.sessionId===id && workspace.entry(id).snapshot?.stage==='completed')cookingFocus.close();
    state.confirmationQueue.enqueue(id,'这顿饭所有菜都做好啦，我已点击完成本次菜肴。请查看本次营养登记状态；如果是多人用餐或人数不明确，先问我自己吃了哪些、吃了多少，再登记个人营养。');
    await continueConfirmations();
  } catch(error){toast(error.message || '还没有完成，请核对步骤后再试。');}
  finally{finishingMeals.delete(id);}
});
initialize();
