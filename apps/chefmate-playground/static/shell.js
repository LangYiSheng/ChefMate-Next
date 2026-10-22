import { icon } from './icons.js';
import {resizeRightPane,panelBounds} from './pane-resize.js';

// Keep the intended click until the owning page accepts its departure. This
// controller has no DOM dependencies so rejected and resumed navigation share
// the same ordering for buttons, links and programmatic page changes.
export class ShellNavigation {
  constructor({ page = 'chat', beforeChange = () => true, applyPage = () => {}, requestSettingsClose = () => {} } = {}) {
    this.page = page;
    this.beforeChange = beforeChange;
    this.applyPage = applyPage;
    this.requestSettingsClose = requestSettingsClose;
    this.blockedIntent = null;
    this.settingsIntent = null;
    this.settingsClosing = false;
    this.replaying = false;
  }

  // A fresh gesture replaces an older blocked click. Its original handlers may
  // continue only when this returns true; otherwise the caller stops capture.
  request(page = 'chat', activate = null) {
    this.blockedIntent = null;
    return this.attempt({ page, activate });
  }

  // After confirming an unsaved center, its owner calls setPage(page) again.
  // Matching the saved intent resumes the original target exactly once.
  setPage(page = 'chat') {
    const resume = this.blockedIntent?.page === page;
    const intent = resume ? this.blockedIntent : { page, activate: null };
    this.blockedIntent = null;
    return this.attempt(intent, resume);
  }

  attempt(intent, resume = false) {
    if (this.page === 'settings' && intent.page !== 'settings' && !intent.settingsAccepted) {
      this.settingsIntent = intent;
      if (!this.settingsClosing) {
        this.settingsClosing = true;
        this.requestSettingsClose();
      }
      return false;
    }
    if (intent.page !== this.page) {
      if (!this.beforeChange(intent.page)) {
        this.blockedIntent = intent;
        return false;
      }
      const previous = this.page;
      this.page = intent.page;
      this.applyPage(intent.page, previous);
    }
    this.blockedIntent = null;
    if (resume && intent.activate) {
      this.replaying = true;
      try { intent.activate(); }
      finally { this.replaying = false; }
    }
    return true;
  }

  acceptSettingsClose() {
    // A duplicate iframe message cannot replay an already accepted click.
    if (this.page !== 'settings') return false;
    const intent = this.settingsIntent || { page: 'chat', activate: null };
    this.settingsIntent = null;
    this.settingsClosing = false;
    intent.settingsAccepted = true;
    return this.attempt(intent, true);
  }

  cancelSettingsClose() {
    this.settingsIntent = null;
    this.settingsClosing = false;
  }

  cancelNavigation() { this.blockedIntent = null; }
}

