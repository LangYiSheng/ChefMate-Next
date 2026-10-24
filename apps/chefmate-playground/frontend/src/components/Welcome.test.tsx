import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Welcome } from './Welcome';

describe('welcome recommendations', () => {
  it('introduces current kitchen, personal intake and preference features with editable message starters', () => {
    const onUse = vi.fn(); render(<Welcome supportsImages={false} onUse={onUse} />);
    expect(screen.getByText('陪我把这一餐做好')).toBeVisible();
    expect(screen.getByText('多人吃饭，记下我那份')).toBeVisible();
    expect(screen.getByText('把工作台调成我喜欢的样子')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '调整体验' }));
    expect(screen.getByText('回复少一点，更清爽')).toBeVisible();
    const card = screen.getByText('回复少一点，更清爽').closest('article')!;
    fireEvent.click(card.querySelector('button')!);
    expect(onUse).toHaveBeenCalledWith('请把推荐回复减少到两条，每次回复只展开一张卡片，其他偏好先保留。', undefined);
  });
});
