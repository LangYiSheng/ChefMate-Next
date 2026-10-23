import {lazy, Suspense} from 'react';
import {fireEvent, render, screen} from '@testing-library/react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {PageBoundary} from './PageBoundary';

function BrokenPage(): never {
  throw new Error('Internal render details should stay out of the page');
}

beforeEach(() => {vi.spyOn(console, 'error').mockImplementation(() => {});});

describe('page error boundary', () => {
  it('contains the failure without removing navigation or another page boundary', () => {
    const navigate = vi.fn();
    render(<>
      <button type="button" onClick={navigate}>主导航仍可用</button>
      <PageBoundary onRetry={vi.fn()} onBack={vi.fn()} pageName="设置"><BrokenPage/></PageBoundary>
      <PageBoundary onRetry={vi.fn()} onBack={vi.fn()}><p>其他区域仍可用</p></PageBoundary>
    </>);
    expect(screen.getByRole('heading', {name: '设置暂时没有打开'})).toBeInTheDocument();
    expect(screen.getByText('其他区域仍可用')).toBeInTheDocument();
    expect(screen.queryByText(/Internal render details/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: '主导航仍可用'})); expect(navigate).toHaveBeenCalledOnce();
  });

  it('calls explicit retry and back actions, and recovers after the parent rebuilds the page', () => {
    const onRetry = vi.fn(), onBack = vi.fn();
    const {rerender} = render(<PageBoundary key="failed" onRetry={onRetry} onBack={onBack}><BrokenPage/></PageBoundary>);
    fireEvent.click(screen.getByRole('button', {name: '重试页面'})); expect(onRetry).toHaveBeenCalledOnce();
    expect(screen.getByRole('alert')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: '返回对话'})); expect(onBack).toHaveBeenCalledOnce();
    rerender(<PageBoundary key="recovered" onRetry={onRetry} onBack={onBack}><p>页面已经恢复</p></PageBoundary>);
    expect(screen.getByText('页面已经恢复')).toBeInTheDocument(); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('catches a rejected lazy import and accepts a new lazy instance on retry', async () => {
    const Failed = lazy(() => Promise.reject(new Error('Chunk download failed')));
    const Recovered = lazy(() => Promise.resolve({default: () => <p>新的页面模块已加载</p>}));
    const onRetry = vi.fn(), onBack = vi.fn();
    const {rerender} = render(<PageBoundary key="attempt-0" onRetry={onRetry} onBack={onBack} pageName="菜谱中心">
      <Suspense fallback={<p>正在加载页面…</p>}><Failed/></Suspense>
    </PageBoundary>);
    expect(await screen.findByRole('heading', {name: '菜谱中心暂时没有打开'})).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: '重试页面'})); expect(onRetry).toHaveBeenCalledOnce();
    rerender(<PageBoundary key="attempt-1" onRetry={onRetry} onBack={onBack} pageName="菜谱中心">
      <Suspense fallback={<p>正在加载页面…</p>}><Recovered/></Suspense>
    </PageBoundary>);
    expect(await screen.findByText('新的页面模块已加载')).toBeInTheDocument(); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
