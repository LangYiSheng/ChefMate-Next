import {useCallback, useEffect, useRef, useState, type ReactNode} from 'react';
import {api, bindHttpAccount, HttpError} from '../../runtime/http';
import {chefStore} from '../../runtime/store';
import {workspaceStore} from '../workspace/store';
import {applyAppearance} from '../settings/appearance';
import {resetUiPreferenceCache} from '../preferences/client';
import {Brand, Icon} from '../../components/Icon';
import {Onboarding} from './Onboarding';
import type {AccountSession, AccountUser} from './types';
import './account.css';

const ACCOUNT_KEY = 'chefmate:browser-account';
let fallbackBrowserAccount: string | null = null;
let browserAccountKnown = false;
let browserSwitchQueue: Promise<void> = Promise.resolve();

/** Remove account-owned browser state before another account can mount the workspace. */
export function switchBrowserAccount(nextId: string | null, valid = () => true) {
  const task = browserSwitchQueue.then(() => resetBrowserAccount(nextId, valid));
  browserSwitchQueue = task.catch(() => undefined);
  return task;
}
async function resetBrowserAccount(nextId: string | null, valid: () => boolean) {
  if (!valid()) return;
  let previous: string | null = fallbackBrowserAccount;
  try {previous = localStorage.getItem(ACCOUNT_KEY);} catch { /* Storage is optional. */ }
  const memoryChanged = browserAccountKnown && fallbackBrowserAccount !== nextId;
  if (previous === nextId && !memoryChanged) {fallbackBrowserAccount = nextId; browserAccountKnown = true; return;}
  await chefStore.resetForAccount(nextId);
  fallbackBrowserAccount = nextId; browserAccountKnown = true;
  workspaceStore.resetForAccount();
  resetUiPreferenceCache();
  if (!valid()) return;
  try {
    if (previous !== nextId) {
      for (const key of Object.keys(localStorage)) if (key.startsWith('chefmate:')) localStorage.removeItem(key);
      if (nextId) localStorage.setItem(ACCOUNT_KEY, nextId);
    }
  } catch { /* Cleared in-memory stores remain isolated when storage is disabled. */ }
  applyAppearance({});
  fallbackBrowserAccount = nextId;
  browserAccountKnown = true;
}

