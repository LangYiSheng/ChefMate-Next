import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {MemorySettings} from './MemorySettings';

const {request} = vi.hoisted(() => ({request: vi.fn()}));
vi.mock('../../runtime/http', () => ({api: request}));
const profile = {display_name: '小厨', household_size: 2, cooking_level: 'comfortable', preferences: ['喜欢家常菜', '快手省时间']};
beforeEach(() => request.mockReset());
describe('Kitchen profile in long-term memory', () => {
  it('shows the explicitly shared profile separately from notes and opens the account editor', async () => {
    request.mockResolvedValue({revision: 4, user_notes: '保留我手写的偏好', summary: '', enabled: true, kitchen_profile: profile});
    const onAccount = vi.fn(); render(<MemorySettings active onDirty={vi.fn()} onBusy={vi.fn()} onAccount={onAccount}/>);
    expect(await screen.findByRole('heading', {name: '厨房档案'})).toBeVisible(); expect(screen.getByText('小厨')).toBeVisible(); expect(screen.getByText('2 人')).toBeVisible(); expect(screen.getByText('日常下厨')).toBeVisible(); expect(screen.getByText('喜欢家常菜、快手省时间')).toBeVisible();
    expect(screen.getByRole('textbox', {name: '你希望记住的事'})).toHaveValue('保留我手写的偏好'); fireEvent.click(screen.getByRole('link', {name: '到账号页修改'})); expect(onAccount).toHaveBeenCalledOnce(); expect(request).toHaveBeenCalledOnce();
  });
  it('explains that a stored profile is inactive while long-term memory is disabled', async () => {
    request.mockResolvedValue({revision: 4, user_notes: '', summary: '', enabled: false, kitchen_profile: profile}); render(<MemorySettings active onDirty={vi.fn()} onBusy={vi.fn()}/>);
    expect(await screen.findByText('当前已停用长期记忆，这份档案暂不用于对话建议。')).toBeVisible(); expect(screen.getByRole('switch', {name: '在对话中使用长期记忆'})).not.toBeChecked();
  });
  it('includes the profile in clear confirmation and removes the projection after the server accepts', async () => {
    request.mockImplementation((_path: string, options?: RequestInit) => Promise.resolve({revision: options?.method === 'DELETE' ? 5 : 4, user_notes: '', summary: '', enabled: true, kitchen_profile: options?.method === 'DELETE' ? {} : profile}));
    render(<MemorySettings active onDirty={vi.fn()} onBusy={vi.fn()}/>); await screen.findByRole('heading', {name: '厨房档案'}); fireEvent.click(screen.getByRole('button', {name: '清空记忆'}));
    expect(screen.getByRole('alertdialog', {name: '清空长期记忆'})).toHaveTextContent('账号资料仍保留'); expect(screen.getByRole('alertdialog', {name: '清空长期记忆'})).toHaveTextContent('厨房档案'); fireEvent.click(screen.getByRole('button', {name: '确认清空'}));
    await waitFor(() => expect(screen.queryByRole('heading', {name: '厨房档案'})).not.toBeInTheDocument()); expect(request).toHaveBeenLastCalledWith('/api/memory?revision=4', {method: 'DELETE'});
  });
});
