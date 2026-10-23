import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {useState} from 'react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {Select, type SelectProps} from './Select';

const options = [{value: 'a', label: 'Alpha'}, {value: 'off', label: 'Unavailable', disabled: true}, {value: 'b', label: 'Beta'}, {value: 'br', label: 'Bravo'}, {value: 'c', label: '清淡'}];
function Example(props: Partial<SelectProps> = {}) {
  const [value, setValue] = useState(props.value ?? 'a');
  return <><label htmlFor="example">用餐方式</label><Select {...props} id="example" name="mode" value={value} onValueChange={setValue} options={props.options || options}/><button type="button">后一个控件</button></>;
}
afterEach(() => vi.unstubAllGlobals());

describe('themed Select', () => {
  it('uses a labelled combobox and real themed options, preserving form name/value without a native select', () => {
    const rendered = render(<form><Example/></form>);
    const trigger = screen.getByRole('combobox', {name: '用餐方式'});
    expect(rendered.container.querySelector('select')).toBeNull();
    fireEvent.click(trigger);
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('option', {name: 'Beta'}));
    expect(trigger).toHaveTextContent('Beta'); expect(trigger).toHaveFocus();
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(new FormData(rendered.container.querySelector('form')!).get('mode')).toBe('b');
  });

  it('keeps DOM focus on the combobox, skips disabled choices, and cancels Escape without committing', () => {
    const parentKey = vi.fn(); render(<div onKeyDown={parentKey}><Example/></div>);
    const trigger = screen.getByRole('combobox'); trigger.focus();
    fireEvent.keyDown(trigger, {key: 'ArrowDown'});
    fireEvent.keyDown(trigger, {key: 'ArrowDown'});
    expect(screen.getByRole('option', {name: 'Beta'})).toHaveAttribute('data-active', 'true');
    expect(trigger).toHaveFocus();
    fireEvent.keyDown(trigger, {key: 'End'});
    expect(screen.getByRole('option', {name: '清淡'})).toHaveAttribute('data-active', 'true');
    parentKey.mockClear(); fireEvent.keyDown(trigger, {key: 'Escape'});
    expect(parentKey).not.toHaveBeenCalled(); expect(trigger).toHaveTextContent('Alpha'); expect(trigger).toHaveFocus();
    fireEvent.keyDown(trigger, {key: 'End'}); fireEvent.keyDown(trigger, {key: 'Home'}); fireEvent.keyDown(trigger, {key: 'ArrowUp'}); fireEvent.keyDown(trigger, {key: 'Enter'});
    expect(trigger).toHaveTextContent('清淡');
  });

  it('supports prefix typeahead and closes Tab without trapping the next focus destination', () => {
    render(<Example/>); const trigger = screen.getByRole('combobox');
    fireEvent.keyDown(trigger, {key: 'b'}); fireEvent.keyDown(trigger, {key: 'r'});
    expect(screen.getByRole('option', {name: 'Bravo'})).toHaveAttribute('data-active', 'true');
    expect(fireEvent.keyDown(trigger, {key: 'Tab'})).toBe(true);
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument(); expect(trigger).toHaveTextContent('Bravo');
    const next = screen.getByRole('button', {name: '后一个控件'}); next.focus(); expect(next).toHaveFocus();
    fireEvent.keyDown(trigger, {key: '清'}); expect(screen.getByRole('option', {name: '清淡'})).toHaveAttribute('data-active', 'true');
  });

  it('enforces required form validity accessibly and omits disabled values from FormData', () => {
    const submit = vi.fn(); const rendered = render(<form onSubmit={event => {event.preventDefault(); submit();}}><Example value="" required/><button type="submit">提交</button></form>);
    fireEvent.click(screen.getByRole('button', {name: '提交'}));
    expect(submit).not.toHaveBeenCalled(); expect(screen.getByRole('alert')).toHaveTextContent('请选择一项'); expect(screen.getByRole('combobox')).toHaveFocus();
    fireEvent.click(screen.getByRole('combobox')); fireEvent.click(screen.getByRole('option', {name: 'Beta'})); fireEvent.click(screen.getByRole('button', {name: '提交'}));
    expect(submit).toHaveBeenCalledOnce();
    rendered.rerender(<form><Select value="a" onValueChange={vi.fn()} options={options} aria-label="已停用" name="mode" required disabled/></form>);
    expect(screen.getByRole('combobox', {name: '已停用'})).toBeDisabled();
    expect(new FormData(rendered.container.querySelector('form')!).has('mode')).toBe(false);
  });

  it('flips above and clamps to the viewport while keeping a popup in its native dialog top layer', async () => {
    vi.stubGlobal('innerWidth', 320); vi.stubGlobal('innerHeight', 400);
    vi.spyOn(HTMLButtonElement.prototype, 'getBoundingClientRect').mockReturnValue({x: 260, y: 340, width: 180, height: 40, left: 260, top: 340, right: 440, bottom: 380, toJSON: () => ({})});
    render(<dialog open><Example/></dialog>); fireEvent.click(screen.getByRole('combobox'));
    const list = screen.getByRole('listbox');
    await waitFor(() => expect(list).toHaveAttribute('data-side', 'above'));
    expect(list.closest('dialog')).not.toBeNull(); expect(Number.parseFloat(list.style.left)).toBeLessThanOrEqual(132); expect(Number.parseFloat(list.style.top)).toBeLessThan(340);
    fireEvent.pointerDown(document.body); expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('cleans up positioning observers and global listeners after an open popup unmounts', () => {
    const disconnect = vi.fn(), observe = vi.fn();
    vi.stubGlobal('ResizeObserver', class {observe = observe; disconnect = disconnect;});
    const remove = vi.spyOn(document, 'removeEventListener'), rendered = render(<Example/>);
    fireEvent.click(screen.getByRole('combobox')); expect(observe).toHaveBeenCalledTimes(2);
    rendered.unmount(); expect(disconnect).toHaveBeenCalledOnce();
    expect(remove).toHaveBeenCalledWith('pointerdown', expect.any(Function), true); expect(remove).toHaveBeenCalledWith('scroll', expect.any(Function), true);
  });
});