// The chat stays mounted while settings occupy the same workspace.
export function initShell({ onSettingsClose = () => {} } = {}) {
  const app = document.getElementById('app');
  const sidebar = document.getElementById('sidebar');
  const toggle = document.getElementById('collapse-sidebar');
  const expand = document.getElementById('expand-sidebar');
  const resize = document.getElementById('sidebar-resize');
  const host = document.getElementById('settings-host');
  const frame = document.getElementById('settings-frame');
  const link = document.querySelector('.settings-link');
  const mobile = matchMedia('(max-width:760px)');
  const detailResizer=resizeRightPane({root:app,handle:document.getElementById('detail-resize'),key:'chefmate:detail-width',observe:[sidebar],
    enabled:()=>!mobile.matches,
    bounds:()=>panelBounds(window.innerWidth-sidebar.getBoundingClientRect().width-24,{remaining:window.innerWidth>1180?420:40})});
  let collapsed = false, width = 248, lastFocus;
  function applyPage(page, previous) {
    const settingsOpen = page === 'settings';
    if (settingsOpen) {
      lastFocus = document.activeElement;
      if (!frame.hasAttribute('src')) frame.src = '/settings?embedded=1';
    }
    app.dataset.page = page;
    host.hidden = !settingsOpen; app.classList.toggle('settings-open', settingsOpen);
    if (settingsOpen) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
    for (const button of sidebar.querySelectorAll('[data-primary-page]')) {
      if (button.dataset.primaryPage === page) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    }
    window.dispatchEvent(new CustomEvent('chefmate:page-change', { detail: { page } }));
    if (previous === 'settings') {
      onSettingsClose();
      const returnFocus = lastFocus?.isConnected && lastFocus.getClientRects().length ? lastFocus
        : mobile.matches ? document.getElementById('open-sidebar') : link;
      returnFocus?.focus();
    }
    if (settingsOpen) document.getElementById('close-details').click();
    // Use the chat controller's normal path for the mobile navigation.
    if (mobile.matches) document.getElementById('close-sidebar').click();
    if (settingsOpen) frame.focus();
  }
  const navigation = new ShellNavigation({
    beforeChange: page => window.dispatchEvent(new CustomEvent('chefmate:before-page-change', {
      cancelable: true, detail: { page },
    })),
    applyPage,
    requestSettingsClose: () => frame.contentWindow?.postMessage({ type: 'chefmate:request-close' }, location.origin),
  });
  const setPage = page => navigation.setPage(page);
  try {
    const saved = JSON.parse(localStorage.getItem('chefmate:shell') || '{}');
    collapsed = saved.collapsed === true;
    if (Number.isFinite(saved.width)) width = Math.max(208, Math.min(360, saved.width));
  } catch { /* Browser storage may be disabled. */ }
  function persist() {
    try { localStorage.setItem('chefmate:shell', JSON.stringify({ collapsed, width })); } catch {}
  }
  function paint() {
    app.classList.toggle('sidebar-collapsed', collapsed && !mobile.matches);
    app.style.setProperty('--sidebar-width', `${width}px`);
    toggle.setAttribute('aria-expanded', String(!collapsed));
    expand.setAttribute('aria-expanded', String(!collapsed));
    resize.setAttribute('aria-valuenow', String(width));
    detailResizer.paint();
  }
  function setCollapsed(value) {
    collapsed = value; paint(); persist();
    if(!mobile.matches)(value ? expand : toggle).focus();
  }
  toggle.addEventListener('click', () => setCollapsed(true));
  expand.addEventListener('click', () => setCollapsed(false));
  mobile.addEventListener('change', paint);
  let drag = null;
  function stopResize() {
    drag = null; app.classList.remove('sidebar-resizing'); persist();
  }
  resize.addEventListener('pointerdown', event => {
    if (event.button !== 0 || mobile.matches) return;
    drag = { x: event.clientX, width };
    resize.setPointerCapture(event.pointerId);
    app.classList.add('sidebar-resizing'); event.preventDefault();
  });
  resize.addEventListener('pointermove', event => {
    if (!drag) return;
    width = Math.max(208, Math.min(360, drag.width + event.clientX - drag.x)); paint();
  });
  resize.addEventListener('pointerup', stopResize);
  resize.addEventListener('pointercancel', stopResize);
  resize.addEventListener('lostpointercapture', stopResize);
  resize.addEventListener('keydown', event => {
    const change = { ArrowLeft: -16, ArrowRight: 16 }[event.key];
    if (!change && !['Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    width = event.key === 'Home' ? 208 : event.key === 'End' ? 360 : Math.max(208, Math.min(360, width + change));
    paint(); persist();
  });
  // Capture runs before the chat's target handlers (including newSession and
  // openSession), so a rejected page change cannot start hidden chat work.
  sidebar.addEventListener('click', event => {
    const target = event.target.closest('.settings-link,.session-link,#new-chat,.brand,[data-primary-page]');
    if (!target) return;
    const settingsLink = target === link;
    if (settingsLink || target.matches('.brand')) event.preventDefault();
    if (navigation.replaying) return;
    const previous = navigation.page;
    const page = settingsLink ? (navigation.page === 'settings' ? 'chat' : 'settings')
      : target.dataset.primaryPage || 'chat';
    // The settings toggle is completely owned by the shell; replaying it after
    // a successful close would open it again. Other targets retain their app
    // listeners and link defaults, and are replayed only after acceptance.
    const activate = settingsLink ? null : () => { target.focus(); target.click(); };
    if (!navigation.request(page, activate)) {
      event.preventDefault(); event.stopImmediatePropagation();
    } else if (page === previous && target.dataset.primaryPage && mobile.matches) {
      document.getElementById('close-sidebar').click();
    }
  }, true);
  window.addEventListener('chefmate:navigation-cancelled', () => navigation.cancelNavigation());
  window.addEventListener('message', event => {
    if (event.origin !== location.origin || event.source !== frame.contentWindow) return;
    if (event.data?.type === 'chefmate:close-settings') navigation.acceptSettingsClose();
    if (event.data?.type === 'chefmate:close-cancelled') navigation.cancelSettingsClose();
    if (event.data?.type === 'chefmate:settings-saved') onSettingsClose();
  });
  document.querySelectorAll('[data-icon]').forEach(node => node.replaceChildren(icon(node.dataset.icon)));
  paint();
  app.dataset.page = 'chat';
  return { setPage, get page() { return app.dataset.page || 'chat'; } };
}
