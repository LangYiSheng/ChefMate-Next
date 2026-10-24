import { useEffect, useId, useRef, useState, useSyncExternalStore } from 'react';
import { api, safeText } from '../runtime/http';
import { chefStore } from '../runtime/store';
import { workspaceStore } from '../features/workspace/store';
import { Icon } from './Icon';
import { Markdown, safeLink } from './Markdown';
import { CardToggle } from './CardToggle';
import './RecipeReview.css';

type Data = Record<string, any>;
export type RecipeReviewRecord = Data & { id: string; recipe_id?: string; revision: number; status: string; document: Data };
const reviewTools = new Set(['create_recipe_draft', 'get_recipe_draft', 'revise_recipe_draft', 'request_recipe_publication', 'confirm_recipe_publication']);
const difficulties: Record<string, string> = { beginner: '新手', normal: '普通', advanced: '进阶' };
const tabs = [['ingredients', '食材', 'utensils'], ['method', '做法', 'list-checks'], ['source', '来源与依据', 'book-open']] as const;

function validRecord(value: any): RecipeReviewRecord | null {
  const id = value?.recipe_id || value?.id;
  if (typeof id !== 'string' || !id || !Number.isInteger(value.revision) || value.revision < 1 || !value.document || typeof value.document !== 'object' || Array.isArray(value.document)) return null;
  if (!['draft', 'saved', 'deleted'].includes(value.status)) return null;
  return { ...value, id };
}

/** Only successful recipe records own a review card; a confirmation receipt is not a recipe. */
export function collectRecipeReviews(message: Data): RecipeReviewRecord[] {
  const records = new Map<string, RecipeReviewRecord>();
  for (const call of message.tool_calls || []) {
    if (!reviewTools.has(call.name || call.tool_name) || call.result?.status !== 'ok') continue;
    const record = validRecord(call.result.draft || call.result.record);
    if (record && (!records.has(record.id) || record.revision >= records.get(record.id)!.revision)) records.set(record.id, record);
  }
  return [...records.values()];
}

function amount(item: Data): string {
  if (item.raw_amount != null && item.raw_amount !== '') return `${item.raw_amount}${item.raw_unit || ''}`;
  return item.amount_text || '用量未知';
}
function time(value: any): string { return typeof value === 'number' && Number.isFinite(value) ? `${value} 分钟` : '未知'; }
/** Canonical sessions can arrive before workspace refresh; answered IDs prevent replaying stale questions. */
function confirmationGate(sessionId: string) {
  const runtime = chefStore.getSnapshot(), entry = workspaceStore.getSnapshot().entries[sessionId];
  const answered = entry?.snapshot?.last_answer?.question_id;
  const candidates = [runtime.cache[sessionId], runtime.currentId === sessionId && runtime.session?.id === sessionId ? runtime.session : null];
  const pending = Boolean(entry?.snapshot?.pending_question || candidates.some(session => {
    const question = session?.pending_question;
    return question ? !answered || question.id !== answered : Boolean(session?.awaiting_user);
  }));
  const syncing = Boolean(!entry?.snapshot || entry.loading || entry.saving || entry.running || entry.failed || entry.conflict || entry.pending
    || runtime.preparing || runtime.sessionLoading[sessionId] || runtime.runs[sessionId] || runtime.continuations[sessionId]
    || runtime.currentId && runtime.currentId !== sessionId
    || candidates.some(session => session?.running));
  return { pending, syncing };
}
function Source({ record }: { record: RecipeReviewRecord }) {
  const doc = record.document;
  return <div className="recipe-review-source"><h4>{record.source_kind === 'image' ? '图片原文转写' : '用户提供的文字'}</h4>
    {record.source_text ? <Markdown text={record.source_text} /> : <p className="recipe-review-muted">来源原文未提供。</p>}
    {record.source_kind === 'image' && record.source_image_ids?.length > 0 && <p className="recipe-review-muted">来源于 {record.source_image_ids.length} 张用户图片。</p>}
    <dl className="recipe-review-evidence">{[['份数依据', doc.servings_evidence], ['耗时依据', record.time_evidence], ['难度依据', record.difficulty_evidence]].map(([label, evidence]) => <div key={label}><dt>{label}</dt><dd>{evidence || '未留存依据'}</dd></div>)}</dl>
    {doc.warnings?.length > 0 && <section><h4>保留的说明与未知项</h4><ul>{doc.warnings.map((warning: string, index: number) => <li key={index}>{warning}</li>)}</ul></section>}
    {doc.references?.length > 0 && <section><h4>参考来源</h4><ul>{doc.references.map((reference: Data, index: number) => { const url = safeLink(reference.url || ''); return <li key={index}>{url ? <a href={url} target="_blank" rel="noopener noreferrer">{reference.title || url}<Icon name="external-link" /></a> : reference.title || '未提供有效链接'}</li>; })}</ul></section>}
  </div>;
}

