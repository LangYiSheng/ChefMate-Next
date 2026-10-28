import {act, renderHook} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {useAnimatedQuota} from './useAnimatedQuota';

afterEach(() => {vi.unstubAllGlobals(); vi.restoreAllMocks();});
describe('quota reveal animation', () => {
  it('grows from zero and moves from the current visible balance on a settlement update', () => {
    let id = 0;
    const frames = new Map<number, FrameRequestCallback>();
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {frames.set(++id,callback); return id;});
    vi.stubGlobal('cancelAnimationFrame', (key: number) => frames.delete(key));
    const advance = (time: number) => act(() => {const queued = [...frames.values()]; frames.clear(); queued.forEach(callback => callback(time));});
    const {result,rerender,unmount} = renderHook(({target}) => useAnimatedQuota(target), {initialProps:{target:80}});
    expect(result.current).toBe(0);
    advance(0); advance(360); expect(result.current).toBeGreaterThan(0); expect(result.current).toBeLessThan(80);
    advance(720); expect(result.current).toBe(80);
    rerender({target:40}); advance(1000); advance(1360); expect(result.current).toBeGreaterThan(40); expect(result.current).toBeLessThan(80);
    advance(1720); expect(result.current).toBe(40);
    rerender({target:10}); unmount(); expect(frames.size).toBe(0);
  });
  it('shows the final value immediately when reduced motion is requested', () => {
    const original = window.matchMedia;
    vi.spyOn(window,'matchMedia').mockImplementation(query => ({...original(query),matches:true}));
    const frames = vi.fn(); vi.stubGlobal('requestAnimationFrame',frames);
    const {result,rerender} = renderHook(({target}) => useAnimatedQuota(target),{initialProps:{target:95 as number | null}});
    expect(result.current).toBe(95); expect(frames).not.toHaveBeenCalled();
    rerender({target:0}); expect(result.current).toBe(0);
    rerender({target:null}); expect(result.current).toBeNull();
  });
});
