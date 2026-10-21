// The native select remains the form value. The shared combobox only presents
// and commits choices, so callers keep their existing change/validation logic.
let sequence = 0;
const element = (tag, className = '') => {
  const node = document.createElement(tag); node.className = className; return node;
};

function choices(input) {
  return [...(input.options || input.children)].map((option,index) => ({option,index})).filter(({option}) => option.tagName === 'OPTION' && !option.hidden).map(({option,index}) => ({
    value: option.value, label: option.label || option.textContent, index,
    disabled: option.disabled || option.parentElement?.tagName === 'OPTGROUP' && option.parentElement.disabled,
  }));
}

export function enhanceSelect(input, surface, {label = ''} = {}) {
  const trigger = element('button', 'control-select-trigger'),copy = element('span', 'control-select-value');
  const arrow = element('span', 'control-select-arrow'); arrow.setAttribute('aria-hidden', 'true');
  const popover = element('div', 'control-select-popover'), list = element('div', 'control-select-list');
  const id = `control-select-${++sequence}`; list.id = `${id}-list`; trigger.id = `${id}-trigger`;
  trigger.type = 'button'; trigger.setAttribute('role', 'combobox'); trigger.setAttribute('aria-haspopup', 'listbox');
  trigger.setAttribute('aria-expanded', 'false'); trigger.setAttribute('aria-controls', list.id);
  list.setAttribute('role', 'listbox'); popover.append(list); popover.hidden = true;
  const nativePopover = typeof popover.showPopover === 'function'; if (nativePopover) popover.setAttribute('popover', 'manual');
  input.classList.add('control-select-native'); input.tabIndex = -1; input.setAttribute('aria-hidden', 'true');
  trigger.append(copy, arrow); surface.append(input, trigger, popover);
  const documentRoot = input.ownerDocument || document;
  const viewport = documentRoot.defaultView || (typeof window !== 'undefined' ? window : null);
  let open = false, active = -1, options = [], rows = [], fingerprint = '', search = '', searchedAt = 0, disconnect = null;

  function accessibleName() {
    const labelledBy = input.getAttribute('aria-labelledby');
    if (labelledBy) { trigger.setAttribute('aria-labelledby', labelledBy); trigger.removeAttribute('aria-label'); }
    else { trigger.removeAttribute('aria-labelledby'); trigger.setAttribute('aria-label', input.getAttribute('aria-label') || label || [...(input.labels || [])].map(item => item.textContent).join(' ').trim() || '选择选项'); }
    list.setAttribute('aria-label', input.getAttribute('aria-label') || label || '可选项');
    for (const attribute of ['aria-describedby', 'aria-invalid']) {
      const value = input.getAttribute(attribute); if (value != null) trigger.setAttribute(attribute, value); else trigger.removeAttribute(attribute);
    }
    trigger.setAttribute('aria-required', String(Boolean(input.required)));
  }
  function highlight(index, scroll = true) {
    active = index;
    rows.forEach((row, item) => row.dataset.active = String(item === active));
    if (rows[active]) {
      trigger.setAttribute('aria-activedescendant', rows[active].id);
      if (open && scroll) rows[active].scrollIntoView?.({block:'nearest'});
    } else trigger.removeAttribute('aria-activedescendant');
  }
  function position() {
    if (!open) return;
    const rect = trigger.getBoundingClientRect(), width = viewport?.innerWidth || documentRoot.documentElement.clientWidth;
    const height = viewport?.innerHeight || documentRoot.documentElement.clientHeight;
    if (!rect.width || rect.bottom <= rect.top || rect.bottom < 0 || rect.top > height || rect.right < 0 || rect.left > width) { close(); return; }
    const padding = 8, gap = 6, below = height - rect.bottom - padding - gap, above = rect.top - padding - gap;
    const upwards = below < 180 && above > below, maximum = Math.max(80, Math.min(340, upwards ? above : below));
    const menuWidth = Math.min(Math.max(rect.width, 180), width - padding * 2);
    popover.style.width = `${menuWidth}px`; popover.style.maxHeight = `${maximum}px`;
    popover.style.left = `${Math.max(padding, Math.min(rect.left, width - menuWidth - padding))}px`;
    const measuredHeight = Math.min(popover.scrollHeight || maximum, maximum);
    popover.style.top = `${upwards ? Math.max(padding, rect.top - gap - measuredHeight) : rect.bottom + gap}px`;
  }
  function outside(event) { if (!surface.contains(event.target)) close(); }
  function externalKey(event) { if (event.key === 'Escape' && open) { event.preventDefault(); event.stopPropagation(); close(true); } }
  function close(focus = false) {
    if (!open) return; open = false;
    if (nativePopover && popover.matches(':popover-open')) popover.hidePopover();
    popover.hidden = true; trigger.setAttribute('aria-expanded', 'false'); trigger.removeAttribute('aria-activedescendant');
    documentRoot.removeEventListener?.('pointerdown', outside, true); documentRoot.removeEventListener?.('keydown', externalKey, true);
    viewport?.removeEventListener('resize', position); viewport?.removeEventListener('scroll', position, true);
    disconnect?.disconnect(); disconnect = null; search = '';
    if (focus && !input.disabled && surface.isConnected) trigger.focus();
  }
  function commit(index) {
    if (input.disabled || options[index]?.disabled || !options[index]) return;
    const changed = input.selectedIndex !== options[index].index; input.selectedIndex = options[index].index;
    close(true); sync();
    if (changed) { input.dispatchEvent(new Event('input', {bubbles:true})); input.dispatchEvent(new Event('change', {bubbles:true})); }
  }
  function render() {
    const next = choices(input), signature = JSON.stringify(next);
    options = next;
    if (signature !== fingerprint) {
      fingerprint = signature; rows = []; list.replaceChildren();
      options.forEach((option, index) => {
        const row = element('div', 'control-select-option'),text = element('span', 'control-select-option-copy');
        row.id = `${id}-option-${index}`; row.setAttribute('role', 'option'); row.setAttribute('aria-disabled', String(Boolean(option.disabled)));
        text.textContent = option.label; row.append(text); row.addEventListener('pointerdown', event => event.preventDefault());
        row.addEventListener('pointermove', event => { if (!option.disabled && event.pointerType !== 'touch') highlight(index, false); });
        row.addEventListener('click', () => commit(index)); rows.push(row); list.append(row);
      });
    }
    rows.forEach((row, index) => row.setAttribute('aria-selected', String(options[index].index === input.selectedIndex)));
  }
  function sync() {
    accessibleName(); trigger.disabled = Boolean(input.disabled); render();
    copy.textContent = options.find(option => option.index === input.selectedIndex)?.label || options.find(option => option.value === input.value)?.label || '请选择';
    if (input.disabled) close();
    if (open) { if (!options[active] || options[active].disabled) highlight(options.findIndex(option => !option.disabled)); position(); }
  }
  function show(direction = 0) {
    if (input.disabled || open || !surface.isConnected) return;
    sync(); open = true; popover.hidden = false; if (nativePopover) popover.showPopover();
    trigger.setAttribute('aria-expanded', 'true'); position(); if (!open) return;
    const selected = options.findIndex(option => option.index === input.selectedIndex && !option.disabled);
    highlight(direction < 0 ? options.findLastIndex(option => !option.disabled) : selected >= 0 ? selected : options.findIndex(option => !option.disabled));
    documentRoot.addEventListener?.('pointerdown', outside, true); documentRoot.addEventListener?.('keydown', externalKey, true);
    viewport?.addEventListener('resize', position); viewport?.addEventListener('scroll', position, true);
    if (typeof MutationObserver !== 'undefined' && documentRoot.documentElement) {
      disconnect = new MutationObserver(() => { if (!surface.isConnected) close(); });
      disconnect.observe(documentRoot.documentElement, {childList:true, subtree:true});
    }
  }
  trigger.addEventListener('click', () => open ? close() : show());
  trigger.addEventListener('keydown', event => {
    if (input.disabled || event.ctrlKey || event.metaKey || event.altKey) return;
    if (['ArrowDown','ArrowUp','Home','End'].includes(event.key)) {
      event.preventDefault(); if (!open) { show(event.key === 'ArrowUp' || event.key === 'End' ? -1 : 0); if (event.key === 'Home') highlight(options.findIndex(option => !option.disabled)); return; }
      const valid = options.map((option,index) => option.disabled ? -1 : index).filter(index => index >= 0);
      const current = valid.indexOf(active), direction = event.key === 'ArrowUp' ? -1 : 1;
      highlight(event.key === 'Home' ? valid[0] : event.key === 'End' ? valid.at(-1) : valid[Math.max(0, Math.min(valid.length - 1, current + direction))] ?? -1);
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault(); if (open) commit(active); else show();
    } else if (event.key === 'Tab') close();
    else if (event.key.length === 1) {
      event.preventDefault(); if (!open) show(); const time = Date.now(); search = time - searchedAt > 700 ? event.key : search + event.key; searchedAt = time;
      const repeated = [...search].every(character => character.toLocaleLowerCase() === search[0].toLocaleLowerCase());
      const needle = (repeated ? search[0] : search).toLocaleLowerCase(), start = Math.max(0, active + 1);
      const indexes = options.map((_,index) => (index + start) % options.length);
      const match = indexes.find(index => !options[index].disabled && options[index].label.toLocaleLowerCase().startsWith(needle)); if (match != null) highlight(match);
    }
  });
  input.addEventListener('focus', () => trigger.focus());
  input.addEventListener('click', event => { event.preventDefault(); if (!input.disabled) { trigger.focus(); show(); } });
  input.addEventListener('invalid', event => { event.preventDefault(); sync(); trigger.focus(); });
  input.form?.addEventListener('reset', () => queueMicrotask(sync));
  trigger.addEventListener('blur', event => { if (!surface.contains(event.relatedTarget)) close(); });
  if (typeof MutationObserver !== 'undefined') new MutationObserver(sync).observe(input, {
    attributes:true, childList:true, subtree:true, characterData:true,
    attributeFilter:['disabled','required','selected','value','label','aria-label','aria-labelledby','aria-describedby','aria-invalid'],
  });
  return {sync, close, trigger};
}