type CardState = { scope: string; record: RecipeReviewRecord; phase: 'loading' | 'ready' | 'error'; error: string; notice: string; saving: boolean };
function RecipeReviewCard({ receipt, sessionId, locked, pendingQuestion, syncing, onAsk }: { receipt: RecipeReviewRecord; sessionId: string; locked: boolean; pendingQuestion: boolean; syncing: boolean; onAsk: (text: string) => void }) {
  const scope = `${sessionId}:${receipt.id}:${receipt.revision}`, [reload, setReload] = useState(0), [tab, setTab] = useState<(typeof tabs)[number][0]>('ingredients');
  const [state, setState] = useState<CardState>({ scope, record: receipt, phase: 'loading', error: '', notice: '', saving: false });
  const [expanded, setExpanded] = useState(true), bodyId = useId();
  const epoch = useRef(0), latestScope = useRef(scope), working = useRef(false), post = useRef<AbortController | null>(null), tabId = useId();
  latestScope.current = scope;
  useEffect(() => {
    const token = ++epoch.current, controller = new AbortController(); working.current = false;
    setState(previous => ({ scope, record: previous.scope === scope ? previous.record : receipt, phase: 'loading', error: '', notice: '', saving: false }));
    api(`/api/cookbook/${encodeURIComponent(receipt.id)}`, { signal: controller.signal }).then(value => {
      if (controller.signal.aborted || epoch.current !== token || latestScope.current !== scope) return;
      const record = validRecord(value);
      if (!record || record.id !== receipt.id) throw new Error('返回的菜谱信息不完整，请重新读取。');
      setState({ scope, record, phase: 'ready', error: '', notice: '', saving: false });
    }).catch(error => {
      if (!controller.signal.aborted && epoch.current === token && latestScope.current === scope) setState(previous => ({ ...previous, phase: 'error', error: safeText(error.message || '暂时无法读取菜谱。') }));
    });
    return () => { controller.abort(); post.current?.abort(); epoch.current++; };
  }, [scope, reload]);
  const current = state.scope === scope, record = current ? state.record : receipt, doc = record.document;
  const saving = current && state.saving, ready = current && state.phase === 'ready', isDraft = record.status === 'draft', deleted = record.status === 'deleted';
  function reread() {
    const entry = workspaceStore.getSnapshot().entries[sessionId], gate = confirmationGate(sessionId);
    // An explicit reread can also recover a failed/late question refresh; it never retries a write.
    if (!entry?.snapshot || entry.error || gate.pending && !entry.snapshot.pending_question) void workspaceStore.load(sessionId, true).catch(() => {});
    setReload(value => value + 1);
  }
  async function publish() {
    const gate = confirmationGate(sessionId);
    if (!ready || !isDraft || locked || pendingQuestion || gate.pending || gate.syncing || working.current) return;
    working.current = true;
    const token = epoch.current, controller = new AbortController(); post.current = controller;
    const valid = () => !controller.signal.aborted && token === epoch.current && latestScope.current === scope;
    setState(previous => ({ ...previous, saving: true, error: '', notice: '' }));
    try {
      const result = await api(`/api/cookbook/${encodeURIComponent(record.id)}/publish`, { method: 'POST', signal: controller.signal, body: JSON.stringify({ revision: record.revision }) });
      if (!valid()) return;
      const saved = validRecord(result);
      if (!saved || saved.id !== record.id || saved.status !== 'saved') throw new Error('保存结果尚未确认，请重新读取菜谱。');
      setState({ scope, record: saved, phase: 'ready', error: '', notice: '已保存到我的菜谱。', saving: false });
    } catch (error) {
      if (valid()) setState(previous => ({ ...previous, phase: 'error', saving: false, error: `${safeText((error as Error).message || '保存结果尚未确认。')} 请重新读取菜谱后核对，不会自动重试保存。` }));
    } finally { if (valid()) { working.current = false; post.current = null; } }
  }
  return <section className="recipe-review" aria-label={`个人菜谱审核：${doc.name || '未命名菜谱'}`}>
    <header className="recipe-review-heading"><span className="recipe-review-icon"><Icon name="book-open" /></span><div><p>个人菜谱 · 第 {record.revision} 版</p><h3>{doc.name || '未命名菜谱'}</h3></div><span className={`recipe-review-state ${record.status}`}>{record.status === 'saved' ? '已保存' : deleted ? '不再可用' : '待核对'}</span><CardToggle title={`个人菜谱：${doc.name || '未命名菜谱'}`} expanded={expanded} controls={bodyId} onToggle={() => setExpanded(value => !value)} /></header>
    <div id={bodyId} className="card-disclosure-body" hidden={!expanded}>
    {doc.description && <p className="recipe-review-description">{doc.description}</p>}
    <dl className="recipe-review-facts"><div><dt>份数</dt><dd>{doc.servings == null ? '未知' : `${doc.servings} 份`}</dd></div><div><dt>总耗时</dt><dd>{time(doc.total_time_min)}</dd></div><div><dt>难度</dt><dd>{difficulties[doc.difficulty] || '未知'}</dd></div></dl>
    <nav className="recipe-review-tabs" role="tablist" aria-label="菜谱审核内容">{tabs.map(([id, label, icon], index) => <button key={id} type="button" role="tab" id={`${tabId}-${id}`} aria-controls={`${tabId}-panel`} aria-selected={tab === id} tabIndex={tab === id ? 0 : -1} onClick={() => setTab(id)} onKeyDown={event => { if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return; event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length; setTab(tabs[next][0]); document.getElementById(`${tabId}-${tabs[next][0]}`)?.focus(); }}><Icon name={icon} />{label}</button>)}</nav>
    <div className="recipe-review-body" role="tabpanel" id={`${tabId}-panel`} aria-labelledby={`${tabId}-${tab}`}>
      {tab === 'ingredients' && <>{Array.isArray(doc.ingredients) && doc.ingredients.length ? <ul className="recipe-review-ingredients">{doc.ingredients.map((item: Data, index: number) => <li key={item.ingredient_id || index}><div><strong>{item.canonical_name || item.raw_name || item.name || '未命名食材'}</strong>{item.amount_evidence && <small>用量依据：{item.amount_evidence}</small>}</div><span>{amount(item)}</span></li>)}</ul> : <p className="recipe-review-muted">食材信息未提供。</p>}{doc.required_tools?.length > 0 && <p className="recipe-review-muted">厨具：{doc.required_tools.join('、')}</p>}</>}
      {tab === 'method' && <><div className="recipe-review-times"><span>备菜 {time(doc.prep_time_min)}</span><span>烹饪 {time(doc.cook_time_min)}</span></div>{Array.isArray(doc.steps) && doc.steps.length ? <ol className="recipe-review-steps">{doc.steps.map((step: string | Data, index: number) => <li key={index}><span>{index + 1}</span><p>{typeof step === 'string' ? step : step.instruction || step.description || ''}</p></li>)}</ol> : <p className="recipe-review-muted">做法信息未提供。</p>}</>}
      {tab === 'source' && <Source record={record} />}
    </div>
    <footer className="recipe-review-footer"><div className="recipe-review-actions">
      {isDraft && <>{!pendingQuestion && <button type="button" className="primary-button" disabled={!ready || locked || saving} onClick={() => void publish()}><Icon name={saving ? 'loader-circle' : 'check-check'} className={saving ? 'spin' : ''} />{saving ? '正在确认保存…' : '确认保存到我的菜谱'}</button>}<button type="button" className="secondary-button" disabled={locked || saving || pendingQuestion || !ready} onClick={() => onAsk(`请先读取并修改个人菜谱草稿 ${record.id}（${doc.name || '这道菜'}，第 ${record.revision} 版）：`)}><Icon name="message-circle" />告诉助手怎么改</button></>}
      {record.status === 'saved' && <button type="button" className="secondary-button" disabled={locked} onClick={() => onAsk(`我想做已保存的个人菜谱 ${record.id}「${doc.name || '这道菜'}」，请读取菜谱并带我核对人数和食材。`)}><Icon name="chef-hat" />聊聊怎么做</button>}
      <button type="button" className="recipe-review-refresh" aria-label="重新读取菜谱" disabled={saving || current && state.phase === 'loading'} onClick={reread}><Icon name="refresh-cw" />重新读取</button>
    </div>
      {deleted ? <p className="recipe-review-muted">这份个人菜谱已删除，不再可用。</p> : pendingQuestion && isDraft ? <p className="recipe-review-note" role="status">请先完成下方的确认问题，这里暂不重复确认保存。</p> : syncing && isDraft ? <p className="recipe-review-note" role="status">会话确认状态尚未就绪，完成确认或重新读取后再保存。</p> : isDraft && <p className="recipe-review-muted">尚未保存到我的菜谱。核对食材、用量与步骤后再确认；原文未明确的信息保留未知。</p>}
      {(!current || state.phase === 'loading') && <p className="recipe-review-note" role="status"><Icon name="loader-circle" className="spin" />正在读取菜谱当前版本…</p>}
    </footer>
    </div>
    {current && state.error && <p className="recipe-review-error" role="alert">{state.error}</p>}
    {current && state.notice && <p className="recipe-review-note" role="status">{state.notice}</p>}
  </section>;
}

export function RecipeReviews({ message, sessionId, locked, onAsk, ownedIds }: { message: Data; sessionId: string; locked: boolean; onAsk: (text: string) => void; ownedIds: Set<string> }) {
  useSyncExternalStore(workspaceStore.subscribe, workspaceStore.getSnapshot);
  useSyncExternalStore(chefStore.subscribe, chefStore.getSnapshot);
  const gate = confirmationGate(sessionId);
  if (message.streaming) return null;
  return <div className="recipe-reviews">{collectRecipeReviews(message).filter(record => ownedIds.has(record.id)).map(record => <RecipeReviewCard key={`${sessionId}:${record.id}`} receipt={record} sessionId={sessionId} locked={locked || gate.syncing} pendingQuestion={gate.pending} syncing={gate.syncing} onAsk={onAsk} />)}</div>;
}
