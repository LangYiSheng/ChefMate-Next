import { icon } from './icons.js';

// Split at a readable boundary, while preserving every character (including spaces).
export function splitToFit(text, fits) {
  const pieces = []; let offset = 0;
  while (offset < text.length) {
    let low = 1, high = text.length - offset, length = 0;
    while (low <= high) {
      const middle = Math.floor((low + high) / 2);
      if (fits(offset, offset + middle)) { length = middle; low = middle + 1; }
      else high = middle - 1;
    }
    length = Math.max(1, length);
    if (offset + length < text.length) {
      const sample = text.slice(offset, offset + length);
      const boundary = [...sample.matchAll(/[。！？；，、\s]/gu)].at(-1)?.index;
      if (boundary != null && boundary >= length * .65) length = boundary + 1;
    }
    // Avoid cutting a surrogate pair in half.
    if (/[\uD800-\uDBFF]/.test(text[offset + length - 1]) && /[\uDC00-\uDFFF]/.test(text[offset + length] || '')) length = length > 1 ? length - 1 : 2;
    pieces.push({ start: offset, end: offset + length }); offset += length;
  }
  return pieces;
}

export function pageForAnchor(pages, anchor) {
  if (!anchor) return 0;
  const index = pages.findIndex(page => page.some(part => part.key === anchor.key && part.start <= anchor.start && part.end > anchor.start));
  if (index >= 0) return index;
  const sameKey = pages.findIndex(page => page.some(part => part.key === anchor.key));
  return sameKey < 0 ? 0 : sameKey;
}

export function swipeDirection(start, end) {
  if (!start || !end) return 0;
  const dx = end.x - start.x, dy = end.y - start.y;
  if (Math.abs(dx) < 48 || Math.abs(dx) < Math.abs(dy) * 1.35 || end.time - start.time > 850) return 0;
  return dx < 0 ? 1 : -1;
}

function sliceElement(element, start, end) {
  const copy = element.cloneNode(false);
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  let current, offset = 0, first, last;
  while ((current = walker.nextNode())) {
    const next = offset + current.length;
    if (!first && start < next) first = [current, Math.max(0, start - offset)];
    if (end <= next) { last = [current, Math.max(0, end - offset)]; break; }
    offset = next;
  }
  if (first && last) {
    const range = document.createRange(); range.setStart(...first); range.setEnd(...last);
    copy.append(range.cloneContents());
  }
  return copy;
}

/** A measured deck, rather than a scroll container. Controls are moved, never cloned. */
export class FocusPager {
  constructor(viewport, controls, { label = '内容', onChange } = {}) {
    this.viewport = viewport; this.controls = controls; this.label = label; this.onChange = onChange;
    this.units = []; this.pages = []; this.index = 0; this.enabled = false;
    this.page = document.createElement('div'); this.page.className = 'focus-page';
    this.measure = document.createElement('div'); this.measure.className = 'focus-page focus-page-measure';
    this.measure.setAttribute('aria-hidden', 'true'); this.measure.inert = true;
    this.previous = this.makeButton('上一张', 'chevron-left', -1);
    this.next = this.makeButton('下一张', 'chevron-right', 1);
    this.counter = document.createElement('span'); this.counter.className = 'focus-page-counter';
    this.counter.setAttribute('aria-live', 'polite');
    controls.replaceChildren(this.previous, this.counter, this.next);
    this.observer = new ResizeObserver(() => this.schedule()); this.observer.observe(viewport);
    viewport.addEventListener('pointerdown', event => {
      if (event.pointerType !== 'touch' || event.target.closest('button,input,textarea,a,select')) return;
      this.touch = { x: event.clientX, y: event.clientY, time: event.timeStamp };
    });
    viewport.addEventListener('pointerup', event => {
      const direction = swipeDirection(this.touch, { x: event.clientX, y: event.clientY, time: event.timeStamp });
      this.touch = null; if (direction) this.go(direction);
    });
    viewport.addEventListener('pointercancel', () => { this.touch = null; });
    // A deliberate wheel gesture turns one card; continuous wheel events do not race through the deck.
    viewport.addEventListener('wheel', event => {
      if (!this.enabled || event.ctrlKey || Math.abs(event.deltaY) + Math.abs(event.deltaX) < 18) return;
      event.preventDefault();
      const now = performance.now(); if (now - (this.lastWheel || 0) < 420) return;
      this.lastWheel = now; this.go((Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY) > 0 ? 1 : -1);
    }, { passive: false });
    viewport.addEventListener('keydown', event => {
      if (event.target.closest('input,textarea,select,button,a')) return;
      if (['ArrowRight', 'PageDown', 'ArrowLeft', 'PageUp'].includes(event.key)) {
        event.preventDefault(); this.go(['ArrowRight', 'PageDown'].includes(event.key) ? 1 : -1);
      }
    });
  }

  makeButton(text, glyph, direction) {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'focus-page-button';
    button.append(icon(glyph), document.createTextNode(text)); button.addEventListener('click', () => this.go(direction));
    return button;
  }
  setEnabled(enabled) { this.enabled = enabled; if (enabled) this.schedule(); }
  anchor() { const part = this.pages[this.index]?.[0]; return part && { key: part.key, start: part.start }; }
  setContent(units, { reset = false, follow = false } = {}) {
    this.pendingAnchor = reset ? null : this.anchor();
    this.follow = follow && (!this.pages.length || this.index === this.pages.length - 1);
    this.units = units.map((value, index) => ({ node: value.node || value, key: value.key || `block-${index}` }));
    if (reset) this.index = 0;
    this.schedule();
  }
  schedule() {
    if (this.frame || !this.enabled) return;
    this.frame = requestAnimationFrame(() => { this.frame = null; if (this.enabled) this.layout(); });
  }

