import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Markdown } from './Markdown';

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });
const arriving = (container: HTMLElement) => [...container.querySelectorAll<HTMLElement>('.stream-character')];

describe('reply character arrival', () => {
  it('keeps mounted history static and only animates new graphemes without withholding text', () => {
    vi.useFakeTimers();
    const view = render(<Markdown text="已经保存的文字" animate />);
    expect(arriving(view.container)).toHaveLength(0);
    view.rerender(<Markdown text="已经保存的文字，新到👨‍👩‍👧‍👦" animate />);
    expect(view.container).toHaveTextContent('已经保存的文字，新到👨‍👩‍👧‍👦');
    expect(arriving(view.container).map(node => node.textContent)).toEqual(['，', '新', '到', '👨‍👩‍👧‍👦']);
    const first = arriving(view.container)[0];
    const delay = first.style.getPropertyValue('--character-delay');
    act(() => vi.advanceTimersByTime(100));
    view.rerender(<Markdown text="已经保存的文字，新到👨‍👩‍👧‍👦啦" animate />);
    expect(arriving(view.container)[0]).toBe(first);
    expect(first.style.getPropertyValue('--character-delay')).toBe(delay);
    act(() => vi.advanceTimersByTime(1000));
    expect(arriving(view.container)).toHaveLength(0);
    expect(view.container).toHaveTextContent('已经保存的文字，新到👨‍👩‍👧‍👦啦');
    view.rerender(<Markdown text="已经保存的文字，新到👨‍👩‍👧‍👦啦" animate />);
    expect(arriving(view.container)).toHaveLength(0);
  });

  it('preserves parsed Markdown, safe links, code and appended text after escaped entities', () => {
    const view = render(<Markdown text="盐 &amp;" animate />);
    view.rerender(<Markdown text={'盐 &amp; 少许\n\n1. **洗菜**\n\n[来源](https://example.com)\n\n`完整代码`\n\n```js\nalert(1)\n```\n\n<img src=x onerror=alert(1)>'} animate />);
    expect(arriving(view.container).slice(0, 2).map(node => node.textContent)).toEqual(['少', '许']);
    expect(screen.getByRole('listitem')).toHaveTextContent('洗菜');
    expect(view.container.querySelector('strong')).toHaveTextContent('洗菜');
    expect(screen.getByRole('link', { name: '来源' })).toHaveAttribute('rel', 'noopener noreferrer');
    expect(view.container.querySelectorAll('code .stream-character')).toHaveLength(0);
    expect(view.container.querySelector('pre code')).toHaveTextContent('alert(1)');
    expect(view.container.querySelector('img')).toBeNull();
  });

  it('bounds long replies to 600 animated graphemes with evenly staggered arrival', () => {
    vi.useFakeTimers();
    const view = render(<Markdown text="" animate />);
    const text = '做'.repeat(1000);
    view.rerender(<Markdown text={text} animate />);
    const characters = arriving(view.container);
    expect(view.container.textContent).toBe(text);
    expect(characters).toHaveLength(600);
    const delays = characters.map(character => parseFloat(character.style.getPropertyValue('--character-delay')));
    expect(new Set(delays).size).toBe(600);
    expect(delays.at(-1)).toBeLessThanOrEqual(420);
    act(() => vi.advanceTimersByTime(900));
    expect(arriving(view.container)).toHaveLength(0);
    expect(view.container.querySelector('p')?.childNodes).toHaveLength(1);
  });

  it('starts a new timeline for rewritten text without leaving removed characters behind', () => {
    vi.useFakeTimers();
    const view = render(<Markdown text="" animate />);
    view.rerender(<Markdown text="你好" animate />);
    const old = arriving(view.container)[0];
    act(() => vi.advanceTimersByTime(100));
    view.rerender(<Markdown text="您好" animate />);
    expect(view.container.textContent).toBe('您好');
    expect(arriving(view.container)[0]).not.toBe(old);
    expect(parseFloat(arriving(view.container)[0].style.getPropertyValue('--character-delay'))).toBe(0);
  });

  it('does not subscribe static Markdown instances to motion preferences', () => {
    const preference = vi.spyOn(window, 'matchMedia');
    const view = render(<Markdown text="旧菜谱" />);
    view.rerender(<Markdown text="更新后的菜谱" />);
    expect(view.container).toHaveTextContent('更新后的菜谱');
    expect(preference).not.toHaveBeenCalled();
    expect(arriving(view.container)).toHaveLength(0);
  });

  it('responds to reduced motion immediately and removes the preference listener on unmount', () => {
    let reduced = false;
    const listeners = new Set<() => void>();
    vi.spyOn(window, 'matchMedia').mockImplementation(query => ({
      media: query, get matches() { return reduced; }, onchange: null,
      addEventListener: (_type: string, callback: EventListenerOrEventListenerObject) => { listeners.add(callback as () => void); },
      removeEventListener: (_type: string, callback: EventListenerOrEventListenerObject) => { listeners.delete(callback as () => void); },
      addListener() {}, removeListener() {}, dispatchEvent: () => true,
    }));
    const view = render(<Markdown text="" animate />);
    view.rerender(<Markdown text="新回复" animate />);
    expect(arriving(view.container)).toHaveLength(3);
    act(() => { reduced = true; listeners.forEach(listener => listener()); });
    expect(arriving(view.container)).toHaveLength(0);
    view.rerender(<Markdown text="新回复全部立即显示" animate />);
    expect(view.container).toHaveTextContent('新回复全部立即显示');
    expect(arriving(view.container)).toHaveLength(0);
    act(() => { reduced = false; listeners.forEach(listener => listener()); });
    expect(arriving(view.container)).toHaveLength(0);
    view.unmount();
    expect(listeners.size).toBe(0);
  });
});
