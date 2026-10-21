import { renderView, node } from './ui-components.js';
import { conversationLabel, snapshotStatus, STAGE_NAMES } from './presentation.js';
import { icon } from './icons.js';
import { imageThumbnail } from './image-preview.js';
import { activityLabel } from './activity.js';
import { focusConversation, focusStartIndex, focusSceneFrames, FocusMotion } from './focus-state.js';
import { FocusPager } from './focus-pager.js';

// Flatten structural wrappers, keeping actual controls and their listeners intact.
function contentBlocks(root) {
  const blocks = [];
  const visit = element => {
    if (element.nodeType === 11) { [...element.children].forEach(visit); return; }
    if (element.nodeType !== 1 || element.classList.contains('ui-component-heading')) return;
    if (element.tagName === 'DETAILS') {
      blocks.push(node('h4', 'focus-section-label', element.querySelector('summary')?.textContent));
      [...element.children].filter(child => child.tagName !== 'SUMMARY').forEach(visit); return;
    }
    if (element.classList.contains('ui-component') || element.classList.contains('paged-content') ||
        element.classList.contains('ui-cooking-step') || element.classList.contains('ui-cooking-guide') ||
        element.classList.contains('ui-timers-guide') || element.classList.contains('ui-timer-panel') ||
        element.classList.contains('timer-create-form') || element.classList.contains('assistant-body') ||
        element.tagName === 'DIV' && !element.className && element.children.length) {
      [...element.children].forEach(visit); return;
    }
    blocks.push(element);
  };
  visit(root); return blocks;
}

