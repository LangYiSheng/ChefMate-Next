import {createContext, useContext, useEffect, useId, useRef, useState, type ReactNode} from 'react';
import {Select} from '../../components/ui/Select';

const GroupTabs = createContext<{active: string; items: readonly string[]; prefix: string} | null>(null);

export function Field({label, value, onChange, hint, type = 'text', options, min, max, step, required, disabled, minLength, maxLength, autoComplete, action}: {
  label: string; value: any; onChange: (value: any) => void; hint?: string; type?: string;
  options?: readonly (readonly [string | number, string])[]; min?: number; max?: number; step?: number | string; required?: boolean; disabled?: boolean; minLength?: number; maxLength?: number; autoComplete?: string; action?: ReactNode;
}) {
  const id = useId();
  const attributes = {id, disabled, 'aria-labelledby': `${id}-label`, 'aria-describedby': hint ? `${id}-hint` : undefined};
  return <div className={`setting-field ${type === 'checkbox' ? 'setting-switch-field' : ''}`}>
    <label className="setting-label" htmlFor={id}><span id={`${id}-label`}>{label}</span>{type === 'checkbox' && hint && <small id={`${id}-hint`}>{hint}</small>}</label>
    <div className={`setting-control ${action ? 'has-field-action' : ''}`}>
      {options ? <Select {...attributes} value={String(value ?? '')} required={required} options={options.map(([key, text]) => ({value: String(key), label: text}))} onValueChange={next => onChange(typeof options[0]?.[0] === 'number' ? Number(next) : next)}/> : type === 'textarea' ? <textarea {...attributes} rows={5} value={value ?? ''} required={required} maxLength={maxLength} onChange={event => onChange(event.target.value)}/> : type === 'checkbox' ? <input {...attributes} role="switch" type="checkbox" checked={value === true} onChange={event => onChange(event.target.checked)}/> : <input {...attributes} type={type} value={value ?? ''} min={min} max={max} step={step ?? (type === 'number' ? 1 : undefined)} minLength={minLength} maxLength={maxLength} autoComplete={autoComplete ?? (type === 'password' ? 'new-password' : undefined)} required={required ?? type === 'number'} onChange={event => onChange(type === 'number' && event.target.value !== '' ? Number(event.target.value) : event.target.value)}/>}
      {hint && type !== 'checkbox' && <small id={`${id}-hint`}>{hint}</small>}{action}
    </div>
  </div>;
}

export function SettingsGroup({title, children}: {title: string; children: ReactNode}) {
  const tabs = useContext(GroupTabs), unique = useId(), index = tabs?.items.indexOf(title) ?? -1;
  const grouped = Boolean(tabs && tabs.items.length > 1 && index >= 0), panelId = grouped ? `${tabs!.prefix}-panel-${index}` : `${unique}-group`;
  const titleId = `${unique}-title`;
  return <section className="settings-group" id={panelId} data-settings-index={title} role={grouped ? 'tabpanel' : undefined} aria-labelledby={grouped ? `${tabs!.prefix}-tab-${index}` : titleId} hidden={grouped && tabs!.active !== title}>{!grouped && <h2 className="settings-group-title" id={titleId}>{title}</h2>}{children}</section>;
}

export function SettingsTabs({items, label, children}: {items: readonly string[]; label: string; children: ReactNode}) {
  const [selected, setSelected] = useState(items[0] || ''), prefix = useId();
  const frame = useRef<number | null>(null), invalidPass = useRef<{hidden: boolean} | null>(null);
  useEffect(() => () => {if (frame.current !== null) cancelAnimationFrame(frame.current);}, []);
  const active = items.includes(selected) ? selected : items[0] || '';
  return <GroupTabs.Provider value={{active, items, prefix}}><div className="settings-tabbed-groups" onInvalidCapture={event => {
    const input = event.target as HTMLElement, panel = input.closest<HTMLElement>('[role=tabpanel]');
    if (invalidPass.current) {if (invalidPass.current.hidden) event.preventDefault(); return;}
    invalidPass.current = {hidden: panel?.hidden === true}; queueMicrotask(() => {invalidPass.current = null;});
    if (!panel?.hidden) return;
    event.preventDefault();
    const index = Number(panel.id.slice(panel.id.lastIndexOf('-') + 1));
    if (items[index]) setSelected(items[index]);
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {frame.current = null; if (!input.isConnected || panel.hidden) return; const target = input.closest('.ui-select')?.querySelector<HTMLElement>('[role=combobox]') || input; target.focus({preventScroll: true}); if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) target.reportValidity();});
  }}>
    {items.length > 1 && <div role="tablist" aria-label={label} className="settings-tabs" onKeyDown={event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role=tab]')], index = buttons.indexOf(event.target as HTMLButtonElement);
      if (index < 0) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
      setSelected(items[next]); buttons[next].focus();
    }}>{items.map((title, index) => <button type="button" role="tab" className="settings-tab" id={`${prefix}-tab-${index}`} aria-controls={`${prefix}-panel-${index}`} aria-selected={active === title} tabIndex={active === title ? 0 : -1} key={title} onClick={() => setSelected(title)}>{title}</button>)}</div>}
    {children}
  </div></GroupTabs.Provider>;
}
