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
