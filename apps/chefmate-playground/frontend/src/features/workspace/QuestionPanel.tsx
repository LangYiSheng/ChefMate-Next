import { useEffect, useState, useSyncExternalStore } from 'react';
import { Icon } from '../../components/Icon';
import { chefStore } from '../../runtime/store';
import { answerMessage, answerPayload, type Json } from './protocol';
import { useViewportHeight, useWorkspace } from './hooks';
import { workspaceStore } from './store';
import { Button, WorkspaceError } from './views';

export function QuestionPanel({ sessionId, running }: { sessionId: string | null; running: boolean; onContinue?: (text: string) => void }) {
  const entry = useWorkspace(sessionId, running), runtime = useSyncExternalStore(chefStore.subscribe, chefStore.getSnapshot);
  const height = useViewportHeight(), [page, setPage] = useState(0), [error, setError] = useState('');
  const question = !running ? entry?.snapshot?.pending_question : null;
  useEffect(() => { setPage(0); setError(''); }, [sessionId, question?.id]);
  if (!entry || !sessionId) return null;
  const continuation = runtime.continuations[sessionId];
  if (!question) return continuation && <div className="react-workspace question-saved" role="status">
    {continuation.stage === 'failed' ? <><p>确认内容已保存，暂时没能开始下一轮。{continuation.error}</p><div className="ui-actions"><Button className="question-confirm" disabled={running} onClick={() => chefStore.retryContinuation(sessionId)}>重试继续</Button><Button className="ui-text-button" onClick={() => chefStore.dismissContinuation(sessionId)}>稍后继续</Button></div></> : <p><Icon name="loader-circle" />确认内容已保存，正在接着处理…</p>}
  </div>;
  const key = `question:${question.id}`, draft = entry.drafts[key] || { optionIds: [], text: '' };
  const disabled = running || entry.saving || Boolean(entry.failed) || runtime.status?.ready === false;
  const update = (patch: Json) => { setError(''); workspaceStore.questionDraft(sessionId, question, patch); };
  const units = [...(question.options || []).map((option: Json) => ({ kind: 'option', option })), ...(question.allow_custom ? [{ kind: 'custom' }] : [])];
  const pageSize = height < 540 ? 1 : 3, pages = Math.max(1, Math.ceil(units.length / pageSize)), current = Math.min(page, pages - 1);
  const submit = async () => {
    if (disabled) return;
    try {
      const payload = answerPayload(question, draft);
      await workspaceStore.dispatch(sessionId, 'answer_question', payload, undefined, { clearDraft: key, continuation: answerMessage(question, payload), requestId: `answer:${question.id}` });
    } catch (caught) { setError(caught instanceof Error ? caught.message : '答案没有保存，请重试。'); }
  };
  return <form className="react-workspace question-form" aria-label="回答这个问题" data-question-id={question.id} onSubmit={event => { event.preventDefault(); void submit(); }}>
    <header className="question-heading"><Icon name="circle-help" /><span>{question.title || '想先确认一下'}</span></header>
    <div className="question-body"><h3 className="question-title">{question.question}</h3><div className="question-options">
      {units.slice(current * pageSize, (current + 1) * pageSize).map((unit: Json) => unit.kind === 'custom' ? <label className="question-custom" key="custom"><span>或者写下你的答案</span><textarea rows={2} maxLength={2000} placeholder="补充你自己的想法…" value={draft.text || ''} disabled={disabled} onChange={event => update({ text: event.target.value })} /></label> : <label className="question-option" key={unit.option.id}><input type={question.multiple ? 'checkbox' : 'radio'} name={`answer-${question.id}`} checked={(draft.optionIds || []).includes(unit.option.id)} value={unit.option.id} disabled={disabled} onChange={event => update({ optionIds: question.multiple ? event.target.checked ? [...new Set([...(draft.optionIds || []), unit.option.id])] : (draft.optionIds || []).filter((id: string) => id !== unit.option.id) : [unit.option.id] })} /><span>{unit.option.label}</span></label>)}
    </div></div>
    {pages > 1 && <nav className="page-controls question-pages" aria-label="问题与选项分页"><Button className="page-button" disabled={!current} onClick={() => setPage(current - 1)}>上一页</Button><span>选项 {current + 1} / {pages}</span><Button className="page-button" disabled={current === pages - 1} onClick={() => setPage(current + 1)}>下一页</Button></nav>}
    {error && <p className="question-error" role="alert">{error}</p>}
    <footer className="question-footer"><span>{question.multiple ? '可多选，也可以补充说明' : '选择或填写后，助手会接着处理'}</span><button type="submit" className="question-confirm" disabled={disabled}><Icon name="arrow-up" />{entry.saving ? '正在保存…' : '确认并继续'}</button></footer>
    {(entry.failed || entry.conflict) && <WorkspaceError sessionId={sessionId} entry={entry} />}
  </form>;
}