type Props = {children: (user: AccountUser, refresh: () => Promise<void>, logout: () => Promise<void>) => ReactNode};
export function AccountGate({children}: Props) {
  const [session, setSession] = useState<AccountSession | null>(null), [error, setError] = useState(''), [expired, setExpired] = useState(false);
  const [checking, setChecking] = useState(true), [switching, setSwitching] = useState(false);
  const alive = useRef(true), epoch = useRef(0), refreshSequence = useRef(0), userRef = useRef<AccountUser | null>(null), verifyingSession = useRef(false);
  const accept = useCallback(async (next: AccountSession) => {
    const version = ++epoch.current;
    if (userRef.current?.id !== next.user?.id) {
      setSwitching(true); setSession(null); userRef.current = null;
    }
    await switchBrowserAccount(next.user?.id || null, () => alive.current && version === epoch.current);
    if (!alive.current || version !== epoch.current) return;
    bindHttpAccount(next.user?.id || null);
    userRef.current = next.user; setSession(next); setError(''); setChecking(false); setSwitching(false); if (next.user) setExpired(false);
  }, []);
  const refresh = useCallback(async () => {
    const version = epoch.current, sequence = ++refreshSequence.current;
    let accepting = false;
    try {
      const next = await api<AccountSession>('/api/auth/me');
      if (!alive.current || version !== epoch.current || sequence !== refreshSequence.current) throw new DOMException('账号刷新已被新的读取替代。', 'AbortError');
      accepting = true; await accept(next);
    } catch (reason) {
      if (!alive.current || sequence !== refreshSequence.current || !accepting && version !== epoch.current) throw new DOMException('账号刷新已被新的读取替代。', 'AbortError');
      if ((reason as Error).name !== 'AbortError') {setError((reason as Error).message); setChecking(false);}
      throw reason;
    }
  }, [accept]);
  const logout = useCallback(async () => {
    await api('/api/auth/logout', {method: 'POST'});
    await accept({user: null, registration_enabled: false});
    history.replaceState({}, '', '/');
    await refresh().catch(() => undefined);
  }, [accept, refresh]);
  useEffect(() => {
    alive.current = true; void refresh().catch(() => undefined);
    const invalid = () => {
      if (!userRef.current || verifyingSession.current) return;
      verifyingSession.current = true; epoch.current++; setExpired(true); setSession(null); setChecking(true);
      void refresh().catch(() => undefined).finally(() => {verifyingSession.current = false;});
    };
    const storage = (event: StorageEvent) => {
      if (event.key !== ACCOUNT_KEY) return;
      epoch.current++; setSession(null); setChecking(true); userRef.current = null; chefStore.dispose();
      void refresh().catch(() => undefined);
    };
    const visible = () => {if (document.visibilityState !== 'hidden') void refresh().catch(() => undefined);};
    window.addEventListener('chefmate:session-expired', invalid); window.addEventListener('storage', storage); window.addEventListener('focus', visible);
    return () => {alive.current = false; epoch.current++; window.removeEventListener('chefmate:session-expired', invalid); window.removeEventListener('storage', storage); window.removeEventListener('focus', visible);};
  }, [accept, refresh]);
  if (checking || switching) return <main className="account-loading" aria-label="正在打开 ChefMate"><Brand className="account-brand-mark"/><p role="status">{switching ? '正在整理你的厨房…' : '正在打开小锅搭子…'}</p></main>;
  if (error && !session) return <main className="account-loading"><Brand className="account-brand-mark"/><h1>厨房暂时没有连上</h1><p role="alert">{error}</p><button className="primary-button" onClick={() => {setChecking(true); void refresh().catch(() => undefined);}}>重新连接</button></main>;
  if (!session?.user) return <AuthScreen registrationEnabled={session?.registration_enabled !== false} expired={expired} onAccepted={async user => {setExpired(false); await accept({user, registration_enabled: session?.registration_enabled});}}/>;
  if (!session.user.onboarding_completed) return <Onboarding user={session.user} onComplete={user => void accept({...session, user})} onLogout={logout}/>;
  return <>{children(session.user, refresh, logout)}</>;
}

