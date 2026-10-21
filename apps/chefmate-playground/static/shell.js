import { icon } from './icons.js';

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
  let collapsed = false, width = 248, lastFocus, pendingNavigation = null;
  function setPage(page = 'chat') {
    app.dataset.page = page;
    const settingsOpen = page === 'settings';
    host.hidden = !settingsOpen; app.classList.toggle('settings-open', settingsOpen);
    if (settingsOpen) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
    for (const button of sidebar.querySelectorAll('[data-primary-page]')) {
      if (button.dataset.primaryPage === page) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    }
    window.dispatchEvent(new CustomEvent('chefmate:page-change', { detail: { page } }));
  }
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
    resize.setAttribute('aria-valuenow', String(width));
  }
  function setCollapsed(value) {
    collapsed = value; paint(); persist();
    (value ? expand : toggle).focus();
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
  function closeSettings() {
    const navigate = pendingNavigation; pendingNavigation = null;
    setPage('chat');
    onSettingsClose(); lastFocus?.focus();
    navigate?.click();
  }
  link.addEventListener('click', event => {
    event.preventDefault();
    if (!host.hidden) {
      frame.contentWindow?.postMessage({ type: 'chefmate:request-close' }, location.origin);
      return;
    }
    lastFocus = document.activeElement;
    if (!frame.hasAttribute('src')) frame.src = '/settings?embedded=1';
    setPage('settings');
    document.getElementById('close-details').click();
    // Close the mobile navigation using the chat controller's normal path.
    if (mobile.matches) document.getElementById('close-sidebar').click();
    frame.focus();
  });
  // Navigation only runs after the settings frame has accepted the close.
  // Capture first so the chat's existing click handlers cannot run early.
  sidebar.addEventListener('click', event => {
    const navigation = event.target.closest('.session-link,#new-chat,.brand,[data-primary-page]');
    if (navigation && !host.hidden) {
      event.preventDefault(); event.stopImmediatePropagation();
      pendingNavigation = navigation;
      frame.contentWindow?.postMessage({ type: 'chefmate:request-close' }, location.origin);
    }
  }, true);
  for (const button of sidebar.querySelectorAll('[data-primary-page]')) button.addEventListener('click', () => {
    setPage(button.dataset.primaryPage);
    if (mobile.matches) document.getElementById('close-sidebar').click();
  });
  sidebar.addEventListener('click', event => {
    if (event.target.closest('.session-link,#new-chat,.brand')) setPage('chat');
  });
  window.addEventListener('message', event => {
    if (event.origin !== location.origin || event.source !== frame.contentWindow) return;
    if (event.data?.type === 'chefmate:close-settings') closeSettings();
    if (event.data?.type === 'chefmate:close-cancelled') pendingNavigation = null;
    if (event.data?.type === 'chefmate:settings-saved') onSettingsClose();
  });
  document.querySelectorAll('[data-icon]').forEach(node => node.replaceChildren(icon(node.dataset.icon)));
  paint();
  app.dataset.page = 'chat';
  return { setPage, get page() { return app.dataset.page || 'chat'; } };
}
