import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CookingFocus, KitchenTimers, MessageViews, QuestionPanel, WorkspacePanel } from './index';
import { workspaceStore } from './store';
import { chefStore } from '../../runtime/store';
import type { View } from './protocol';

const form: View = { id: 'prefs', kind: 'preference_form', revision: 1, title: '用餐条件', editable: true, data: { fields: [{ name: 'servings', input_type: 'number', label: '用餐人数' }], values: { servings: 2 } } };
const snapshot = (views: View[], stage = 'planning', extra = {}) => ({ status: 'ok', workspace: { revision: 1, stage, selected_recipe_ids: [], ...extra }, views });
const ids: string[] = [];
const seed = (views: View[], stage = 'planning', extra = {}) => { const id = `render-${crypto.randomUUID()}`; ids.push(id); workspaceStore.receive(id, snapshot(views, stage, extra)); return id; };
beforeEach(() => { vi.spyOn(workspaceStore, 'load').mockImplementation(async id => workspaceStore.entry(id).snapshot); });
afterEach(() => { ids.forEach(id => workspaceStore.remove(id)); ids.length = 0; });
describe('React workspace behavior', () => {
  it('keeps input edits controlled and preserves the draft when the panel remounts', () => {
    const id = seed([form]); const rendered = render(<WorkspacePanel sessionId={id} running={false} onContinue={() => {}} />);
    fireEvent.change(screen.getByRole('spinbutton', { name: '用餐人数' }), { target: { value: '4' } });
    expect(screen.getByRole('spinbutton', { name: '用餐人数' })).toHaveValue(4); rendered.unmount();
    render(<WorkspacePanel sessionId={id} running={false} onContinue={() => {}} />);
    expect(screen.getByRole('spinbutton', { name: '用餐人数' })).toHaveValue(4); expect(screen.getByRole('button', { name: '保存条件并继续' })).toBeEnabled();
  });
  it('shows current inline controls, while previous view identities remain read only', () => {
    const id = seed([form]), older = { ...form, id: 'previous' }, message = { actions: [{ kind: 'show_component', component: older }] };
    const rendered = render(<MessageViews message={message} sessionId={id} running={false} onContinue={() => {}} />);
    expect(screen.getByText('历史记录')).toBeInTheDocument(); expect(screen.getByRole('spinbutton', { name: '用餐人数' })).toBeDisabled();
    rendered.rerender(<MessageViews message={{ actions: [{ kind: 'show_component', component: form }] }} sessionId={id} running={false} onContinue={() => {}} />);
    expect(screen.queryByText('历史记录')).not.toBeInTheDocument(); expect(screen.getByRole('spinbutton', { name: '用餐人数' })).toBeEnabled();
  });
  it('keeps only the latest message owner editable when backend guidance preserves a view identity', () => {
    const current = { ...form, revision: 3 }, olderMessage = { id: 'older', role: 'assistant', content: '', actions: [{ kind: 'show_component', component: { ...form, revision: 1 } }] }, latestMessage = { id: 'latest', role: 'assistant', content: '', actions: [{ kind: 'show_component', component: { ...form, revision: 2 } }] }, id = seed([current]);
    const state = chefStore.getSnapshot(); vi.spyOn(chefStore, 'getSnapshot').mockReturnValue({ ...state, cache: { ...state.cache, [id]: { id, title: '测试', messages: [olderMessage, latestMessage] } as any } });
    render(<><MessageViews message={olderMessage} sessionId={id} running={false} onContinue={() => {}} /><MessageViews message={latestMessage} sessionId={id} running={false} onContinue={() => {}} /></>);
    const inputs = screen.getAllByRole('spinbutton', { name: '用餐人数' }); expect(inputs[0]).toBeDisabled(); expect(inputs[1]).toBeEnabled(); expect(screen.getAllByText('历史记录')).toHaveLength(1);
  });
  it('surfaces an inline recipe detail in the current workspace focus after the action succeeds', async () => {
    const choice: View = { id: 'choices', kind: 'recipe_choices', revision: 1, editable: true, data: { recipes: [{ recipe_id: 'egg', name: '鸡蛋' }] } }, id = seed([choice]), onFocus = vi.fn();
    let finish!: (value: any) => void; vi.spyOn(workspaceStore, 'dispatch').mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    render(<MessageViews message={{ actions: [{ kind: 'show_component', component: choice }] }} sessionId={id} running={false} onContinue={() => {}} onFocus={onFocus} />);
    fireEvent.click(screen.getByRole('button', { name: '详情' })); expect(onFocus).not.toHaveBeenCalled();
    await act(async () => { finish({ status: 'ok' }); }); expect(onFocus).toHaveBeenCalledExactlyOnceWith('recipe_detail');
  });
  it('does not expose a question until a running model turn has completed', () => {
    const id = seed([], 'idea'); workspaceStore.setRunning(id, true); workspaceStore.receive(id, snapshot([], 'idea', { revision: 2, pending_question: { id: 'q', question: '能吃辣吗？', options: [{ id: 'yes', label: '可以' }], allow_custom: true } }));
    const rendered = render(<QuestionPanel sessionId={id} running />); expect(screen.queryByText('能吃辣吗？')).not.toBeInTheDocument();
    rendered.rerender(<QuestionPanel sessionId={id} running={false} />); expect(screen.getByText('能吃辣吗？')).toBeInTheDocument();
  });
  it('validates a required answer and saves with a stable continuation request id', async () => {
    const question = { id: 'question-id', question: '能吃辣吗？', options: [{ id: 'yes', label: '可以' }], allow_custom: true, multiple: false }, id = seed([], 'idea', { pending_question: question });
    const dispatch = vi.spyOn(workspaceStore, 'dispatch').mockResolvedValue({ status: 'ok' }); render(<QuestionPanel sessionId={id} running={false} />);
    fireEvent.click(screen.getByRole('button', { name: '确认并继续' })); expect(screen.getByRole('alert')).toHaveTextContent('选一个答案'); expect(dispatch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('radio', { name: '可以' })); fireEvent.click(screen.getByRole('button', { name: '确认并继续' }));
    await waitFor(() => expect(dispatch).toHaveBeenCalledWith(id, 'answer_question', { question_id: question.id, option_ids: ['yes'], text: '' }, undefined, expect.objectContaining({ clearDraft: 'question:question-id', requestId: 'answer:question-id' })));
  });
  it('opens a React focus dialog without moving the existing input and restores focus on close', () => {
    const show = vi.fn(function (this: HTMLDialogElement) { this.open = true; });
    Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: show });
    Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value: function (this: HTMLDialogElement) { this.open = false; } });
    const id = seed([form]), parent = document.createElement('div'), input = document.createElement('input'); parent.append(input); document.body.append(parent); input.value = '图片旁边的文字草稿'; input.focus();
    const rendered = render(<CookingFocus sessionId={id} onClose={() => {}} onContinue={() => {}}><textarea aria-label="专注输入框" defaultValue="由 React 管理" /></CookingFocus>);
    expect(show).toHaveBeenCalledTimes(1); expect(input.parentElement).toBe(parent); expect(input.value).toBe('图片旁边的文字草稿'); expect(screen.getByRole('textbox', { name: '专注输入框' })).toHaveValue('由 React 管理');
    rendered.unmount(); expect(document.activeElement).toBe(input); parent.remove();
  });
  it('renders server deadline countdown and timer commands through the shared store', () => {
    const id = seed([{ id: 'timers', kind: 'timers', revision: 1, editable: true, data: { timers: [{ id: 'soup', label: '汤', seconds: 90, status: 'paused', remaining_seconds: 45 }] } }], 'cooking');
    const dispatch = vi.spyOn(workspaceStore, 'dispatch').mockResolvedValue({ status: 'ok' }); render(<KitchenTimers sessionId={id} />);
    fireEvent.click(screen.getByRole('button', { name: /厨房计时：汤/ })); expect(screen.getByLabelText('剩余 45 秒')).toHaveTextContent('0:45');
    fireEvent.click(screen.getByRole('button', { name: '继续汤' })); expect(dispatch).toHaveBeenCalledWith(id, 'resume_timer', { timer_id: 'soup' }, expect.objectContaining({ id: 'timers' }));
  });
});
