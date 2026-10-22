import { api, safeText, normalizeSession, normalizeToolCall, readEvents } from './chat-http.js';
import { moveDrafts, releaseImages, imageMetadata, matchesDraft, recoverLocalImages } from './conversation-state.js';
import { recoverRunningTurn } from './workspace.js';
import { TOOL_NAMES } from './presentation.js';

// Owns a submitted turn from preparation through streaming, cancellation and recovery.
export function createTurnController({
  state, ui, workspace, busy, pendingQuestion, savedAnswer, draftKey, maxImages,
  toast, announce, persistDraft, updateComposer, resizeInput, blankTitle, scheduleRender,
  renderConversation, renderSessions, renderQueryPanel, renderComponentsPanel,
  loadSessions, loadComponents, pollRemoteSession
}) {
  function applyEvent(run, event) {
    const message = run.message;
    if (event.type === 'model_usage') message.usage = event.usage;
    if (event.type === 'context_usage') message.context_usage=event.context_usage;
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
      if (event.name === 'load_skill' && run.sessionId === state.currentId) renderComponentsPanel();
    }
    if (event.type === 'ui_action') {
      message.actions ||= [];
      if (event.action) message.actions.push(event.action);
      workspace.load(run.sessionId).catch(() => {});
    }
    if (event.type === 'skill_loaded') {
      message.skills ||= [];
      message.skills.push(event.skill || event);
      if (run.sessionId === state.currentId) renderComponentsPanel();
    }
    if (event.type === 'result') {
      const result = event.result || event;
      message.content = result.text ?? message.content;
      message.recipes = result.status === 'completed' ? result.recipes || [] : [];
      message.selected_recipe = result.status === 'completed' ? result.selected_recipe : null;
      message.snapshot = result.snapshot;
      message.usage = result.usage;
      message.context_usage=result.context_usage ?? message.context_usage;
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
          loadComponents(id);
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
    if (blankTitle(session.title) && !session.title_custom) session.title = input.slice(0, 26) || '图片对话';
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
      if (id === state.currentId) loadComponents(id);
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

  return { sendMessage, cancelTurn };
}
