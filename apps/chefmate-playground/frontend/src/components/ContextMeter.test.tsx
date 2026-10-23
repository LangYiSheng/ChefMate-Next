import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { ContextMeter } from './ContextMeter';

it('shows a proportional category bar before expansion and keeps estimates separate from reports', () => {
  const inspect = vi.fn(), { container } = render(<ContextMeter context={{ estimated_tokens: 1200, categories: [{ key: 'system', label: '系统提示词', estimated_tokens: 900 }, { key: 'conversation', label: '对话', estimated_tokens: 300 }, { key: 'memory', label: '长期记忆', estimated_tokens: 0 }] }} onInspect={inspect} />);
  expect(screen.getByRole('img', { name: /分类占比/ })).toBeInTheDocument();
  const segments = container.querySelectorAll<HTMLElement>('.context-meter-bar>span');
  expect(segments).toHaveLength(2); expect(segments[0].style.flexGrow).toBe('900'); expect(segments[1].style.flexGrow).toBe('300');
  expect(segments[0].style.getPropertyValue('--context-color')).not.toBe(segments[1].style.getPropertyValue('--context-color'));
  fireEvent.click(screen.getByText('本次上下文')); expect(container.querySelector('details')).toHaveAttribute('open');
  expect(screen.getByText('长期记忆')).toBeInTheDocument(); fireEvent.click(screen.getByRole('button', { name: '查看请求轨迹' })); expect(inspect).toHaveBeenCalledOnce();
});

it('leaves missing and malformed values unknown rather than presenting them as measured zero', () => {
  const { container } = render(<ContextMeter context={{ categories: [{ key: 'system', estimated_tokens: NaN }, { key: 'conversation', estimated_tokens: -4 }] }} onInspect={() => {}} />);
  expect(container).toHaveTextContent('未估算'); expect(container.querySelectorAll('.context-meter-bar>span')).toHaveLength(0);
});
