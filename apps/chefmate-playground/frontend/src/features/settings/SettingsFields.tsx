import {useId, type ReactNode} from 'react';

export function Field({label, value, onChange, hint, type = 'text', options, min, max, step, required, disabled, maxLength, autoComplete}: {
  label: string; value: any; onChange: (value: any) => void; hint?: string; type?: string;
  options?: readonly (readonly [string | number, string])[]; min?: number; max?: number; step?: number | string; required?: boolean; disabled?: boolean; maxLength?: number; autoComplete?: string;
}) {
  const id = useId();
  const attributes = {id, disabled, 'aria-labelledby': `${id}-label`, 'aria-describedby': hint ? `${id}-hint` : undefined};
  return <div className={`setting-field ${type === 'checkbox' ? 'setting-switch-field' : ''}`}>
    <label className="setting-label" htmlFor={id}><span id={`${id}-label`}>{label}</span>{type === 'checkbox' && hint && <small id={`${id}-hint`}>{hint}</small>}</label>
    <div className="setting-control">
      {options ? <select {...attributes} value={value ?? ''} required={required} onChange={event => onChange(typeof options[0][0] === 'number' ? Number(event.target.value) : event.target.value)}>{options.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select> : type === 'textarea' ? <textarea {...attributes} rows={5} value={value ?? ''} required={required} maxLength={maxLength} onChange={event => onChange(event.target.value)}/> : type === 'checkbox' ? <input {...attributes} role="switch" type="checkbox" checked={value === true} onChange={event => onChange(event.target.checked)}/> : <input {...attributes} type={type} value={value ?? ''} min={min} max={max} step={step ?? (type === 'number' ? 1 : undefined)} maxLength={maxLength} autoComplete={autoComplete ?? (type === 'password' ? 'new-password' : undefined)} required={required ?? type === 'number'} onChange={event => onChange(type === 'number' && event.target.value !== '' ? Number(event.target.value) : event.target.value)}/>}
      {hint && type !== 'checkbox' && <small id={`${id}-hint`}>{hint}</small>}
    </div>
  </div>;
}

export function SettingsGroup({title, children}: {title: string; children: ReactNode}) {
  return <section className="settings-group"><h2>{title}</h2>{children}</section>;
}
