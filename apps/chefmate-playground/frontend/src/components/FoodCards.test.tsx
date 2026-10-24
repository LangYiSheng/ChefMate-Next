import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../runtime/http';
import { FoodCards } from './FoodCards';

vi.mock('../runtime/http', async importOriginal => ({ ...await importOriginal<typeof import('../runtime/http')>(), api: vi.fn() }));
const request = vi.mocked(api);
const receipt = { id: 'meal-a', status: 'draft', revision: 2, date: '2026-10-24', document: { title: '午饭', items: [{ name: '米饭', portion: '一碗' }] } };
beforeEach(() => request.mockReset());

describe('food record card actions', () => {
  it('keeps confirmation and correction on the draft, then removes actions after saving', async () => {
    request.mockImplementation(async (_path, options) => options?.method === 'POST' ? { ...receipt, status: 'saved' } : receipt);
    const onAsk = vi.fn();
    render(<FoodCards message={{ tool_calls: [{ name: 'create_food_log_draft', result: { status: 'ok', draft: receipt } }] }} locked={false} onAsk={onAsk} ownedIds={new Set([receipt.id])} />);
    await waitFor(() => expect(request).toHaveBeenCalledOnce());
    expect(screen.queryByRole('button', { name: '查看最近营养' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '修改这顿饭' }));
    expect(onAsk).toHaveBeenCalledWith('我想调整2026-10-24的“午饭”：');
    fireEvent.click(screen.getByRole('button', { name: '确认记录' }));
    await screen.findByText('已记录');
    expect(screen.queryByRole('button', { name: '确认记录' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '修改这顿饭' })).not.toBeInTheDocument();
    expect(request).toHaveBeenLastCalledWith('/api/food-logs/meal-a/publish', expect.objectContaining({ method: 'POST', body: JSON.stringify({ revision: 2 }) }));
  });

  it('collapses from the heading and restores the same loaded draft and disclosure without reading or saving again', async () => {
    request.mockResolvedValue({ ...receipt, revision: 3 });
    const view = render(<FoodCards message={{ tool_calls: [{ name: 'create_food_log_draft', result: { status: 'ok', draft: receipt } }] }} locked={false} onAsk={vi.fn()} ownedIds={new Set([receipt.id])} />);
    await waitFor(() => expect(request).toHaveBeenCalledOnce());
    const details = view.container.querySelector('details')!;
    details.open = true;
    const body = screen.getByRole('button', { name: '确认记录' }).closest('.card-disclosure-body')!;
    const collapse = screen.getByRole('button', { name: '收起饮食记录：午饭' });
    expect(collapse).toHaveAttribute('aria-expanded', 'true');
    expect(collapse).toHaveAttribute('aria-controls', body.id);
    fireEvent.click(collapse);
    expect(body).not.toBeVisible(); expect(screen.getByRole('heading', { name: '午饭' })).toBeVisible();
    expect(screen.queryByRole('button', { name: '确认记录' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '展开饮食记录：午饭' }));
    expect(body).toBeVisible(); expect(details.open).toBe(true);
    expect(screen.getByRole('button', { name: '确认记录' })).toBeEnabled();
    expect(request).toHaveBeenCalledOnce();
  });

  it('allows the nutrition summary to fold with the same heading control', () => {
    render(<FoodCards message={{ tool_calls: [{ name: 'search_food_logs', result: { status: 'ok', total: 2, summary: {} } }] }} locked={false} onAsk={vi.fn()} ownedIds={new Set()} />);
    fireEvent.click(screen.getByRole('button', { name: '收起最近的饮食记录' }));
    expect(screen.getByText(/2 条已保存记录/)).not.toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '展开最近的饮食记录' }));
    expect(screen.getByText(/2 条已保存记录/)).toBeVisible();
  });
});
