// Shared login policy. Native credential suggestions remain browser-controlled.
(() => {
  const initialized = new WeakMap();
  function install(form) {
    if (initialized.has(form)) { initialized.get(form)(); return; }
    const username = form.querySelector('input[name="username"], input[name="user"], input[autocomplete="username"], #userId');
    const password = form.querySelector('input[type="password"]');
    if (!username || !password || password.autocomplete === 'new-password') return;
    let eligible = false;
    let activated = false;
    function hints(enabled) {
      eligible = enabled;
      form.autocomplete = enabled ? 'on' : 'off';
      username.autocomplete = enabled ? 'username' : 'off';
      // Stable semantics let the browser associate both fields as one login.
      password.autocomplete = activated ? 'current-password' : 'off';
    }
    function reset() {
      activated = false;
      username.value = form.dataset?.savedOracleUser || '';
      password.value = '';
      username.readOnly = true;
      password.readOnly = true;
      hints(false);
    }
    function isVisible() {
      const dialog = form.closest('dialog');
      return (!dialog || dialog.open) && form.getClientRects().length > 0 &&
        getComputedStyle(form).visibility !== 'hidden';
    }
    let wasVisible = isVisible();
    function checkAppearance() {
      const visible = isVisible();
      if (visible && !wasVisible) reset();
      wasVisible = visible;
    }
    initialized.set(form, checkAppearance);
    const dialog = form.closest('dialog');
    dialog?.addEventListener('close', () => { wasVisible = false; reset(); });
    dialog?.addEventListener('beforetoggle', (event) => {
      if (event.newState === 'open') { reset(); wasVisible = true; }
      else wasVisible = false;
    });
    reset();
    function activate(event) {
      if (!event.isTrusted || activated) return;
      activated = true;
      username.readOnly = false;
      password.readOnly = false;
      hints(eligible);
    }
    // A real click/key event unlocks both fields before native credential fill.
    // Programmatic autofocus on page load must not unlock them.
    form.addEventListener('pointerdown', activate, true);
    form.addEventListener('keydown', activate, true);
    username.addEventListener('focus', () => { if (activated) hints(eligible); });
    password.addEventListener('focus', () => { if (activated) password.readOnly = false; });
    username.addEventListener('input', (event) => {
      if (!activated) { username.value = form.dataset?.savedOracleUser || ''; password.value = ''; return; }
      if (event.isComposing) return;
      const enable = Array.from(username.value.trim()).length >= 3;
      if (enable !== eligible) hints(enable);
    });
    username.addEventListener('compositionend', () => {
      if (activated) hints(Array.from(username.value.trim()).length >= 3);
    });
    // Reject unattended load-time autofill only until a trusted form gesture.

    const guard = window.setInterval(() => {
      if (!form.isConnected) { clearInterval(guard); return; }
      checkAppearance();
      if (!activated) { username.value = form.dataset?.savedOracleUser || ''; password.value = ''; }
    }, 200);
    window.addEventListener('pageshow', (event) => { if (event.persisted) reset(); });
    form.addEventListener('reset', () => queueMicrotask(reset));
  }
  function scan() { document.querySelectorAll('form').forEach(install); }
  scan();
  new MutationObserver(scan).observe(document.body, { childList:true, subtree:true, attributes:true, attributeFilter:['open', 'hidden', 'class', 'style'] });
})();
