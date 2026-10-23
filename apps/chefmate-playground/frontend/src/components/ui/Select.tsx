import {useEffect, useId, useLayoutEffect, useRef, useState, type AriaAttributes, type FocusEventHandler} from 'react';
import {createPortal} from 'react-dom';
import {Icon} from '../Icon';
import './select.css';

export type SelectOption = {value: string; label: string; disabled?: boolean};
export type SelectProps = {
  value: string; onValueChange: (value: string) => void; options: readonly SelectOption[];
  id?: string; name?: string; required?: boolean; disabled?: boolean; placeholder?: string; className?: string;
  'aria-label'?: string; 'aria-labelledby'?: string; 'aria-describedby'?: string; 'aria-invalid'?: AriaAttributes['aria-invalid'];
  onBlur?: FocusEventHandler<HTMLButtonElement>;
};
type Placement = {left: number; top: number; width: number; maxHeight: number; side: 'above' | 'below'};

export function Select({value, onValueChange, options, id, name, required, disabled, placeholder = '请选择', className = '', onBlur, ...aria}: SelectProps) {
  const unique = useId(), triggerId = id || `${unique}-select`, popupId = `${unique}-listbox`, errorId = `${unique}-error`;
  const trigger = useRef<HTMLButtonElement>(null), popup = useRef<HTMLDivElement>(null), optionRefs = useRef<(HTMLDivElement | null)[]>([]);
  const search = useRef(''), searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [open, setOpen] = useState(false), [active, setActive] = useState(-1), [invalid, setInvalid] = useState(false), [placement, setPlacement] = useState<Placement | null>(null);
  const selectedIndex = options.findIndex(option => option.value === value), selected = options[selectedIndex];
  const enabled = options.flatMap((option, index) => option.disabled ? [] : [index]);
  const portalHost = trigger.current?.closest('dialog') || document.body;
  const clearSearch = () => {search.current = ''; if (searchTimer.current) clearTimeout(searchTimer.current); searchTimer.current = null;};
  const close = (restoreFocus = false) => {setOpen(false); setPlacement(null); clearSearch(); if (restoreFocus) trigger.current?.focus({preventScroll: true});};
  const choose = (index: number, restoreFocus = true) => {
    const option = options[index];
    if (!option || option.disabled || disabled) return;
    if (option.value !== value) onValueChange(option.value);
    setInvalid(false); close(restoreFocus);
  };
  const show = (preferred = selectedIndex) => {
    if (disabled || !enabled.length) return;
    setActive(enabled.includes(preferred) ? preferred : enabled[0]); setOpen(true);
  };
  useEffect(() => {if (selected && value !== '') setInvalid(false);}, [value, Boolean(selected)]);
  useEffect(() => {if (disabled) {setOpen(false); setPlacement(null); clearSearch();}}, [disabled]);
  useEffect(() => () => {if (searchTimer.current) clearTimeout(searchTimer.current);}, []);

  useLayoutEffect(() => {
    if (!open) return;
    const anchor = trigger.current, list = popup.current;
    if (!anchor || !list) return;
    let mounted = true;
    const position = () => {
      if (!mounted || !anchor.isConnected) return;
      const rect = anchor.getBoundingClientRect(), view = window.visualViewport;
      const left = view?.offsetLeft || 0, top = view?.offsetTop || 0, width = view?.width || window.innerWidth, height = view?.height || window.innerHeight;
      const margin = 8, gap = 6, bottom = top + height, right = left + width;
      if (rect.bottom < top || rect.top > bottom || rect.right < left || rect.left > right) {close(); return;}
      const desired = Math.min(320, list.scrollHeight || options.length * 40 + 12);
      const below = Math.max(0, bottom - margin - rect.bottom - gap), above = Math.max(0, rect.top - top - margin - gap);
      const side = below < Math.min(desired, 160) && above > below ? 'above' : 'below';
      const maxHeight = Math.max(0, Math.min(320, height - margin * 2, side === 'above' ? above : below));
      const popupWidth = Math.min(Math.max(rect.width, 160), Math.max(0, width - margin * 2));
      const next: Placement = {left: Math.max(left + margin, Math.min(rect.left, right - popupWidth - margin)), top: side === 'above' ? Math.max(top + margin, rect.top - gap - Math.min(desired, maxHeight)) : Math.min(rect.bottom + gap, bottom - margin), width: popupWidth, maxHeight, side};
      setPlacement(previous => previous && Object.keys(next).every(key => previous[key as keyof Placement] === next[key as keyof Placement]) ? previous : next);
    };
    const outside = (event: Event) => {const node = event.target as Node; if (!anchor.contains(node) && !list.contains(node)) close();};
    const scroll = (event: Event) => {if (!list.contains(event.target as Node)) position();};
    position();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(position);
    observer?.observe(anchor); observer?.observe(list);
    document.addEventListener('pointerdown', outside, true); document.addEventListener('focusin', outside);
    document.addEventListener('scroll', scroll, true); window.addEventListener('resize', position);
    window.visualViewport?.addEventListener('resize', position); window.visualViewport?.addEventListener('scroll', position);
    return () => {
      mounted = false;
      observer?.disconnect(); document.removeEventListener('pointerdown', outside, true); document.removeEventListener('focusin', outside);
      document.removeEventListener('scroll', scroll, true); window.removeEventListener('resize', position);
      window.visualViewport?.removeEventListener('resize', position); window.visualViewport?.removeEventListener('scroll', position);
    };
  }, [open, options]);
  useLayoutEffect(() => {
    if (!open || !placement) return;
    if (!enabled.length) {close(); return;}
    const next = options[active] && !options[active].disabled ? active : enabled.includes(selectedIndex) ? selectedIndex : enabled[0] ?? -1;
    if (next !== active) setActive(next);
    optionRefs.current[next]?.scrollIntoView?.({block: 'nearest'});
  }, [open, active, options, placement]);

  return <span className={`ui-select ${className}`} data-disabled={disabled || undefined}>
    <button ref={trigger} id={triggerId} type="button" role="combobox" className="ui-select-trigger" aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? popupId : undefined} aria-activedescendant={open && active >= 0 ? `${popupId}-${active}` : undefined} aria-required={required || undefined} aria-label={aria['aria-label']} aria-labelledby={aria['aria-labelledby']} aria-describedby={[aria['aria-describedby'], invalid ? errorId : ''].filter(Boolean).join(' ') || undefined} aria-invalid={invalid || aria['aria-invalid']} disabled={disabled} onBlur={onBlur} onClick={() => open ? close() : show()} onKeyDown={event => {
      if (disabled) return;
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        const index = enabled.indexOf(active), next = event.key === 'Home' ? enabled[0] : event.key === 'End' ? enabled.at(-1) : !open ? selectedIndex >= 0 && enabled.includes(selectedIndex) ? selectedIndex : event.key === 'ArrowUp' ? enabled.at(-1) : enabled[0] : enabled[(index + (event.key === 'ArrowDown' ? 1 : -1) + enabled.length) % enabled.length];
        if (next !== undefined) {setActive(next); setOpen(true);}
      } else if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault(); if (open) choose(active); else show();
      } else if (event.key === 'Escape' && open) {
        event.preventDefault(); event.stopPropagation(); close(true);
      } else if (event.key === 'Tab' && open) {
        choose(active, false); close();
      } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        if (searchTimer.current) clearTimeout(searchTimer.current);
        search.current += event.key.normalize('NFKC').toLocaleLowerCase();
        const repeated = [...search.current].every(character => character === search.current[0]), query = repeated ? search.current[0] : search.current;
        const start = Math.max(-1, active), ordered = [...enabled.filter(index => index > start), ...enabled.filter(index => index <= start)];
        const match = ordered.find(index => options[index].label.trim().normalize('NFKC').toLocaleLowerCase().startsWith(query));
        if (match !== undefined) {setActive(match); setOpen(true);}
        searchTimer.current = setTimeout(() => {search.current = ''; searchTimer.current = null;}, 700);
      }
    }}><span className={!selected ? 'ui-select-placeholder' : ''}>{selected?.label || placeholder}</span><Icon name="chevron-down"/></button>
    {name && <input type="hidden" name={name} value={value} disabled={disabled}/>}
    {required && <input className="ui-select-validity" tabIndex={-1} aria-hidden="true" type="text" value={selected && value !== '' ? 'selected' : ''} required disabled={disabled} onChange={() => {}} onInvalid={event => {event.preventDefault(); setInvalid(true); trigger.current?.focus({preventScroll: true});}}/>}
    {invalid && <span id={errorId} className="ui-select-error" role="alert">请选择一项。</span>}
    {open && createPortal(<div ref={popup} id={popupId} role="listbox" className="ui-select-popup" aria-labelledby={aria['aria-labelledby'] || triggerId} data-side={placement?.side} onClick={event => event.stopPropagation()} onPointerDown={event => event.stopPropagation()} style={placement ? {left: placement.left, top: placement.top, width: placement.width, maxHeight: placement.maxHeight} : {visibility: 'hidden'}}>
      {options.map((option, index) => <div ref={node => {optionRefs.current[index] = node;}} id={`${popupId}-${index}`} role="option" aria-selected={option.value === value} aria-disabled={option.disabled || undefined} data-active={active === index || undefined} className="ui-select-option" key={`${option.value}-${index}`} onPointerMove={() => {if (!option.disabled) setActive(index);}} onPointerDown={event => event.preventDefault()} onClick={() => choose(index)}><span>{option.label}</span>{option.value === value && <Icon name="check"/>}</div>)}
    </div>, portalHost)}
  </span>;
}
