(() => {
  const palettes = {
    violet: { accent: '#A78BFA', strong: '#6D28D9', soft: '#F1EAFF', rgb: '109, 40, 217' },
    blue: { accent: '#3B82F6', strong: '#1D4ED8', soft: '#EAF2FF', rgb: '29, 78, 216' },
    teal: { accent: '#14B8A6', strong: '#0F766E', soft: '#E5F7F5', rgb: '15, 118, 110' },
    indigo: { accent: '#818CF8', strong: '#4338CA', soft: '#EEF0FF', rgb: '67, 56, 202' },
    cyan: { accent: '#22D3EE', strong: '#0E7490', soft: '#E6F8FB', rgb: '14, 116, 144' },
    emerald: { accent: '#34D399', strong: '#047857', soft: '#E7F7F0', rgb: '4, 120, 87' },
    amber: { accent: '#FBBF24', strong: '#A16207', soft: '#FFF6DF', rgb: '161, 98, 7' },
    rose: { accent: '#FB7185', strong: '#BE123C', soft: '#FFF0F2', rgb: '190, 18, 60' },
    orange: { accent: '#FB923C', strong: '#C2410C', soft: '#FFF1E8', rgb: '194, 65, 12' },
    slate: { accent: '#94A3B8', strong: '#475569', soft: '#EDF1F5', rgb: '71, 85, 105' }
  };
  const storageKey = 'ooc-accent-palette';
  const root = document.documentElement;
  const pickers = [...document.querySelectorAll('[data-palette-picker]')];
  const buttons = [...document.querySelectorAll('[data-accent]')];
  const saveDefaultButton = document.querySelector('[data-save-site-default]');
  const paletteStatus = document.querySelector('[data-palette-status]');
  let hasPersonalChoice = false;
  let selected = 'violet';
  try {
    const saved = localStorage.getItem(storageKey);
    if (palettes[saved]) { selected = saved; hasPersonalChoice = true; }
  } catch { /* Violet remains the default when storage is blocked. */ }

  function applyPalette(name, persist = false) {
    const palette = palettes[name] || palettes.violet;
    const isDark = root.dataset.theme === 'dark';
    root.dataset.accent = name;
    root.style.setProperty('--accent', palette.accent);
    root.style.setProperty('--accent-strong', isDark ? palette.accent : palette.strong);
    root.style.setProperty('--accent-soft', isDark ? `rgba(${palette.rgb}, .17)` : palette.soft);
    root.style.setProperty('--accent-rgb', palette.rgb);
    buttons.forEach((button) => button.setAttribute('aria-checked', String(button.dataset.accent === name)));
    if (persist) {
      try { localStorage.setItem(storageKey, name); } catch { /* Keep the current page usable when storage is blocked. */ }
      hasPersonalChoice = true;
    }
  }

  applyPalette(selected);
  if (!hasPersonalChoice) {
    fetch('/api/portal/preferences', { credentials: 'same-origin' })
      .then((response) => response.ok ? response.json() : null)
      .then((data) => {
        if (data && palettes[data.defaultAccent] && !localStorage.getItem(storageKey)) applyPalette(data.defaultAccent);
      })
      .catch(() => {});
  }
  document.addEventListener('ooc-theme-change', () => applyPalette(root.dataset.accent || 'violet'));
  buttons.forEach((button) => button.addEventListener('click', () => {
    applyPalette(button.dataset.accent, true);
    pickers.forEach((picker) => { picker.open = false; });
  }));
  if (saveDefaultButton) {
    saveDefaultButton.addEventListener('click', async () => {
      saveDefaultButton.disabled = true;
      if (paletteStatus) { paletteStatus.textContent = 'Saving website default…'; paletteStatus.hidden = false; }
      try {
        const cookie = document.cookie.split('; ').find((part) => part.startsWith('ora_csrf='));
        const token = cookie ? decodeURIComponent(cookie.slice('ora_csrf='.length)) : '';
        const response = await fetch('/api/portal/preferences/default-accent', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
          body: JSON.stringify({ accent: selected })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || 'Could not save the website default.');
        document.body.dataset.defaultAccent = data.defaultAccent;
        if (paletteStatus) paletteStatus.textContent = `${selected[0].toUpperCase()}${selected.slice(1)} is now the website default.`;
      } catch (error) {
        if (paletteStatus) paletteStatus.textContent = error.message || 'Could not save the website default.';
      } finally {
        saveDefaultButton.disabled = false;
      }
    });
  }
  buttons.forEach((button) => button.addEventListener('keydown', (event) => {
    const index = buttons.indexOf(button);
    let next = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % buttons.length;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index + buttons.length - 1) % buttons.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = buttons.length - 1;
    if (next !== null) {
      event.preventDefault();
      buttons[next].focus();
      buttons[next].click();
    }
  }));
  document.addEventListener('click', (event) => {
    if (!event.target.closest('[data-palette-picker]')) pickers.forEach((picker) => { picker.open = false; });
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') pickers.forEach((picker) => { picker.open = false; });
  });
})();
