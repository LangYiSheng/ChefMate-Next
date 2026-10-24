import { useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore, type CSSProperties } from 'react';
import { chefStore } from '../../runtime/store';
import { Icon } from '../../components/Icon';
import { timerPresentation, workspaceTimers, timerCapacity, type Timer } from './protocol';
import { workspaceStore } from './store';
import { useWorkspace } from './hooks';
import { Button, TimerForm, TimerRow, WorkspaceError } from './views';

export function visibleTimers(timers: Timer[], acknowledged: string[], now: number) {
  const unique = new Map<string, Timer>();
  for (const timer of timers) {
    const key = timer.step_id ? `${timer.recipe_id}:${timer.step_id}` : timer.id, previous = unique.get(key);
    if (!previous || !['running', 'paused'].includes(previous.status) || ['running', 'paused'].includes(timer.status)) unique.set(key, timer);
  }
  const priority = (timer: Timer) => timerPresentation(timer, now).expired ? 0 : timer.status === 'running' ? 1 : 2;
  return [...unique.values()].filter(timer => timerPresentation(timer, now).expired ? !acknowledged.includes(timer.id) : ['running', 'paused'].includes(timer.status))
    .sort((a, b) => priority(a) - priority(b) || (timerPresentation(a, now).remaining ?? Infinity) - (timerPresentation(b, now).remaining ?? Infinity));
}

