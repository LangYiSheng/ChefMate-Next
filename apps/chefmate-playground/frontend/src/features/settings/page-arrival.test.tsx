import {useRef} from 'react';
import {act, fireEvent, render, screen} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {usePageArrival} from './page-arrival';

function Page({page}: {page: string}) {
  const target = useRef<HTMLDivElement>(null);
  usePageArrival(target, page);
  return <div ref={target}><input aria-label="保留的草稿" defaultValue=""/></div>;
}
afterEach(() => {vi.restoreAllMocks();});

describe('consistent page entrance', () => {
  it('replays on page changes while keeping mounted drafts and cancels on exit', () => {
    const cancel = vi.fn(), animate = vi.fn((_frames: Keyframe[], _options: KeyframeAnimationOptions) => ({cancel}));
    Object.defineProperty(HTMLElement.prototype, 'animate', {configurable: true, value: animate});
    const view = render(<Page page="profile"/>);
    expect(animate).toHaveBeenCalledOnce();
    expect(animate.mock.calls[0][1]).toMatchObject({duration: 380, easing: 'cubic-bezier(.22,1,.36,1)'});
    fireEvent.change(screen.getByLabelText('保留的草稿'), {target: {value: '还没保存'}});
    view.rerender(<Page page="quota"/>);
    expect(screen.getByLabelText('保留的草稿')).toHaveValue('还没保存');
    expect(animate).toHaveBeenCalledTimes(2); expect(cancel).toHaveBeenCalledOnce();
    view.unmount(); expect(cancel).toHaveBeenCalledTimes(2);
    delete (HTMLElement.prototype as any).animate;
  });
  it('respects reduced motion and stops an in-flight animation when it changes', () => {
    const cancel = vi.fn(), animate = vi.fn(() => ({cancel})), listeners: (() => void)[] = [];
    Object.defineProperty(HTMLElement.prototype, 'animate', {configurable: true, value: animate});
    const media = {matches: true, addEventListener: (_event: string, listener: () => void) => listeners.push(listener), removeEventListener: vi.fn()};
    vi.spyOn(window, 'matchMedia').mockReturnValue(media as unknown as MediaQueryList);
    const view = render(<Page page="profile"/>); expect(animate).not.toHaveBeenCalled();
    media.matches = false; view.rerender(<Page page="quota"/>); expect(animate).toHaveBeenCalledOnce();
    act(() => {media.matches = true; listeners[0]();}); expect(cancel).toHaveBeenCalledOnce();
    view.unmount(); expect(media.removeEventListener).toHaveBeenCalledOnce();
    delete (HTMLElement.prototype as any).animate;
  });
});
