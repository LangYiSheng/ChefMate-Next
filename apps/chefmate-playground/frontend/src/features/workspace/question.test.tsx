import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { QuestionPanel, questionPages } from './QuestionPanel';
import { workspaceStore } from './store';
import { chefStore } from '../../runtime/store';
import type { Json } from './protocol';

const ids: string[] = [];
const originalHeight = window.innerHeight, originalWidth = window.innerWidth;
const options = [{ id: 'a', label: '少油' }, { id: 'b', label: '少盐' }, { id: 'c', label: '不放辣椒' }];
const question = (extra: Json = {}) => ({ id: 'preferences', question: '这餐需要考虑哪些？', options, allow_custom: true, multiple: true, ...extra });
function seed(pending: Json) {
  const id = `question-${crypto.randomUUID()}`;
  ids.push(id);
  workspaceStore.receive(id, { status: 'ok', workspace: { revision: 8, stage: 'idea', selected_recipe_ids: [], pending_question: pending }, views: [] });
  return id;
}
function viewport(width: number, height: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: height });
}
function measuredLayout(readBodyHeight: () => number) {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const height = this.classList.contains('question-body') ? readBodyHeight() : this.classList.contains('question-title') ? 28 : this.classList.contains('question-custom') ? 100 : this.hasAttribute('data-question-unit') ? 50 : 0;
    return { x: 0, y: 0, width: 640, height, top: 0, left: 0, right: 640, bottom: height, toJSON: () => ({}) };
  });
}
beforeEach(() => {
  vi.spyOn(workspaceStore, 'load').mockImplementation(async id => workspaceStore.entry(id).snapshot);
  const runtime = chefStore.getSnapshot();
  vi.spyOn(chefStore, 'getSnapshot').mockReturnValue({ ...runtime, status: { ...runtime.status, ready: true } } as typeof runtime);
});
afterEach(() => { ids.forEach(id => workspaceStore.remove(id)); ids.length = 0; viewport(originalWidth, originalHeight); });