function AuthScreen({registrationEnabled, expired, onAccepted}: {registrationEnabled: boolean; expired: boolean; onAccepted: (user: AccountUser) => Promise<void>}) {
  const [mode, setMode] = useState<'login' | 'register'>('login'), [username, setUsername] = useState(''), [password, setPassword] = useState(''), [confirm, setConfirm] = useState('');
  const [showPassword, setShowPassword] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const working = useRef(false), alive = useRef(true);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  useEffect(() => {if (!registrationEnabled) {setMode('login'); setPassword(''); setConfirm(''); setError('');}}, [registrationEnabled]);
  async function submit() {
    if (working.current) return;
    if (mode === 'register' && password !== confirm) {setError('两次输入的密码不一致，再核对一下吧。'); return;}
    working.current = true; setBusy(true); setError('');
    try {
      const result = await api<AccountSession>(`/api/auth/${mode}`, {method: 'POST', body: JSON.stringify({username: username.trim(), password})});
      if (!result.user) throw new Error('登录信息没有返回，请重新登录。');
      if (alive.current) {setPassword(''); setConfirm(''); await onAccepted(result.user);}
    } catch (reason) {if (alive.current) setError(reason instanceof HttpError && reason.status === 429 ? '试得有点快啦，请稍等片刻再试。' : (reason as Error).message);}
    finally {working.current = false; if (alive.current) setBusy(false);}
  }
  return <main className="account-entry">
    <section className="account-story" aria-label="认识 ChefMate"><a className="account-wordmark" href="/" aria-label="ChefMate 首页"><Brand/><span>ChefMate</span></a>
      <div className="account-story-body"><div className="account-welcome-art" aria-hidden="true"><span className="account-art-orbit orbit-one"/><span className="account-art-orbit orbit-two"/><Brand className="account-hero-mark"/><span className="account-art-note"><Icon name="leaf"/>一点新鲜灵感</span><span className="account-art-note note-second"><Icon name="timer"/>一顿刚好的饭</span></div>
        <p className="account-eyebrow">你和小锅搭子的厨房</p><h1>从今晚吃什么，<br/>到一起把饭做好。</h1><p>找菜谱、备好料、照看锅里的时间。<br/>日常的一餐，也值得好好对待。</p>
        <div className="account-story-features"><span><Icon name="book-open"/>收好拿手菜</span><span><Icon name="utensils"/>记住你的口味</span><span><Icon name="timer"/>陪你一步步做</span></div></div>
      <p className="account-story-footer">把灵感留在这里，把热气腾腾带上桌。</p>
    </section>
    <section className="account-entry-form"><div className="account-form-sheet" key={mode}><span className="account-eyebrow">{mode === 'login' ? '好久不见，开饭啦' : '给自己留一个厨房的位置'}</span><h2>{mode === 'login' ? '欢迎回来' : '认识一下吧'}</h2><p className="account-form-intro">{mode === 'login' ? '登录后，接着上次的灵感继续。' : '注册账号，开始和小锅搭子一起做饭。'}</p>
      {expired && <p className="account-notice" role="status">登录已过期，请重新登录。已保存的内容还在。</p>}
      <form onSubmit={event => {event.preventDefault(); void submit();}} aria-label={mode === 'login' ? '账号登录' : '注册账号'}>
        <label className="account-field"><span>账号</span><input aria-label="账号" name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} required minLength={3} maxLength={32} pattern="[a-zA-Z0-9_.-]+" title="3–32 位英文字母、数字、点号、下划线或短横线" value={username} onChange={event => {setUsername(event.target.value); setError('');}} disabled={busy} placeholder="给你的厨房起个账号"/>{mode === 'register' && <small>3–32 位字母、数字、点号、下划线或短横线</small>}</label>
        <div className="account-field"><label htmlFor="auth-password">密码</label><div className="account-password"><input id="auth-password" name="password" type={showPassword ? 'text' : 'password'} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} required minLength={mode === 'register' ? 10 : undefined} maxLength={128} value={password} onChange={event => {setPassword(event.target.value); setError('');}} disabled={busy} placeholder={mode === 'register' ? '至少 10 位，长一些更安心' : '输入你的密码'}/><button type="button" className="icon-button" aria-label={showPassword ? '隐藏密码' : '显示密码'} aria-pressed={showPassword} disabled={busy} onClick={() => setShowPassword(!showPassword)}><Icon name={showPassword ? 'eye-off' : 'eye'}/></button></div></div>
        {mode === 'register' && <label className="account-field"><span>再确认一次密码</span><input aria-label="再确认一次密码" name="confirm-password" type={showPassword ? 'text' : 'password'} autoComplete="new-password" required minLength={10} maxLength={128} value={confirm} onChange={event => {setConfirm(event.target.value); setError('');}} disabled={busy}/></label>}
        {error && <p className="error-box" role="alert">{error}</p>}
        <button type="submit" className="primary-button account-submit" disabled={busy}>{busy ? <><Icon name="loader-circle" className="spin"/>正在{mode === 'login' ? '登录' : '注册'}…</> : <>{mode === 'login' ? '回到我的厨房' : '创建账号'}<Icon name="arrow-right"/></>}</button>
      </form>
      {registrationEnabled && <p className="account-switch">{mode === 'login' ? '第一次来？' : '已经有账号了？'}<button className="text-button" type="button" disabled={busy} onClick={() => {setMode(mode === 'login' ? 'register' : 'login'); setPassword(''); setConfirm(''); setError('');}}>{mode === 'login' ? '注册一个账号' : '去登录'}</button></p>}
      <p className="account-footnote">账号与密码由当前 ChefMate 服务保存。<br/>忘记密码时，请联系这套服务的管理员。</p>
    </div></section>
  </main>;
}
