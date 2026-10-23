import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ActivityText, activityFrame } from './ActivityText';

afterEach(() => vi.useRealTimers());
describe('public activity typing', () => {
  it('uses the original readable pace, keeps Unicode complete and caps long messages', () => {
    expect(activityFrame('切菜🥬', 0)).toBe('切'); expect(activityFrame('切菜🥬', 217)).toBe('切菜🥬');
    expect(activityFrame('长'.repeat(100), 1800)).toHaveLength(100);
    expect(activityFrame('马上呈现', 0, true)).toBe('马上呈现');
  });
  it('does not restart on streaming renders, resets a new status and cleans its timer', () => {
    vi.useFakeTimers();
    const view = render(<ActivityText text="正在分析查询结果" />), visible = () => view.container.querySelector('.activity-text')!.textContent;
    expect(visible()).toBe('正'); expect(screen.getByRole('status')).toHaveTextContent('正在分析查询结果');
    act(() => vi.advanceTimersByTime(144)); expect(visible()).toBe('正在');
    view.rerender(<ActivityText text="正在分析查询结果" />); act(() => vi.advanceTimersByTime(144)); expect(visible()).toBe('正在分析');
    view.rerender(<ActivityText text="读取菜谱" />); expect(visible()).toBe('读');
    view.unmount(); expect(vi.getTimerCount()).toBe(0);
  });
  it('shows the full status without a timer for reduced motion and inactive scenes', () => {
    vi.useFakeTimers();
    const view = render(<ActivityText text="正在读取" active={false} />);
    expect(view.container.querySelector('.activity-text')).toHaveTextContent('正在读取'); expect(vi.getTimerCount()).toBe(0);
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    vi.spyOn(window, 'matchMedia').mockReturnValue({ ...media, matches: true });
    view.rerender(<ActivityText text="正在分析" />);
    expect(view.container.querySelector('.activity-text')).toHaveTextContent('正在分析'); expect(vi.getTimerCount()).toBe(0);
  });
});
