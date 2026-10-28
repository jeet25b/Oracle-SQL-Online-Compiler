(() => {
  'use strict';

  let nextId = 0;
  const enhanced = new WeakMap();

  function enhanceSelect(select) {
    if (!(select instanceof HTMLSelectElement) || enhanced.has(select)) return;
    if (select.hidden || select.multiple || select.size > 1 || select.getAttribute('aria-hidden') === 'true'
      || select.closest('[hidden]') || select.matches('[data-native-select],.native-select')) return;

    const required = select.required;
    const wrapper = document.createElement('span');
    wrapper.className = 'theme-select' + (select.classList.contains('navigator-select') ? ' theme-select-navigator' : '');
    wrapper.dataset.themeSelect = '';

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'theme-select-trigger';
    trigger.id = (select.id || 'themeSelect' + (++nextId)) + 'Control';
    trigger.setAttribute('role', 'combobox');
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    if (select.getAttribute('aria-label')) trigger.setAttribute('aria-label', select.getAttribute('aria-label'));
    const value = document.createElement('span');
    value.className = 'theme-select-value';
    const arrow = document.createElement('span');
    arrow.className = 'theme-select-arrow';
    arrow.setAttribute('aria-hidden', 'true');
    arrow.textContent = '▾';
    trigger.append(value, arrow);

    const menu = document.createElement('div');
    menu.className = 'theme-select-menu';
    menu.id = trigger.id + 'Options';
    menu.setAttribute('role', 'listbox');
    menu.hidden = true;
    trigger.setAttribute('aria-controls', menu.id);

    const originalStyle = getComputedStyle(select);
    wrapper.style.minWidth = originalStyle.minWidth;
    wrapper.style.maxWidth = originalStyle.maxWidth;
    wrapper.style.marginTop = originalStyle.marginTop;
    wrapper.style.marginRight = originalStyle.marginRight;
    wrapper.style.marginBottom = originalStyle.marginBottom;
    wrapper.style.marginLeft = originalStyle.marginLeft;
    trigger.style.fontFamily = originalStyle.fontFamily;
    trigger.style.fontSize = originalStyle.fontSize;
    trigger.style.fontWeight = originalStyle.fontWeight;
    trigger.style.lineHeight = originalStyle.lineHeight;
    const controlHeight = Number.parseFloat(originalStyle.height);
    if (Number.isFinite(controlHeight) && controlHeight >= 32) {
      trigger.style.height = String(controlHeight) + 'px';
      trigger.style.minHeight = String(controlHeight) + 'px';
    }
    if (select.classList.contains('navigator-select')) wrapper.style.marginBottom = '11px';

    select.parentNode.insertBefore(wrapper, select);
    wrapper.append(select, trigger);
    (select.closest('dialog') || document.body).append(menu);
    select.classList.add('theme-select-native');
    select.setAttribute('aria-hidden', 'true');
    select.tabIndex = -1;
    if (required) trigger.setAttribute('aria-required', 'true');
    select.dataset.themeSelectEnhanced = 'true';
    trigger.dataset.themeSelectOwner = select.id || trigger.id;
    menu.dataset.themeSelectOwner = trigger.dataset.themeSelectOwner;
    select.labels?.forEach((label) => {
      if (label.htmlFor === select.id) label.htmlFor = trigger.id;
    });

    let repositionHandlers = null;
    let validationMessage = null;

    function refresh() {
      const selected = select.selectedOptions[0];
      value.textContent = selected?.textContent?.trim() || '';
      trigger.disabled = select.disabled;
      trigger.setAttribute('aria-invalid', String(Boolean(validationMessage && required && !select.value)));
      menu.replaceChildren();
      [...select.options].forEach((option, index) => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'theme-select-option';
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', String(option.selected));
        item.disabled = option.disabled;
        item.dataset.optionIndex = String(index);
        item.textContent = option.textContent.trim();
        item.addEventListener('click', () => {
          select.selectedIndex = index;
          select.dispatchEvent(new Event('input', { bubbles: true }));
          select.dispatchEvent(new Event('change', { bubbles: true }));
          close(true);
        });
        menu.append(item);
      });
      if (validationMessage && select.value) {
        validationMessage.remove();
        validationMessage = null;
      }
    }

    for (const property of ['value', 'selectedIndex']) {
      const descriptor = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, property);
      if (!descriptor?.get || !descriptor?.set) continue;
      try {
        Object.defineProperty(select, property, {
          configurable: true,
          enumerable: descriptor.enumerable,
          get() { return descriptor.get.call(this); },
          set(nextValue) { descriptor.set.call(this, nextValue); refresh(); },
        });
      } catch { /* Selection still synchronizes on change/input events. */ }
    }

    function placeMenu() {
      const rect = trigger.getBoundingClientRect();
      const margin = 8;
      const availableBelow = window.innerHeight - rect.bottom - margin;
      const availableAbove = rect.top - margin;
      const openAbove = availableBelow < 190 && availableAbove > availableBelow;
      const available = Math.max(100, Math.min(360, openAbove ? availableAbove : availableBelow));
      menu.style.maxHeight = '';
      const menuHeight = Math.min(360, menu.scrollHeight);
      const width = Math.min(Math.max(rect.width, 180), window.innerWidth - margin * 2);
      const left = Math.max(margin, Math.min(rect.left, window.innerWidth - width - margin));
      menu.style.width = String(width) + 'px';
      menu.style.maxHeight = String(available) + 'px';
      menu.style.left = String(left) + 'px';
      menu.style.top = String(openAbove ? Math.max(margin, rect.top - Math.min(available, menuHeight) - 4) : rect.bottom + 4) + 'px';
    }

    function close(restoreFocus = false) {
      menu.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
      if (repositionHandlers) {
        window.removeEventListener('resize', repositionHandlers, true);
        window.removeEventListener('scroll', repositionHandlers, true);
        repositionHandlers = null;
      }
      if (restoreFocus) trigger.focus();
    }

    function open(focusSelected = false) {
      if (trigger.disabled) return;
      refresh();
      menu.hidden = false;
      placeMenu();
      trigger.setAttribute('aria-expanded', 'true');
      repositionHandlers = placeMenu;
      window.addEventListener('resize', repositionHandlers, true);
      window.addEventListener('scroll', repositionHandlers, true);
      if (focusSelected) {
        const selected = menu.querySelector('.theme-select-option[aria-selected="true"]');
        (selected || menu.querySelector('.theme-select-option:not(:disabled)'))?.focus();
      }
    }

    trigger.addEventListener('click', () => menu.hidden ? open(true) : close());
    trigger.addEventListener('keydown', (event) => {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
        event.preventDefault();
        open(true);
      } else if (event.key === 'Escape' && !menu.hidden) {
        event.preventDefault();
        close(true);
      }
    });
    menu.addEventListener('keydown', (event) => {
      const options = [...menu.querySelectorAll('.theme-select-option:not(:disabled)')];
      const current = options.indexOf(document.activeElement);
      let next = current;
      if (event.key === 'ArrowDown') next = Math.min(options.length - 1, current + 1);
      else if (event.key === 'ArrowUp') next = Math.max(0, current - 1);
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = options.length - 1;
      else if (event.key === 'Escape') { event.preventDefault(); close(true); return; }
      else if (event.key === 'Tab') { close(); return; }
      else return;
      event.preventDefault();
      options[next]?.focus();
    });
    document.addEventListener('pointerdown', (event) => {
      if (!wrapper.contains(event.target) && !menu.contains(event.target)) close();
    }, true);
    select.addEventListener('change', refresh);
    select.addEventListener('invalid', (event) => {
      if (!required || select.value) return;
      event.preventDefault();
      if (!validationMessage) {
        validationMessage = document.createElement('span');
        validationMessage.className = 'theme-select-error';
        validationMessage.setAttribute('role', 'alert');
        validationMessage.textContent = 'Choose an option to continue.';
        wrapper.append(validationMessage);
      }
      trigger.focus();
      open(true);
    });
    new MutationObserver(refresh).observe(select, { childList: true, subtree: true, attributes: true });
    enhanced.set(select, { refresh });
    refresh();
  }

  function scan(root) {
    if (root instanceof HTMLSelectElement) enhanceSelect(root);
    root.querySelectorAll?.('select').forEach(enhanceSelect);
  }

  document.querySelectorAll('select').forEach(enhanceSelect);
  new MutationObserver((records) => {
    for (const record of records) {
      if (record.type === 'childList') record.addedNodes.forEach((node) => {
        if (node instanceof Element) scan(node);
      });
      else if (record.target instanceof HTMLSelectElement) enhanced.get(record.target)?.refresh();
      else if (record.target instanceof Element) scan(record.target);
    }
  }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['hidden'] });
  document.addEventListener('reset', (event) => {
    window.setTimeout(() => event.target.querySelectorAll?.('select[data-theme-select-enhanced]').forEach((select) => enhanced.get(select)?.refresh()));
  }, true);
})();
