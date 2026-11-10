(() => {
  'use strict';
  const icons = {
    home:'<path d="m3 10 9-7 9 7v10H3Z"/><path d="M9 20v-7h6v7"/>',
    code:'<path d="m8 6-6 6 6 6m8-12 6 6-6 6m-3-15-2 18"/>',
    classes:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M9 9v11"/>',
    tasks:'<rect x="5" y="3" width="14" height="18" rx="2"/><path d="m8 9 1 1 2-2m2 1h3M8 14h8M8 17h6"/>',
    test:'<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 7h8M8 11h8m-8 4 2 2 4-4"/>',
    history:'<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
    activity:'<path d="M2 12h5l3-8 4 16 3-8h5"/>',
    attendance:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 10h18m-13 5 3 3 5-5"/>',
    calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 10h18M7 14h2m4 0h2m2 3h-2"/>',
    announcement:'<path d="M4 10v5h4l11 5V5L8 10Zm4 5 2 6M22 10v5"/>',
    user:'<circle cx="12" cy="8" r="4"/><path d="M4 22v-3a8 8 0 0 1 16 0v3"/>',
    users:'<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3m1-16a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 5"/>',
    settings:'<path d="m9 3 6 0 1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1Z"/><circle cx="12" cy="12" r="3"/>',
    review:'<path d="M4 3h16v18H4ZM8 7h8M8 11h5m-5 5 2 2 6-5"/>',
    plus:'<path d="M12 4v16M4 12h16"/>',
    menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
  };
  function addIcons(root) {
    root.querySelectorAll('.metric-icon').forEach(node => {
      const label=node.closest('.metric-card')?.textContent.toLowerCase() || '';
      node.dataset.icon=label.includes('student')?'users':label.includes('test')?'test':label.includes('review')||label.includes('revision')?'review':label.includes('assignment')?'tasks':'classes';
      node.textContent='';
    });
    root.querySelectorAll('[data-icon]').forEach(node => {
      if (node.querySelector(':scope > .workspace-icon')) return;
      const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
      svg.classList.add('workspace-icon'); svg.setAttribute('viewBox','0 0 24 24');
      svg.setAttribute('aria-hidden','true'); svg.innerHTML = icons[node.dataset.icon] || icons.code;
      node.prepend(svg);
    });
    root.querySelectorAll('.side-link.active').forEach(node => node.setAttribute('aria-current','page'));
  }
  addIcons(document);
  const menu = document.querySelector('[data-workspace-menu]');
  function closeNavigation() { document.body.classList.remove('navigation-open'); menu?.setAttribute('aria-expanded','false'); }
  menu?.addEventListener('click', () => {
    const open = document.body.classList.toggle('navigation-open');
    menu.setAttribute('aria-expanded',String(open));
    if (open) document.querySelector('.side-nav a')?.focus();
  });
  document.addEventListener('click', event => {
    if (!event.target.closest('.sidebar, [data-workspace-menu]')) closeNavigation();
    if (event.target.closest('.side-link')) closeNavigation();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && document.body.classList.contains('navigation-open')) { closeNavigation(); menu?.focus(); }
  });
  document.addEventListener('focusin', event => {
    if (document.body.classList.contains('navigation-open') && !event.target.closest('.sidebar,[data-workspace-menu]')) closeNavigation();
  });
  function revealAnchor() {
    let id;
    try { id=decodeURIComponent(location.hash.slice(1)); } catch { return; }
    if (!id) return;
    const target=document.getElementById(id);
    for (let node=target;node;node=node.parentElement) if (node.tagName==='DETAILS') node.open=true;
    if (target) requestAnimationFrame(()=>target.scrollIntoView({block:'start'}));
  }
  window.addEventListener('hashchange',revealAnchor); revealAnchor();

  const enhanced = new WeakMap();
  let scheduled = false;
  function columnLabel(header) { return (header.querySelector?.('[data-roster-sort]')?.textContent || header.textContent).replace(/[↕↑↓]/g,'').trim() || 'Column'; }
  function applyColumns(table, hidden) {
    table.querySelectorAll('tr:not(.ui-detail-row)').forEach(row => {
      [...row.children].forEach((cell,index) => {
        if (cell.matches('th,td') && cell.colSpan === 1) cell.classList.toggle('ui-column-hidden',hidden.has(index));
      });
    });
  }
  window.WorkspaceUI = {applyColumns, columnLabel};
  function tableDetails(table, state) {
    table.querySelectorAll('tbody > tr:not(.ui-detail-row)').forEach(row => {
      if (row.children.length !== state.headers.length) return;
      let button = row.querySelector('.ui-row-details-button');
      if (!state.hidden.size || (table.id === 'liveStudentMonitorTable' && state.hidden.has(state.headers.indexOf('Status')))) {
        button?.remove();
        if (row.nextElementSibling?.classList.contains('ui-detail-row')) row.nextElementSibling.remove();
        return;
      }
      if (button) return;
      button = document.createElement('button'); button.type='button'; button.className='ui-row-details-button';
      button.textContent='Details'; button.setAttribute('aria-expanded','false');
      button.addEventListener('click', () => {
        const next=row.nextElementSibling;
        if (next?.classList.contains('ui-detail-row')) { next.remove(); button.setAttribute('aria-expanded','false'); return; }
        const details=document.createElement('tr'); details.className='ui-detail-row';
        const cell=document.createElement('td'); cell.colSpan=state.headers.length-state.hidden.size;
        const list=document.createElement('dl'); list.className='ui-detail-grid';
        state.hidden.forEach(index => {
          const pair=document.createElement('div'), name=document.createElement('dt'), value=document.createElement('dd');
          name.textContent=state.headers[index]; value.textContent=row.children[index]?.textContent.trim() || '—';
          pair.append(name,value); list.append(pair);
        });
        cell.append(list); details.append(cell); row.after(details); button.setAttribute('aria-expanded','true');
      });
      row.children[0].append(button);
    });
  }
  function enhanceTables() {
    scheduled=false;
    document.querySelectorAll('.activity-table-wrap > table, .history-table-wrap > table, .daily-review-table > table').forEach((table,index) => {
      if (table.id === 'attendanceDailyTable') return;
      if (table.closest('[hidden]')) return;
      let state=enhanced.get(table);
      if (!state) {
        const headers=[...table.querySelectorAll('thead tr:first-child th')];
        if (headers.length<5) return;
        const key=`workspace-columns:${location.pathname}:${table.id || index}${table.dataset.columnStorageVersion ? `:${table.dataset.columnStorageVersion}` : ''}`;
        let hidden=[];
        try { hidden=JSON.parse(localStorage.getItem(key) || '[]'); } catch {}
        if (!Array.isArray(hidden)) hidden=[];
        state={headers:headers.map(columnLabel),hidden:new Set(hidden.filter(value=>Number.isInteger(value)&&value>0&&value<headers.length&&(table.hasAttribute('data-column-toggle-last')||value<headers.length-1)))};
        enhanced.set(table,state);
        const region=table.parentElement;
        if(!region.hasAttribute('tabindex')) region.tabIndex=0;
        if(!region.hasAttribute('role')) region.setAttribute('role','region');
        if(!region.hasAttribute('aria-label')) region.setAttribute('aria-label','Scrollable report table');
        const toolbar=document.createElement('div'); toolbar.className='ui-table-controls';
        const hint=document.createElement('span'); hint.textContent='Choose columns to keep the report focused.';
        const chooser=document.createElement('details'); chooser.className='ui-columns';
        const summary=document.createElement('summary'); summary.textContent='Columns';
        const options=document.createElement('div'); options.className='ui-column-options';
        headers.forEach((header,column) => {
          if (column===0 || (column===headers.length-1 && !table.hasAttribute('data-column-toggle-last'))) return;
          const label=document.createElement('label'), input=document.createElement('input');
          input.type='checkbox'; input.checked=!state.hidden.has(column);
          input.addEventListener('change', () => {
            if (input.checked) state.hidden.delete(column); else state.hidden.add(column);
            try { localStorage.setItem(key,JSON.stringify([...state.hidden])); } catch {}
            table.querySelectorAll('.ui-detail-row').forEach(row=>row.remove());
            table.querySelectorAll('.ui-row-details-button').forEach(button=>button.setAttribute('aria-expanded','false'));
            applyColumns(table,state.hidden); tableDetails(table,state);
          });
          label.append(input,document.createTextNode(state.headers[column])); options.append(label);
        });
        chooser.append(summary,options); toolbar.append(hint,chooser); table.parentElement.before(toolbar);
      }
      applyColumns(table,state.hidden); tableDetails(table,state);
    });
  }
  function scheduleTables() { if (!scheduled) { scheduled=true; requestAnimationFrame(enhanceTables); } }
  // Column menus follow the portal popup rule: dismiss on outside click or Escape.
  document.addEventListener('click', event => {
    document.querySelectorAll('details.ui-columns[open]').forEach(menu => {
      if (!menu.contains(event.target)) menu.removeAttribute('open');
    });
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    document.querySelectorAll('details.ui-columns[open]').forEach(menu => {
      menu.removeAttribute('open');
      menu.querySelector('summary')?.focus();
    });
  });
  enhanceTables();
  const content=document.querySelector('.dashboard-main, .ide-main');
  if (content) new MutationObserver(records => {
    if (records.some(record => [...record.addedNodes].some(node => node.nodeType===1 && !node.matches('.workspace-icon,.ui-table-controls,.ui-row-details-button,.ui-detail-row')))) scheduleTables();
  }).observe(content,{childList:true,subtree:true});

  // Drafts are opt-in, tab-local and account-scoped. Never retain credentials or files.
  document.querySelectorAll('form[data-recover-draft]').forEach(form => {
    const key=`workspace-form-draft:${document.body.dataset.userId || 'anonymous'}:${form.dataset.recoverDraft}`;
    const fields=()=>[...form.elements].filter(field=>field.name && !['password','hidden','file','submit','button'].includes(field.type));
    const notice=document.createElement('div'); notice.className='ui-form-feedback'; notice.setAttribute('role','status');
    const label=document.createElement('span'); notice.append(label); form.prepend(notice);
    let previous=null;
    try { previous=JSON.parse(sessionStorage.getItem(key) || 'null'); } catch {}
    if (Array.isArray(previous)) {
      label.textContent='A draft is available in this tab. ';
      const restore=document.createElement('button'), discard=document.createElement('button');
      restore.type=discard.type='button'; restore.className=discard.className='ui-secondary-action';
      restore.textContent='Restore draft'; discard.textContent='Discard';
      restore.addEventListener('click',()=>{
        const available=fields();
        previous.forEach((saved,index)=>{
          const field=available[index];
          if (!field || field.name!==saved.name || field.type!==saved.type) return;
          if (field.type==='checkbox' || field.type==='radio') field.checked=Boolean(saved.checked);
          else if (field.multiple) [...field.options].forEach(option=>option.selected=Array.isArray(saved.value)&&saved.value.includes(option.value));
          else field.value=String(saved.value ?? '');
          field.dispatchEvent(new Event('change',{bubbles:true}));
        });
        restore.remove(); discard.remove(); label.textContent='Draft restored. Select any file uploads again before saving.';
      });
      discard.addEventListener('click',()=>{try{sessionStorage.removeItem(key);}catch{} restore.remove();discard.remove();label.textContent='Draft discarded.';});
      notice.append(restore,discard);
    }
    let timer;
    form.addEventListener('input',()=>{
      clearTimeout(timer);
      timer=setTimeout(()=>{
        const saved=fields().map(field=>({name:field.name,type:field.type,checked:field.checked,value:field.multiple?[...field.selectedOptions].map(option=>option.value):field.value}));
        try { const json=JSON.stringify(saved); if(json.length>500000) return; sessionStorage.setItem(key,json); label.textContent='Draft saved in this tab. Uploaded files are not included.'; }
        catch { label.textContent='Draft recovery is unavailable in this browser. Keep this form open until you save.'; }
      },300);
    });
  });

  // Preserve existing AJAX handlers; native POST forms receive a double-submit guard.
  document.addEventListener('submit', event => {
    const form=event.target;
    if (event.defaultPrevented || !form.matches('form[method=post],form[method=POST]')) return;
    if (form.dataset.uiSubmitting==='true') { event.preventDefault(); return; }
    form.dataset.uiSubmitting='true'; form.setAttribute('aria-busy','true');
    const button=event.submitter;
    if (button) { button.dataset.uiLabel=button.textContent; button.textContent='Saving…'; button.setAttribute('aria-disabled','true'); }
  });
  window.addEventListener('pageshow', () => document.querySelectorAll('form[data-ui-submitting]').forEach(form => {
    delete form.dataset.uiSubmitting; form.removeAttribute('aria-busy');
    form.querySelectorAll('[data-ui-label]').forEach(button => { button.textContent=button.dataset.uiLabel; delete button.dataset.uiLabel; button.removeAttribute('aria-disabled'); });
  }));
})();
