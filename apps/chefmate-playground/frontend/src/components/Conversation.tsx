import { memo, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { chefStore } from '../runtime/store';
import type { Message, RunState, RuntimeState } from '../runtime/types';
import { MessageViews } from '../features/workspace';
import { Brand, Icon } from './Icon';
import { Markdown } from './Markdown';
import { Welcome } from './Welcome';
import { ImagePreview, imageURL } from './ImagePreview';
import { FoodCards, foodRecords } from './FoodCards';
import { RecipeReviews, collectRecipeReviews } from './RecipeReview';
import { assistantProgress, recipeSearchCards } from './chat-presentation';
import { ActivityText } from './ActivityText';
import { ExecutionTrace } from './developer/ExecutionTrace';

const ChatMessage = memo(function ChatMessage({ message, sessionId, running, developer, onRecipe, onAsk, foodIds, recipeIds, onFocus, run }: {
  message: Message; sessionId: string; running: boolean; developer: boolean; foodIds: Set<string>; recipeIds: Set<string>;
  onRecipe: (id: string, snapshot?: Record<string, any>) => void; onAsk: (text: string) => void;
  onFocus: (kind: string) => void;
  run?: RunState | null;
}) {
  const [image, setImage] = useState<number | null>(null);
  const showContent = message.content && (!message.streaming || message.resultComplete) && !message.cancelled && !message.error;
  const recipes = recipeSearchCards(message);
  return <article className={`message ${message.role}`} aria-label={message.role === 'user' ? '你的消息' : 'ChefMate 的回复'}>
    {message.images?.length ? <div className="message-images">{message.images.map((attachment, index) => <button key={attachment.id} type="button" className="image-thumbnail" aria-label={`预览图片：${attachment.name || '消息图片'}`} onClick={() => setImage(index)}><img src={imageURL(sessionId, attachment)} alt={attachment.name || '消息图片'} loading="lazy" /></button>)}</div> : null}
    {message.role === 'user' ? message.content && <div className="user-bubble">{message.content}</div> : <>
      <div className="assistant-heading"><Brand className="assistant-icon" /><span>ChefMate</span></div>
      <div className="assistant-body markdown">{showContent && <Markdown text={message.content} />}</div>
      {message.streaming && !message.error && !message.cancelled && <ActivityText text={assistantProgress(message, run)} />}
      {!message.streaming && <><MessageViews message={message} sessionId={sessionId} running={running} onContinue={text => { void chefStore.enqueueContinuation(sessionId, text); }} onRecipe={onRecipe} onFocus={onFocus} />
        {recipes.length > 0 && <div className="recipe-results">{recipes.map((recipe: Record<string, any>) =>
          <button type="button" className="recipe-result" key={recipe.recipe_id || recipe.id} onClick={() => onRecipe(recipe.recipe_id || recipe.id, message.snapshot)}><span className="recipe-result-copy"><span className="recipe-result-name">{recipe.name}{(recipe.nutrition_status === 'estimated' || recipe.nutrition?.estimated) && <small className="estimate-marker">营养估算</small>}</span>
            <span className="recipe-result-meta">{recipe.total_time_min == null ? '耗时未知' : `${recipe.total_time_min} 分钟`}</span></span><Icon name="chevron-right" /></button>)}</div>}
        <FoodCards message={message} locked={running} onAsk={onAsk} ownedIds={foodIds} />
        <RecipeReviews message={message} sessionId={sessionId} locked={running} onAsk={onAsk} ownedIds={recipeIds} />
      </>}
      <ExecutionTrace message={message} developer={developer} />
      {message.error && <div className="error-box" role="alert">{message.error}{message.retryMessage != null && !running && <button type="button" className="retry-button" onClick={() => void chefStore.retryMessage(message, sessionId)}>重试这次请求</button>}</div>}
      {message.cancelled && <div className="message-meta">本次执行已停止。{message.retryMessage != null && !running && <button type="button" className="text-button" onClick={() => void chefStore.retryMessage(message, sessionId)}>重新执行</button>}</div>}
    </>}
    {image != null && <ImagePreview images={message.images || []} index={image} sessionId={sessionId} onIndex={setImage} onClose={() => setImage(null)} />}
  </article>;
});

export function Conversation({ state, developer, onRecipe, onAsk, onFocus, active = true }: { state: RuntimeState; active?: boolean; developer: boolean; onRecipe: (id: string, snapshot?: Record<string, any>) => void; onAsk: (text: string, image?: boolean) => void; onFocus: (kind: string) => void }) {
  const scroll = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const positions = useRef(new Map<string, { top: number; stick: boolean }>());
  const selected = useRef<string | null>(null);
  const messages = state.session?.messages || [];
  const running = Boolean(state.session?.running || state.currentId && state.runs[state.currentId]);
  const run = state.currentId ? state.runs[state.currentId] : null;
  const owners = useMemo(() => { const map = new Map<string, number>(); messages.forEach((message, i) => foodRecords(message).forEach(record => map.set(record.id, i))); return map; }, [messages]);
  const recipeOwners = useMemo(() => {
    const map = new Map<string, { index: number; revision: number }>();
    messages.forEach((message, index) => collectRecipeReviews(message).forEach(record => {
      if (!map.has(record.id) || record.revision >= map.get(record.id)!.revision) map.set(record.id, { index, revision: record.revision });
    })); return map;
  }, [messages]);
  useLayoutEffect(() => {
    const element = scroll.current; if (!element || !active) return;
    const key = state.currentId || state.blankId;
    if (selected.current !== key) { const saved = positions.current.get(key); stick.current = saved?.stick ?? true; element.scrollTop = saved?.top ?? element.scrollHeight; selected.current = key; }
    if (stick.current) element.scrollTop = messages.length ? element.scrollHeight : 0;
  }, [messages, state.currentId, state.blankId, active]);
  return <div ref={scroll} className="conversation" tabIndex={-1} aria-label="聊天记录" onScroll={event => { if (!active) return; const el = event.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100; if (selected.current) positions.current.set(selected.current, { top: el.scrollTop, stick: stick.current }); }}>
    {state.loading && <p className="panel-loading" role="status">正在读取对话…</p>}
    {!messages.length ? <Welcome supportsImages={Boolean(state.status?.supports_images)} onUse={onAsk} /> : <div className="messages">{messages.map((message, i) =>
      <ChatMessage key={`${state.currentId}:${message.id || `${message.turn_id || i}:${message.role}`}`} message={message} sessionId={state.currentId!} running={running} developer={developer} run={run} onRecipe={onRecipe} onAsk={onAsk} onFocus={onFocus} foodIds={new Set([...owners].filter(([, index]) => index === i).map(([id]) => id))} recipeIds={new Set([...recipeOwners].filter(([, owner]) => owner.index === i).map(([id]) => id))} />)}</div>}
  </div>;
}
