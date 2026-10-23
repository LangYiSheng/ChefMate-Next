import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { chefStore } from '../runtime/store';
import { Conversation } from './Conversation';
import type { Message } from '../runtime/types';
vi.mock('../features/workspace', () => ({ MessageViews: () => null }));

describe('conversation reading position', () => {
  it('keeps independent scroll positions across sessions and hidden settings navigation', () => {
    const state = (id: string) => ({ ...chefStore.getSnapshot(), currentId: id, session: { id, title: id, messages: [{ id: id + ':1', role: 'user' as const, content: id }] } });
    const props = { developer: false, onRecipe: vi.fn(), onAsk: vi.fn(), onFocus: vi.fn() };
    const view = render(<Conversation {...props} state={state('a')} />);
    const element = screen.getByLabelText('聊天记录');
    Object.defineProperties(element, { scrollHeight: { value: 2000 }, clientHeight: { value: 400 } });
    element.scrollTop = 280; fireEvent.scroll(element);
    view.rerender(<Conversation {...props} state={state('b')} />);
    expect(element.scrollTop).toBe(2000);
    element.scrollTop = 850; fireEvent.scroll(element);
    view.rerender(<Conversation {...props} state={state('a')} />);
    expect(element.scrollTop).toBe(280);
    view.rerender(<Conversation {...props} state={state('a')} active={false} />);
    view.rerender(<Conversation {...props} state={state('a')} active />);
    expect(element.scrollTop).toBe(280);
    view.rerender(<Conversation {...props} state={state('b')} />);
    expect(element.scrollTop).toBe(850);
  });

  it('keeps normal streaming progress at the reply and reserves technical details for developer mode', () => {
    const message: Message = { role: 'assistant', content: '尚未完成的过程文字', streaming: true, process: ['内部执行说明'],
      usage: { model_calls: 7, input_tokens: 12345 }, tool_calls: [{ name: 'record_cooked_meal_intake', running: true, arguments: { internal: '工具内部参数' }, result: { detail: '工具内部结果' } }] };
    const state = { ...chefStore.getSnapshot(), currentId: 'a', session: { id: 'a', title: '本餐', running: true, messages: [message] },
      runs: { a: { sessionId: 'a', requestId: 'request', input: '登记', status: '正在登记这顿饭…' } } };
    const props = { state, onRecipe: vi.fn(), onAsk: vi.fn(), onFocus: vi.fn() };
    const view = render(<Conversation {...props} developer={false} />);
    expect(screen.getByRole('status')).toHaveTextContent('正在登记这顿饭…');
    expect(view.container.querySelector('.assistant-progress')).not.toBeNull(); expect(view.container.querySelector('.execution-trace')).toBeNull();
    for (const hidden of ['内部执行说明', '工具内部参数', '工具内部结果', '12345', '尚未完成的过程文字']) expect(view.container).not.toHaveTextContent(hidden);
    view.rerender(<Conversation {...props} developer />);
    expect(screen.getByText('执行过程')).toBeInTheDocument(); expect(view.container).toHaveTextContent('工具内部参数'); expect(view.container).toHaveTextContent('12345');
  });

  it('does not treat aggregated earlier recipe references as recommendations in a nutrition reply', () => {
    const recipes = [{ recipe_id: 'old-search', name: '豆豉鲮鱼油麦菜', total_time_min: 15 }, { recipe_id: 'cooked', name: '蚝油生菜', total_time_min: 15 }];
    const message: Message = { role: 'assistant', content: '已登记你吃完整盘蚝油生菜。', recipes,
      tool_calls: [{ name: 'get_cooked_meal_intake', result: { status: 'ok', intake_status: 'awaiting_portions', recipes: [{ recipe_id: 'cooked', name: '蚝油生菜' }] } },
        { name: 'record_cooked_meal_intake', result: { status: 'ok', intake_status: 'saved', recipes: [{ recipe_id: 'cooked', name: '蚝油生菜' }] } }] };
    const state = { ...chefStore.getSnapshot(), currentId: 'a', session: { id: 'a', title: '本餐', messages: [message] } };
    const view = render(<Conversation state={state} developer={false} onRecipe={vi.fn()} onAsk={vi.fn()} onFocus={vi.fn()} />);
    expect(view.container).toHaveTextContent('已登记你吃完整盘蚝油生菜。'); expect(view.container.querySelector('.recipe-results')).toBeNull();
    expect(screen.queryByRole('button', { name: /豆豉鲮鱼油麦菜/ })).not.toBeInTheDocument();
  });

  it('keeps visible errors and stop/retry actions in normal mode', () => {
    const state = { ...chefStore.getSnapshot(), currentId: 'a', session: { id: 'a', title: '本餐', messages: [
      { role: 'assistant' as const, content: '', error: '请求暂时失败', retryMessage: '重试输入' },
      { role: 'assistant' as const, content: '', cancelled: true, retryMessage: '停止的输入' },
    ] } };
    render(<Conversation state={state} developer={false} onRecipe={vi.fn()} onAsk={vi.fn()} onFocus={vi.fn()} />);
    expect(screen.getByRole('alert')).toHaveTextContent('请求暂时失败'); expect(screen.getByRole('button', { name: '重试这次请求' })).toBeInTheDocument();
    expect(screen.getByText('本次执行已停止。')).toBeInTheDocument(); expect(screen.getByRole('button', { name: '重新执行' })).toBeInTheDocument();
  });
});
