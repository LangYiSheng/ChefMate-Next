import { useState } from 'react';
import { chefStore } from '../runtime/store';
import type { RuntimeState, Session } from '../runtime/types';
import { Brand, Icon } from './Icon';
import { Dialog } from './Dialog';

const stages: Record<string, string> = { idea: '闲聊', planning: '正在选菜', prep: '备菜', shopping: '备菜', cooking: '烹饪', completed: '已完成' };
export function Sidebar({ state, collapsed, onCollapse, onClose, navigate, notify, onInspect, developer }: {
  state: RuntimeState; collapsed: boolean; onCollapse: (value: boolean) => void; onClose: () => void;
  navigate: (page: 'chat' | 'settings' | 'recipes' | 'journal', after?: () => void) => void; notify: (text: string) => void; onInspect: () => void;
  developer: boolean;
}) {
  const [menu, setMenu] = useState<Session | null>(null);
  const [action, setAction] = useState<'rename' | 'delete' | null>(null);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const latest = state.session?.messages?.findLast(message => message.role === 'assistant');
  const context = latest?.context_usage;
  function close() { if (!busy) { setMenu(null); setAction(null); setError(''); } }
  async function save() {
    if (!menu || busy) return;
    setBusy(true); setError('');
    try {
      if (action === 'rename') await chefStore.renameSession(menu.id, name.trim());
      else await chefStore.deleteSession(menu.id);
      setMenu(null); setAction(null);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <>
    <div className="brand-row"><button type="button" className="brand brand-button" onClick={() => navigate('chat', () => chefStore.newSession())} aria-label="ChefMate 首页">
      <Brand /><span className="brand-copy"><strong>ChefMate</strong><small>您的个性化烹饪助手</small></span></button>
      <button className="icon-button desktop-only" type="button" onClick={() => onCollapse(!collapsed)} aria-label={collapsed ? '展开侧栏' : '收起侧栏'}><Icon name={collapsed ? 'panel-left-open' : 'panel-left-close'} /></button>
      <button className="icon-button mobile-only" type="button" onClick={onClose} aria-label="收起会话导航"><Icon name="x" /></button></div>
    <nav className="primary-navigation" aria-label="厨房空间">
      <button type="button" className="new-chat" title="新对话" onClick={() => navigate('chat', () => chefStore.newSession())}><Icon name="plus" /><span className="new-chat-label">新对话</span></button>
      <button type="button" title="食光手记" onClick={() => navigate('journal')}><Icon name="utensils" /><span>食光手记</span></button>
      <button type="button" title="菜谱中心" onClick={() => navigate('recipes')}><Icon name="book-open" /><span>菜谱中心</span></button>
    </nav><div className="sidebar-heading">最近会话</div>
    <nav className="sessions" aria-label="最近会话">{state.sessions.map(session => {
      const running = Boolean(state.runs[session.id] || session.running);
      const waiting = !running && session.awaiting_user;
      return <div key={session.id} className={`session-row ${session.id === state.currentId ? 'active' : ''} ${waiting ? 'awaiting' : ''}`}>
        <button type="button" className="session-link" title={session.title || '新对话'} aria-current={session.id === state.currentId ? 'page' : undefined}
          onClick={() => navigate('chat', () => { void chefStore.openSession(session.id).catch(error => notify(error.message)); })}>{session.title || '新对话'}
          <small className="session-stage">{waiting ? '等你回答' : stages[session.conversation_state?.stage] || '闲聊'}</small></button>
        {running && <span className="session-running" aria-label="正在执行" />}{waiting && <span className="session-awaiting" aria-label="有问题等你回答" />}
        {session.pinned && <span className="session-pinned" aria-label="已置顶"><Icon name="star" /></span>}
        <button type="button" className="session-more" aria-label={`对话操作：${session.title || '新对话'}`} onClick={() => { setMenu(session); setError(''); }}><Icon name="ellipsis" /></button>
      </div>;
    })}{!state.sessions.length && !state.sessionsLoading && <div className="session-empty">从一个新对话开始吧。</div>}
      {(state.nextCursor || state.sessionsLoading) && <button type="button" className="session-load-more" disabled={state.sessionsLoading} onClick={() => void chefStore.moreSessions().catch(error => notify(error.message))}>{state.sessionsLoading ? '正在读取…' : '加载更多对话'}</button>}
    </nav>
    {developer && context && <details className="context-meter"><summary>本次上下文 <strong>≈ {Number(context.estimated_tokens || 0).toLocaleString()} tokens</strong></summary>
      <div className="context-meter-body"><dl>{context.categories?.map((row: Record<string, any>) => <div key={row.key}><dt>{row.label || row.key}</dt><dd>≈ {row.estimated_tokens ?? '—'}</dd></div>)}</dl>
        <p className="context-meter-caption">分类数值为估算 Token，不含图片与服务端封装。</p><button type="button" onClick={onInspect}>查看请求轨迹</button></div></details>}
    <div className="sidebar-footer"><button type="button" className="settings-link" title="设置" onClick={() => navigate('settings')}><Icon name="settings" /><span>设置</span></button></div>
    {menu && <Dialog title={action === 'rename' ? '重命名对话' : action === 'delete' ? '删除这段对话？' : '对话操作'} onClose={close}>
      {!action ? <div className="react-session-actions"><button type="button" onClick={() => { setAction('rename'); setName(menu.title || '新对话'); }}><Icon name="copy" />重命名</button>
        <button type="button" onClick={async () => { try { await chefStore.pinSession(menu.id, !menu.pinned); close(); } catch (e) { notify((e as Error).message); } }}><Icon name="star" />{menu.pinned ? '取消置顶' : '置顶'}</button>
        <button type="button" disabled={Boolean(menu.running || state.runs[menu.id])} onClick={() => setAction('delete')}><Icon name="trash-2" />删除</button><button type="button" onClick={close}>关闭</button></div> :
        <form onSubmit={event => { event.preventDefault(); void save(); }}><p>{action === 'rename' ? '给这段对话起个方便查找的名字。' : '对话和聊天图片将被删除。个人菜谱、食光手记和长期记忆会保留。'}</p>
          {action === 'rename' && <input className="ui-input" aria-label="对话名称" value={name} maxLength={80} required disabled={busy} onChange={event => setName(event.target.value)} />}
          {error && <p className="error-box" role="alert">{error}</p>}<div className="session-dialog-actions"><button type="button" disabled={busy} onClick={close}>取消</button><button className="primary-button" disabled={busy || action === 'rename' && !name.trim()} type="submit">{busy ? '正在保存…' : action === 'rename' ? '保存名称' : '删除对话'}</button></div></form>}
    </Dialog>}
  </>;
}
