import { memo, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { chefStore } from '../runtime/store';
import type { Message, RuntimeState } from '../runtime/types';
import { MessageViews } from '../features/workspace';
import { messageViews } from '../features/workspace/protocol';
import { Brand, Icon } from './Icon';
import { Markdown } from './Markdown';
import { Welcome } from './Welcome';
import { ImagePreview, imageURL } from './ImagePreview';
import { FoodCards, foodRecords } from './FoodCards';

const toolNames: Record<string, string> = { find_ingredients: '识别食材', search_recipes: '筛选菜谱', get_recipe: '读取菜谱', show_preference_form: '整理用餐条件', show_recipe_choices: '整理菜品选择', set_meal_plan: '安排餐单', show_meal_plan: '核对餐单', show_prep_checklist: '整理备菜清单', show_cooking_guide: '整理烹饪步骤', start_timer: '开始计时', load_skill: '读取组件指南', ask_user: '向你确认细节', create_recipe_draft: '整理个人菜谱草稿', create_food_log_draft: '整理饮食记录', maintain_memory_summary: '维护长期记忆' };

const ChatMessage = memo(function ChatMessage({ message, sessionId, running, developer, onRecipe, onAsk, foodIds, onFocus }: {
  message: Message; sessionId: string; running: boolean; developer: boolean; foodIds: Set<string>;
  onRecipe: (id: string, snapshot?: Record<string, any>) => void; onAsk: (text: string) => void;
  onFocus: (kind: string) => void;
}) {
  const [image, setImage] = useState<number | null>(null);
  const showContent = message.content && (!message.streaming || message.resultComplete) && !message.cancelled && !message.error;
  return <article className={`message ${message.role}`} aria-label={message.role === 'user' ? '你的消息' : 'ChefMate 的回复'}>
    {message.images?.length ? <div className="message-images">{message.images.map((attachment, index) => <button key={attachment.id} type="button" className="image-thumbnail" aria-label={`预览图片：${attachment.name || '消息图片'}`} onClick={() => setImage(index)}><img src={imageURL(sessionId, attachment)} alt={attachment.name || '消息图片'} loading="lazy" /></button>)}</div> : null}
    {message.role === 'user' ? message.content && <div className="user-bubble">{message.content}</div> : <>
      <div className="assistant-heading"><Brand className="assistant-icon" /><span>ChefMate</span></div>
      <div className="assistant-body markdown">{showContent && <Markdown text={message.content} />}</div>
      {!message.streaming && <><MessageViews message={message} sessionId={sessionId} running={running} onContinue={text => { void chefStore.enqueueContinuation(sessionId, text); }} onRecipe={onRecipe} onFocus={onFocus} />
        {!messageViews(message).length && message.recipes?.length > 0 && <div className="recipe-results">{message.recipes.map((recipe: Record<string, any>) =>
          <button type="button" className="recipe-result" key={recipe.recipe_id || recipe.id} onClick={() => onRecipe(recipe.recipe_id || recipe.id, message.snapshot)}><span className="recipe-result-copy"><span className="recipe-result-name">{recipe.name}{(recipe.nutrition_status === 'estimated' || recipe.nutrition?.estimated) && <small className="estimate-marker">营养估算</small>}</span>
            <span className="recipe-result-meta">{recipe.total_time_min == null ? '耗时未知' : `${recipe.total_time_min} 分钟`}</span></span><Icon name="chevron-right" /></button>)}</div>}
        <FoodCards message={message} locked={running} onAsk={onAsk} ownedIds={foodIds} />
      </>}
      {(message.streaming || developer) && <details className="execution-trace" open={message.streaming ? true : undefined}><summary className="trace-summary"><Icon name={message.streaming ? 'loader-circle' : 'circle-check'} />{message.streaming ? message.recovering ? '连接中断，正在恢复…' : '正在处理…' : '执行过程'}</summary>
        {message.usage && <p className="usage-summary">{message.usage.model_calls ?? '—'} 次模型请求 · 输入 {message.usage.input_tokens ?? '未报告'} · 输出 {message.usage.output_tokens ?? '未报告'} · 缓存读取 {message.usage.cached_tokens ?? '未报告'} tokens</p>}
        {(message.process || []).map((text: string, i: number) => <details key={i} className="trace-note"><summary>{text.slice(0, 100)}</summary><Markdown text={text} /></details>)}
        {message.streaming && message.content && !message.resultComplete && <div className="trace-note"><Markdown text={message.content} /></div>}
        {(message.tool_calls || []).map((call, i) => <details key={call.call_id || i} className="tool-call"><summary>{call.running ? '使用中 · ' : ''}{toolNames[call.name] || '使用厨房工具'}</summary><pre>{JSON.stringify({ arguments: call.arguments, result: call.result }, null, 2)}</pre></details>)}
      </details>}
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
  const owners = useMemo(() => { const map = new Map<string, number>(); messages.forEach((message, i) => foodRecords(message).forEach(record => map.set(record.id, i))); return map; }, [messages]);
  useLayoutEffect(() => {
    const element = scroll.current; if (!element || !active) return;
    const key = state.currentId || state.blankId;
    if (selected.current !== key) { const saved = positions.current.get(key); stick.current = saved?.stick ?? true; element.scrollTop = saved?.top ?? element.scrollHeight; selected.current = key; }
    if (stick.current) element.scrollTop = messages.length ? element.scrollHeight : 0;
  }, [messages, state.currentId, state.blankId, active]);
  return <div ref={scroll} className="conversation" tabIndex={-1} aria-label="聊天记录" onScroll={event => { if (!active) return; const el = event.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100; if (selected.current) positions.current.set(selected.current, { top: el.scrollTop, stick: stick.current }); }}>
    {state.loading && <p className="panel-loading" role="status">正在读取对话…</p>}
    {!messages.length ? <Welcome supportsImages={Boolean(state.status?.supports_images)} onUse={onAsk} /> : <div className="messages">{messages.map((message, i) =>
      <ChatMessage key={`${state.currentId}:${message.id || `${message.turn_id || i}:${message.role}`}`} message={message} sessionId={state.currentId!} running={running} developer={developer} onRecipe={onRecipe} onAsk={onAsk} onFocus={onFocus} foodIds={new Set([...owners].filter(([, index]) => index === i).map(([id]) => id))} />)}</div>}
  </div>;
}
