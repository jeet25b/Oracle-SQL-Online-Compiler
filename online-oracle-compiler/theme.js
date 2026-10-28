(() => {
  const key = 'ooc-color-mode';
  const root = document.documentElement;
  let mode = 'dark';
  try {
    const savedMode = localStorage.getItem(key);
    mode = savedMode === 'light' || savedMode === 'dark' ? savedMode : 'dark';
  } catch { /* Dark is the first-visit default. */ }

  function applyMode(nextMode) {
    mode = nextMode === 'dark' ? 'dark' : 'light';
    root.dataset.theme = mode;
    document.dispatchEvent(new CustomEvent('ooc-theme-change', { detail: mode }));
    document.querySelectorAll('[data-theme-toggle]').forEach((button) => {
      const dark = mode === 'dark';
      button.setAttribute('aria-pressed', String(dark));
      button.setAttribute('aria-label', `Switch to ${dark ? 'light' : 'dark'} mode`);
      button.textContent = dark ? '☀ Light' : '☾ Dark';
    });
    try { localStorage.setItem(key, mode); } catch { /* The current selection still applies to this page. */ }
  }

  applyMode(mode);
  document.querySelectorAll('[data-theme-toggle]').forEach((button) => {
    button.addEventListener('click', () => applyMode(mode === 'dark' ? 'light' : 'dark'));
  });
})();

(() => {
  const theme = document.querySelector('[data-theme-toggle]');
  if (!theme || document.getElementById('pageBrightnessButton')) return;
  const style = document.createElement('style');
  style.textContent = `
    .page-brightness{position:relative;display:inline-flex;font-size:12px}
    .page-brightness-panel{position:absolute;right:0;top:calc(100% + 10px);z-index:10001;width:260px;max-width:85vw;padding:16px;display:grid;gap:13px;border:1px solid var(--ide-border,var(--line,#cbd6e4));border-radius:12px;background:var(--ide-panel,var(--paper,#fff));color:var(--ide-text,var(--ink,#20314b));box-shadow:0 12px 32px #0002}
    .page-brightness-panel[hidden]{display:none}
    .page-brightness-panel label{display:flex;justify-content:space-between;gap:8px}
    .page-brightness-panel input{width:100%;margin:0;accent-color:var(--ide-accent,var(--accent,#7863dc))}
    .page-brightness-panel small{color:var(--ide-muted,var(--muted,#64748b));line-height:1.5}
    .page-brightness-reset{padding:8px;border:1px solid var(--ide-border,var(--line,#cbd6e4));border-radius:7px;background:var(--ide-panel-2,var(--paper,#f5f8fc));color:inherit;cursor:pointer}
  `;
  document.head.append(style);
  const wrap = document.createElement('span');
  wrap.className = 'page-brightness';
  const button = document.createElement('button');
  button.id = 'pageBrightnessButton';
  button.type = 'button';
  button.className = theme.className.replace(/theme-toggle|theme-mode-button/g, '');
  button.textContent = '☀ Brightness';
  button.setAttribute('aria-expanded', 'false');
  button.setAttribute('aria-controls', 'pageBrightnessPanel');
  const panel = document.createElement('div');
  panel.id = 'pageBrightnessPanel';
  panel.className = 'page-brightness-panel';
  panel.hidden = true;
  panel.innerHTML = '<label for="pageBrightnessSlider">Page brightness <output id="pageBrightnessValue" for="pageBrightnessSlider">100%</output></label><input id="pageBrightnessSlider" type="range" min="60" max="100" step="5" value="100" aria-describedby="pageBrightnessHint"><small id="pageBrightnessHint">Dims this page only. Screen brightness stays unchanged.</small><button type="button" class="page-brightness-reset">Reset to 100%</button>';
  wrap.append(button, panel);
  theme.after(wrap);
  const shade = document.createElement('div');
  shade.setAttribute('aria-hidden', 'true');
  shade.style.cssText = 'position:fixed;inset:0;width:100vw;height:100vh;max-width:none;max-height:none;margin:0;padding:0;border:0;background:#000;pointer-events:none;z-index:2147483647;opacity:0';
  const popover = typeof shade.showPopover === 'function';
  if (popover) shade.setAttribute('popover', 'manual');
  function mountShade() {
    (document.fullscreenElement || document.body).append(shade);
    if (popover && !shade.matches(':popover-open')) shade.showPopover();
  }
  mountShade();
  document.addEventListener('fullscreenchange', mountShade);
  const slider = panel.querySelector('input');
  const output = panel.querySelector('output');
  const key = 'ooc-workspace-brightness';
  function apply(value) {
    const number = Number(value);
    const brightness = Number.isFinite(number) ? Math.max(60, Math.min(100, number)) : 100;
    slider.value = String(brightness);
    output.value = `${brightness}%`;
    shade.style.opacity = String(1 - brightness / 100);
    try { localStorage.setItem(key, String(brightness)); } catch { /* Session control remains available. */ }
  }
  let saved = 100;
  try { saved = localStorage.getItem(key) ?? 100; } catch { /* Use normal brightness. */ }
  apply(saved);
  slider.addEventListener('input', () => apply(slider.value));
  panel.querySelector('button').addEventListener('click', () => apply(100));
  function setOpen(open) {
    panel.hidden = !open;
    button.setAttribute('aria-expanded', String(open));
  }
  button.addEventListener('click', () => setOpen(panel.hidden));
  document.addEventListener('pointerdown', event => {
    if (!wrap.contains(event.target)) setOpen(false);
  }, true);
  wrap.addEventListener('keydown', event => {
    if (event.key === 'Escape') { setOpen(false); button.focus(); }
  });
})();

