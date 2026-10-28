(() => {
  'use strict';
  const registrations = new WeakMap();

  function register(input, initialItems = [], onSelect = null) {
    if (!input) return { setItems() {}, close() {} };
    const existing = registrations.get(input);
    if (existing) {
      existing.setItems(initialItems);
      return existing;
    }

    const nativeList = document.getElementById(input.getAttribute('list'));
    if (nativeList?.tagName === 'DATALIST') nativeList.remove();
    const menuId = input.getAttribute('aria-controls') || `${input.id || 'portal-search'}Suggestions`;
    let menu = document.getElementById(menuId);
    if (menu?.tagName === 'DATALIST') {
      menu.remove();
      menu = null;
    }
    if (!menu) {
      menu = document.createElement('div');
      menu.id = menuId;
    }
    menu.className = 'theme-suggestion-menu';
    menu.setAttribute('role', 'listbox');
    menu.setAttribute('aria-label', 'Matching suggestions');
    menu.hidden = true;
    document.body.append(menu);

    input.removeAttribute('list');
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', menuId);

    let items = [];
    let matches = [];
    let activeIndex = -1;
    let isFocused = false;
    let selecting = false;

    function close() {
      menu.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
      activeIndex = -1;
    }
    function positionMenu() {
      const rect = input.getBoundingClientRect();
      menu.style.left = `${Math.max(8, rect.left)}px`;
      menu.style.top = `${rect.bottom + 6}px`;
      menu.style.width = `${Math.max(180, rect.width)}px`;
      menu.style.maxWidth = `calc(100vw - ${Math.max(8, rect.left)}px - 8px)`;
    }
    function render() {
      const query = input.value.trim().toLocaleLowerCase();
      matches = items.filter((item) => !query || `${item.label} ${item.detail || ''}`.toLocaleLowerCase().includes(query)).slice(0, 10);
      menu.replaceChildren();
      matches.forEach((item, index) => {
        const option = document.createElement('button');
        option.type = 'button';
        option.id = `${menuId}-option-${index}`;
        option.className = 'theme-suggestion-option';
        option.setAttribute('role', 'option');
        option.setAttribute('aria-selected', String(index === activeIndex));
        const label = document.createElement('span');
        label.textContent = item.label;
        option.append(label);
        if (item.detail) {
          const detail = document.createElement('small');
          detail.textContent = item.detail;
          option.append(detail);
        }
        option.addEventListener('pointerdown', (event) => event.preventDefault());
        option.addEventListener('click', () => {
          input.value = item.value ?? item.label;
          onSelect?.(item);
          selecting = true;
          input.dispatchEvent(new Event('input', { bubbles: true }));
          selecting = false;
          close();
          input.focus();
        });
        menu.append(option);
      });
      const open = isFocused && matches.length > 0;
      menu.hidden = !open;
      input.setAttribute('aria-expanded', String(open));
      if (open) positionMenu();
    }
    function setItems(nextItems) {
      items = (Array.isArray(nextItems) ? nextItems : []).filter((item) => item && item.label)
        .map((item) => ({ ...item, label: String(item.label), detail: String(item.detail || '') }));
      activeIndex = -1;
      render();
    }
    input.addEventListener('focus', () => { isFocused = true; render(); });
    input.addEventListener('blur', () => { isFocused = false; setTimeout(close, 0); });
    input.addEventListener('input', () => { activeIndex = -1; if (!selecting) render(); });
    input.addEventListener('keydown', (event) => {
      if (menu.hidden || !matches.length) return;
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        const direction = event.key === 'ArrowDown' ? 1 : -1;
        activeIndex = activeIndex < 0 ? (direction > 0 ? 0 : matches.length - 1) : (activeIndex + direction + matches.length) % matches.length;
        const options = [...menu.querySelectorAll('[role="option"]')];
        options.forEach((option, index) => option.setAttribute('aria-selected', String(index === activeIndex)));
        input.setAttribute('aria-activedescendant', options[activeIndex].id);
        options[activeIndex].scrollIntoView({ block: 'nearest' });
      } else if (event.key === 'Enter' && activeIndex >= 0) {
        event.preventDefault();
        menu.querySelectorAll('[role="option"]')[activeIndex]?.click();
      } else if (event.key === 'Escape') {
        event.preventDefault();
        close();
      }
    });
    document.addEventListener('pointerdown', (event) => {
      if (event.target !== input && !menu.contains(event.target)) close();
    });
    window.addEventListener('resize', () => { if (!menu.hidden) positionMenu(); });
    window.addEventListener('scroll', () => { if (!menu.hidden) positionMenu(); }, true);

    const api = { setItems, close };
    registrations.set(input, api);
    setItems(initialItems);
    return api;
  }

  window.PortalSearchSuggestions = { register };
  document.querySelectorAll('input[data-search-suggestions]').forEach((input) => register(input));
})();