export class CookingFocus {
  constructor(workspace, { session, onChange, activity = () => ({}), renderMessage, retry }) {
    this.workspace = workspace; this.session = session; this.onChange = onChange;
    this.activity = activity; this.renderMessage = renderMessage; this.retry = retry;
    this.root = document.getElementById('kitchen-focus'); this.content = document.getElementById('focus-content');
    this.reply = document.getElementById('focus-reply'); this.title = document.getElementById('focus-title');
    this.subtitle = document.getElementById('focus-subtitle'); this.stage = document.getElementById('focus-stage');
    this.status = document.getElementById('focus-activity'); this.assistant = document.getElementById('focus-assistant');
    this.work = document.getElementById('focus-workspace'); this.workToggle = document.getElementById('focus-work-toggle');
    this.tools = document.getElementById('focus-tools');
    this.slot = document.getElementById('focus-composer-slot'); this.prompts = document.getElementById('focus-prompts');
    this.composer = document.querySelector('.composer-area'); this.input = document.getElementById('composer-input');
    this.homeAnchor = document.createComment('composer-home'); this.composer.before(this.homeAnchor);
    this.originalPlaceholder = this.input.placeholder; this.active = null; this.talking = false; this.closing = false;
    this.motion = new FocusMotion(); this.talk = document.getElementById('focus-talk-toggle');
    this.suggestionToggle = document.getElementById('focus-suggestions-toggle');
    this.suggestions = document.getElementById('reply-suggestions');
    this.suggestionToggle.addEventListener('click', () => this.showSuggestions(!this.root.classList.contains('suggestions-open')));
    this.suggestions?.addEventListener('click', () => this.showSuggestions(false));
    if (this.suggestions) {
      const syncSuggestions = () => {
        this.suggestionToggle.hidden = this.suggestions.hidden || !this.suggestions.children.length;
        if (this.suggestionToggle.hidden) this.showSuggestions(false);
      };
      this.suggestionObserver = new MutationObserver(syncSuggestions);
      this.suggestionObserver.observe(this.suggestions, { attributes:true, attributeFilter:['hidden'], childList:true });
      syncSuggestions();
    }
    this.contentPager = new FocusPager(this.content, document.getElementById('focus-content-pages'), { label: '内容' });
    this.replyPager = new FocusPager(this.reply, document.getElementById('focus-reply-pages'), { label: '交流' });
    this.workspace.onTimerStateChange = () => this.contentPager.schedule();
    document.getElementById('exit-focus').addEventListener('click', () => this.close());
    this.workToggle.addEventListener('click', () => this.setTalking(false));
    this.talk.addEventListener('click', () => this.setTalking(true));
    this.composer.addEventListener('submit', () => { if (this.active) this.setTalking(true); });
    this.root.addEventListener('keydown', event => {
      if (document.querySelector('dialog[open]')) return;
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopPropagation();
        if (this.root.classList.contains('suggestions-open')) { this.showSuggestions(false); this.suggestionToggle.focus(); }
        else if (this.talking) { this.setTalking(false); this.talk.focus(); }
        else this.close();
      }
      if (event.key === 'Tab') {
        const controls = [...this.root.querySelectorAll('button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),a[href],[tabindex="0"]')]
          .filter(control => control.getClientRects().length && !control.closest('[inert]'));
        const first = controls[0], last = controls.at(-1);
        if (event.shiftKey && (document.activeElement === first || !this.root.contains(document.activeElement))) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || !this.root.contains(document.activeElement))) { event.preventDefault(); first?.focus(); }
      }
    });
  }

  captureSource(view, trigger) {
    const card = trigger?.closest('.ui-component') || document.querySelector(`.ui-component[data-view-id="${CSS.escape(view.id)}"]`);
    return { card, trigger, viewId: view.id };
  }
  sourceCard(source) {
    return source?.card?.isConnected ? source.card
      : source?.viewId ? document.querySelector(`#app .ui-component[data-view-id="${CSS.escape(source.viewId)}"]`) : null;
  }
  animate(source, closing, finish) {
    const main = document.querySelector('#app .main');
    const frames = focusSceneFrames(innerWidth + 24, closing);
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const animations = !reduced && typeof this.root.animate === 'function'
      ? [[this.root, frames.focus], [main, frames.main]].filter(([element]) => element).map(([element, keyframes]) =>
        element.animate(keyframes, { duration: closing ? 260 : 340, easing: 'cubic-bezier(.22,.75,.2,1)', fill: 'both' })) : [];
    this.motion.start(animations.length ? { finished: Promise.all(animations.map(animation => animation.finished)), cancel: () => animations.forEach(animation => animation.cancel()) } : null, finish);
  }
  open(view, sessionId, source = this.captureSource(view)) {
    const previous = this.active;
    this.motion.cancel(); this.closing = false; this.completeClose = null;
    this.root.classList.remove('focus-closing'); this.root.style.pointerEvents = '';
    const startIndex = previous?.sessionId === sessionId ? previous.startIndex : focusStartIndex(this.session());
    this.active = { kind: view.kind, id: view.id, view, sessionId, source, startIndex };
    this.signature = null; this.replySignature = null; this.contentIdentity = null; this.wasRunning = false;
    this.talking = Boolean(this.activity(sessionId).running); this.root.hidden = false;
    this.slot.append(this.composer); this.composer.hidden = false;
    document.getElementById('app').inert = true; document.body.classList.add('focus-scene-active');
    this.updateTalking(); this.refresh(); this.animate(source, false);
    document.getElementById('exit-focus').focus(); this.onChange?.();
  }
  setTalking(talking) { this.talking = talking; this.updateTalking(); }
  showSuggestions(open) { this.root.classList.toggle('suggestions-open', open); this.suggestionToggle.setAttribute('aria-expanded', String(open)); }
  updateTalking() {
    this.root.classList.toggle('talking', this.talking);
    this.work.hidden = this.talking; this.work.inert = this.talking;
    this.assistant.hidden = !this.talking; this.assistant.inert = !this.talking;
    this.talk.setAttribute('aria-pressed', String(this.talking)); this.workToggle.setAttribute('aria-pressed', String(!this.talking));
    this.contentPager.setEnabled(Boolean(this.active && !this.talking)); this.replyPager.setEnabled(Boolean(this.active && this.talking));
    this.input.placeholder = this.talking ? '还有哪里不确定？接着问问…' : '问问这份清单或当前步骤…';
  }
  renderConversation(active, state) {
    const signature = JSON.stringify([state.messages, state.failed]);
    if (signature !== this.replySignature) {
      const units = [];
      state.messages.forEach((message, index) => {
        const parts = [];
        if (message.images?.length) {
          for (let imageIndex = 0; imageIndex < message.images.length; imageIndex += 3) {
            const images = node('div', 'message-images');
            message.images.slice(imageIndex, imageIndex + 3).forEach((image, offset) => images.append(imageThumbnail(active.sessionId, image, message.images, imageIndex + offset)));
            parts.push(images);
          }
        }
        if (message.content && !message.error && !message.cancelled) {
          const copy = this.renderMessage ? this.renderMessage(message.content) : node('p', '', message.content);
          parts.push(...contentBlocks(copy));
        }
        if (message.error || message.cancelled) parts.push(node('p', message.error ? 'focus-message-error' : 'focus-message-note', message.error || '本次回复已停止。'));
        if (state.retry === message && this.retry) {
          const retry = node('button', 'retry-button focus-retry', message.cancelled ? '重新执行' : '重试这次回复'); retry.type = 'button';
          retry.prepend(icon('refresh-cw')); retry.addEventListener('click', () => this.retry(message)); parts.push(retry);
        }
        parts.forEach((part, partIndex) => {
          const item = node('article', `focus-message ${message.role}`);
          if (partIndex === 0) item.append(node('div', 'focus-message-label', message.role === 'user' ? '你' : 'ChefMate'));
          const copy = node('div', 'focus-message-copy'); copy.append(part); item.append(copy);
          units.push({ node: item, key: `message-${index}-${partIndex}` });
        });
      });
      if (!units.length) units.push({ node: node('div', 'focus-empty', '遇到不确定的地方，随时问问助手。清单和做菜进度都会保留。'), key: 'empty' });
      this.replyPager.setContent(units, { follow: true }); this.replySignature = signature;
    }
    this.status.hidden = !state.status || !state.running;
    if (state.status !== this.activityText) {
      this.activityText = state.status;
      this.status.replaceChildren(state.running ? activityLabel(state.status, `focus:${active.sessionId}:${state.status}`) : node('span', '', state.status));
    }
  }
  refresh() {
    if (!this.active) return;
    const active = this.active, entry = this.workspace.entry(active.sessionId);
    if (entry.snapshot?.stage === 'cooking' && active.kind==='prep_checklist') active.kind = 'cooking_guide';
    const view = entry.views.find(item => item.kind === active.kind) || (entry.loading || entry.presentationPending ? active.view : null);
    if (!view) { this.close(); return; }
    active.id = view.id; active.view = view; this.root.dataset.kind = view.kind;
    const cooking = view.kind === 'cooking_guide' && view.data.recipe_id;
    this.title.textContent = cooking ? view.data.recipe_name : view.title || '厨房工作区';
    this.subtitle.textContent = cooking ? `已完成 ${(view.data.steps || []).filter(step=>step.status==='done').length} / ${(view.data.steps || []).length} 步`
      : conversationLabel(snapshotStatus(entry.snapshot || {}));
    const stage = ({ prep_checklist:'备菜清单', cooking_guide:'专心做菜', recipe_detail:'菜谱做法' })[view.kind]
      || STAGE_NAMES[entry.snapshot?.stage] || '厨房工作区';
    this.stage.textContent = stage; document.getElementById('focus-work-label').textContent = stage;
    const signature = JSON.stringify([view, entry.saving, entry.viewState[view.id], entry.drafts[view.id]]);
    if (signature !== this.signature) {
      const displayedStep = view.data.steps?.[entry.viewState[view.id]?.stepIndex]
        || view.data.steps?.find(step=>step.id===view.data.active_step_id) || view.data.steps?.[0];
      const identity = JSON.stringify([view.kind, view.data.recipe_id, view.data.recipe_ids, displayedStep?.id,
        entry.viewState[view.id]?.stepIndex, entry.viewState[view.id]?.detailSection]);
      const ctx = { ...this.workspace.context(view, active.sessionId), focused: true };
      ctx.onViewState = () => { this.workspace.onInlineChange?.(); this.refresh(); };
      const focusedTool = this.tools.contains(document.activeElement) ? document.activeElement?.dataset.controlKey : null;
      const blocks = contentBlocks(renderView(view, ctx)).filter(block => !cooking || !block.matches('.ui-recipe-name,.cooking-overview'));
      this.tools.replaceChildren(...blocks.filter(block => block.classList.contains('step-navigator')));
      this.work.classList.toggle('with-step-tools', Boolean(this.tools.children.length));
      this.tools.querySelectorAll('button').forEach((button, index) => { button.dataset.controlKey = `focus-step-${index}`; });
      if (focusedTool) [...this.tools.querySelectorAll('[data-control-key]')].find(control => control.dataset.controlKey === focusedTool && !control.disabled)?.focus({ preventScroll: true });
      const units = blocks.filter(block => !block.classList.contains('step-navigator')).map((block, index) => ({ node: block,
        key: block.dataset.focusKey || block.querySelector('[data-control-key]')?.dataset.controlKey || `content-${index}` }));
      this.contentPager.setContent(units, { reset: identity !== this.contentIdentity }); this.contentIdentity = identity; this.signature = signature;
    }
    const state = focusConversation(this.session(), active.startIndex, this.activity(active.sessionId));
    if (state.running && !this.wasRunning) this.setTalking(true);
    this.wasRunning = state.running; this.root.classList.toggle('focus-running', state.running);
    this.talk.replaceChildren(icon('message-circle'), node('span', '', state.running ? '助手正在回复' : '问问助手'));
    this.renderConversation(active, state);
    this.workspace.tick();
  }
  close({ immediate = false } = {}) {
    const active = this.active;
    if (!active) { if (immediate && this.closing && this.completeClose) { this.motion.cancel(); this.completeClose(); } return; }
    this.active = null; this.closing = true; this.root.classList.add('focus-closing'); this.root.style.pointerEvents = 'none';
    this.contentPager.setEnabled(false); this.replyPager.setEnabled(false);
    const finish = () => {
      if (!this.closing) return;
      this.completeClose = null; this.closing = false; this.root.hidden = true; this.root.style.pointerEvents = '';
      this.root.classList.remove('focus-closing', 'talking', 'focus-running'); document.body.classList.remove('focus-scene-active');
      this.showSuggestions(false);
      this.homeAnchor.after(this.composer); this.composer.hidden = false; this.input.placeholder = this.originalPlaceholder;
      document.getElementById('app').inert = false; this.contentPager.clear(); this.replyPager.clear(); this.status.hidden = true;
      this.onChange?.();
      const source = this.activity(active.sessionId).running ? null
        : active.source?.trigger?.isConnected ? active.source.trigger : this.sourceCard(active.source)?.querySelector('.focus-button');
      (source || this.input).focus({ preventScroll: true });
    };
    this.completeClose = finish;
    if (immediate) { this.motion.cancel(); finish(); } else this.animate(active.source, true, finish);
  }
}
