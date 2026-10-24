import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type CSSProperties } from 'react';
import { chefStore } from './runtime/store';
import { Sidebar } from './components/Sidebar';
import { Composer } from './components/Composer';
import { Conversation } from './components/Conversation';
import { Diagnostics, RecipeDetails } from './components/Details';
import { Icon } from './components/Icon';
import { DeveloperWatermark } from './components/DeveloperWatermark';
import { PageBoundary } from './components/PageBoundary';
import { initAppearance } from './features/settings/appearance';
import { CookingFocus, KitchenTimers, WorkspacePanel, initializeWorkspace } from './features/workspace';
import { workspaceStore } from './features/workspace/store';
import { viewChoices } from './features/workspace/protocol';
import { initializeUiPreferences } from './features/preferences/client';

type Page = 'chat' | 'settings' | 'recipes' | 'journal';
const loadSettings = () => import('./features/settings/Settings').then(module => ({ default: module.Settings }));
const loadCenters = () => import('./features/centers/KitchenCenters').then(module => ({ default: module.KitchenCenters }));
const developmentBase = import.meta.env.DEV ? import.meta.env.BASE_URL.replace(/\/$/, '') : '';
const pathFor = (page: Page) => `${developmentBase}${page === 'chat' ? '/' : `/${page}`}`;
const pageFor = (): Page => ({ '/settings': 'settings', '/recipes': 'recipes', '/journal': 'journal' })[location.pathname.slice(developmentBase.length)] as Page || 'chat';
function local<T>(key: string, fallback: T): T { try { return JSON.parse(localStorage.getItem(key) || 'null') ?? fallback; } catch { return fallback; } }

