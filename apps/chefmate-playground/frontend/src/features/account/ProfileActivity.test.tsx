import {act, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {ProfileActivity} from './ProfileActivity';
import {daysInKitchen} from './AccountPanel';

const {request} = vi.hoisted(() => ({request: vi.fn()}));
vi.mock('../../runtime/http', () => ({api: request}));
const activity = (value: number) => ({period: {from: '2026-10-28', to: '2026-10-28'}, daily: [{date: '2026-10-28', model_calls: 1, known_totals: {total_tokens: value}}]});
beforeEach(() => {request.mockReset();});

describe('personal profile activity', () => {
  it('uses inclusive local calendar days and handles missing dates', () => {
    const noon = new Date(2026, 9, 28, 12);
    expect(daysInKitchen(new Date(2026, 9, 28, 0).toISOString(), noon)).toBe(1);
    expect(daysInKitchen(new Date(2026, 9, 27, 23, 59).toISOString(), noon)).toBe(2);
    expect(daysInKitchen(new Date(2026, 8, 28).toISOString(), noon)).toBe(31);
    expect(daysInKitchen('', noon)).toBeNull();
  });
  it('renders only the active account activity and ignores an old response', async () => {
    const pending: {resolve: (value: unknown) => void; signal: AbortSignal}[] = [];
    request.mockImplementation((_path, options) => new Promise(resolve => pending.push({resolve, signal: options.signal})));
    const view = render(<ProfileActivity owner="first" active/>);
    view.rerender(<ProfileActivity owner="second" active/>);
    expect(pending[0].signal.aborted).toBe(true);
    await act(async () => {pending[1].resolve(activity(321));});
    expect(screen.getByText(/321 Token/)).toBeVisible();
    await act(async () => {pending[0].resolve(activity(999));});
    expect(screen.queryByText(/999 Token/)).not.toBeInTheDocument();
    view.rerender(<ProfileActivity owner="second" active={false}/>);
    expect(pending[1].signal.aborted).toBe(true);
  });
  it('does not fetch hidden profiles and retries a failed request', async () => {
    request.mockRejectedValueOnce(new Error('活动暂时无法读取')).mockResolvedValue(activity(123));
    const view = render(<ProfileActivity owner="first" active={false}/>);
    expect(request).not.toHaveBeenCalled();
    view.rerender(<ProfileActivity owner="first" active/>);
    await screen.findByText('活动暂时无法读取');
    fireEvent.click(screen.getByRole('button', {name: '重试'}));
    await waitFor(() => expect(screen.getByText(/123 Token/)).toBeVisible());
    expect(request).toHaveBeenCalledTimes(2);
  });
});
