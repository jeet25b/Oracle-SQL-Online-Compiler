(() => {
  'use strict';
  const csrfToken = () => {
    const cookie = document.cookie.split('; ').find((part) => part.startsWith('ora_csrf='));
    return cookie ? decodeURIComponent(cookie.slice('ora_csrf='.length)) : '';
  };
  const send = (url, payload) => fetch(url, {
    method: 'POST', credentials: 'same-origin', keepalive: true,
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
    body: JSON.stringify(payload),
  }).catch(() => {});
  const editors = new Map([['#editor', 'PL/SQL'], ['#cliInput', 'SQL*Plus']]);
  editors.forEach((source, selector) => document.querySelector(selector)?.addEventListener('paste', (event) => {
    const text = event.clipboardData?.getData('text/plain') ?? event.clipboardData?.getData('text') ?? '';
    if (text) send('/api/compiler/paste-events', { source, text, truncated: text.length > 65536 });
  }));
  let visibility = document.visibilityState;
  let focused = document.hasFocus();
  const log = (kind) => send('/api/compiler/activity-events', { kind, source: 'Workspace' });
  log(visibility === 'visible' ? 'page_visible' : 'page_hidden');
  document.addEventListener('visibilitychange', () => {
    const next = document.visibilityState;
    if (next === visibility) return;
    visibility = next;
    log(next === 'visible' ? 'page_visible' : 'page_hidden');
  });
  window.addEventListener('blur', () => { if (focused) { focused = false; log('window_blur'); } });
  window.addEventListener('focus', () => { if (!focused) { focused = true; log('window_focus'); } });
})();
