import type { ReactNode } from 'react';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RuntimeState } from './runtime/types';
import App from './App';
import type {AccountUser} from './features/account/types';

const fixture = vi.hoisted(() => {
  const runtimeListeners = new Set<() => void>(), workspaceListeners = new Set<() => void>();
  const data = { runtime: null as RuntimeState | null, workspace: { entries: {} as Record<string, any>, reminders: [], tick: 0 } };
  return { data, runtimeListeners, workspaceListeners,
    chefStore: { subscribe: (listener: () => void) => { runtimeListeners.add(listener); return () => runtimeListeners.delete(listener); },
      getSnapshot: () => data.runtime!, start: vi.fn(async () => {}), dispose: vi.fn(), loadStatus: vi.fn(async () => {}),
      setDraft: vi.fn(), newSession: vi.fn(), openSession: vi.fn(async () => {}), enqueueContinuation: vi.fn() },
    workspaceStore: { subscribe: (listener: () => void) => { workspaceListeners.add(listener); return () => workspaceListeners.delete(listener); },
      getSnapshot: () => data.workspace },
  };
});

vi.mock('./runtime/store', () => ({ chefStore: fixture.chefStore }));
vi.mock('./features/workspace/store', () => ({ workspaceStore: fixture.workspaceStore }));
vi.mock('./features/settings/appearance', () => ({ initAppearance: () => () => {} }));
vi.mock('./components/Sidebar', () => ({ Sidebar: ({openSettings, onLogout}: {openSettings: (section: string) => void; onLogout: () => void}) => <nav aria-label="会话导航内容">最近会话<button onClick={() => openSettings('account-profile')}>打开个人资料</button><button onClick={() => openSettings('memory')}>打开个人偏好</button><button onClick={onLogout}>退出测试菜单</button></nav> }));
vi.mock('./components/Composer', () => ({ Composer: () => <label>对话输入<input aria-label="对话输入" /></label> }));
vi.mock('./components/Conversation', () => ({ Conversation: ({ active, onRecipe }: { active: boolean; onRecipe: (id: string) => void }) => <div aria-label="聊天记录" data-active={active}>已有消息<button onClick={() => onRecipe('tomato-eggs')}>查看已选菜谱</button></div> }));
vi.mock('./components/Details', () => ({ Diagnostics: () => <div>开发请求诊断</div>, RecipeDetails: () => <div>菜谱详情内容</div> }));
vi.mock('./features/settings/Settings', () => ({ Settings: ({initialSection}: {initialSection: string}) => <div aria-label={'设置内容：' + initialSection}>设置内容</div> }));
vi.mock('./features/centers/KitchenCenters', () => ({ KitchenCenters: () => <div>个人中心内容</div> }));
vi.mock('./features/workspace', () => ({
  initializeWorkspace: () => () => {},
  WorkspacePanel: () => <div>厨房任务内容</div>,
  KitchenTimers: () => null,
  CookingFocus: ({ active, onClose, children }: { active: boolean; onClose: () => void; children: ReactNode }) =>
    <section aria-label="烹饪专注模式" aria-hidden={!active} inert={!active} data-active={active}>
      <button type="button" onClick={onClose}>退出专注模式</button>{children}
    </section>,
}));

beforeEach(() => {
  localStorage.clear(); history.replaceState({}, '', '/static/');
  fixture.runtimeListeners.clear(); fixture.workspaceListeners.clear();
  const session = { id: 'session-a', title: '今天做什么', messages: [] };
  fixture.data.runtime = { status: { ready: true }, sessions: [session], currentId: session.id, session, cache: { [session.id]: session },
    blankId: 'draft:initial', drafts: {}, imageDrafts: {}, imageUploads: {}, runs: {}, continuations: {}, loading: false, preparing: false,
    sessionsLoading: false, sessionsError: null, sessionLoading: {}, nextCursor: null, error: null };
  fixture.data.workspace = { entries: {}, reminders: [], tick: 0 };
});
afterEach(() => { cleanup(); fixture.runtimeListeners.clear(); fixture.workspaceListeners.clear(); localStorage.clear(); });

function preferences(enabled: boolean) {
  act(() => {
    localStorage.setItem('chefmate:developer-settings', JSON.stringify({ enabled }));
    window.dispatchEvent(new CustomEvent('chefmate:settings-saved'));
  });
}
function workspace(views: Record<string, any>[]) {
  act(() => {
    fixture.data.workspace = { ...fixture.data.workspace, entries: { 'session-a': { snapshot: { stage: 'prep' }, views } } };
    fixture.workspaceListeners.forEach(listener => listener());
  });
}
function pointer(target: Element | Window, type: string, clientX: number, pointerId = 1) {
  const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientX, button: 0 });
  Object.defineProperty(event, 'pointerId', { value: pointerId });
  fireEvent(target, event);
}
function capture(handle: HTMLElement) {
  const release = vi.fn();
  Object.defineProperties(handle, {
    setPointerCapture: { value: vi.fn() }, hasPointerCapture: { value: () => true }, releasePointerCapture: { value: release },
  });
  return release;
}