export default function App() {
  const state = useSyncExternalStore(chefStore.subscribe, chefStore.getSnapshot);
  const canFocus = useSyncExternalStore(workspaceStore.subscribe, () => {
    const entry = state.currentId ? workspaceStore.getSnapshot().entries[state.currentId] : undefined;
    return Boolean(entry && viewChoices(entry.views, entry.snapshot?.stage).some(view => view.kind !== 'timers'));
  });
  const [page, setPage] = useState<Page>(pageFor);
  const [pageAttempt, setPageAttempt] = useState(0);
  const Settings = useMemo(() => lazy(loadSettings), [pageAttempt]);
  const KitchenCenters = useMemo(() => lazy(loadCenters), [pageAttempt]);
  const [collapsed, setCollapsed] = useState(() => local('chefmate:sidebar-collapsed', false));
  const [mobileOpen, setMobileOpen] = useState(false);
  const [mobile, setMobile] = useState(() => window.matchMedia('(max-width: 760px)').matches);
  const [detail, setDetail] = useState(false);
  const [tab, setTab] = useState<'workspace' | 'recipe' | 'query'>('workspace');
  const [selection, setSelection] = useState<{ id: string; snapshot?: Record<string, any> } | null>(null);
  const [focus, setFocus] = useState(false);
  const [focusKind, setFocusKind] = useState('');
  const [notice, setNotice] = useState('');
  const [prefsVersion, setPrefsVersion] = useState(0);
  const [width, setWidth] = useState(() => local('chefmate:sidebar-width', 248));
  const [rightWidth, setRightWidth] = useState(() => local('chefmate:detail-width', 390));
  const [resizing, setResizing] = useState(false);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dragging = useRef<'left' | 'right' | null>(null);
  const editorPositions = useRef(new Map<string, { start: number; end: number }>());
  const developer = local<Record<string, any>>('chefmate:developer-settings', {}).enabled === true;
  const running = Boolean(state.session?.running || state.currentId && state.runs[state.currentId]);
  const notify = useCallback((text: string) => {
    setNotice(text); if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setNotice(''), 4200);
  }, []);
  useEffect(() => {
    const cleanupWorkspace = initializeWorkspace({ notify });
    const cleanupAppearance = initAppearance();
    const cleanupPreferences = initializeUiPreferences();
    void chefStore.start().catch(error => notify(error.message));
    const preference = () => { setPrefsVersion(value => value + 1); };
    window.addEventListener('storage', preference);
    window.addEventListener('chefmate:appearance', preference);
    window.addEventListener('chefmate:settings-saved', preference);
    return () => {
      cleanupPreferences(); cleanupWorkspace(); if (typeof cleanupAppearance === 'function') cleanupAppearance(); chefStore.dispose();
      window.removeEventListener('storage', preference); window.removeEventListener('chefmate:appearance', preference);
      window.removeEventListener('chefmate:settings-saved', preference);
      if (toastTimer.current) clearTimeout(toastTimer.current);
    };
  }, [notify]);
  useEffect(() => { setSelection(null); setFocus(false); setTab('workspace'); }, [state.currentId]);
  useEffect(() => {
    const request = state.kitchenDisplayRequest;
    if (!request) return;
    if (request.sessionId !== state.currentId || page !== 'chat') { chefStore.acknowledgeKitchenDisplay(request.action_id); return; }
    if (request.mode === 'focus' && !canFocus) return;
    setFocusKind(request.view_kind || ''); setFocus(request.mode === 'focus');
    chefStore.acknowledgeKitchenDisplay(request.action_id);
  }, [state.kitchenDisplayRequest, state.currentId, canFocus, page]);
  useEffect(() => { if (!developer && tab === 'query') setTab('workspace'); }, [developer, tab]);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 760px)');
    const resize = () => { setMobile(media.matches); if (!media.matches) setMobileOpen(false); };
    media.addEventListener('change', resize); return () => media.removeEventListener('change', resize);
  }, []);
  useEffect(() => {
    const listener = () => {
      const next = pageFor();
      const resume = () => { setPage(next); setMobileOpen(false); setFocus(false); if (next !== 'chat') setDetail(false); history.replaceState({}, '', pathFor(next)); };
      const event = new CustomEvent('chefmate:before-page-change', { cancelable: true, detail: { page: next, resume } });
      if (window.dispatchEvent(event)) resume(); else history.pushState({}, '', pathFor(page));
    };
    window.addEventListener('popstate', listener); return () => window.removeEventListener('popstate', listener);
  }, [page]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented || document.querySelector('dialog[open]')) return;
      if (focus) setFocus(false); else if (detail) setDetail(false); else setMobileOpen(false);
    };
    window.addEventListener('keydown', escape); return () => window.removeEventListener('keydown', escape);
  }, [focus, detail]);
  useEffect(() => {
    const move = (event: PointerEvent) => {
      if (dragging.current === 'left') setWidth(Math.max(208, Math.min(360, event.clientX)));
      if (dragging.current === 'right') setRightWidth(Math.max(300, Math.min(720, innerWidth - event.clientX)));
    };
    const stop = () => { dragging.current = null; setResizing(false); };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', stop); window.addEventListener('pointercancel', stop);
    return () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', stop); window.removeEventListener('pointercancel', stop); };
  }, []);
  useEffect(() => { try { localStorage.setItem('chefmate:sidebar-width', JSON.stringify(width)); localStorage.setItem('chefmate:detail-width', JSON.stringify(rightWidth)); } catch { /* The layout remains usable without browser storage. */ } }, [width, rightWidth]);
  function navigate(next: Page, after?: () => void) {
    const resume = () => {
      setPage(next); setMobileOpen(false); setFocus(false); if (next !== 'chat') setDetail(false);
      if (location.pathname !== pathFor(next)) history.pushState({}, '', pathFor(next));
      after?.();
    };
    const event = new CustomEvent('chefmate:before-page-change', { cancelable: true, detail: { page: next, resume } });
    if (!window.dispatchEvent(event)) return;
    resume();
  }
  function ask(text: string, image = false) {
    navigate('chat', () => {
      chefStore.setDraft(text);
      requestAnimationFrame(() => document.getElementById('composer-input')?.focus());
      if (image) notify('文字已放入输入框，再附上一张图片就可以发送。');
    });
  }
  function openRecipe(id: string, snapshot?: Record<string, any>) { setSelection({ id, snapshot }); setTab('recipe'); setDetail(true); }
  function openFocus(kind = '') { if (!canFocus) return; setFocusKind(kind); setFocus(true); }
  const title = page === 'chat' ? state.session?.title || '新对话' : { settings: '设置', recipes: '菜谱中心', journal: '食光手记' }[page];
  const stage = state.session?.conversation_state;
  const banner = state.status && !state.status.ready ? !state.status.configured ? '模型尚未配置，请到设置里完成模型连接。' : !state.status.database?.ready ? '菜谱数据库暂时不可用，请检查数据库连接。' : '服务暂时未就绪，请稍后重试。' : '';
  const prefs = local<Record<string, any>>('chefmate:ui-settings', {});
  return <>
    {developer && <DeveloperWatermark />}
    <a className="skip-link" href="#composer-input">跳到输入框</a>
    <div inert={focus} className={`app react-app ${focus ? 'focus-scene-active' : ''} ${resizing ? 'pane-resizing' : ''} ${collapsed ? 'sidebar-collapsed' : ''} ${mobileOpen ? 'sidebar-open' : ''} ${detail ? 'details-open' : ''} ${prefs.density === 'compact' ? 'compact-density' : ''}`}
      data-page={page} data-preferences={prefsVersion} style={{ '--sidebar-width': `${collapsed ? 64 : width}px`, '--detail-width': `${rightWidth}px` } as CSSProperties}>
      <aside className="sidebar" aria-label="会话导航" inert={mobile && !mobileOpen}><Sidebar state={state} page={page} collapsed={collapsed} onCollapse={value => { setCollapsed(value); try { localStorage.setItem('chefmate:sidebar-collapsed', JSON.stringify(value)); } catch { /* Keep this session's layout. */ } }}
        onClose={() => setMobileOpen(false)} navigate={navigate} notify={notify} developer={developer} onInspect={() => { setDetail(true); setTab('query'); }} />
        {!collapsed && <div className="sidebar-resize" role="separator" aria-label="调整侧栏宽度" aria-orientation="vertical" aria-valuemin={208} aria-valuemax={360} aria-valuenow={width} tabIndex={0}
          onPointerDown={event => { event.preventDefault(); dragging.current = 'left'; setResizing(true); }} onKeyDown={event => { if (['ArrowLeft', 'ArrowRight'].includes(event.key)) { event.preventDefault(); setWidth(Math.max(208, Math.min(360, width + (event.key === 'ArrowLeft' ? -8 : 8)))); } }} />}</aside>
      <main className="main"><header className="topbar"><div className="topbar-title"><button type="button" className="icon-button mobile-only" aria-label="展开会话导航" aria-expanded={mobileOpen} onClick={() => setMobileOpen(true)}><Icon name="panel-left" /></button>
        <span className="chat-title">{title}</span>{page === 'chat' && stage?.stage && <span className="conversation-stage">{({ idea: '闲聊', planning: '选菜', prep: '备菜', cooking: '烹饪', completed: '已完成' } as Record<string, string>)[stage.stage]}</span>}</div>
        <div className="topbar-actions"><KitchenTimers sessionId={state.currentId} active={!focus} onOpenSession={id => navigate('chat', () => { void chefStore.openSession(id).catch(error => notify(error.message)); })} />
          {page === 'chat' && <>{canFocus && <button type="button" className="panel-toggle" aria-label="打开烹饪工作台" onClick={() => openFocus()}><Icon name="maximize" /><span>做菜</span></button>}
            <button type="button" className="panel-toggle" aria-label={detail ? '收起右侧栏' : '展开右侧栏'} aria-expanded={detail} onClick={() => setDetail(!detail)}><Icon name="panel-right" /><span>详情</span></button></>}</div></header>
        {banner && <div className="connection-banner" role="status">{banner}<button type="button" onClick={() => navigate('settings')}>打开设置</button><button type="button" onClick={() => void chefStore.loadStatus().catch(error => notify(error.message))}>重新连接</button></div>}
        {state.error && <div className="error-box" role="alert">{state.error}</div>}
        <PageBoundary key={`${page}:${pageAttempt}`} onRetry={() => setPageAttempt(value => value + 1)} onBack={() => navigate('chat')}><Suspense fallback={<p className="panel-loading" role="status">正在打开页面…</p>}>
          {page === 'settings' && <Settings onClose={() => navigate('chat')} onSaved={() => { void chefStore.loadStatus().catch(error => notify(error.message)); setPrefsVersion(value => value + 1); }} notify={notify} />}
          {(page === 'recipes' || page === 'journal') && <KitchenCenters page={page} onRecipe={openRecipe} onChat={text => navigate('chat', () => { chefStore.newSession(); chefStore.setDraft(text); })} onRecord={() => navigate('chat', () => { chefStore.newSession(); chefStore.setDraft('我想记录刚刚吃的一餐，请先整理草稿让我核对。'); })} onReturn={() => navigate('chat')} notify={notify} />}
        </Suspense></PageBoundary>
        <section className="chat-page" hidden={page !== 'chat'} aria-label="对话工作区"><Conversation state={state} active={page === 'chat' && !focus} developer={developer} onRecipe={openRecipe} onAsk={ask} onFocus={openFocus} />{!focus && <Composer state={state} positions={editorPositions.current} notify={notify} />}</section>
      </main>
      <aside className="detail-panel" inert={!detail} aria-hidden={!detail} aria-label="厨房工作区"><div className="right-pane-resize" role="separator" aria-label="调整右侧栏宽度" aria-orientation="vertical" aria-valuemin={300} aria-valuemax={720} aria-valuenow={rightWidth} tabIndex={0} onPointerDown={event => { event.preventDefault(); dragging.current = 'right'; setResizing(true); }} onKeyDown={event => { if (['ArrowLeft', 'ArrowRight'].includes(event.key)) { event.preventDefault(); setRightWidth(Math.max(300, Math.min(720, rightWidth + (event.key === 'ArrowLeft' ? 8 : -8)))); } }} />
        <div className="panel-header"><div className="panel-tabs" role="tablist" aria-label="详情内容">{([['workspace', '工作区'], ...(selection ? [['recipe', '菜谱详情']] : []), ...(developer ? [['query', '调试']] : [])] as ['workspace' | 'recipe' | 'query', string][]).map(([id, name]) => <button key={id} className={`panel-tab ${tab === id ? 'active' : ''}`} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{name}</button>)}</div><button type="button" className="icon-button" aria-label="收起详情面板" onClick={() => setDetail(false)}><Icon name="x" /></button></div>
        <div className="panel-content" role="tabpanel">{tab === 'query' && developer ? <Diagnostics sessionId={state.currentId} status={state.status} developer /> : tab === 'recipe' ? <RecipeDetails selection={selection} onAsk={ask} /> : <WorkspacePanel sessionId={state.currentId} running={running} onContinue={text => { if (state.currentId) void chefStore.enqueueContinuation(state.currentId, text); }} onRecipe={openRecipe} onFocus={openFocus} notify={notify} />}</div>
      </aside>
    </div>
    {mobileOpen && <button type="button" className="drawer-backdrop" aria-label="关闭侧栏" onClick={() => setMobileOpen(false)} />}
    {state.currentId && <CookingFocus key={state.currentId} active={focus} sessionId={state.currentId} initialKind={focusKind} onClose={() => setFocus(false)} onContinue={text => { void chefStore.enqueueContinuation(state.currentId!, text); }}>{focus && <Composer state={state} positions={editorPositions.current} focus notify={notify} />}</CookingFocus>}
    {notice && <div className="toast" role="status">{notice}</div>}
  </>;
}
