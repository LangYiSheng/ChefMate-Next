import { useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react';
import { chefStore } from '../runtime/store';
import type { RuntimeState } from '../runtime/types';
import { QuestionPanel } from '../features/workspace';
import { workspaceStore } from '../features/workspace/store';
import { Icon } from './Icon';
import { ImagePreview, imageURL } from './ImagePreview';
import { replySuggestions } from './suggestions';
export { imageURL } from './ImagePreview';

export type EditorPositions = Map<string, { start: number; end: number }>;
export function Composer({ state, focus = false, notify, positions }: { state: RuntimeState; focus?: boolean; notify: (text: string) => void; positions?: EditorPositions }) {
  const input = useRef<HTMLTextAreaElement>(null);
  const upload = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<number | null>(null);
  const workspace = useSyncExternalStore(workspaceStore.subscribe, workspaceStore.getSnapshot);
  const key = state.currentId || state.blankId;
  const text = state.drafts[key] || '';
  const images = state.imageDrafts[key] || [];
  const run = state.currentId ? state.runs[state.currentId] : null;
  const running = Boolean(run || state.session?.running);
  const uploading = Boolean(state.imageUploads[key]);
  const reading = Boolean(state.currentId && state.sessionLoading[state.currentId]);
  const continuation = state.currentId ? state.continuations[state.currentId] : null;
  const pending = chefStore.pendingQuestion();
  const recoverable = chefStore.recoverableDraftKey();
  const entry = state.currentId ? workspace.entries[state.currentId] : null;
  const suggestions = !running && !reading && !pending && !continuation ? replySuggestions(state.session?.messages || [], state.status, entry?.snapshot ? { ...entry.snapshot, views: entry.views } : null) : [];
  useLayoutEffect(() => {
    const element = input.current, saved = positions?.get(key);
    if (element && saved) element.setSelectionRange(saved.start, saved.end);
    return () => { if (element) positions?.set(key, { start: element.selectionStart, end: element.selectionEnd }); };
  }, [key, positions, Boolean(pending)]);
  useEffect(() => setPreview(null), [key]);
  useEffect(() => { if (focus) input.current?.focus({ preventScroll: true }); }, [focus]);
  const resizeInput = useCallback(() => {
    const element = input.current; if (!element) return;
    element.style.height = 'auto';
    const limit = parseFloat(getComputedStyle(element).maxHeight) || 180;
    element.style.height = `${Math.min(limit, element.scrollHeight)}px`;
    element.style.overflowY = element.scrollHeight > limit ? 'auto' : 'hidden';
  }, []);
  useLayoutEffect(resizeInput, [resizeInput, text, key, Boolean(pending)]);
  useEffect(() => {
    const element = input.current; if (!element) return;
    let alive = true;
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(resizeInput);
    observer?.observe(element.parentElement!);
    window.addEventListener('resize', resizeInput); window.addEventListener('chefmate:appearance', resizeInput);
    void document.fonts?.ready.then(() => { if (alive) resizeInput(); });
    return () => { alive = false; observer?.disconnect(); window.removeEventListener('resize', resizeInput); window.removeEventListener('chefmate:appearance', resizeInput); };
  }, [resizeInput, key, Boolean(pending)]);
  function send() {
    void chefStore.sendMessage(text).then(result => { if (result.error) notify(result.error); });
  }
  function attach(files: File[]) {
    void chefStore.uploadImages(files).catch(error => notify(error.message));
  }
  return <div className="composer-area">
    {recoverable && <button type="button" className="resume-draft" onClick={() => chefStore.resumeDraft(recoverable)}><Icon name="undo-2" />继续未发送的消息</button>}
    {state.currentId && <QuestionPanel sessionId={state.currentId} running={running} onContinue={text => { void chefStore.enqueueContinuation(state.currentId!, text); }} />}
    {suggestions.length > 0 && <div className="reply-suggestions" aria-label="接下来可以聊">{suggestions.map((reply: Record<string, string>, i: number) =>
      <button type="button" key={`${reply.title}:${i}`} onClick={() => { chefStore.setDraft(reply.text); input.current?.focus(); }}>{reply.title}</button>)}</div>}
    {!pending && images.length > 0 && <div className="image-drafts" aria-label="待发送图片">{images.map((image, index) => <div key={image.id} className="image-draft">
      <button type="button" className="image-thumbnail" aria-label={`预览图片：${image.name}`} onClick={() => setPreview(index)}><img src={imageURL(state.currentId, image)} alt={image.name || '待发送图片'} /></button>
      <button type="button" className="image-remove" aria-label={`移除图片：${image.name}`} disabled={uploading || running || state.preparing} onClick={() => void chefStore.removeImage(image.id).catch(error => notify(error.message))}><Icon name="x" size={14} /></button>
    </div>)}</div>}
    {uploading && <span className="image-status" role="status">正在压缩和准备图片…</span>}
    {reading && <div className="turn-status" role="status">正在读取这段对话，读取完成后就可以发送。</div>}
    {!pending && <form className="composer" onSubmit={event => { event.preventDefault(); if (!reading) send(); }}>
      <label className="visually-hidden" htmlFor="composer-input">告诉 ChefMate 你想吃什么</label>
      <textarea key={key} ref={input} id="composer-input" rows={1} value={text} maxLength={8000} autoComplete="off"
        placeholder="告诉我想吃什么，也可以补充条件…"
        onChange={event => chefStore.setDraft(event.target.value)}
        onSelect={event => positions?.set(key, { start: event.currentTarget.selectionStart, end: event.currentTarget.selectionEnd })}
        onBlur={event => positions?.set(key, { start: event.currentTarget.selectionStart, end: event.currentTarget.selectionEnd })}
        onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (!running && !reading && !state.preparing && !uploading) send(); } }}
        onPaste={event => { const files = [...event.clipboardData.files]; if (files.length && state.status?.supports_images) { event.preventDefault(); if (!running && !state.preparing) attach(files); } }} />
      <div className="composer-bottom">
        {state.status?.supports_images && <button type="button" className="attach-button" aria-label="上传图片" title="上传图片 · 自动压缩" disabled={uploading || running || state.preparing} onClick={() => upload.current?.click()}><Icon name="image-plus" /></button>}
        <input ref={upload} type="file" accept="image/jpeg,image/png,image/webp" multiple hidden onChange={event => { attach([...(event.target.files || [])]); event.target.value = ''; }} />
        <span className="composer-hint">Enter 发送 · Shift + Enter 换行</span>
        {running ? <button type="button" className="send-button stop-button" aria-label="停止本次执行" disabled={run?.cancelling} onClick={() => void chefStore.cancelTurn().catch(error => notify(error.message))}><Icon name="square" /></button> :
          <button type="submit" className="send-button" aria-label="发送消息" disabled={!state.status?.ready || reading || state.preparing || uploading || Boolean(continuation) || !text.trim() && !images.length}><Icon name="arrow-up" /></button>}
      </div>
    </form>}
    {preview != null && <ImagePreview images={images} index={preview} sessionId={state.currentId} onIndex={setPreview} onClose={() => setPreview(null)} />}
  </div>;
}
