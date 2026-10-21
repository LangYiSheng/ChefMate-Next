import { renderView, node } from './ui-components.js';
import { conversationLabel, snapshotStatus, STAGE_NAMES } from './presentation.js';
import { icon } from './icons.js';
import { imageThumbnail } from './image-preview.js';
import { focusConversation, focusStartIndex, focusTransform, FocusMotion } from './focus-state.js';

export class CookingFocus {
  constructor(workspace, { session, onChange, activity = () => ({}), renderMessage, retry }) {
    this.workspace = workspace; this.session = session; this.onChange = onChange;
    this.activity = activity; this.renderMessage = renderMessage; this.retry = retry;
    this.root = document.getElementById('kitchen-focus'); this.content = document.getElementById('focus-content');
    this.reply = document.getElementById('focus-reply'); this.title = document.getElementById('focus-title');
    this.subtitle = document.getElementById('focus-subtitle'); this.stage = document.getElementById('focus-stage');
    this.status = document.getElementById('focus-activity'); this.assistant = document.getElementById('focus-assistant');
    this.slot = document.getElementById('focus-composer-slot'); this.prompts = document.getElementById('focus-prompts');
    this.composer = document.querySelector('.composer-area'); this.input = document.getElementById('composer-input');
    this.homeAnchor = document.createComment('composer-home'); this.composer.before(this.homeAnchor);
    this.originalPlaceholder = this.input.placeholder; this.active = null; this.talking = false; this.closing = false;
    this.motion = new FocusMotion(); this.talk = document.getElementById('focus-talk-toggle');
    document.getElementById('exit-focus').addEventListener('click', () => this.close());
    this.root.addEventListener('keydown', event => {
      if (document.querySelector('dialog[open]')) return;
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopPropagation();
        if (this.talking) { this.talking = false; this.updateTalking(); this.talk.focus(); }
        else this.close();
      }
      if (event.key === 'Tab') {
        const controls = [...this.root.querySelectorAll('button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),a[href],summary')]
          .filter(control => control.getClientRects().length && !control.closest('[inert]'));
        const first = controls[0], last = controls.at(-1);
        if (event.shiftKey && (document.activeElement === first || !this.root.contains(document.activeElement))) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || !this.root.contains(document.activeElement))) { event.preventDefault(); first?.focus(); }
      }
    });
    this.talk.addEventListener('click', () => {
      this.talking = !this.talking; this.updateTalking();
      if (this.talking) this.input.focus();
    });
  }

  captureSource(view, trigger) {
    const card = trigger?.closest('.ui-component') || document.querySelector(`.ui-component[data-view-id="${CSS.escape(view.id)}"]`);
    return { card, trigger, viewId: view.id, rect: card?.getBoundingClientRect() };
  }

  sourceCard(source) {
    return source?.card?.isConnected ? source.card
      : source?.viewId ? document.querySelector(`#app .ui-component[data-view-id="${CSS.escape(source.viewId)}"]`) : null;
  }

  animate(source, closing, finish) {
    this.motion.cancel();
    const target = this.root.getBoundingClientRect();
    const rect = closing ? this.sourceCard(source)?.getBoundingClientRect() : source?.rect;
    const transform = focusTransform(rect, target);
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const animation = !reduced && typeof this.root.animate === 'function'
      ? this.root.animate(closing
        ? [{ transform: 'none', opacity: 1, borderRadius: '20px' }, { transform: transform || 'translateY(14px) scale(.985)', opacity: 0, borderRadius: '14px' }]
        : [{ transform: transform || 'translateY(14px) scale(.985)', opacity: .2, borderRadius: '14px' }, { transform: 'none', opacity: 1, borderRadius: '20px' }],
      { duration: closing ? 230 : 330, easing: 'cubic-bezier(.22,.8,.25,1)', fill: 'both' }) : null;
    this.motion.start(animation, finish);
  }

  open(view, sessionId, source = this.captureSource(view)) {
    const previous = this.active;
    this.motion.cancel(); this.closing = false; this.completeClose = null;
    this.root.classList.remove('focus-closing'); this.root.style.pointerEvents = '';
    const startIndex = previous?.sessionId === sessionId ? previous.startIndex
      : focusStartIndex(this.session());
    this.active = { kind: view.kind, id: view.id, view, sessionId, source, startIndex };
    this.signature = null; this.replySignature = null;
    this.talking = Boolean(this.activity(sessionId).running); this.root.hidden = false;
    this.slot.append(this.composer); this.composer.hidden = false;
    document.getElementById('app').inert = true;
    this.updateTalking(); this.refresh(); this.animate(source, false);
    document.getElementById('exit-focus').focus(); this.onChange?.();
  }

  updateTalking() {
    this.root.classList.toggle('talking', this.talking);
    this.assistant.hidden = !this.talking;
    this.assistant.inert = !this.talking;
    this.assistant.setAttribute('aria-hidden', String(!this.talking));
    this.talk.setAttribute('aria-expanded', String(this.talking));
    this.talk.replaceChildren(icon(this.talking ? 'chevron-down' : 'message-circle'), document.createTextNode(this.talking ? '收起交流' : '问问助手'));
    this.input.placeholder = '问问这份清单或当前步骤…';
    this.prompts.replaceChildren();
    const questions = this.active?.kind === 'shopping_list'
      ? ['有些食材买不到，怎么替换？', '这些食材应该怎么挑？']
      : ['这一步需要注意什么？', '当前做法可以再简单一点吗？'];
    for (const text of questions) {
      const button = node('button', 'composer-choice focus-prompt', text); button.type = 'button';
      button.addEventListener('click', () => { this.input.value = text; this.input.dispatchEvent(new Event('input', { bubbles: true })); this.input.focus(); });
      this.prompts.append(button);
    }
  }

  renderConversation(active, state) {
    const signature = JSON.stringify([state.messages, state.status, state.running]);
    if (signature !== this.replySignature) {
      const nearBottom = this.reply.scrollHeight - this.reply.scrollTop - this.reply.clientHeight < 80;
      const focused = document.activeElement;
      const focusOnRetry = this.reply.contains(focused) && focused?.classList.contains('focus-retry');
      const children = state.messages.map(message => {
        const item = node('article', `focus-message ${message.role}`);
        item.append(node('div', 'focus-message-label', message.role === 'user' ? '你' : 'ChefMate'));
        if (message.images?.length) {
          const images = node('div', 'message-images');
          message.images.forEach((image, index) => images.append(imageThumbnail(active.sessionId, image, message.images, index)));
          item.append(images);
        }
        if (message.content && !message.error && !message.cancelled) {
          const copy = node('div', 'focus-message-copy');
          copy.append(this.renderMessage ? this.renderMessage(message.content) : document.createTextNode(message.content));
          item.append(copy);
        }
        if (message.error || message.cancelled) item.append(node('p', message.error ? 'focus-message-error' : 'focus-message-note', message.error || '本次回复已停止。'));
        if (state.retry === message && this.retry) {
          const retry = node('button', 'retry-button focus-retry', message.cancelled ? '重新执行' : '重试这次回复'); retry.type = 'button';
          retry.prepend(icon('refresh-cw')); retry.addEventListener('click', () => this.retry(message)); item.append(retry);
        }
        return item;
      });
      if (!children.length) children.push(node('div', 'focus-empty', '遇到不确定的地方，随时问问助手。清单和做菜进度都会保留。'));
      this.reply.replaceChildren(...children); this.replySignature = signature;
      if (nearBottom) this.reply.scrollTop = this.reply.scrollHeight;
      if (focusOnRetry) this.reply.querySelector('.focus-retry')?.focus({ preventScroll: true });
    }
    this.status.hidden = !state.status || !state.running;
    this.status.classList.toggle('running', state.running); this.status.classList.toggle('failed', state.failed);
    this.status.replaceChildren();
    if (state.status) this.status.append(icon(state.running ? 'loader-circle' : state.failed ? 'circle-alert' : 'info'), node('span', '', state.status));
  }

  refresh() {
    if (!this.active) return;
    const active = this.active, entry = this.workspace.entry(active.sessionId);
    if (entry.snapshot?.stage === 'cooking' && ['prep_checklist', 'shopping_list'].includes(active.kind)) active.kind = 'cooking_guide';
    const view = entry.views.find(item => item.kind === active.kind) || (entry.loading || entry.presentationPending ? active.view : null);
    if (!view) { this.close(); return; }
    active.id = view.id; active.view = view; this.root.dataset.kind = view.kind;
    this.title.textContent = view.title || '厨房工作区';
    this.subtitle.textContent = conversationLabel(snapshotStatus(entry.snapshot || {}));
    this.stage.textContent = ({ shopping_list:'采购清单', prep_checklist:'备菜清单', cooking_guide:'专心做菜', recipe_detail:'菜谱做法' })[view.kind]
      || STAGE_NAMES[entry.snapshot?.stage] || '厨房工作区';
    const signature = JSON.stringify([view, entry.saving, entry.viewState[view.id], entry.drafts[view.id]]);
    if (signature !== this.signature) {
      const scroll = this.content.scrollTop, key = this.content.contains(document.activeElement) ? document.activeElement?.dataset.controlKey : null;
      const ctx = { ...this.workspace.context(view, active.sessionId), focused: true };
      ctx.onViewState = () => { this.workspace.onInlineChange?.(); this.refresh(); };
      this.content.replaceChildren(renderView(view, ctx)); this.content.scrollTop = scroll; this.signature = signature;
      if (key) [...this.content.querySelectorAll('[data-control-key]')].find(control => control.dataset.controlKey === key)?.focus({ preventScroll: true });
    }
    const state = focusConversation(this.session(), active.startIndex, this.activity(active.sessionId));
    this.root.classList.toggle('focus-running', state.running);
    this.talk.replaceChildren(icon(!this.talking && state.running ? 'loader-circle' : this.talking ? 'chevron-down' : 'message-circle'),
      document.createTextNode(this.talking ? '收起交流' : state.running ? '助手正在回复' : '问问助手'));
    this.renderConversation(active, state);
  }

  close({ immediate = false } = {}) {
    const active = this.active;
    if (!active) {
      if (immediate && this.closing && this.completeClose) { this.motion.cancel(); this.completeClose(); }
      return;
    }
    this.active = null; this.closing = true; this.root.classList.add('focus-closing'); this.root.style.pointerEvents = 'none';
    const finish = () => {
      if (!this.closing) return;
      this.completeClose = null;
      this.closing = false; this.root.hidden = true; this.root.style.pointerEvents = '';
      this.root.classList.remove('focus-closing', 'talking', 'focus-running');
      this.homeAnchor.after(this.composer); this.composer.hidden = false; this.input.placeholder = this.originalPlaceholder;
      document.getElementById('app').inert = false; this.content.replaceChildren(); this.reply.replaceChildren(); this.status.hidden = true;
      this.onChange?.();
      const source = this.activity(active.sessionId).running ? null
        : active.source?.trigger?.isConnected ? active.source.trigger : this.sourceCard(active.source)?.querySelector('.focus-button');
      (source || this.input).focus({ preventScroll: true });
    };
    this.completeClose = finish;
    if (immediate) { this.motion.cancel(); finish(); }
    else this.animate(active.source, true, finish);
  }
}
