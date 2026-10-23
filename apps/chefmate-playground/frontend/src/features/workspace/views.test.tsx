import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CookingFocus, KitchenTimers, MessageViews, QuestionPanel, WorkspacePanel } from './index';
import { workspaceStore } from './store';
import { chefStore } from '../../runtime/store';
import type { View } from './protocol';
import { ReadingPages, splitReadingText } from './ReadingPages';

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
  it('renders every preparation item, orders pending first, and keeps same-name ingredient identities distinct', () => {
    const items = [{ id: 'done', canonical_name: '鸡蛋', amount_text: '2个', status: 'ready' }, { id: 'skip', canonical_name: '盐', status: 'skipped' }, ...Array.from({ length: 5 }, (_, index) => ({ id: `pending-${index}`, canonical_name: index ? `食材${index}` : '鸡蛋', amount_text: '1个', status: 'pending' }))];
    const view: View = { id: 'prep', kind: 'prep_checklist', revision: 1, editable: true, data: { items } }, id = seed([view], 'prep');
    const dispatch = vi.spyOn(workspaceStore, 'dispatch').mockResolvedValue({ status: 'ok' }); render(<WorkspacePanel sessionId={id} running={false} onContinue={() => {}} />);
    const list = screen.getByRole('list', { name: '完整备料清单' }); expect(list.children).toHaveLength(7);
    expect(list.children[0]).toHaveAttribute('data-prep-id', 'pending-0'); expect(list.children[5]).toHaveClass('prep-resolved');
    expect(screen.queryByRole('tablist', { name: '步骤显示方式' })).not.toBeInTheDocument(); expect(screen.queryByRole('navigation', { name: '备菜分页' })).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('checkbox', { name: '鸡蛋已备齐' })[0]);
    expect(dispatch).toHaveBeenCalledWith(id, 'check_prep_item', { item_id: 'pending-0', status: 'ready' }, view);
  });
  it('automatically reveals a new timer without stealing the current text input', () => {
    const id = seed([], 'cooking'); render(<><textarea aria-label="交流草稿" /><KitchenTimers sessionId={id} /></>);
    const input = screen.getByRole('textbox', { name: '交流草稿' }); input.focus();
    act(() => workspaceStore.receive(id, { ...snapshot([{ id: 'timers', kind: 'timers', revision: 2, editable: true, data: { timers: [{ id: 'soup', label: '汤', seconds: 60, remaining_seconds: 60, status: 'running' }] } }], 'cooking'), workspace: { revision: 2, stage: 'cooking' } }));
    expect(screen.getByRole('dialog', { name: '厨房计时' })).toBeInTheDocument(); expect(document.activeElement).toBe(input);
  });
  it('keeps a timer-name draft and focus when a timer expires, and Escape closes the popover before focus mode', () => {
    Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function (this: HTMLDialogElement) { this.open = true; } });
    Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value: function (this: HTMLDialogElement) { this.open = false; } });
    const timer = { id: 'soup', label: '汤', seconds: 5, status: 'running', deadline_at: '2026-01-01T00:00:05Z' }, id = seed([{ id: 'timers', kind: 'timers', revision: 1, editable: true, data: { timers: [timer] } }], 'cooking'), onClose = vi.fn();
    let now = Date.parse('2026-01-01T00:00:00Z'); workspaceStore.entry(id).clock = () => now;
    render(<CookingFocus sessionId={id} onClose={onClose} onContinue={() => {}}><textarea aria-label="交流草稿" /></CookingFocus>);
    fireEvent.click(screen.getByRole('button', { name: /厨房计时：汤/ })); fireEvent.click(screen.getByRole('button', { name: '添加计时器' }));
    const popup = screen.getByRole('dialog', { name: '厨房计时' });
    const name = within(popup).getByRole('textbox', { name: '计时名称' }); fireEvent.change(name, { target: { value: '下一锅汤' } }); name.focus();
    act(() => { now += 6000; workspaceStore.tick(); }); expect(within(popup).getByRole('textbox', { name: '计时名称' })).toHaveValue('下一锅汤'); expect(document.activeElement).toBe(name);
    const composer = screen.getByRole('textbox', { name: '交流草稿' }); composer.focus(); fireEvent.keyDown(composer, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: '厨房计时' })).not.toBeInTheDocument(); expect(onClose).not.toHaveBeenCalled(); expect(document.activeElement).toBe(composer);
  });
  it('keeps multi-choice answers across short-screen pages and submits selections from all pages', async () => {
    const oldHeight = window.innerHeight; Object.defineProperty(window, 'innerHeight', { configurable: true, value: 450 });
    const question = { id: 'paged', question: '这餐要考虑哪些？', multiple: true, allow_custom: true, options: [{ id: 'a', label: '少油' }, { id: 'b', label: '少盐' }] }, id = seed([], 'idea', { pending_question: question });
    const dispatch = vi.spyOn(workspaceStore, 'dispatch').mockResolvedValue({ status: 'ok' }); render(<QuestionPanel sessionId={id} running={false} />);
    fireEvent.click(screen.getByRole('checkbox', { name: '少油' })); fireEvent.click(screen.getByRole('button', { name: '下一页' })); fireEvent.click(screen.getByRole('checkbox', { name: '少盐' }));
    fireEvent.click(screen.getByRole('button', { name: '下一页' })); fireEvent.change(screen.getByRole('textbox'), { target: { value: '不要葱' } });
    fireEvent.click(screen.getByRole('button', { name: '确认并继续' }));
    await waitFor(() => expect(dispatch).toHaveBeenCalledWith(id, 'answer_question', { question_id: 'paged', option_ids: ['a', 'b'], text: '不要葱' }, undefined, expect.anything()));
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: oldHeight });
  });
  it('preserves every character in long cooking directions across reading pages', () => {
    const text = '翻炒均匀，保持小火。\n注意锅底，不要烧干。🥘'.repeat(90), pages = splitReadingText(text, 120);
    expect(pages.length).toBeGreaterThan(1); expect(pages.join('')).toBe(text);
    render(<ReadingPages text={text} label="步骤说明" />); expect(screen.getByRole('navigation', { name: '步骤说明分页' })).toBeInTheDocument();
    const first = document.querySelector('.workspace-reading-viewport')!.textContent;
    fireEvent.click(screen.getByRole('button', { name: '下一页' })); expect(document.querySelector('.workspace-reading-viewport')!.textContent).not.toBe(first);
  });
  it('applies inline expansion limits immediately and keeps explicit reveal controls for other views', () => {
    localStorage.setItem('chefmate:ui-settings', JSON.stringify({ inline_limit: 1 }));
    const choice: View = { id: 'choices-limit', kind: 'recipe_choices', revision: 1, editable: true, title: '候选菜品', data: { recipes: [] } };
    const detail: View = { id: 'detail-limit', kind: 'recipe_detail', revision: 1, editable: false, title: '菜谱详情', data: { recipe: { name: '鸡蛋' } } };
    const views = [form, choice, detail], id = seed(views), message = { actions: views.map(component => ({ kind: 'show_component', component })) };
    const rendered = render(<MessageViews message={message} sessionId={id} running={false} onContinue={() => {}} />);
    expect(document.querySelectorAll('.workspace-inline-view')).toHaveLength(1); expect(document.querySelector('.workspace-inline-view [data-view-id]')).toHaveAttribute('data-view-id', 'choices-limit');
    act(() => { localStorage.setItem('chefmate:ui-settings', JSON.stringify({ inline_limit: 2 })); window.dispatchEvent(new Event('chefmate:appearance')); });
    expect(document.querySelectorAll('.workspace-inline-view')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: /菜谱详情.*鸡蛋/ })); expect(document.querySelectorAll('.workspace-inline-view')).toHaveLength(3);
    rendered.unmount(); localStorage.removeItem('chefmate:ui-settings');
  });
  it('keeps an empty quantity draft empty, reports validation, and discards edits restored to baseline', () => {
    const view: View = { id: 'plan', kind: 'meal_plan', revision: 1, editable: true, data: { recipes: [{ recipe_id: 'egg', name: '炒鸡蛋', target_servings: 2 }] } }, id = seed([view]);
    const dispatch = vi.spyOn(workspaceStore, 'dispatch').mockResolvedValue({ status: 'ok' }); render(<WorkspacePanel sessionId={id} running={false} onContinue={() => {}} />);
    const input = screen.getByRole('spinbutton', { name: '炒鸡蛋的配方等效份量' }); fireEvent.change(input, { target: { value: '' } }); expect(input).toHaveValue(null);
    fireEvent.click(screen.getByRole('button', { name: '保存份量' })); expect(screen.getByRole('alert')).toHaveTextContent('0.1 到 100'); expect(dispatch).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: '2' } }); expect(workspaceStore.entry(id).drafts.plan).toBeUndefined();
  });
});
