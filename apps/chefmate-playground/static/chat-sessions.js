import { node as element } from './ui-components.js';
import { SessionFeed } from './session-feed.js';
import { SessionMenu } from './session-menu.js';
import { api, normalizeSession } from './chat-http.js';
import { moveDrafts, recoverLocalImages, releaseImages } from './conversation-state.js';
import { icon } from './icons.js';
import { conversationLabel } from './presentation.js';

// Owns session pagination, selection, draft handoff and server-run recovery polling.
export function createSessionController({
  state, ui, workspace, cookingFocus, currentSession, busy, pendingQuestion,
  toast, announce, clearQuestion, draftKey, persistDraft, resizeInput, renderConversation,
  renderQueryPanel, updateComposer, updateConversationState, resetRecipe, loadComponents,
  setSidebar, renderWelcomeTemplates
}) {
  function renderSessions() {
    const scroll=ui.sessions.scrollTop,focused=ui.sessions.contains(document.activeElement)?document.activeElement.closest('[data-session-id]')?.dataset.sessionId:null;
    const focusClass=document.activeElement?.classList.contains('session-more')?'session-more':'session-link';
    sessionObserver?.disconnect();ui.sessions.replaceChildren();
    if (!state.sessions.length && !sessionFeed.loading) ui.sessions.append(element('div', 'session-empty', '从一个新对话开始吧。'));
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
      if(session.pinned){const mark=element('span','session-pinned');mark.append(icon('star'));mark.setAttribute('aria-label','已置顶');row.append(mark);}
      const more=element('button','session-more');more.type='button';more.append(icon('ellipsis'));
      more.setAttribute('aria-label',`对话操作：${title || '新对话'}`);more.title='对话操作';more.setAttribute('aria-haspopup','menu');more.setAttribute('aria-controls','session-menu');more.setAttribute('aria-expanded','false');
      more.onclick=()=>sessionMenu.open(session,more);row.append(more);
      ui.sessions.append(row);
    }
    if(sessionFeed.cursor || sessionFeed.loading || sessionFeed.error){
      const tail=element('button','session-load-more',sessionFeed.loading?'正在读取…':sessionFeed.error?'加载失败，点此重试':'加载更多对话');
      tail.type='button';tail.disabled=sessionFeed.loading;tail.setAttribute('aria-label',tail.textContent);
      tail.onclick=()=>void loadSessions({more:Boolean(sessionFeed.cursor)}).catch(()=>{});ui.sessions.append(tail);
      if(sessionFeed.cursor && !sessionFeed.loading && !sessionFeed.error)sessionObserver?.observe(tail);
    }
    ui.sessions.setAttribute('aria-busy',String(sessionFeed.loading));ui.sessions.scrollTop=scroll;
    if(focused)ui.sessions.querySelector(`[data-session-id="${CSS.escape(focused)}"] .${focusClass}`)?.focus({preventScroll:true});
    updateConversationState();
  }

  const sessionFeed=new SessionFeed(({limit,cursor})=>api(`/api/sessions?limit=${limit}${cursor?`&cursor=${encodeURIComponent(cursor)}`:''}`),{
    onChange:()=>{state.sessions=sessionFeed.items;renderSessions();},
  });
  const sessionMenu=new SessionMenu({isBusy:busy,returnFocus:id=>{
    const control=ui.sessions.querySelector(`[data-session-id="${CSS.escape(id)}"] .session-more`);
    (control || ui['new-chat']).focus({preventScroll:true});
  },onAction:async(action,session,title)=>{
    if(action==='delete'){
      await api(`/api/sessions/${encodeURIComponent(session.id)}`,{method:'DELETE'});
      state.cache.delete(session.id);state.drafts.delete(session.id);state.failedDrafts.delete(session.id);
      releaseImages(state.imageDrafts.get(session.id));state.imageDrafts.delete(session.id);state.imageUploads.delete(session.id);
      state.confirmationQueue.delete(session.id);clearQuestion(session.id);workspace.remove(session.id);
      sessionFeed.items=sessionFeed.items.filter(item=>item.id!==session.id);state.sessions=sessionFeed.items;
      if(state.currentId===session.id)selectBlank();
    }else{
      const result=await api(`/api/sessions/${encodeURIComponent(session.id)}`,{method:'PATCH',body:JSON.stringify(action==='pin'?{pinned:!session.pinned}:{title})});
      Object.assign(session,result);const cached=state.cache.get(session.id);if(cached)Object.assign(cached,result);
      if(state.currentId===session.id)renderConversation();
    }
    try{await loadSessions();}catch{toast('操作已保存，列表暂时无法同步，可点击重试。');}
  }});
  let sessionObserver;
  if('IntersectionObserver' in window){
    sessionObserver=new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting) && !sessionFeed.error)void loadSessions({more:true}).catch(()=>{});},{root:ui.sessions,rootMargin:'0px 0px 100px 0px'});
  }
  async function loadSessions(options) {return sessionFeed.load(options);}
  ui.sessions.addEventListener('scroll',()=>{
    if(!sessionObserver && ui.sessions.scrollTop+ui.sessions.clientHeight>=ui.sessions.scrollHeight-100)void loadSessions({more:true}).catch(()=>{});
  },{passive:true});
  window.addEventListener('focus',()=>{if(!state.initializing && !document.querySelector('dialog[open]') && !sessionFeed.loading)void loadSessions().catch(()=>{});});
  function selectBlank() {
    state.selectionEpoch++;
    state.currentId = null;
    try { localStorage.removeItem('chefmate:active-session'); } catch { /* In-memory selection remains usable. */ }
    workspace.select(null);
    state.componentsEpoch++;
    clearTimeout(state.remotePoll);
    resetRecipe();
    ui['composer-input'].value = state.drafts.get(state.blankId) || '';
    resizeInput();
    renderConversation();
    renderSessions();
    renderQueryPanel();
    updateComposer();
  }

  function newSession({retainDraft=false}={}) {
    if(state.creating || state.preparing)return false;
    cookingFocus.close({immediate:true});
    persistDraft();
    if(retainDraft && (state.drafts.get(state.blankId)?.trim() || state.imageDrafts.get(state.blankId)?.length))state.failedDrafts.set(state.blankId,true);
    if(!state.failedDrafts.has(state.blankId)) {
      releaseImages(state.imageDrafts.get(state.blankId));
      state.imageDrafts.delete(state.blankId);state.drafts.delete(state.blankId);
    }
    state.imageUploads.delete(state.blankId);
    state.blankId=`draft:${crypto.randomUUID()}`;
    selectBlank();setSidebar(false);renderWelcomeTemplates(true);ui['composer-input'].focus();
    return true;
  }

  function recoverMissingSession(id, epoch) {
    if(id!==state.currentId || epoch!==state.selectionEpoch || state.runs.has(id))return false;
    // Browser selection can outlive a cleared/replaced server data directory.
    // Bring unsent work back to the local draft before forgetting that selection.
    persistDraft();
    if(state.drafts.get(state.blankId)?.trim() || state.imageDrafts.get(state.blankId)?.length){
      state.failedDrafts.set(state.blankId,true);
      state.blankId=`draft:${crypto.randomUUID()}`;
    }
    moveDrafts(id,state.blankId,state.drafts,state.imageDrafts);
    if(state.imageDrafts.has(state.blankId))state.imageDrafts.set(state.blankId,recoverLocalImages(state.imageDrafts.get(state.blankId)));
    state.cache.delete(id);state.failedDrafts.delete(id);state.imageUploads.delete(id);
    state.confirmationQueue.delete(id);clearQuestion(id);workspace.remove(id);
    sessionFeed.items=sessionFeed.items.filter(item=>item.id!==id);state.sessions=sessionFeed.items;
    selectBlank();
    toast('之前的对话已不存在，已打开新对话；未发送的内容已保留。');
    return true;
  }

  async function openSession(id) {
    if(cookingFocus.active?.sessionId!==id)cookingFocus.close({immediate:true});
    persistDraft();
    const epoch = ++state.selectionEpoch;
    state.currentId = id;
    try { localStorage.setItem('chefmate:active-session',id); } catch { /* In-memory selection remains usable. */ }
    workspace.setRunning(id,true);
    workspace.select(id);
    state.components = [];
    loadComponents(id);
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
      } catch (error) {
        if(error.status===404 && recoverMissingSession(id,epoch))return;
        if (epoch === state.selectionEpoch) toast(error.message);
      }
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
        else { await loadSessions(); workspace.load(id).catch(() => {}); loadComponents(id); announce('本次执行已结束。'); }
      } catch(error) {
        if(error.status===404 && recoverMissingSession(id,epoch))return;
        if (id === state.currentId && epoch === state.selectionEpoch) pollRemoteSession(id, epoch);
      }
    }, 2500);
  }

  return { renderSessions, loadSessions, selectBlank, newSession, openSession, pollRemoteSession };
}
