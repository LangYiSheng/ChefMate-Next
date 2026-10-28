import {fireEvent, render, screen} from '@testing-library/react';
import {useState} from 'react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {Dialog} from './Dialog';

beforeEach(() => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {configurable: true, value(this: HTMLDialogElement) {
    this.setAttribute('open', ''); this.querySelector<HTMLElement>('button:not(:disabled)')?.focus();
  }});
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {configurable: true, value(this: HTMLDialogElement) {this.removeAttribute('open');}});
});

describe('native dialog focus and cancellation', () => {
  it.each(['continue', 'cancel'])('closes only the nested confirmation with %s and restores its trigger', dismissal => {
    const closeOuter = vi.fn();
    function NestedDialogs() {
      const [confirm, setConfirm] = useState(false);
      return <Dialog title="外层编辑器" onClose={closeOuter}>
        <button type="button" onClick={() => setConfirm(true)}>打开确认</button>
        {confirm && <Dialog title="确认离开" role="alertdialog" onClose={() => setConfirm(false)}>
          <button type="button" onClick={() => setConfirm(false)}>继续编辑</button>
        </Dialog>}
      </Dialog>;
    }
    render(<NestedDialogs/>);
    const trigger = screen.getByRole('button', {name: '打开确认'}); trigger.focus(); fireEvent.click(trigger);
    const confirmation = screen.getByRole('alertdialog', {name: '确认离开'});
    expect(confirmation).toHaveAttribute('open');
    expect(screen.getByRole('button', {name: '继续编辑'})).toHaveFocus();
    if (dismissal === 'cancel') fireEvent(confirmation, new Event('cancel', {cancelable: true})); else fireEvent.click(screen.getByRole('button', {name: '继续编辑'}));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.getByRole('dialog', {name: '外层编辑器'})).toHaveAttribute('open');
    expect(closeOuter).not.toHaveBeenCalled(); expect(trigger).toHaveFocus();
  });
});
