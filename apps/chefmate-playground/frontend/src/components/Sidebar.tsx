import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import { chefStore } from '../runtime/store';
import type { RuntimeState, Session } from '../runtime/types';
import { Brand, Icon } from './Icon';
import { Dialog } from './Dialog';

const stages: Record<string, string> = { idea: '闲聊', planning: '正在选菜', prep: '备菜', shopping: '备菜', cooking: '烹饪', completed: '已完成' };
type Page = 'chat' | 'settings' | 'recipes' | 'journal';
export function Sidebar({ state, page, collapsed, onCollapse, onClose, navigate, notify, onInspect, developer }: {
  state: RuntimeState; collapsed: boolean; onCollapse: (value: boolean) => void; onClose: () => void;
  page: Page; navigate: (page: Page, after?: () => void) => void; notify: (text: string) => void; onInspect: () => void;
  developer: boolean;
}) {
  const [menu, setMenu] = useState<Session | null>(null);
  const [action, setAction] = useState<'rename' | 'delete' | null>(null);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [position, setPosition] = useState({ left: 12, top: 12 });
  const menuPanel = useRef<HTMLDivElement>(null);
  const menuTrigger = useRef<HTMLButtonElement | null>(null);
  const newChat = useRef<HTMLButtonElement>(null);
  const latest = state.session?.messages?.findLast(message => message.role === 'assistant');
  const context = latest?.context_usage;
  const selected = menu ? state.sessions.find(session => session.id === menu.id) || menu : null;
  const selectedRunning = Boolean(selected && (selected.running || state.runs[selected.id] || state.cache[selected.id]?.running));
  function close(restoreFocus = true) {
    setMenu(null); setAction(null); setError('');
    if (restoreFocus) {
      const restore = () => (menuTrigger.current?.isConnected ? menuTrigger.current : newChat.current)?.focus({ preventScroll: true });
      if (action) requestAnimationFrame(restore); else restore();
    }
  }
  function openMenu(session: Session, trigger: HTMLButtonElement) {
    if (busy) return;
    if (menu?.id === session.id && !action) { close(); return; }
    menuTrigger.current = trigger; setMenu(session); setAction(null); setError('');
  }
  useLayoutEffect(() => {
    if (!menu || action || !menuTrigger.current || !menuPanel.current) return;
    const anchor = menuTrigger.current.getBoundingClientRect(), bounds = menuPanel.current.getBoundingClientRect();
    const width = bounds.width || 170, height = bounds.height || 138;
    setPosition({ left: Math.max(12, Math.min(anchor.right - width, innerWidth - width - 12)),
      top: Math.max(12, anchor.bottom + 6 + height <= innerHeight - 12 ? anchor.bottom + 6 : anchor.top - height - 6) });
    menuPanel.current.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
  }, [menu?.id, action]);
  useEffect(() => {
    if (!menu || action) return;
    const outside = (event: PointerEvent) => {
      if (!busy && !menuPanel.current?.contains(event.target as Node) && !menuTrigger.current?.contains(event.target as Node)) close(false);
    };
    const changed = (event: Event) => {
      if (!busy && (!(event.target instanceof Node) || !menuPanel.current?.contains(event.target))) close(false);
    };
    document.addEventListener('pointerdown', outside); window.addEventListener('resize', changed); window.addEventListener('scroll', changed, true);
    return () => { document.removeEventListener('pointerdown', outside); window.removeEventListener('resize', changed); window.removeEventListener('scroll', changed, true); };
  }, [menu?.id, action, busy]);
  function menuKeys(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); if (!busy) close(); return; }
    if (event.key === 'Tab') { if (!busy) close(); return; }
    if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const buttons = [...(menuPanel.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') || [])];
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
      : (index + (event.key === 'ArrowUp' ? -1 : 1) + buttons.length) % buttons.length;
    buttons[next]?.focus();
  }
  async function pin() {
    if (!selected || busy) return;
    setBusy(true); setError('');
    try { await chefStore.pinSession(selected.id, !selected.pinned); close(); }
    catch (caught) { const message = (caught as Error).message; setError(message); notify(message); }
    finally { setBusy(false); }
  }
  async function save() {
    if (!selected || busy || action === 'delete' && selectedRunning) return;
    setBusy(true); setError('');
    try {
      if (action === 'rename') await chefStore.renameSession(selected.id, name.trim());
      else await chefStore.deleteSession(selected.id);
      close();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <>
    <div className="brand-row"><button type="button" className="brand brand-button" onClick={() => navigate('chat', () => chefStore.newSession())} aria-label="ChefMate 首页">
      <Brand /><span className="brand-copy"><strong>ChefMate</strong><small>您的个性化烹饪助手</small></span></button>
      <button type="button" className="rail-brand" title="展开侧栏" aria-label="展开侧栏" onClick={() => onCollapse(false)}><Brand /></button>
      <button className="icon-button desktop-only" type="button" onClick={() => onCollapse(!collapsed)} aria-label={collapsed ? '展开侧栏' : '收起侧栏'}><Icon name={collapsed ? 'panel-left-open' : 'panel-left-close'} /></button>
      <button className="icon-button mobile-only" type="button" onClick={onClose} aria-label="收起会话导航"><Icon name="x" /></button></div>
    <nav className="primary-navigation" aria-label="厨房空间">
      <button ref={newChat} type="button" className="new-chat" title="新对话" aria-current={page === 'chat' ? 'page' : undefined} onClick={() => navigate('chat', () => chefStore.newSession())}><Icon name="plus" /><span className="new-chat-label">新对话</span></button>
      <button type="button" title="食光手记" aria-current={page === 'journal' ? 'page' : undefined} onClick={() => navigate('journal')}><Icon name="utensils" /><span>食光手记</span></button>
      <button type="button" title="菜谱中心" aria-current={page === 'recipes' ? 'page' : undefined} onClick={() => navigate('recipes')}><Icon name="book-open" /><span>菜谱中心</span></button>
    </nav><div className="sidebar-heading">最近会话</div>
    <nav className="sessions" aria-label="最近会话">{state.sessions.map(session => {
      const running = Boolean(state.runs[session.id] || session.running);
      const waiting = !running && session.awaiting_user;
      return <div key={session.id} className={`session-row ${session.id === state.currentId ? 'active' : ''} ${waiting ? 'awaiting' : ''}`}>
        <button type="button" className="session-link" title={session.title || '新对话'} aria-current={page === 'chat' && session.id === state.currentId ? 'page' : undefined}
          onClick={() => navigate('chat', () => { void chefStore.openSession(session.id).catch(error => notify(error.message)); })}><span className="session-title">{session.title || '新对话'}</span>
          <small className="session-stage">{waiting ? '等你回答' : stages[session.conversation_state?.stage] || '闲聊'}</small></button>
        {running && <span className="session-running" aria-label="正在执行" />}{waiting && <span className="session-awaiting" aria-label="有问题等你回答" />}
        {session.pinned && <span className="session-pinned" aria-label="已置顶"><Icon name="star" /></span>}
        <button type="button" className="session-more" aria-label={`对话操作：${session.title || '新对话'}`} aria-haspopup="menu" aria-controls={menu?.id === session.id && !action ? 'session-menu' : undefined} aria-expanded={menu?.id === session.id && !action}
          onClick={event => openMenu(session, event.currentTarget)} onKeyDown={event => { if (['ArrowDown', 'ArrowUp'].includes(event.key)) { event.preventDefault(); openMenu(session, event.currentTarget); } }}><Icon name="ellipsis" /></button>
      </div>;
    })}{!state.sessions.length && !state.sessionsLoading && !state.sessionsError && <div className="session-empty">从一个新对话开始吧。</div>}
      {state.sessionsError ? <div className="session-error" role="alert">{state.sessionsError}<button type="button" disabled={state.sessionsLoading} onClick={() => void (state.nextCursor ? chefStore.moreSessions() : chefStore.loadSessions()).catch(error => notify(error.message))}>{state.sessionsLoading ? '正在重试…' : '加载失败，重试'}</button></div>
        : (state.nextCursor || state.sessionsLoading) && <button type="button" className="session-load-more" disabled={state.sessionsLoading} onClick={() => void chefStore.moreSessions().catch(error => notify(error.message))}>{state.sessionsLoading ? '正在读取…' : '加载更多对话'}</button>}
    </nav>
    {developer && context && <details className="context-meter"><summary>本次上下文 <strong>≈ {Number(context.estimated_tokens || 0).toLocaleString()} tokens</strong></summary>
      <div className="context-meter-body"><dl>{context.categories?.map((row: Record<string, any>) => <div key={row.key}><dt>{row.label || row.key}</dt><dd>≈ {row.estimated_tokens ?? '—'}</dd></div>)}</dl>
        <p className="context-meter-caption">分类数值为估算 Token，不含图片与服务端封装。</p><button type="button" onClick={onInspect}>查看请求轨迹</button></div></details>}
    <div className="sidebar-footer"><button type="button" className="settings-link" title="设置" aria-current={page === 'settings' ? 'page' : undefined} onClick={() => navigate('settings')}><Icon name="settings" /><span>设置</span></button></div>
    {selected && !action && createPortal(<div ref={menuPanel} id="session-menu" className="session-menu" role="menu" aria-label={`对话操作：${selected.title || '新对话'}`} aria-busy={busy} style={position} onKeyDown={menuKeys}>
      <button type="button" role="menuitem" disabled={busy} onClick={() => { setAction('rename'); setName(selected.title || '新对话'); setError(''); }}><Icon name="copy" />重命名</button>
      <button type="button" role="menuitem" disabled={busy} onClick={() => void pin()}><Icon name="star" />{busy ? '正在保存…' : selected.pinned ? '取消置顶' : '置顶'}</button>
      <button type="button" role="menuitem" className="delete-action" disabled={busy || selectedRunning} onClick={() => { setAction('delete'); setError(''); }}><Icon name="trash-2" />删除</button>
      {error && <p className="session-error" role="alert">{error}</p>}
    </div>, document.body)}
    {selected && action && <Dialog title={action === 'rename' ? '重命名对话' : '删除这段对话？'} onClose={() => { if (!busy) close(); }}>
        <form onSubmit={event => { event.preventDefault(); void save(); }}><p>{action === 'rename' ? '给这段对话起个方便查找的名字。' : '对话和聊天图片将被删除。个人菜谱、食光手记和长期记忆会保留。'}</p>
          {action === 'rename' && <input className="ui-input" aria-label="对话名称" value={name} maxLength={80} required disabled={busy} onChange={event => setName(event.target.value)} />}
          {error && <p className="error-box" role="alert">{error}</p>}<div className="session-dialog-actions"><button type="button" disabled={busy} onClick={() => close()}>取消</button><button className="primary-button" disabled={busy || action === 'rename' && !name.trim() || action === 'delete' && selectedRunning} type="submit">{busy ? '正在保存…' : action === 'rename' ? '保存名称' : '删除对话'}</button></div></form>
    </Dialog>}
  </>;
}
