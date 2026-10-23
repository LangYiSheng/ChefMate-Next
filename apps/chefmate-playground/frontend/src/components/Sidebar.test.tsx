import type { ReactNode } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { chefStore } from '../runtime/store';
import type { RuntimeState } from '../runtime/types';
import { Sidebar } from './Sidebar';

vi.mock('./Dialog', () => ({ Dialog: ({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) =>
  <section role="dialog" aria-label={title}>{children}<button type="button" onClick={onClose}>关闭窗口</button></section> }));

const session = { id: 'a', title: '周末的番茄菜', messages: [], pinned: false, running: false };
const notify = vi.fn(), onCollapse = vi.fn(), navigate = vi.fn();
function state(extra: Partial<RuntimeState> = {}): RuntimeState {
  return { ...chefStore.getSnapshot(), sessions: [session], currentId: 'a', session, cache: { a: session }, ...extra };
}
function props(extra: Partial<Parameters<typeof Sidebar>[0]> = {}) {
  return { state: state(), page: 'chat' as const, collapsed: false, developer: false, onCollapse, onClose: vi.fn(), navigate, notify, onInspect: vi.fn(), ...extra };
}
beforeEach(() => { notify.mockReset(); onCollapse.mockReset(); navigate.mockReset(); });

describe('Sidebar navigation and anchored session menu', () => {
  it('marks the active page, gives session titles a clipping boundary, and expands the collapsed rail', () => {
    const { container } = render(<Sidebar {...props({ page: 'journal', collapsed: true })} />);
    expect(screen.getByRole('button', { name: '食光手记' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('button', { name: '菜谱中心' })).not.toHaveAttribute('aria-current');
    expect(container.querySelector('.session-title')).toHaveTextContent('周末的番茄菜');
    fireEvent.click(container.querySelector('.rail-brand')!); expect(onCollapse).toHaveBeenCalledWith(false);
    expect(screen.getByRole('button', { name: 'ChefMate 首页' })).toBeInTheDocument();
  });

  it('opens an anchored menu without a dialog and supports arrow keys, Escape and outside clicks', () => {
    render(<Sidebar {...props()} />);
    const trigger = screen.getByRole('button', { name: '对话操作：周末的番茄菜' });
    vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue({ top: 100, bottom: 132, left: 210, right: 238, width: 28, height: 32, x: 210, y: 100, toJSON: () => ({}) });
    fireEvent.click(trigger);
    const menu = screen.getByRole('menu'); expect(menu).toHaveStyle({ top: '138px', left: '68px' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument(); expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(within(menu).getByRole('menuitem', { name: '重命名' })).toHaveFocus();
    fireEvent.keyDown(menu, { key: 'ArrowDown' }); expect(within(menu).getByRole('menuitem', { name: '置顶' })).toHaveFocus();
    fireEvent.keyDown(menu, { key: 'End' }); expect(within(menu).getByRole('menuitem', { name: '删除' })).toHaveFocus();
    fireEvent.keyDown(menu, { key: 'Escape' }); expect(screen.queryByRole('menu')).not.toBeInTheDocument(); expect(trigger).toHaveFocus();
    fireEvent.click(trigger); fireEvent.pointerDown(document.body); expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    fireEvent.click(trigger); fireEvent(window, new Event('resize')); expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });

  it('uses a dialog only for rename and saves against the selected session', async () => {
    const rename = vi.spyOn(chefStore, 'renameSession').mockResolvedValue(undefined);
    render(<Sidebar {...props()} />);
    fireEvent.click(screen.getByRole('button', { name: '对话操作：周末的番茄菜' }));
    fireEvent.click(screen.getByRole('menuitem', { name: '重命名' }));
    expect(screen.queryByRole('menu')).not.toBeInTheDocument(); expect(screen.getByRole('dialog', { name: '重命名对话' })).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: '对话名称' }), { target: { value: '  家常番茄菜  ' } });
    fireEvent.click(screen.getByRole('button', { name: '保存名称' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(rename).toHaveBeenCalledExactlyOnceWith('a', '家常番茄菜');
  });

  it('locks pin requests and keeps an error reviewable in the menu', async () => {
    let reject!: (error: Error) => void;
    const pin = vi.spyOn(chefStore, 'pinSession').mockImplementation(() => new Promise<void>((_resolve, no) => { reject = no; }));
    render(<Sidebar {...props()} />);
    fireEvent.click(screen.getByRole('button', { name: '对话操作：周末的番茄菜' })); fireEvent.click(screen.getByRole('menuitem', { name: '置顶' }));
    expect(screen.getByRole('menu')).toHaveAttribute('aria-busy', 'true'); expect(screen.getByRole('menuitem', { name: '正在保存…' })).toBeDisabled();
    await act(async () => reject(new Error('暂时无法置顶')));
    expect(screen.getByRole('alert')).toHaveTextContent('暂时无法置顶'); expect(screen.getByRole('menuitem', { name: '置顶' })).toBeEnabled();
    expect(pin).toHaveBeenCalledExactlyOnceWith('a', true); expect(notify).toHaveBeenCalledWith('暂时无法置顶');
  });

  it('retries a failed initial list and failed pagination through their matching actions', async () => {
    const load = vi.spyOn(chefStore, 'loadSessions').mockResolvedValue(undefined), more = vi.spyOn(chefStore, 'moreSessions').mockResolvedValue(undefined);
    const initial = props({ state: state({ sessions: [], sessionsError: '会话列表暂时不可用', nextCursor: null }) });
    const { rerender } = render(<Sidebar {...initial} />);
    expect(screen.queryByText('从一个新对话开始吧。')).not.toBeInTheDocument(); fireEvent.click(screen.getByRole('button', { name: '加载失败，重试' }));
    await waitFor(() => expect(load).toHaveBeenCalledOnce());
    rerender(<Sidebar {...props({ state: state({ sessionsError: '下一页暂时不可用', nextCursor: 'page-2' }) })} />);
    fireEvent.click(screen.getByRole('button', { name: '加载失败，重试' })); await waitFor(() => expect(more).toHaveBeenCalledOnce());
  });

  it('keeps deletion unavailable while the owning session is running', () => {
    render(<Sidebar {...props({ state: state({ cache: { a: { ...session, running: true } } }) })} />);
    fireEvent.click(screen.getByRole('button', { name: '对话操作：周末的番茄菜' }));
    expect(screen.getByRole('menuitem', { name: '删除' })).toBeDisabled();
    fireEvent.click(screen.getByRole('menuitem', { name: '删除' })); expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
