// Shared presentation around native controls. Native focus, selection, form
// validation and keyboard behavior stay on the actual input/select/textarea.
function element(tag, className = '') {
  const item = document.createElement(tag);
  item.className = className;
  return item;
}

export function stepControl(input, direction) {
  if (input.disabled || input.readOnly || !['up', 'down'].includes(direction)) return false;
  const previous = input.value;
  try { direction === 'up' ? input.stepUp() : input.stepDown(); }
  catch { return false; }
  if (input.value === previous) return false;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}

export function enhanceControl(input, { label = '', stepper = true } = {}) {
  if (input._controlSurface) {
    input._syncControl?.();
    return input._controlSurface;
  }
  const type = input.type;
  const isSwitch = type === 'checkbox';
  const isSelect = input.tagName === 'SELECT';
  const isNumber = type === 'number' && stepper;
  const isPassword = type === 'password';
  const surface = element('span', `control-surface${isSwitch ? ' control-switch' : ''}${isSelect ? ' control-select' : ''}${isNumber ? ' control-number' : ''}${input.tagName === 'TEXTAREA' ? ' control-multiline' : ''}`);
  input.classList.add('control-input');
  input._controlSurface = surface;
  let touched = false;
  const buttons = [];
  const auxiliary = [];
  const sync = () => {
    surface.dataset.disabled = String(Boolean(input.disabled));
    if (touched && !isSwitch) input.setAttribute('aria-invalid', String(!input.validity.valid));
    for (const [button, direction] of buttons) {
      const number = input.value === '' ? null : Number(input.value);
      const limit = direction === 'down' ? input.min : input.max;
      button.disabled = input.disabled || input.readOnly || number !== null && limit !== ''
        && (direction === 'down' ? number <= Number(limit) : number >= Number(limit));
    }
    for (const button of auxiliary) button.disabled = input.disabled;
  };
  input._syncControl = sync;
  if (isSwitch) {
    input.setAttribute('role', 'switch');
    const track = element('span', 'control-switch-track');
    track.setAttribute('aria-hidden', 'true');
    surface.append(input, track);
  } else if (isNumber) {
    const makeStep = (copy, direction) => {
      const button = element('button', 'control-step');
      button.type = 'button';
      button.textContent = copy;
      button.setAttribute('aria-label', `${direction === 'up' ? '增加' : '减少'}${label}`);
      button.addEventListener('click', event => {
        event.preventDefault();
        stepControl(input, direction);
        sync();
      });
      buttons.push([button, direction]);
      return button;
    };
    surface.append(makeStep('−', 'down'), input, makeStep('+', 'up'));
  } else {
    surface.append(input);
    if (isSelect) {
      const arrow = element('span', 'control-select-arrow');
      arrow.setAttribute('aria-hidden', 'true');
      surface.append(arrow);
    }
    if (isPassword) {
      const reveal = element('button', 'control-reveal');
      reveal.type = 'button'; reveal.textContent = '显示';
      reveal.setAttribute('aria-label', `显示${label}`);
      reveal.setAttribute('aria-pressed', 'false');
      auxiliary.push(reveal);
      reveal.addEventListener('click', event => {
        event.preventDefault();
        if (input.disabled) return;
        const showing = input.type === 'password';
        input.type = showing ? 'text' : 'password';
        reveal.textContent = showing ? '隐藏' : '显示';
        reveal.setAttribute('aria-label', `${showing ? '隐藏' : '显示'}${label}`);
        reveal.setAttribute('aria-pressed', String(showing));
      });
      surface.append(reveal);
    }
  }
  input.addEventListener('input', sync);
  input.addEventListener('change', sync);
  input.addEventListener('blur', () => { touched = true; sync(); });
  input.addEventListener('invalid', () => { touched = true; sync(); });
  if (typeof MutationObserver !== 'undefined') new MutationObserver(sync).observe(input, {
    attributes: true, attributeFilter: ['disabled', 'readonly', 'min', 'max', 'step'],
  });
  sync();
  return surface;
}

export function styleControls(root) {
  for (const input of root.querySelectorAll('input:not([type="file"]):not([type="hidden"]),select,textarea')) {
    if (!input._controlSurface) {
      const parent = input.parentNode;
      const next = input.nextSibling;
      const wrapper = enhanceControl(input, { label: input.getAttribute('aria-label') || input.name || '' });
      if (next) parent.insertBefore(wrapper, next); else parent.append(wrapper);
    } else input._syncControl?.();
  }
}
