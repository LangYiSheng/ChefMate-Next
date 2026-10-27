import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { chefStore } from '../../runtime/store';
import { Icon } from '../../components/Icon';
import { type Json, messageViews, viewChoices, stages, labels } from './protocol';
import { workspaceStore } from './store';
import { Button, Hint, ViewCard, WorkspaceError, CompletionNotice } from './views';
import { useWorkspace } from './hooks';
export { QuestionPanel } from './QuestionPanel';
import { KitchenTimers } from './Timers';
import { FocusConversation } from './FocusConversation';
export { KitchenTimers, visibleTimers } from './Timers';
export { useWorkspace } from './hooks';
import './workspace.css';
export { initializeWorkspace, workspaceStore } from './store';

type PanelProps = { sessionId: string | null; running: boolean; onContinue: (text: string) => void; onRecipe?: (id: string) => void; onFocus?: (kind: string) => void; notify?: (text: string) => void };
export function WorkspacePanel({ sessionId, running, onContinue, onRecipe, onFocus }: PanelProps) {
  const entry = useWorkspace(sessionId, running);
  if (!sessionId || !entry) return <div className="panel-empty">从一个会话开始。选菜、备菜、步骤和计时都会随当前任务保存。</div>;
  const choices = viewChoices(entry.views, entry.snapshot?.stage), active = choices.find(view => view.kind === entry.activeKind) || choices[0], hiddenDrafts = Object.entries(entry.drafts).filter(([, draft]) => draft.kind === 'recipe_choices' && !choices.some(view => view.kind === draft.kind));
  return <div className="react-workspace"><div className="workspace-heading"><span className="workspace-stage">{stages[entry.snapshot?.stage] || '厨房任务'}</span><Button className="ui-text-button" disabled={entry.loading || entry.saving} onClick={() => workspaceStore.load(sessionId)}>{entry.loading ? '读取中…' : '刷新'}</Button></div>{entry.saving && <p className="workspace-sync" role="status">正在保存…</p>}<WorkspaceError sessionId={sessionId} entry={entry} /><CompletionNotice entry={entry} />{!!hiddenDrafts.length && <div className="error-box">还有未确认的菜谱选择。可放弃这份选择继续当前餐单，或告诉助手返回选菜阶段复核。<Button className="ui-text-button" disabled={entry.saving} onClick={() => hiddenDrafts.forEach(([id]) => workspaceStore.discard(sessionId, id))}>放弃未确认的选择</Button></div>}<nav className="workspace-navigation" aria-label="工作区内容">{choices.map(view => <Button className={`workspace-nav${active?.kind === view.kind ? ' active' : ''}`} key={view.id} aria-pressed={active?.kind === view.kind} onClick={() => workspaceStore.setActive(sessionId, view.kind)}>{labels[view.kind]}</Button>)}</nav>{active ? <ViewCard key={active.id} view={active} entry={entry} sessionId={sessionId} onContinue={onContinue} onRecipe={onRecipe} onFocus={onFocus} /> : <div className="panel-empty">{entry.loading ? '正在读取任务…' : '告诉助手你的想法，合适的表单、菜品和清单会在这里打开。'}</div>}</div>;
}
const readInlineLimit = () => { try { return JSON.parse(localStorage.getItem('chefmate:ui-settings') || '{}').inline_limit === 1 ? 1 : 2; } catch { return 2; } };
function viewSummary(view: Json) {
  const data = view.data || {};
  if (data.recipes) return `${data.recipes.length} 道菜 · ${data.recipes.slice(0, 3).map((recipe: Json) => recipe.name).join('、')}`;
  if (data.recipe) return data.recipe.name;
  if (data.items) return `${data.items.length} 项备料`;
  if (data.steps) return `${data.recipe_name || '烹饪'} · ${data.steps.length} 个步骤`;
  if (data.timers) return `${data.timers.length} 个计时器`;
  return view.kind === 'preference_form' ? '食材、时间和用餐偏好' : '查看任务内容';
}
export function MessageViews({ message, sessionId, running, onContinue, onRecipe, onFocus }: { message: Json; sessionId: string; running: boolean; onContinue: (text: string) => void; onRecipe?: (id: string) => void; onFocus?: (kind: string) => void; notify?: (text: string) => void }) {
  const entry = useWorkspace(sessionId, running), runtime = useSyncExternalStore(chefStore.subscribe, chefStore.getSnapshot);
  const [limit, setLimit] = useState(readInlineLimit), [expanded, setExpanded] = useState<Record<string, boolean>>({});
  useEffect(() => { const update = () => setLimit(readInlineLimit()); window.addEventListener('chefmate:appearance', update); window.addEventListener('storage', update); return () => { window.removeEventListener('chefmate:appearance', update); window.removeEventListener('storage', update); }; }, []);
  if (!entry) return null;
  const views = messageViews(message).filter(view => view.kind !== 'task_progress' && !(['cooking', 'completed'].includes(entry.snapshot?.stage) && ['prep_checklist', 'preference_form', 'recipe_choices'].includes(view.kind)));
  const history = runtime.cache[sessionId]?.messages || [];
  const items = views.map((view, index) => {
    const current = entry.views.find(item => item.id === view.id && item.kind === view.kind);
    const owner = history.findLast(source => messageViews(source).some(candidate => candidate.id === view.id));
    const ownsView = owner ? owner === message || Boolean(owner.id && owner.id === message.id) : !current || view.revision >= current.revision;
    return { view, index, current, live: Boolean(current && ownsView) };
  });
  const defaults = new Set(items.filter(item => item.view.placement === 'inline').sort((left, right) => Number(Boolean(right.live && right.current?.editable)) - Number(Boolean(left.live && left.current?.editable)) || right.index - left.index).slice(0, limit).map(item => item.view.id));
  return <div className="react-workspace react-inline-workspace">{items.map(({ view, current, live }) => {
    const open = expanded[view.id] ?? defaults.has(view.id);
    if (!open) return <section className="workspace-view-receipt" key={view.id} data-view-id={view.id} data-view-kind={view.kind}>
      <button className="view-receipt" type="button" aria-expanded={false} onClick={() => setExpanded(values => ({ ...values, [view.id]: true }))}><Icon name="layers" /><span><strong>{view.title || labels[view.kind]}</strong><small>{viewSummary(current || view)}{!live ? ' · 历史记录' : ''}</small></span><Icon name="chevron-down" /></button>
      {current && onFocus && <Button className="ui-text-button" aria-label={`全屏查看${view.title || labels[view.kind]}`} onClick={() => onFocus(view.kind)}><Icon name="maximize" /></Button>}
    </section>;
    return <div className="workspace-inline-view" key={view.id}><ViewCard view={live ? current! : view} entry={entry} sessionId={sessionId} historical={!live} onContinue={onContinue} onRecipe={onRecipe} onFocus={onFocus} onCollapse={() => setExpanded(values => ({ ...values, [view.id]: false }))} /></div>;
  })}</div>;
}
function restoreFocusEntrance(target: HTMLElement | null) {
  if (!target?.isConnected || target.closest('[inert],[hidden],[aria-hidden="true"]')) return;
  const modal = [...document.querySelectorAll<HTMLElement>('dialog[open],[aria-modal="true"]')].find(element => !element.closest('[inert],[hidden],[aria-hidden="true"]'));
  if (modal && !modal.contains(target)) return;
  target.focus({ preventScroll: true });
}
export function CookingFocus({ sessionId, onClose, onContinue, children, initialKind, active = true, renderConversation }: { sessionId: string; onClose: () => void; onContinue: (text: string) => void; children?: ReactNode; initialKind?: string; active?: boolean; renderConversation?: (onFocus: (kind: string) => void) => ReactNode }) {
  const entry = useWorkspace(sessionId), runtime = useSyncExternalStore(chefStore.subscribe, chefStore.getSnapshot);
  const scene = useRef<HTMLElement>(null), [talking, setTalking] = useState(false), [kind, setKind] = useState(initialKind || '');
  const previousStage = useRef(entry?.snapshot?.stage), close = useRef(onClose);
  const entrance = useRef<HTMLElement | null>(null), wasActive = useRef(false), mounted = useRef(false);
  close.current = onClose;
  useEffect(() => {
    const stage = entry?.snapshot?.stage;
    if (active && previousStage.current !== 'completed' && stage === 'completed') close.current();
    else if (active && stage !== previousStage.current) {
      setKind(({ idea: 'preference_form', planning: 'meal_plan', prep: 'prep_checklist', cooking: 'cooking_guide' } as Record<string, string>)[stage] || '');
      setTalking(false);
    }
    previousStage.current = stage;
  }, [active, entry?.snapshot?.stage]);
  useEffect(() => { if (active) { setKind(initialKind || ''); setTalking(false); } }, [active, initialKind, sessionId]);
  useLayoutEffect(() => {
    if (active && !wasActive.current) {
      const element = scene.current, previous = document.activeElement as HTMLElement | null;
      // Record the entrance before the mounted Composer's passive autofocus effect.
      entrance.current = previous && !element?.contains(previous) ? previous : null;
      if (element && !element.contains(previous) && !document.querySelector('dialog[open],[aria-modal="true"]')) element.querySelector<HTMLButtonElement>('#exit-focus')?.focus({ preventScroll: true });
    } else if (!active && wasActive.current) {
      const target = entrance.current; entrance.current = null;
      // Layout setup runs after all commit mutations, including main's inert removal.
      restoreFocusEntrance(target);
    }
    wasActive.current = active;
  }, [active]);
  useLayoutEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      const target = entrance.current;
      // A StrictMode effect replay cancels this; a real unmount waits for inert removal.
      if (target) queueMicrotask(() => {
        if (mounted.current || entrance.current !== target) return;
        entrance.current = null; restoreFocusEntrance(target);
      });
    };
  }, []);
  if (!entry) return null;
  const choices = viewChoices(entry.views, entry.snapshot?.stage), selected = choices.find(view => view.kind === kind) || choices.find(view => view.kind === (entry.snapshot?.stage === 'cooking' ? 'cooking_guide' : 'prep_checklist')) || choices[0];
  const cached = runtime.cache[sessionId], messages = cached?.messages || [], run = runtime.runs[sessionId];
  return <section ref={scene} id="kitchen-focus" data-active-view={talking ? 'conversation' : selected?.kind} className={`kitchen-focus react-cooking-focus${active ? ' is-active' : ''}`} aria-label="烹饪专注模式" aria-hidden={!active} inert={!active} tabIndex={-1} onKeyDown={event => {
    if (!active || event.defaultPrevented || event.nativeEvent.isComposing || document.querySelector('dialog[open]')) return;
    if (event.key === 'Escape') { event.preventDefault(); if (talking) setTalking(false); else onClose(); }
    if (event.key !== 'Tab') return;
    const elements = [...(scene.current?.querySelectorAll<HTMLElement>('button,a[href],input,textarea,select,summary,[tabindex]') || [])].filter(element => {
      if (element.tabIndex < 0 || element.matches(':disabled,input[type="hidden"]') || element.closest('[hidden],[inert]')) return false;
      const disclosure = element.closest('details:not([open])');
      if (disclosure && element !== disclosure.querySelector('summary')) return false;
      const style = getComputedStyle(element);
      return style.display !== 'none' && style.visibility !== 'hidden';
    }).sort((left, right) => left.compareDocumentPosition(right) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1);
    const first = elements[0], last = elements.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  }}>
    <header className="focus-header"><div className="focus-heading"><h2>{selected?.data.recipe_name || stages[entry.snapshot?.stage] || '厨房任务'}</h2><span className="focus-header-progress">{entry.running ? '助手正在处理…' : '专注做菜'}</span></div><div className="focus-header-actions"><KitchenTimers sessionId={sessionId} active={active} /><Button id="exit-focus" className="focus-button" aria-label="退出专注模式" onClick={onClose}><Icon name="x" /></Button></div></header>
    <nav className="focus-modes" aria-label="专注模式内容">{choices.map(view => <Button key={view.id} aria-pressed={!talking && selected?.kind === view.kind} onClick={() => { setKind(view.kind); setTalking(false); }}>{labels[view.kind]}</Button>)}<Button aria-pressed={talking} onClick={() => setTalking(!talking)}>和助手聊聊</Button></nav>
    <div className="focus-body"><div className="focus-deck"><WorkspaceError sessionId={sessionId} entry={entry} /><div className="focus-content" tabIndex={0}>{talking ? <div className="focus-chat-surface">{renderConversation?.(nextKind => { setKind(nextKind); setTalking(false); }) || <FocusConversation messages={messages} run={run} sessionId={sessionId} active={active} expanded />}</div> : selected ? <ViewCard key={selected.id} view={selected} entry={entry} sessionId={sessionId} focused onContinue={onContinue} onFocus={nextKind => { setKind(nextKind); setTalking(false); }} /> : <Hint>当前还没有可展示的任务内容。</Hint>}</div></div></div>
    {children && <footer className="focus-footer"><div className="focus-footer-inner">{!talking && <FocusConversation messages={messages} run={run} sessionId={sessionId} active={active} />}<div className="focus-composer-slot">{children}</div></div></footer>}
  </section>;
}
