import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CookingFocus } from './index';
import { FocusConversation } from './FocusConversation';
import { workspaceStore } from './store';
import { chefStore } from '../../runtime/store';
import type { Message } from '../../runtime/types';

let id: string;
beforeEach(() => { id = `focus-conversation-${crypto.randomUUID()}`; });
afterEach(() => { workspaceStore.remove(id); });

describe('focus conversation alongside the task', () => {
  it('shows the current tool, final answer and error below the kitchen content', () => {
    const run = { sessionId: id, requestId: 'r', input: '', status: '正在理解需求…' };
    const messages: Message[] = [{ role: 'assistant', content: '较早的回复' }, { role: 'user', content: '帮我进入备菜' },
      { role: 'assistant', content: '内部过程', streaming: true, tool_calls: [{ name: 'show_prep_checklist', running: true }] }];
    const rendered = render(<FocusConversation messages={messages} run={run} sessionId={id} active />);
    expect(screen.getByRole('status')).toHaveTextContent('整理备菜清单…');
    expect(screen.getByText('帮我进入备菜')).toBeInTheDocument();
    expect(screen.queryByText('较早的回复')).not.toBeInTheDocument(); expect(screen.queryByText('内部过程')).not.toBeInTheDocument();
    rendered.rerender(<FocusConversation messages={[...messages.slice(0, 2), { role: 'assistant', content: '现在先准备鸡蛋。' }]} sessionId={id} active />);
    expect(screen.getByText('现在先准备鸡蛋。')).toBeInTheDocument(); expect(screen.queryByRole('status')).not.toBeInTheDocument();
    rendered.rerender(<FocusConversation messages={[...messages.slice(0, 2), { role: 'assistant', content: '', error: '连接暂时中断', retryMessage: '进入备菜' }]} sessionId={id} active />);
    expect(screen.getByRole('alert')).toHaveTextContent('连接暂时中断'); expect(screen.getByRole('button', { name: '重试这次请求' })).toBeInTheDocument();
  });

  it('follows prep and cooking stage transitions while keeping the focus scene open', () => {
    vi.spyOn(workspaceStore, 'load').mockResolvedValue(null);
    const receive = (stage: string, views: any[]) => workspaceStore.receive(id, { workspace: { revision: 1, stage }, views });
    receive('planning', [{ id: 'form', kind: 'preference_form', revision: 1, data: { fields: [], values: {} } }]);
    const close = vi.fn(); render(<CookingFocus sessionId={id} onClose={close} onContinue={() => {}}><textarea aria-label="专注输入" /></CookingFocus>);
    fireEvent.click(screen.getByRole('button', { name: '和助手聊聊' }));
    act(() => receive('prep', [{ id: 'prep', kind: 'prep_checklist', revision: 2, data: { items: [] } }]));
    expect(screen.getByRole('button', { name: '备菜' })).toHaveAttribute('aria-pressed', 'true');
    act(() => receive('cooking', [{ id: 'guide', kind: 'cooking_guide', revision: 3, data: { recipe_id: 'eggs', recipe_name: '鸡蛋', active_step_id: 'one', steps: [{ id: 'one', ordinal: 0, instruction: '打散鸡蛋。' }] } }]));
    expect(screen.getByRole('button', { name: '做法' })).toHaveAttribute('aria-pressed', 'true');
    expect(document.querySelector('.ui-step-instruction')).toHaveTextContent('打散鸡蛋。'); expect(close).not.toHaveBeenCalled();
    expect(screen.getByRole('textbox', { name: '专注输入' })).toBeInTheDocument();
  });

  it('keeps the latest answer visible when a focused card is selected', () => {
    vi.spyOn(workspaceStore, 'load').mockResolvedValue(null);
    workspaceStore.receive(id, { workspace: { revision: 1, stage: 'prep' }, views: [{ id: 'prep', kind: 'prep_checklist', revision: 1, data: { items: [] } }] });
    const base = chefStore.getSnapshot();
    vi.spyOn(chefStore, 'getSnapshot').mockReturnValue({ ...base, cache: { [id]: { id, title: '本餐', messages: [{ role: 'user', content: '下一步做什么' }, { role: 'assistant', content: '先把葱洗干净。' }] } } });
    render(<CookingFocus sessionId={id} onClose={() => {}} onContinue={() => {}}><textarea aria-label="专注输入" /></CookingFocus>);
    expect(screen.getByText('先把葱洗干净。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '备菜' })).toHaveAttribute('aria-pressed', 'true');
  });
});
