import { node as element } from './ui-components.js';
import { api } from './chat-http.js';
import { QuestionState, answerRequestId, matchesAnswer } from './question-state.js';
import { QuestionComposer } from './question.js';
import { prepareImage } from './image-input.js';
import { imageThumbnail } from './image-preview.js';
import { releaseImages, suggestedReplies } from './conversation-state.js';
import { renderStarters } from './welcome.js';
import { icon } from './icons.js';
import { conversationLabel, snapshotStatus } from './presentation.js';
import { activityLabel } from './activity.js';

// Keeps draft attachments, question answers and saved-action continuations in one lifecycle.
export function createComposerController({
  state, ui, workspace, cookingFocus, currentSession, busy, toast,
  renderSessions, sendMessage, openSession, selectBlank, setSidebar
}) {
  const questions=new QuestionState({
    read:id=>JSON.parse(localStorage.getItem(`chefmate:question:${id}`) || 'null'),
    write:(id,value)=>value ? localStorage.setItem(`chefmate:question:${id}`,JSON.stringify(value)) : localStorage.removeItem(`chefmate:question:${id}`),
  });
  const answering=new Set(),continuingAnswers=new Set();
  const questionComposer=new QuestionComposer(ui['question-composer'],{state:questions,onAnswer:submitAnswer,
    onRetry:id=>{const entry=questions.get(id);if(entry)questions.set(id,{...entry,stage:'saved',error:''});updateComposer();}});
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
    const replies=suggestedReplies(currentSession()?.messages,state.status?.reply_suggestions!==false,
      workspace.entries.get(state.currentId)?.snapshot,state.status?.reply_suggestion_count);
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
    ui['conversation-stage'].hidden = !stage || !state.currentId || !['chat',undefined].includes(ui.app.dataset.page);
    const status=snapshot?snapshotStatus(snapshot):currentSession()?.conversation_state || {};
    ui['conversation-stage'].textContent = conversationLabel(status);
    ui['conversation-stage'].title=conversationLabel(status);
    const kitchen=document.getElementById('current-kitchen');
    const kind=stage==='prep'?'prep_checklist':['cooking','completed'].includes(stage)?'cooking_guide':null;
    const taskView=workspace.entries.get(state.currentId)?.views.find(view=>view.kind===kind);
    kitchen.hidden=!taskView || !['chat',undefined].includes(ui.app.dataset.page);
    kitchen.replaceChildren(icon(stage==='prep'?'shopping-bag':'cooking-pot'),element('span','',stage==='prep'?'查看备菜':stage==='completed'?'查看本次做菜':'继续做菜'));
    kitchen.onclick=()=>{if(taskView)workspace.onFocus(taskView,state.currentId);};
    if (snapshot) {
      const item = state.sessions.find(item => item.id === state.currentId);
      if (item) {item.conversation_state = status;item.awaiting_user=Boolean(snapshot.pending_question) && !busy();}
      const badge = ui.sessions.querySelector(`[data-session-id="${CSS.escape(state.currentId)}"] .session-stage`);
      if (badge) {badge.textContent = snapshot.pending_question && !busy() ? '等你回答' : conversationLabel(status);badge.title=badge.textContent;}
    }
  }
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

  function persistDraft() { state.drafts.set(draftKey(), ui['composer-input'].value); }

  return { questions, pendingQuestion, savedAnswer, draftKey, fillComposer, renderWelcomeTemplates, resumeDraft, uploadImages, updateConversationState, maxImages, continueConfirmations, resizeInput, updateComposer, persistDraft };
}
