import { useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore, type CSSProperties } from 'react';
import { Icon } from '../../components/Icon';
import { chefStore } from '../../runtime/store';
import { answerMessage, answerPayload, type Json } from './protocol';
import { useViewportHeight, useWorkspace } from './hooks';
import { workspaceStore, type Entry } from './store';
import { Button, WorkspaceError } from './views';
import './question.css';

/** Small answer sets scroll together; substantial overflow is divided by measured row heights. */
export function questionPages(heights: number[], availableHeight: number, gap = 8): number[][] {
  const all = heights.map((_, index) => index);
  if (heights.length <= 4 || availableHeight <= 0 || heights.some(height => !Number.isFinite(height) || height <= 0)) return [all];
  const total = heights.reduce((sum, height) => sum + height, 0) + Math.max(0, heights.length - 1) * gap;
  if (total <= availableHeight * 1.75) return [all];
  const pages: number[][] = [];
  let page: number[] = [], used = 0;
  for (const [index, height] of heights.entries()) {
    if (page.length && used + gap + height > availableHeight) { pages.push(page); page = []; used = 0; }
    used += (page.length ? gap : 0) + height;
    page.push(index);
  }
  if (page.length) pages.push(page);
  return pages.length ? pages : [all];
}

function QuestionForm({ sessionId, question, entry, ready, height }: { sessionId: string; question: Json; entry: Entry; ready: boolean; height: number }) {
  const form = useRef<HTMLFormElement>(null), customInput = useRef<HTMLTextAreaElement>(null);
  const alive = useRef(true), submitting = useRef(false), layout = useRef(''), id = useId();
  const [page, setPage] = useState(0), [error, setError] = useState(''), [customOpen, setCustomOpen] = useState(false);
  const [pages, setPages] = useState<number[][]>([]);
  const key = `question:${question.id}`, draft = entry.drafts[key] || { optionIds: [], text: '' };
  const disabled = entry.running || entry.saving || Boolean(entry.failed || entry.conflict) || !ready;
  const customSelected = customOpen || Boolean(draft.text);
  const units = [...(question.options || []).map((option: Json) => ({ kind: 'option', option })), ...(question.allow_custom ? [{ kind: 'custom' }] : [])];
  const layoutKey = JSON.stringify([question.question, question.options, question.allow_custom]);
  const visible = pages.length ? pages[Math.min(page, pages.length - 1)] : units.map((_, index) => index);
  const current = Math.min(page, Math.max(0, pages.length - 1));

  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useLayoutEffect(() => {
    const root = form.current, body = root?.querySelector<HTMLElement>('.question-body'), title = root?.querySelector<HTMLElement>('.question-title'), options = root?.querySelector<HTMLElement>('.question-options');
    if (!root || !body || !options || !title) return;
    const rows = [...options.querySelectorAll<HTMLElement>('[data-question-unit]')];
    const measure = () => {
      const measured = rows.map(row => row.getBoundingClientRect().height);
      const maxHeight = Number.parseFloat(getComputedStyle(body).maxHeight);
      const bodyHeight = Number.isFinite(maxHeight) ? maxHeight : body.getBoundingClientRect().height;
      const titleHeight = title.getBoundingClientRect().height + (Number.parseFloat(getComputedStyle(title).marginBottom) || 0);
      const gap = Number.parseFloat(getComputedStyle(options).rowGap) || 8;
      const next = questionPages(measured, Math.max(0, bodyHeight - titleHeight), gap);
      const signature = JSON.stringify(next);
      if (layout.current === signature) return;
      layout.current = signature;
      const focusedRow = rows.findIndex(row => row.contains(document.activeElement));
      if (focusedRow >= 0) setPage(Math.max(0, next.findIndex(indices => indices.includes(focusedRow))));
      setPages(next);
    };
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    [root, body, ...rows].forEach(element => observer?.observe(element));
    window.addEventListener('resize', measure);
    window.addEventListener('chefmate:appearance', measure);
    return () => { observer?.disconnect(); window.removeEventListener('resize', measure); window.removeEventListener('chefmate:appearance', measure); };
  }, [layoutKey, height]);

  const update = (patch: Json) => { setError(''); workspaceStore.questionDraft(sessionId, question, patch); };
  const submit = async () => {
    if (disabled || submitting.current) return;
    submitting.current = true;
    try {
      const payload = answerPayload(question, draft);
      await workspaceStore.dispatch(sessionId, 'answer_question', payload, undefined, { clearDraft: key, continuation: answerMessage(question, payload), requestId: `answer:${question.id}` });
    } catch (caught) { if (alive.current) setError(caught instanceof Error ? caught.message : '答案没有保存，请重试。'); }
    finally { submitting.current = false; }
  };

  return <form ref={form} className="react-workspace react-question question-form" style={{ '--question-viewport-height': `${height}px` } as CSSProperties} aria-label="回答这个问题" data-question-id={question.id} onSubmit={event => { event.preventDefault(); void submit(); }}>
    <header className="question-heading"><Icon name="circle-help" /><span>{question.title || '想先确认一下'}</span></header>
    <div className="question-body" tabIndex={0} aria-label="问题和回答选项">
      <h3 className="question-title" id={`${id}-title`}>{question.question}</h3>
      <div className="question-options" role="group" aria-labelledby={`${id}-title`}>
        {units.map((unit: Json, index) => {
          const hidden = !visible.includes(index);
          return <div className="question-unit" data-page-hidden={hidden} aria-hidden={hidden || undefined} inert={hidden} key={unit.kind === 'custom' ? 'custom' : unit.option.id}>{unit.kind === 'custom'
            ? <div className={`question-option question-custom${customSelected ? ' selected' : ''}`} data-question-unit onClick={event => { if (disabled || hidden || (event.target as HTMLElement).closest('input,textarea,label')) return; setCustomOpen(true); customInput.current?.focus(); }}>
              <input type="checkbox" id={`${id}-custom`} aria-label="其他答案或补充说明" aria-controls={`${id}-custom-text`} data-choice-shape={question.multiple ? 'checkbox' : 'radio'} checked={customSelected} disabled={disabled || hidden} onChange={event => { setCustomOpen(event.target.checked); if (event.target.checked) customInput.current?.focus(); else update({ text: '' }); }} />
              <textarea ref={customInput} id={`${id}-custom-text`} aria-label="其他答案或补充说明" rows={1} maxLength={2000} placeholder="其他答案或补充说明" value={draft.text || ''} disabled={disabled || hidden} onFocus={() => setCustomOpen(true)} onChange={event => { setCustomOpen(true); update({ text: event.target.value }); }} />
            </div>
            : <label className={`question-option${(draft.optionIds || []).includes(unit.option.id) ? ' selected' : ''}`} data-question-unit>
              <input type={question.multiple ? 'checkbox' : 'radio'} name={`${id}-answer`} checked={(draft.optionIds || []).includes(unit.option.id)} value={unit.option.id} disabled={disabled || hidden} onChange={event => update({ optionIds: question.multiple ? event.target.checked ? [...new Set([...(draft.optionIds || []), unit.option.id])] : (draft.optionIds || []).filter((optionId: string) => optionId !== unit.option.id) : [unit.option.id] })} /><span>{unit.option.label}</span>
            </label>}</div>;
        })}
      </div>
    </div>
    {pages.length > 1 && <nav className="question-pages" aria-label="问题与选项分页"><Button className="secondary-button page-button" disabled={!current || disabled} onClick={() => setPage(current - 1)}><Icon name="chevron-left" />上一页</Button><span aria-live="polite">选项 {current + 1} / {pages.length}</span><Button className="secondary-button page-button" disabled={current === pages.length - 1 || disabled} onClick={() => setPage(current + 1)}>下一页<Icon name="chevron-right" /></Button></nav>}
    {error && <p className="question-error" role="alert">{error}</p>}
    <footer className="question-footer"><span>{question.multiple ? '可多选，也可以填写补充说明' : '选择一项，或填写自己的答案；也可以补充说明'}</span><button type="submit" className="primary-button question-confirm" disabled={disabled}><Icon name="arrow-up" />{entry.saving ? '正在保存…' : '确认并继续'}</button></footer>
    {(entry.failed || entry.conflict) && <WorkspaceError sessionId={sessionId} entry={entry} />}
  </form>;
}

export function QuestionPanel({ sessionId, running }: { sessionId: string | null; running: boolean; onContinue?: (text: string) => void }) {
  const entry = useWorkspace(sessionId, running), runtime = useSyncExternalStore(chefStore.subscribe, chefStore.getSnapshot), height = useViewportHeight();
  if (!entry || !sessionId) return null;
  const question = !running ? entry.snapshot?.pending_question : null;
  if (question) return <QuestionForm key={`${sessionId}:${question.id}`} sessionId={sessionId} question={question} entry={entry} ready={runtime.status?.ready !== false} height={height} />;
  const continuation = runtime.continuations[sessionId];
  return continuation && <div className="react-workspace react-question question-saved" role="status">
    {continuation.stage === 'failed' ? <><p>确认内容已保存，暂时没能开始下一轮。{continuation.error}</p><div className="ui-actions"><Button className="primary-button question-confirm" disabled={running} onClick={() => chefStore.retryContinuation(sessionId)}>重试继续</Button><Button className="ui-text-button" onClick={() => chefStore.dismissContinuation(sessionId)}>稍后继续</Button></div></> : <p><Icon name="loader-circle" />确认内容已保存，正在接着处理…</p>}
  </div>;
}