describe('question answer layout and draft ownership', () => {
  it('shows three choices and a matching selectable custom row together on the reported desktop size', async () => {
    viewport(1715, 1244); measuredLayout(() => 420);
    const pending = question({ multiple: false }), id = seed(pending), dispatch = vi.spyOn(workspaceStore, 'dispatch').mockResolvedValue({ status: 'ok' });
    render(<QuestionPanel sessionId={id} running={false} />);
    expect(screen.getAllByRole('radio')).toHaveLength(3);
    expect(screen.queryByRole('navigation', { name: '问题与选项分页' })).not.toBeInTheDocument();
    const custom = screen.getByRole('checkbox', { name: '其他答案或补充说明' });
    expect(custom.closest('.question-option')).toHaveClass('question-custom');
    fireEvent.click(screen.getByRole('radio', { name: '少油' }));
    fireEvent.click(custom);
    const input = screen.getByRole('textbox', { name: '其他答案或补充说明' });
    expect(input).toHaveFocus();
    fireEvent.change(input, { target: { value: '不要葱' } });
    expect(custom).toBeChecked();
    expect(screen.getByRole('radio', { name: '少油' })).toBeChecked();
    const confirm = screen.getByRole('button', { name: '确认并继续' });
    expect(confirm).toHaveClass('primary-button', 'question-confirm');
    fireEvent.click(confirm);
    await waitFor(() => expect(dispatch).toHaveBeenCalledExactlyOnceWith(id, 'answer_question', { question_id: pending.id, option_ids: ['a'], text: '不要葱' }, undefined, expect.objectContaining({ clearDraft: 'question:preferences', requestId: 'answer:preferences', continuation: expect.stringContaining('少油；不要葱') })));
  });

  it.each([[390, 844], [844, 390], [1715, 1244]])('keeps a small question in one scrollable body in focus mode at %s × %s', (width, height) => {
    viewport(width, height); measuredLayout(() => height < 500 ? 118 : 280);
    const id = seed(question());
    render(<div className="react-cooking-focus"><footer className="focus-footer"><div className="focus-composer-slot"><QuestionPanel sessionId={id} running={false} /></div></footer></div>);
    expect(screen.getAllByRole('checkbox')).toHaveLength(4);
    expect(screen.getByRole('textbox', { name: '其他答案或补充说明' })).toBeEnabled();
    expect(screen.queryByRole('navigation', { name: '问题与选项分页' })).not.toBeInTheDocument();
    expect(document.querySelector('.question-body')).toHaveAttribute('tabindex', '0');
    expect(document.querySelectorAll('[data-page-hidden="true"]')).toHaveLength(0);
  });

  it('uses measured row capacity only for substantial overflow, preserving every page and draft when resized', async () => {
    let bodyHeight = 160;
    measuredLayout(() => bodyHeight);
    const pending = question({ options: [...options, { id: 'd', label: '低脂' }, { id: 'e', label: '高蛋白' }, { id: 'f', label: '两人份' }] });
    const id = seed(pending), dispatch = vi.spyOn(workspaceStore, 'dispatch').mockResolvedValue({ status: 'ok' });
    render(<QuestionPanel sessionId={id} running={false} />);
    expect(screen.getByRole('navigation', { name: '问题与选项分页' })).toHaveTextContent('选项 1 / 4');
    fireEvent.click(screen.getByRole('checkbox', { name: '少油' }));
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '不放辣椒' }));
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '高蛋白' }));
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '不要葱' } });
    bodyHeight = 520;
    fireEvent.resize(window);
    expect(screen.queryByRole('navigation', { name: '问题与选项分页' })).not.toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: '少油' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: '不放辣椒' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: '高蛋白' })).toBeChecked();
    expect(screen.getByRole('textbox')).toHaveValue('不要葱');
    expect(workspaceStore.entry(id).drafts['question:preferences']).toEqual({ kind: 'question', baseRevision: 8, optionIds: ['a', 'c', 'e'], text: '不要葱' });
    fireEvent.click(screen.getByRole('button', { name: '确认并继续' }));
    await waitFor(() => expect(dispatch).toHaveBeenCalledWith(id, 'answer_question', { question_id: 'preferences', option_ids: ['a', 'c', 'e'], text: '不要葱' }, undefined, expect.objectContaining({ requestId: 'answer:preferences' })));
  });

  it('restores custom and ordinary selections after remount without introducing draft fields', () => {
    const id = seed(question());
    const first = render(<QuestionPanel sessionId={id} running={false} />);
    fireEvent.click(screen.getByRole('checkbox', { name: '少盐' }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '请用平底锅' } });
    first.unmount();
    render(<QuestionPanel sessionId={id} running={false} />);
    expect(screen.getByRole('checkbox', { name: '少盐' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: '其他答案或补充说明' })).toBeChecked();
    expect(screen.getByRole('textbox')).toHaveValue('请用平底锅');
    fireEvent.click(screen.getByRole('checkbox', { name: '其他答案或补充说明' }));
    expect(screen.getByRole('textbox')).toHaveValue('');
    expect(screen.getByRole('checkbox', { name: '少盐' })).toBeChecked();
    expect(workspaceStore.entry(id).drafts['question:preferences']).toEqual({ kind: 'question', baseRevision: 8, optionIds: ['b'], text: '' });
  });

  it('keeps the focused custom answer visible when a virtual keyboard reduces measured capacity', () => {
    let bodyHeight = 520;
    measuredLayout(() => bodyHeight);
    const id = seed(question({ options: [...options, { id: 'd', label: '低脂' }, { id: 'e', label: '高蛋白' }, { id: 'f', label: '两人份' }] }));
    render(<QuestionPanel sessionId={id} running={false} />);
    const input = screen.getByRole('textbox');
    act(() => input.focus());
    fireEvent.change(input, { target: { value: '正在输入的答案' } });
    bodyHeight = 160;
    fireEvent.resize(window);
    expect(screen.getByRole('navigation', { name: '问题与选项分页' })).toHaveTextContent('选项 4 / 4');
    expect(input).toBeEnabled();
    expect(input).toHaveFocus();
    expect(screen.getByRole('textbox')).toHaveValue('正在输入的答案');
  });

  it('keeps the new session clear of a late failed submission and prevents duplicate dispatches', async () => {
    const id = seed(question()), nextId = seed(question({ id: 'another', question: '下一餐呢？' }));
    let reject!: (reason: Error) => void;
    const dispatch = vi.spyOn(workspaceStore, 'dispatch').mockImplementation(() => new Promise((_, fail) => { reject = fail; }));
    const rendered = render(<QuestionPanel sessionId={id} running={false} />);
    fireEvent.click(screen.getByRole('checkbox', { name: '少油' }));
    fireEvent.click(screen.getByRole('button', { name: '确认并继续' }));
    fireEvent.click(screen.getByRole('button', { name: '确认并继续' }));
    expect(dispatch).toHaveBeenCalledTimes(1);
    rendered.rerender(<QuestionPanel sessionId={nextId} running={false} />);
    await act(async () => reject(new Error('旧会话请求失败')));
    expect(screen.getByText('下一餐呢？')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('retains short sets while measured pagination accounts for variable height rows and missing measurements', () => {
    expect(questionPages([45, 45, 45, 110], 70)).toEqual([[0, 1, 2, 3]]);
    expect(questionPages([45, 45, 45, 45, 110], 105)).toEqual([[0, 1], [2, 3], [4]]);
    expect(questionPages([45, 45, 45, 45, 110], 300)).toEqual([[0, 1, 2, 3, 4]]);
    expect(questionPages([0, 0, 0, 0, 0], 105)).toEqual([[0, 1, 2, 3, 4]]);
  });
});
