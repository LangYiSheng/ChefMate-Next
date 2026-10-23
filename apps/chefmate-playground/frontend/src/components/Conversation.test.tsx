import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { chefStore } from '../runtime/store';
import { Conversation } from './Conversation';
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
});