// Shared theme-aware tooltips replace browser/OS title bubbles on every page.
(() => {
  if (document.getElementById('siteThemeTooltip')) return;
  const tip = document.createElement('div');
  tip.id = 'siteThemeTooltip';
  tip.className = 'site-theme-tooltip';
  tip.setAttribute('role', 'tooltip');
  tip.hidden = true;
  const popover = typeof tip.showPopover === 'function';
  if (popover) tip.setAttribute('popover', 'manual');
  document.body.append(tip);
  let anchor = null;
  let timer = 0;
  function convert(element) {
    if (!(element instanceof Element)) return;
    const title = element.getAttribute('title');
    if (title !== null) {
      if (title.trim()) element.dataset.themeTooltip = title;
      else delete element.dataset.themeTooltip;
      element.removeAttribute('title');
    }
  }
  function scan(element) {
    convert(element);
    element.querySelectorAll?.('[title]').forEach(convert);
  }
  scan(document.documentElement);
  new MutationObserver(records => {
    for (const record of records) {
      if (record.type === 'attributes') convert(record.target);
      else record.addedNodes.forEach(scan);
    }
    if (anchor && !anchor.isConnected) hide();
  }).observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ['title'] });
  function hide() {
    clearTimeout(timer);
    if (anchor) {
      const ids = (anchor.getAttribute('aria-describedby') || '').split(/\s+/).filter(id => id && id !== tip.id);
      if (ids.length) anchor.setAttribute('aria-describedby', ids.join(' '));
      else anchor.removeAttribute('aria-describedby');
    }
    anchor = null;
    if (popover && tip.matches(':popover-open')) tip.hidePopover();
    tip.hidden = true;
  }
  function show(element, delay = 350) {
    if (!element || !element.dataset.themeTooltip) return;
    if (anchor === element) return;
    hide();
    anchor = element;
    timer = setTimeout(() => {
      if (!anchor?.isConnected) return hide();
      (anchor.closest('dialog[open]') || document.fullscreenElement || document.body).append(tip);
      tip.textContent = anchor.dataset.themeTooltip;
      tip.hidden = false;
      if (popover) tip.showPopover();
      const ids = (anchor.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
      anchor.setAttribute('aria-describedby', [...new Set([...ids, tip.id])].join(' '));
      const rect = anchor.getBoundingClientRect();
      const width = tip.offsetWidth, height = tip.offsetHeight;
      let left;
      let top;
      if (anchor.closest('.ide-nav')) {
        left = rect.right + width + 6 <= innerWidth ? rect.right + 6 : rect.left - width - 6;
        left = Math.max(8, Math.min(left, innerWidth - width - 8));
        top = Math.max(8, Math.min(rect.top + rect.height / 2 - height / 2, innerHeight - height - 8));
      } else {
        left = Math.max(8, Math.min(rect.left + rect.width / 2 - width / 2, innerWidth - width - 8));
        top = rect.bottom + height + 8 <= innerHeight ? rect.bottom + 6 : Math.max(8, rect.top - height - 6);
      }
      tip.style.left = `${left}px`;
      tip.style.top = `${top}px`;
    }, delay);
  }
  document.addEventListener('pointerover', event => show(event.target.closest?.('[data-theme-tooltip]')));
  document.addEventListener('pointerout', event => { if (anchor && !anchor.contains(event.relatedTarget)) hide(); });
  document.addEventListener('focusin', event => show(event.target.closest?.('[data-theme-tooltip]'), 0));
  document.addEventListener('focusout', hide);
  document.addEventListener('pointerdown', hide);
  document.addEventListener('keydown', event => { if (event.key === 'Escape') hide(); });
  document.addEventListener('scroll', hide, true);
  window.addEventListener('resize', hide);
})();
