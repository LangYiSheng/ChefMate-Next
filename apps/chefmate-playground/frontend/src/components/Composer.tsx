import { useEffect, useRef, useState } from 'react';
import { chefStore } from '../runtime/store';
import type { RuntimeState } from '../runtime/types';
import { QuestionPanel } from '../features/workspace';
import { Icon } from './Icon';
import { Dialog } from './Dialog';

export function imageURL(sessionId: string | null, image: Record<string, any>): string {
  return image.previewUrl || image.url || `/api/sessions/${encodeURIComponent(sessionId || '')}/images/${encodeURIComponent(image.id)}`;
}

export function Composer({ state, focus = false, notify }: { state: RuntimeState; focus?: boolean; notify: (text: string) => void }) {
  const input = useRef<HTMLTextAreaElement>(null);
  const upload = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<Record<string, any> | null>(null);
  const key = state.currentId || state.blankId;
  const text = state.drafts[key] || '';
  const images = state.imageDrafts[key] || [];
  const run = state.currentId ? state.runs[state.currentId] : null;
  const running = Boolean(run || state.session?.running);
  const uploading = Boolean(state.imageUploads[key]);
  const continuation = state.currentId ? state.continuations[state.currentId] : null;
  const pending = chefStore.pendingQuestion();
  const recoverable = chefStore.recoverableDraftKey();
  const latest = state.session?.messages?.findLast(message => message.role === 'assistant');
  const prefs = (() => { try { return JSON.parse(localStorage.getItem('chefmate:ui-settings') || '{}'); } catch { return {}; } })();
  const suggestions = !running && !pending && !continuation && prefs.reply_suggestions !== false ? latest?.suggested_replies || [] : [];
  useEffect(() => { if (focus) input.current?.focus({ preventScroll: true }); }, [focus]);
  useEffect(() => {
    if (!input.current) return;
    input.current.style.height = 'auto';
    input.current.style.height = `${Math.min(180, input.current.scrollHeight)}px`;
  }, [text, key]);
  function send() {
    void chefStore.sendMessage(text).then(result => { if (result.error) notify(result.error); });
  }
  function attach(files: File[]) {
    void chefStore.uploadImages(files).catch(error => notify(error.message));
  }
  return <div className="composer-area">
    {recoverable && <button type="button" className="resume-draft" onClick={() => chefStore.resumeDraft(recoverable)}><Icon name="undo-2" />继续未发送的消息</button>}
    {state.currentId && <QuestionPanel sessionId={state.currentId} running={running} onContinue={text => { void chefStore.enqueueContinuation(state.currentId!, text); }} />}
    {suggestions.length > 0 && <div className="reply-suggestions" aria-label="接下来可以聊">{suggestions.slice(0, prefs.reply_suggestion_count || 3).map((reply: Record<string, string>, i: number) =>
      <button type="button" key={`${reply.title}:${i}`} onClick={() => { chefStore.setDraft(reply.text); input.current?.focus(); }}>{reply.title}</button>)}</div>}
    {images.length > 0 && <div className="image-drafts" aria-label="待发送图片">{images.map(image => <div key={image.id} className="image-draft">
      <button type="button" className="image-thumbnail" aria-label={`预览图片：${image.name}`} onClick={() => setPreview(image)}><img src={imageURL(state.currentId, image)} alt={image.name || '待发送图片'} /></button>
      <button type="button" className="image-remove" aria-label={`移除图片：${image.name}`} disabled={running || state.preparing} onClick={() => void chefStore.removeImage(image.id).catch(error => notify(error.message))}><Icon name="x" size={14} /></button>
    </div>)}</div>}
    {uploading && <span className="image-status" role="status">正在压缩和准备图片…</span>}
    {(running || state.preparing) && <div className="turn-status" role="status"><Icon name="loader-circle" />{run?.status || (state.preparing ? '正在准备…' : '正在恢复执行状态…')}</div>}
    <form className="composer" onSubmit={event => { event.preventDefault(); send(); }}>
      <label className="visually-hidden" htmlFor="composer-input">告诉 ChefMate 你想吃什么</label>
      <textarea ref={input} id="composer-input" rows={1} value={text} maxLength={8000} autoComplete="off"
        placeholder={pending ? '先回答上面的问题，确认后继续…' : '告诉我想吃什么，也可以补充条件…'}
        onChange={event => chefStore.setDraft(event.target.value)}
        onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (!running && !pending && !state.preparing && !uploading) send(); } }}
        onPaste={event => { const files = [...event.clipboardData.files]; if (files.length && state.status?.supports_images) { event.preventDefault(); if (!running && !state.preparing) attach(files); } }} />
      <div className="composer-bottom">
        {state.status?.supports_images && <button type="button" className="attach-button" aria-label="上传图片" title="上传图片 · 自动压缩" disabled={uploading || running || state.preparing} onClick={() => upload.current?.click()}><Icon name="image-plus" /></button>}
        <input ref={upload} type="file" accept="image/jpeg,image/png,image/webp" multiple hidden onChange={event => { attach([...(event.target.files || [])]); event.target.value = ''; }} />
        <span className="composer-hint">Enter 发送 · Shift + Enter 换行</span>
        {running ? <button type="button" className="send-button stop-button" aria-label="停止本次执行" disabled={run?.cancelling} onClick={() => void chefStore.cancelTurn().catch(error => notify(error.message))}><Icon name="square" /></button> :
          <button type="submit" className="send-button" aria-label="发送消息" disabled={!state.status?.ready || state.preparing || uploading || Boolean(pending || continuation) || !text.trim() && !images.length}><Icon name="arrow-up" /></button>}
      </div>
    </form>
    {preview && <Dialog title="图片预览" className="image-preview" onClose={() => setPreview(null)}><img src={imageURL(state.currentId, preview)} alt={preview.name || '图片'} /><button type="button" onClick={() => setPreview(null)}>关闭图片预览</button></Dialog>}
  </div>;
}
