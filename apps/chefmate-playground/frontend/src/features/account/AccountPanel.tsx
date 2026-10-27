import {useEffect, useRef, useState} from 'react';
import {api} from '../../runtime/http';
import {Icon} from '../../components/Icon';
import {Dialog} from '../../components/Dialog';
import type {AccountUser} from './types';
import {QuotaOverview} from './QuotaOverview';
import {AccountLeavePrompt, useAccountLeaveGuard} from './leave-guard';

export type AccountSection = 'profile' | 'quota' | 'security';
const noChange = () => {};
export function AccountPanel({user, refresh, notify, section = 'profile', active = true, embedded = false, onDirty = noChange, onBusy = noChange, onDirtySections = noChange}: {
  user: AccountUser; refresh: () => Promise<void>; notify: (message: string) => void; logout?: () => Promise<void>;
  section?: AccountSection; active?: boolean; embedded?: boolean; onDirty?: (dirty: boolean) => void; onBusy?: (busy: boolean) => void; onDirtySections?: (sections: Record<AccountSection, boolean>) => void;
}) {
  const [name, setName] = useState(user.display_name), [baseline, setBaseline] = useState(user.display_name);
  const [currentPassword, setCurrentPassword] = useState(''), [password, setPassword] = useState(''), [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [message, setMessage] = useState(''), [reload, setReload] = useState(false);
  const alive = useRef(true), working = useRef(false), refreshRef = useRef(refresh); refreshRef.current = refresh;
  const owner = useRef(user.id), profileDirty = name !== baseline, securityDirty = Boolean(currentPassword || password || confirm), dirty = profileDirty || securityDirty;
  const {pendingLeave, setPendingLeave} = useAccountLeaveGuard(!embedded && dirty, !embedded && Boolean(busy), setError);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  useEffect(() => {onDirty(dirty);}, [dirty, onDirty]);
  useEffect(() => {onDirtySections({profile: profileDirty, security: securityDirty, quota: false});}, [profileDirty, securityDirty, onDirtySections]);
  useEffect(() => {onBusy(Boolean(busy));}, [busy, onBusy]);
  useEffect(() => {
    if (owner.current !== user.id || !profileDirty || name === user.display_name) {setName(user.display_name); setBaseline(user.display_name); owner.current = user.id;}
  }, [user.id, user.display_name]);
  useEffect(() => {
    if (!active) return;
    void Promise.resolve(refreshRef.current()).catch(problem => {if (alive.current && problem.name !== 'AbortError') setError(problem.message);});
  }, [active, section, user.id]);
  async function run(kind: string, action: () => Promise<void>, success: string) {
    if (working.current) return;
    working.current = true; setBusy(kind); setError(''); setMessage('');
    try {await action(); if (alive.current) {setMessage(success); notify(success);}}
    catch (problem) {if (alive.current && (problem as Error).name !== 'AbortError') setError((problem as Error).message);}
    finally {working.current = false; if (alive.current) setBusy('');}
  }
  const readLatest = () => {setName(user.display_name); setBaseline(user.display_name); setReload(false); void run('refresh', refresh, '账号信息已更新。');};
  const heading = {profile: ['个人资料', '让小锅搭子用你喜欢的方式称呼你。'], quota: ['我的额度', '查看会员、基础额度与充值余额。'], security: ['账号安全', '管理登录密码，保护自己的厨房资料。']}[section];
  return <section className={'account-page account-settings-panel' + (embedded ? ' is-embedded' : '')} aria-label={heading[0]} hidden={!active}>
    {!embedded && <header className="account-page-heading"><div><h1>{heading[0]}</h1><p>{heading[1]}</p></div></header>}
    <div className="account-inline-toolbar"><button type="button" className="text-button" disabled={Boolean(busy)} onClick={() => profileDirty ? setReload(true) : readLatest()}><Icon name="refresh-cw"/>读取最新</button></div>
    {error && <p className="error-box" role="alert">{error}</p>}{message && <p className="account-success" role="status">{message}</p>}
    {section === 'profile' && <>
      <div className="account-profile-heading"><span className="account-avatar" aria-hidden="true">{(user.display_name || user.username).slice(0, 1).toLocaleUpperCase()}</span><div><h2>{user.display_name || user.username}</h2><p>@{user.username}<span className="ui-badge">{user.role === 'admin' ? '管理员' : '厨房伙伴'}</span></p></div></div>
      <form className="account-profile-form account-identity-form" aria-label="个人资料" onSubmit={event => {event.preventDefault(); void run('profile', async () => {
        const result = await api<{user: AccountUser}>('/api/auth/profile', {method: 'PATCH', body: JSON.stringify({display_name: name.trim()})});
        if (alive.current) {setName(result.user.display_name); setBaseline(result.user.display_name);} await refresh();
      }, '个人资料已保存。');}}>
        <label className="account-field"><span>称呼</span><input aria-label="称呼" value={name} required maxLength={40} onChange={event => setName(event.target.value)} disabled={Boolean(busy)}/><small>这个称呼会作为 ChefMate 对你的称呼告诉模型，不受个人偏好开关影响。</small></label>
        <dl className="account-identity-details"><div><dt>登录账号</dt><dd>{user.username}</dd></div><div><dt>加入厨房</dt><dd>{user.created_at ? new Date(user.created_at).toLocaleDateString('zh-CN') : '尚未记录'}</dd></div></dl>
        <div className="account-form-actions"><button className="primary-button" type="submit" disabled={Boolean(busy) || !name.trim() || !profileDirty}>{busy === 'profile' ? '正在保存…' : '保存资料'}</button></div>
      </form>
    </>}
    {section === 'quota' && <QuotaOverview quota={user.quota} onRefresh={refresh}/>}
    {section === 'security' && <form className="account-password-form" aria-label="更新密码" onSubmit={event => {event.preventDefault(); if (password !== confirm) {setError('两次新密码不一致，请重新核对。'); return;} void run('password', async () => {
      await api('/api/auth/password', {method: 'PATCH', body: JSON.stringify({current_password: currentPassword, new_password: password})});
      if (alive.current) {setCurrentPassword(''); setPassword(''); setConfirm('');} await refresh();
    }, '密码已更新。');}}>
      <div className="account-notice"><Icon name="shield-check"/><span>更新密码后，其他设备需要重新登录。</span></div>
      <label className="account-field"><span>当前密码</span><input type="password" autoComplete="current-password" value={currentPassword} required maxLength={128} onChange={event => setCurrentPassword(event.target.value)} disabled={Boolean(busy)}/></label>
      <label className="account-field"><span>新密码</span><input type="password" autoComplete="new-password" aria-label="新密码" value={password} required minLength={10} maxLength={128} onChange={event => setPassword(event.target.value)} disabled={Boolean(busy)}/><small>至少 10 位，请使用独立的密码。</small></label>
      <label className="account-field"><span>确认新密码</span><input type="password" autoComplete="new-password" value={confirm} required minLength={10} maxLength={128} onChange={event => setConfirm(event.target.value)} disabled={Boolean(busy)}/></label>
      <div className="account-form-actions"><button className="primary-button" type="submit" disabled={Boolean(busy)}>{busy === 'password' ? '正在更新…' : '更新密码'}</button></div>
    </form>}
    {!embedded && <AccountLeavePrompt action={pendingLeave} onCancel={() => setPendingLeave(null)}/>}
    {reload && <Dialog title="重新读取账号资料？" onClose={() => setReload(false)}><p>会放弃尚未保存的称呼，读取服务器中的最新资料。</p><div className="account-form-actions"><button type="button" className="secondary-button" onClick={() => setReload(false)}>继续编辑</button><button type="button" className="primary-button" onClick={readLatest}>放弃并读取</button></div></Dialog>}
  </section>;
}