export function KitchenTimers({ sessionId, onOpenSession, active: surfaceActive = true }: { sessionId: string | null; onOpenSession?: (id: string) => void; active?: boolean }) {
  const entry = useWorkspace(sessionId), state = useSyncExternalStore(workspaceStore.subscribe, workspaceStore.getSnapshot);
  const [open, setOpen] = useState(false), [creating, setCreating] = useState(false), [highlight, setHighlight] = useState<string | undefined>();
  const [position, setPosition] = useState<CSSProperties>({});
  const root = useRef<HTMLDivElement>(null), panel = useRef<HTMLElement>(null), trigger = useRef<HTMLButtonElement>(null);
  const focusOnOpen = useRef(false), seenEvent = useRef(state.timerEvent?.sequence || 0), id = useId();
  const activeSurface = () => {
    if (!surfaceActive || root.current?.closest('[inert],[hidden]')) return false;
    const modal = [...document.querySelectorAll<HTMLDialogElement>('dialog[open]')].at(-1);
    return !modal || Boolean(root.current && modal.contains(root.current));
  };
  const close = (restore = false) => { setOpen(false); if (restore) trigger.current?.focus({ preventScroll: true }); };
  useEffect(() => { setOpen(false); setCreating(false); setHighlight(undefined); }, [sessionId]);
  useEffect(() => { if (!surfaceActive) { setOpen(false); setCreating(false); } }, [surfaceActive]);
  useEffect(() => {
    const event = state.timerEvent;
    if (!event || event.sequence <= seenEvent.current) return;
    seenEvent.current = event.sequence;
    if (event.sessionId !== sessionId || !activeSurface()) return;
    setHighlight(event.timerId);
    if (event.reason !== 'expired') setCreating(false);
    focusOnOpen.current = event.reason === 'inspect';
    setOpen(true);
  }, [state.timerEvent, sessionId, surfaceActive]);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (root.current && !root.current.contains(event.target as Node)) close(false); };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || !activeSurface()) return;
      // The user can still be typing in the composer after an automatic reminder.
      // Close only this overlay, preserving that focus and the cooking dialog.
      event.preventDefault(); event.stopImmediatePropagation(); close(Boolean(panel.current?.contains(document.activeElement)));
    };
    document.addEventListener('pointerdown', outside); document.addEventListener('keydown', escape, true);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape, true); };
  }, [open, surfaceActive]);
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const anchor = trigger.current?.getBoundingClientRect(), element = panel.current;
      if (!anchor || !element) return;
      const viewport = window.visualViewport, width = viewport?.width || innerWidth, height = viewport?.height || innerHeight;
      const x = viewport?.offsetLeft || 0, y = viewport?.offsetTop || 0, popupWidth = Math.min(380, width - 24);
      const scene = root.current?.closest('.react-cooking-focus')?.getBoundingClientRect();
      const top = Math.max(y + 12, Math.min(anchor.bottom + 8, y + height - element.getBoundingClientRect().height - 12));
      setPosition({ width: popupWidth, maxHeight: y + height - top - 12, left: Math.max(x + 12, Math.min(anchor.right - popupWidth, x + width - popupWidth - 12)) - (scene?.left || 0), top: top - (scene?.top || 0) });
    };
    place(); window.addEventListener('resize', place); window.visualViewport?.addEventListener('resize', place); window.visualViewport?.addEventListener('scroll', place);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(place);
    if (panel.current) observer?.observe(panel.current);
    return () => { observer?.disconnect(); window.removeEventListener('resize', place); window.visualViewport?.removeEventListener('resize', place); window.visualViewport?.removeEventListener('scroll', place); };
  }, [open, creating, sessionId]);
  useEffect(() => {
    if (!open) return;
    if (focusOnOpen.current) { panel.current?.querySelector<HTMLElement>(creating ? 'input:not(:disabled)' : 'button:not(:disabled)')?.focus({ preventScroll: true }); focusOnOpen.current = false; }
    const row = [...(panel.current?.querySelectorAll<HTMLElement>('[data-timer-row]') || [])].find(row => row.dataset.timerRow === highlight);
    const body = panel.current?.querySelector<HTMLElement>('.timer-popup-body');
    if (row && body) body.scrollTop = Math.max(0, body.scrollTop + row.getBoundingClientRect().top - body.getBoundingClientRect().top);
  }, [open, highlight, creating]);
  const openSession = (targetId: string) => { if (onOpenSession) onOpenSession(targetId); else void chefStore.openSession(targetId).catch(() => {}); close(); };
  const timers = workspaceTimers(entry?.views || []), visible = entry ? visibleTimers(timers, entry.acknowledged, entry.clock()) : [];
  const first = visible[0], display = first && timerPresentation(first, entry!.clock());
  const reminders = state.reminders.filter(item => item.sessionId !== sessionId);
  if (!visible.length && !reminders.length && entry?.snapshot?.stage !== 'cooking') return null;
  const view = entry?.views.find(view => view.kind === 'timers') || { id: '', kind: 'timers', revision: entry?.snapshot?.revision ?? 0, editable: true, data: { timers } };
  const { limit, active } = timerCapacity(entry?.snapshot, entry?.views || [], entry?.clock() || Date.now());
  return <div className="react-kitchen-timers" ref={root}>
    <button type="button" ref={trigger} className="timer-trigger" data-state={display?.state || (reminders.length ? 'expired' : 'empty')} aria-label={first ? `厨房计时：${first.label}，${display!.clock}，${display!.status}` : reminders.length ? '查看其他会话的到时提醒' : '打开厨房计时'} aria-expanded={open} aria-controls={id} onClick={() => { focusOnOpen.current = !open; setOpen(!open); if (!open) setCreating(false); }}><Icon name="timer" /><span className="timer-trigger-clock">{display?.clock || (reminders.length ? '时间到' : '计时')}</span>{visible.length > 1 && <span className="timer-trigger-count">{visible.length}</span>}{reminders.length > 0 && <span className="timer-trigger-count">!</span>}</button>
    {open && <section id={id} ref={panel} style={position} className="react-timer-popover" role="dialog" aria-label="厨房计时">
      <header className="timer-popup-heading"><h3>{creating ? '新建计时' : '厨房计时'}</h3><div><Button className="timer-popup-button" aria-label={creating ? '返回计时列表' : '添加计时器'} disabled={!creating && (!entry || entry.snapshot?.stage !== 'cooking' || entry.saving || active >= limit)} onClick={() => { focusOnOpen.current = true; setCreating(!creating); }}><Icon name={creating ? 'arrow-left' : 'plus'} /></Button><Button className="timer-popup-button" aria-label="关闭计时浮层" onClick={() => close(true)}><Icon name="x" /></Button></div></header>
      <div className="timer-popup-body">{entry && sessionId && <WorkspaceError sessionId={sessionId} entry={entry} />}
      {creating && entry && sessionId ? <TimerForm view={view} ctx={{ sessionId, entry }} onStarted={() => setCreating(false)} /> : <div className="timer-popup-list">{visible.length && entry && sessionId ? visible.map(timer => <div className={highlight === timer.id ? 'timer-highlight' : ''} key={timer.id}><TimerRow timer={timer} view={view} ctx={{ sessionId, entry }} /></div>) : <p className="timer-popup-empty">还没有进行中的计时。</p>}{reminders.map(item => <article className="timer-popup-row" data-state="expired" key={`${item.sessionId}:${item.timer.id}`}><div className="timer-popup-copy"><strong>{item.timer.label}时间到</strong><p>另一个会话的计时提醒</p></div><div className="timer-popup-actions"><Button className="timer-popup-button" onClick={() => openSession(item.sessionId)}>查看会话</Button><Button className="timer-popup-button" onClick={() => workspaceStore.acknowledge(item.sessionId, item.timer.id)}>知道了</Button></div></article>)}</div>}</div>
      {entry && <p className="timer-popup-policy">{active >= limit ? `已有 ${active} / ${limit} 个计时，请先结束已有计时` : `最多 ${limit} 个同时计时`} · {entry.snapshot?.cooking_settings?.clear_timer_on_step_change ? '换步骤自动清除' : '换步骤保留计时'}</p>}
    </section>}
  </div>;
}
