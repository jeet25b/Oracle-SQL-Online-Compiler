(() => {
  window.portalLastActivityAt = Date.now();
  for (const name of ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart']) {
    document.addEventListener(name, () => { window.portalLastActivityAt = Date.now(); }, { passive: true });
  }
  let busy = false;
  async function check() {
    if (busy) return;
    // Dashboard/compiler heartbeats already validate the server-side session.
    if (document.body.classList.contains('dashboard-page') || document.querySelector('#terminalInput')) return;
    busy = true;
    try {
      if (!document.body.classList.contains('dashboard-page') && !document.querySelector('#terminalInput')) {
        const token = document.cookie.split('; ').find(value => value.startsWith('ora_csrf='))?.slice('ora_csrf='.length) || '';
        const response = await fetch('/api/portal/activity/heartbeat', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': decodeURIComponent(token) }, body: JSON.stringify({ page: 'portal', active: Date.now() - window.portalLastActivityAt < 30000 }) });
        if (response.status === 401) window.location.replace('/portal/');
      }
    } catch {} finally { busy = false; }
  }
  setInterval(check, 15000);
  document.addEventListener('visibilitychange', check);
})();
