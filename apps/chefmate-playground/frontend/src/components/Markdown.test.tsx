import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Markdown, safeLink } from './Markdown';

describe('assistant text boundary', () => {
  it('keeps model HTML as text and rejects executable or credential-bearing URLs', () => {
    render(<Markdown text={'<img src=x onerror=alert(1)>\n\n[运行](javascript:alert)\n[公开来源](https://example.com/recipe)\n**确认**和`克重`'} />);
    expect(document.querySelector('img')).toBeNull();
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '运行' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: '公开来源' })).toHaveAttribute('rel', 'noopener noreferrer');
    expect(safeLink('https://user:secret@example.com')).toBeNull();
    expect(document.querySelector('strong')).toHaveTextContent('确认');
  });
  it('renders completed lists and a streaming unclosed code fence without executing it', () => {
    render(<Markdown text={'1. 备菜\n2. 做饭\n\n```js\nwindow.alert(1)'} />);
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(document.querySelector('pre code')).toHaveTextContent('window.alert(1)');
  });
  it('restores tables, nested lists, emphasis, quotes and task lists used by recipe replies', () => {
    render(<Markdown text={'## 用量\n\n| 食材 | 克重 |\n| --- | --- |\n| 鸡蛋 | 100 g |\n\n> *先洗手*\n\n- [x] 备菜\n  - 清洗\n- [ ] 下锅\n\n~~可跳过~~'} />);
    expect(screen.getByRole('table')).toHaveTextContent('鸡蛋');
    expect(document.querySelector('blockquote em')).toHaveTextContent('先洗手');
    expect(document.querySelector('ul ul')).toHaveTextContent('清洗');
    expect(screen.getAllByRole('checkbox')).toHaveLength(2);
    expect(document.querySelector('del')).toHaveTextContent('可跳过');
  });
});
