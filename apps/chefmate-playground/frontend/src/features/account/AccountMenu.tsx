import {useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent} from 'react';
import {createPortal} from 'react-dom';
import {Icon} from '../../components/Icon';
import {api} from '../../runtime/http';
import type {AccountQuota, AccountUser} from './types';
import {QuotaOverview} from './QuotaOverview';
import './account-menu.css';

export function AccountMenu({account, onPreferences, onProfile, onQuota, onSettings, onAdmin, onLogout}: {
  account: AccountUser; onPreferences: () => void; onProfile: () => void; onQuota: () => void;
  onSettings: () => void; onAdmin: () => void; onLogout: () => void;
}) {
  const [open, setOpen] = useState(false), [quota, setQuota] = useState(account.quota), [loading, setLoading] = useState(false), [error, setError] = useState('');
  const [position, setPosition] = useState({left: 12, top: 12});
  const trigger = useRef<HTMLButtonElement>(null), panel = useRef<HTMLDivElement>(null), id = useId();
  const owner = useRef(account.id), alive = useRef(true); owner.current = account.id;
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  const refreshQuota = useCallback(async () => {
    const current = await api<{quota: AccountQuota}>('/api/auth/quota');
    if (!current?.quota) throw new Error('暂时无法读取额度概览。');
    if (alive.current && owner.current === account.id) setQuota(current.quota);
  }, [account.id]);
  const close = (restore = true) => {setOpen(false); if (restore) trigger.current?.focus({preventScroll: true});};
  useEffect(() => {setQuota(account.quota);}, [account.id, account.quota]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController(); setLoading(true); setError('');
    api<{quota: AccountQuota}>('/api/auth/quota', {signal: controller.signal}).then(value => {
      if (!controller.signal.aborted) {if (!value?.quota) throw new Error('暂时无法读取额度概览。'); setQuota(value.quota);}
    }).catch(problem => {if (!controller.signal.aborted) setError(problem.message);}).finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
  }, [open, account.id]);
  useLayoutEffect(() => {
    if (!open || !trigger.current || !panel.current) return;
    const anchor = trigger.current.getBoundingClientRect(), bounds = panel.current.getBoundingClientRect();
    const width = bounds.width || 300, height = bounds.height || 424;
    const beside = anchor.right + width + 20 <= innerWidth;
    setPosition({left: Math.max(10, Math.min(beside ? anchor.right + 10 : anchor.left, innerWidth - width - 10)), top: Math.max(10, Math.min(beside ? anchor.bottom - height : anchor.top - height - 10, innerHeight - height - 10))});
  }, [open, quota, loading, error]);
  useLayoutEffect(() => {if (open) panel.current?.querySelector<HTMLButtonElement>('button')?.focus({preventScroll: true});}, [open]);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {if (!panel.current?.contains(event.target as Node) && !trigger.current?.contains(event.target as Node)) close(false);};
    const resized = () => close(false);
    document.addEventListener('pointerdown', outside); window.addEventListener('resize', resized);
    return () => {document.removeEventListener('pointerdown', outside); window.removeEventListener('resize', resized);};
  }, [open]);
  const choose = (action: () => void) => {close(false); action();};
  function keys(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); close(); return;}
    if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const buttons = [...(panel.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') || [])], index = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowUp' ? -1 : 1) + buttons.length) % buttons.length;
    buttons[next]?.focus();
  }
  return <>
    <button ref={trigger} type="button" className="settings-link sidebar-account-link" title={'账号菜单：' + (account.display_name || account.username)} aria-label={'账号菜单：' + (account.display_name || account.username)} aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen(value => !value)} onKeyDown={event => {if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {event.preventDefault(); setOpen(true);}}}><span className="sidebar-avatar" aria-hidden="true">{(account.display_name || account.username).slice(0, 1).toLocaleUpperCase()}</span><span>{account.display_name || account.username}</span><Icon name="chevron-right"/></button>
    {open && createPortal(<div ref={panel} id={id} className="account-popover" role="dialog" aria-label="账号快捷菜单" style={position} onKeyDown={keys} onBlur={event => {const next = event.relatedTarget as Node | null; if (next && !event.currentTarget.contains(next) && !trigger.current?.contains(next)) close(false);}}>
      <div className="account-popover-identity"><strong>{account.display_name || account.username}</strong><span>@{account.username}</span></div>
      <button type="button" className="account-usage-mini" onClick={() => choose(onQuota)} aria-label="查看我的额度"><QuotaOverview quota={quota} compact onRefresh={refreshQuota}/><span className="account-usage-mini-note">{loading ? '正在更新…' : error || '查看额度明细'}<Icon name="chevron-right"/></span></button>
      <div className="account-popover-divider"/>
      <nav aria-label="账号操作"><button type="button" onClick={() => choose(onPreferences)}><Icon name="sliders-horizontal"/>个人偏好</button><button type="button" onClick={() => choose(onProfile)}><Icon name="user-round"/>个人资料</button><button type="button" onClick={() => choose(onSettings)}><Icon name="settings"/>设置</button>{account.role === 'admin' && <button type="button" onClick={() => choose(onAdmin)}><Icon name="shield-check"/>管理平台</button>}</nav>
      <div className="account-popover-divider"/>
      <button type="button" className="account-popover-logout" onClick={() => choose(onLogout)}><Icon name="log-out"/>退出账户</button>
    </div>, document.body)}
  </>;
}
