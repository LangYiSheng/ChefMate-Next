import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CookingFocus } from './index';
import { FocusConversation } from './FocusConversation';
import { workspaceStore } from './store';
import { chefStore } from '../../runtime/store';
import type { Message } from '../../runtime/types';
import { Conversation } from '../../components/Conversation';

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
    expect(screen.queryByText('帮我进入备菜')).not.toBeInTheDocument(); expect(screen.queryByText('ChefMate')).not.toBeInTheDocument();
    rendered.rerender(<FocusConversation messages={[...messages.slice(0, 2), { role: 'assistant', content: '', error: '连接暂时中断', retryMessage: '进入备菜' }]} sessionId={id} active />);
    expect(screen.getByRole('alert')).toHaveTextContent('连接暂时中断'); expect(screen.getByRole('button', { name: '重试这次请求' })).toBeInTheDocument();
  });

  it('collapses the reply, reveals a new question automatically, and keeps failures visible', () => {
    const props = { sessionId: id, active: true };
    const messages: Message[] = [{ role: 'user', request_id: 'one', content: '第一问' }, { role: 'assistant', content: '第一答' }];
    const rendered = render(<FocusConversation {...props} messages={messages} />);
    fireEvent.click(screen.getByRole('button', { name: '收起助手回复' }));
    expect(screen.getByRole('button', { name: '展开助手回复' })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByText('第一答')).not.toBeVisible();
    rendered.rerender(<FocusConversation {...props} messages={[...messages, { role: 'user', request_id: 'two', content: '下一步' }, { role: 'assistant', content: '', streaming: true, tool_calls: [{ name: 'show_cooking_guide', running: true }] }]} />);
    expect(screen.getByRole('button', { name: '收起助手回复' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('下一步')).toBeVisible(); expect(screen.getByRole('status')).toHaveTextContent('整理烹饪步骤');
    rendered.rerender(<FocusConversation {...props} messages={[...messages, { role: 'user', request_id: 'two', content: '下一步' }, { role: 'assistant', content: '', error: '连接暂时中断', retryMessage: '下一步' }]} />);
    fireEvent.click(screen.getByRole('button', { name: '收起助手回复' })); expect(screen.getByRole('alert')).toBeVisible();
  });

  it('uses the ordinary conversation presentation in the full chat view', () => {
    vi.spyOn(workspaceStore, 'load').mockResolvedValue(null);
    workspaceStore.receive(id, { workspace: { revision: 1, stage: 'prep' }, views: [{ id: 'prep', kind: 'prep_checklist', revision: 1, data: { items: [] } }] });
    const state = { ...chefStore.getSnapshot(), currentId: id, session: { id, title: '本餐', messages: [{ role: 'user' as const, content: '这一步怎么做？' }, { role: 'assistant' as const, content: '切成小块即可。' }] } };
    const rendered = render(<CookingFocus sessionId={id} onClose={() => {}} onContinue={() => {}} renderConversation={onFocus => <Conversation state={state} developer={false} onAsk={() => {}} onRecipe={() => {}} onFocus={onFocus} />}><textarea aria-label="专注输入" /></CookingFocus>);
    fireEvent.click(screen.getByRole('button', { name: '和助手聊聊' }));
    expect(rendered.container.querySelector('.focus-chat-surface .user-bubble')).toHaveTextContent('这一步怎么做？');
    expect(screen.getByLabelText('ChefMate 的回复')).toHaveTextContent('切成小块即可。');
    expect(rendered.container.querySelector('.focus-reply')).toBeNull();
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