describe('App surface coordination', () => {
  it('keeps the legacy account URL usable and routes account shortcuts to settings sections', async () => {
    const base = import.meta.env.DEV ? import.meta.env.BASE_URL.replace(/\/$/, '') : '';
    history.replaceState({}, '', base + '/account'); render(<App/>); expect(await screen.findByLabelText('设置内容：account-profile')).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: '打开个人偏好'})); expect(await screen.findByLabelText('设置内容：memory')).toBeVisible(); expect(location.pathname + location.search).toBe(base + '/settings?section=memory');
    fireEvent.click(screen.getByRole('button', {name: '打开个人资料'})); expect(await screen.findByLabelText('设置内容：account-profile')).toBeVisible(); expect(location.pathname + location.search).toBe(base + '/settings?section=account-profile');
  });
  it('dispatches the same draft guard before logout and resumes only after it accepts', () => {
    const logout = vi.fn().mockResolvedValue(undefined); let resume: (() => void) | undefined;
    const guard = (event: Event) => {const navigation = event as CustomEvent<{page: string; resume: () => void}>; if (navigation.detail.page === 'logout') {event.preventDefault(); resume = navigation.detail.resume;}};
    window.addEventListener('chefmate:before-page-change', guard); render(<App logout={logout}/>); fireEvent.click(screen.getByRole('button', {name: '退出测试菜单'})); expect(logout).not.toHaveBeenCalled(); expect(resume).toBeTypeOf('function'); act(() => resume?.()); expect(logout).toHaveBeenCalledOnce(); window.removeEventListener('chefmate:before-page-change', guard);
  });
  it('distinguishes a configured model from a missing recipe service for ordinary accounts', () => {
    fixture.data.runtime!.status = {ready: false, configured: false, model_configured: true, database: {ready: false}};
    render(<App account={{id: 'user-a', role: 'user', developer_allowed: false} as AccountUser}/>);
    expect(screen.getByText('菜谱服务尚未连接，请联系管理员。')).toBeInTheDocument(); expect(screen.queryByText('管理员尚未配置模型连接。')).not.toBeInTheDocument(); expect(screen.queryByRole('button', {name: '管理平台'})).not.toBeInTheDocument();
  });
  it('requires the server account permission even if developer mode was enabled in browser storage', () => {
    localStorage.setItem('chefmate:developer-settings', JSON.stringify({enabled: true}));
    render(<App account={{id: 'user-a', role: 'user', developer_allowed: false} as AccountUser}/>);
    fireEvent.click(screen.getByRole('button', {name: '展开右侧栏'})); expect(screen.queryByRole('tab', {name: '调试', hidden: true})).not.toBeInTheDocument(); expect(screen.queryByText('开发请求诊断')).not.toBeInTheDocument();
  });
  it('allows an administrator to open diagnostics without a separate developer grant', () => {
    localStorage.setItem('chefmate:developer-settings', JSON.stringify({enabled: true}));
    render(<App account={{id: 'promoted-admin', role: 'admin', developer_allowed: false} as AccountUser}/>);
    fireEvent.click(screen.getByRole('button', {name: '展开右侧栏'}));
    fireEvent.click(screen.getByRole('tab', {name: '调试'}));
    expect(screen.getByText('开发请求诊断')).toBeInTheDocument();
  });
  it('drags the left separator by pointer distance without snapping to a viewport coordinate', () => {
    const view = render(<App />), app = view.container.querySelector('.app')!;
    const handle = screen.getByRole('separator', { name: '调整侧栏宽度' }), release = capture(handle);
    expect(handle.parentElement).toBe(app);
    pointer(handle, 'pointerdown', 242);
    expect(app).toHaveAttribute('data-resizing', 'left');
    pointer(window, 'pointermove', 242);
    expect(handle).toHaveAttribute('aria-valuenow', '248');
    pointer(window, 'pointermove', 282);
    expect(handle).toHaveAttribute('aria-valuenow', '288');
    pointer(window, 'pointermove', 600, 2);
    expect(handle).toHaveAttribute('aria-valuenow', '288');
    pointer(window, 'pointerup', 282);
    expect(release).toHaveBeenCalledWith(1); expect(app).not.toHaveAttribute('data-resizing');
    pointer(window, 'pointermove', 320);
    expect(handle).toHaveAttribute('aria-valuenow', '288');
    fireEvent.keyDown(handle, { key: 'ArrowLeft' });
    expect(handle).toHaveAttribute('aria-valuenow', '280');
  });

  it('resizes the right panel in the opposite direction and releases capture on cancel and unmount', () => {
    const view = render(<App />), app = view.container.querySelector('.app')!;
    expect(screen.queryByRole('separator', { name: '调整右侧栏宽度' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '展开右侧栏' }));
    const handle = screen.getByRole('separator', { name: '调整右侧栏宽度' }), release = capture(handle);
    expect(handle.parentElement).toBe(app);
    pointer(handle, 'pointerdown', 1424);
    pointer(window, 'pointermove', 1424);
    expect(handle).toHaveAttribute('aria-valuenow', '390');
    pointer(window, 'pointermove', 1384);
    expect(handle).toHaveAttribute('aria-valuenow', '430');
    pointer(window, 'pointermove', 600);
    expect(handle).toHaveAttribute('aria-valuenow', '720');
    pointer(window, 'pointercancel', 600);
    expect(release).toHaveBeenCalledWith(1); expect(app).not.toHaveAttribute('data-resizing');
    pointer(handle, 'pointerdown', 600);
    view.unmount();
    expect(release).toHaveBeenCalledTimes(2);
  });

  it('exposes diagnostics only while developer mode is enabled and returns to workspace when disabled', () => {
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: '展开右侧栏' }));
    expect(screen.queryByRole('tab', { name: '调试', hidden: true })).not.toBeInTheDocument();
    expect(screen.queryByText('开发请求诊断')).not.toBeInTheDocument();
    preferences(true);
    fireEvent.click(screen.getByRole('tab', { name: '调试' }));
    expect(screen.getByText('开发请求诊断')).toBeInTheDocument();
    preferences(false);
    expect(screen.queryByRole('tab', { name: '调试', hidden: true })).not.toBeInTheDocument();
    expect(screen.queryByText('开发请求诊断')).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '工作区' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('厨房任务内容')).toBeInTheDocument();
  });

  it('keeps the detail surface mounted but inaccessible after closing', () => {
    const view = render(<App />), panel = view.container.querySelector('aside[aria-label="厨房工作区"]')!;
    expect(panel).toHaveAttribute('inert'); expect(panel).toHaveAttribute('aria-hidden', 'true');
    fireEvent.click(screen.getByRole('button', { name: '展开右侧栏' }));
    expect(panel).not.toHaveAttribute('inert'); expect(panel).toHaveAttribute('aria-hidden', 'false');
    expect(screen.queryByRole('tab', { name: '菜谱详情' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看已选菜谱' }));
    fireEvent.click(screen.getByRole('tab', { name: '菜谱详情' })); expect(within(panel as HTMLElement).getByText('菜谱详情内容')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '收起详情面板' }));
    expect(view.container.querySelector('aside[aria-label="厨房工作区"]')).toBe(panel);
    expect(panel).toHaveAttribute('inert'); expect(panel).toHaveAttribute('aria-hidden', 'true');
    expect(panel).toHaveTextContent('菜谱详情内容');
    fireEvent.click(screen.getByRole('button', { name: '展开右侧栏' }));
    expect(screen.getByRole('tab', { name: '菜谱详情' })).toHaveAttribute('aria-selected', 'true');
  });

  it('offers focus only for an actual kitchen view and coordinates the active scene with inert main content', () => {
    const view = render(<App />), app = view.container.querySelector('[data-page="chat"]')!, focus = view.container.querySelector('[aria-label="烹饪专注模式"]')!;
    expect(screen.queryByRole('button', { name: '打开烹饪工作台' })).not.toBeInTheDocument();
    workspace([{ id: 'timers', kind: 'timers', revision: 1, data: { timers: [] } }]);
    expect(screen.queryByRole('button', { name: '打开烹饪工作台' })).not.toBeInTheDocument();
    workspace([{ id: 'prep', kind: 'prep_checklist', revision: 1, data: { items: [] } }]);
    expect(focus).toHaveAttribute('aria-hidden', 'true'); expect(focus).toHaveAttribute('inert');
    fireEvent.click(screen.getByRole('button', { name: '打开烹饪工作台' }));
    expect(app).toHaveAttribute('inert'); expect(focus).toHaveAttribute('data-active', 'true');
    expect(focus).not.toHaveAttribute('inert'); expect(focus).toHaveAttribute('aria-hidden', 'false');
    expect(within(focus as HTMLElement).getByRole('textbox', { name: '对话输入' })).toBeInTheDocument();
    expect(screen.getByLabelText('聊天记录')).toHaveAttribute('data-active', 'false');
    fireEvent.click(within(focus as HTMLElement).getByRole('button', { name: '退出专注模式' }));
    expect(app).not.toHaveAttribute('inert'); expect(focus).toHaveAttribute('data-active', 'false'); expect(focus).toHaveAttribute('inert');
    expect(view.container.querySelector('[aria-label="烹饪专注模式"]')).toBe(focus);
    expect(screen.getByLabelText('聊天记录')).toHaveAttribute('data-active', 'true');
    expect(screen.getByRole('textbox', { name: '对话输入' })).toBeInTheDocument();
  });
});