  layout() {
    if (!this.viewport.clientHeight || !this.viewport.clientWidth) return;
    const focused = this.viewport.contains(document.activeElement) ? document.activeElement?.dataset.controlKey : null;
    const anchor = this.pendingAnchor || this.anchor(); this.pendingAnchor = null;
    this.viewport.replaceChildren(this.page, this.measure);
    const height = this.viewport.clientHeight;
    this.measure.style.height = `${height}px`;
    const fits = nodes => {
      this.measure.replaceChildren(...nodes);
      return this.measure.scrollHeight <= height + 1;
    };
    const pages = []; let page = [];
    const add = part => {
      if (page.length && !fits([...page.map(item => item.node), part.node])) { pages.push(page); page = []; }
      page.push(part);
    };
    const addControls = unit => (unit.controls || []).forEach((control, index) => add({ key: `${unit.key}-actions-${index}`, node: control, start: 0, end: 1 }));
    for (const unit of this.units) {
      if (unit.skipText) { addControls(unit); continue; }
      const length = Math.max(1, unit.node.textContent.length);
      const onlyHeadings = page.length && page.every(part => part.node.matches('.ui-recipe-name,.ui-step-heading,.ui-hint,.focus-section-label,.checklist-scope,.section-tabs'));
      const fillIntroduction = onlyHeadings && length > 24 && !unit.node.querySelector('input,button,select,textarea') &&
        !fits([...page.map(part => part.node), unit.node]);
      if (fits([unit.node]) && !fillIntroduction) {
        add({ ...unit, start: 0, end: length });
        addControls(unit);
        continue;
      }
      // Ordinary long instructions and rich text retain inline formatting. An unusually long
      // interactive row gets its complete copy followed by its original, still-bound controls.
      const interactive = [...unit.node.querySelectorAll('input,button,select,textarea')];
      const source = interactive.length ? unit.node.cloneNode(true) : unit.node;
      if (interactive.length) source.querySelectorAll('input,button,select,textarea').forEach(control => control.remove());
      const text = source.textContent;
      if (!text.length && !interactive.length) { add({ ...unit, start: 0, end: length }); continue; }
      // Use the space below this card's heading for its first passage. Measuring every
      // fragment against an empty page would strand a title above a large blank area.
      if (page.length && text.length && !fits([...page.map(part => part.node), sliceElement(source, 0, Math.min(24, text.length))])) {
        pages.push(page); page = [];
      }
      const leading = page.map(part => part.node);
      const chunks = splitToFit(text, (start, end) => fits([...(start === 0 ? leading : []), sliceElement(source, start, end)]));
      for (const chunk of chunks) add({ key: unit.key, ...chunk, node: sliceElement(source, chunk.start, chunk.end) });
      if (interactive.length) {
        unit.controls = interactive.map(control => {
          const actions = document.createElement('div'); actions.className = 'focus-overflow-actions';
          if (!control.getAttribute('aria-label')) control.setAttribute('aria-label', text.slice(0, 80));
          actions.append(control); return actions;
        });
        unit.node = source; // Subsequent resizes keep the text and live controls separately.
        unit.skipText = !text.length;
      }
      addControls(unit);
    }
    if (page.length || !pages.length) pages.push(page);
    this.pages = pages;
    this.index = this.follow ? pages.length - 1 : Math.min(pages.length - 1, pageForAnchor(pages, anchor));
    this.follow = false; this.measure.replaceChildren(); this.show(0, focused);
  }

  go(delta) {
    const next = Math.max(0, Math.min(this.pages.length - 1, this.index + delta));
    if (next === this.index) return;
    const direction = next > this.index ? 1 : -1; this.index = next; this.follow = false;
    this.show(direction); this.pendingAnchor = this.anchor(); this.onChange?.(this.index);
  }
  show(direction = 0, focusKey = null) {
    const focused = focusKey || (this.viewport.contains(document.activeElement) ? document.activeElement?.dataset.controlKey : null);
    this.page.replaceChildren(...(this.pages[this.index] || []).map(part => part.node));
    this.previous.disabled = this.index <= 0; this.next.disabled = this.index >= this.pages.length - 1;
    this.counter.textContent = `${this.label} ${this.index + 1} / ${Math.max(1, this.pages.length)}`;
    this.viewport.setAttribute('aria-label', `${this.label}，第 ${this.index + 1} 张，共 ${Math.max(1, this.pages.length)} 张`);
    if (direction && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      this.page.animate([{ opacity: .35, transform: `translateX(${direction * 22}px)` }, { opacity: 1, transform: 'none' }], { duration: 180, easing: 'ease-out' });
    }
    if (focused) [...this.page.querySelectorAll('[data-control-key]')].find(control => control.dataset.controlKey === focused)?.focus({ preventScroll: true });
  }
  clear() { this.enabled = false; if (this.frame) cancelAnimationFrame(this.frame); this.frame = null; this.units = []; this.pages = []; this.index = 0; this.page.replaceChildren(); this.measure.replaceChildren(); }
  destroy() {this.clear();this.observer.disconnect();}
}
