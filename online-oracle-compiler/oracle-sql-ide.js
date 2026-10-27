const compilerSourceLabel = source => ({'SQL*Plus': 'SQLCL', 'PL/SQL': 'SQL WorkSheet'}[source] || source || 'SQL WorkSheet');
(async () => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const shell = $('#ideShell');
  const main = $('#ideMain');
  const workspaceResumeKey = `oracle-workspace-resume:${document.body.dataset.userId || 'local'}`;
  let workspaceResume = null;
  try { workspaceResume = JSON.parse(sessionStorage.getItem(workspaceResumeKey) || 'null'); } catch {}
  window.addEventListener('pagehide', () => {
    try {
      sessionStorage.setItem(workspaceResumeKey, JSON.stringify({
        view: main.dataset.view,
        fullscreen: shell.classList.contains('fullscreen-mode'),
      }));
    } catch {}
  });
  const editor = $('#sqlEditor');
  const plsqlStarterMenu = $('#plsqlStarterMenu');
  const plsqlStarterToggle = $('#plsqlStarterPopupToggle');
  const plsqlCleanCode = $('#plsqlCleanCode');
  const plsqlAutoCommit = $('#plsqlAutoCommit');
  const editMetrics = {
    plsql: { typedChars: 0, pastedChars: 0, pasteCount: 0, editsAfterPaste: 0, hasExternalInput: false, inputMethods: new Set() },
    sqlplus: { typedChars: 0, pastedChars: 0, pasteCount: 0, editsAfterPaste: 0, hasExternalInput: false, inputMethods: new Set() },
  };
  let internalClipboard = null;
  const normalizeClipboardText = value => String(value || '').replace(/\r\n?/g, '\n').trim();
  function rememberInternalCopy(text) {
    const normalized = normalizeClipboardText(text);
    if (normalized) internalClipboard = { text: normalized, copiedAt: Date.now() };
  }
  function consumeInternalCopy(text) {
    if (!internalClipboard || Date.now() - internalClipboard.copiedAt > 120000) {
      internalClipboard = null;
      return false;
    }
    const matches = normalizeClipboardText(text) === internalClipboard.text;
    internalClipboard = null;
    return matches;
  }
  const inputProvenance = {
    plsql: { text: editor?.value || '', sources: Array((editor?.value || '').length).fill(''), pending: '' },
    sqlplus: { text: '', sources: [], pending: '' },
  };
  function trackInputProvenance(source, element, event) {
    const trace = inputProvenance[source];
    const next = element.value;
    if (!event?.isTrusted && !trace.pending) {
      trace.text = next; trace.sources = Array(next.length).fill(''); return;
    }
    const previous = trace.text;
    let prefix = 0;
    while (prefix < previous.length && prefix < next.length && previous[prefix] === next[prefix]) prefix += 1;
    let suffix = 0;
    while (suffix < previous.length - prefix && suffix < next.length - prefix
      && previous[previous.length - 1 - suffix] === next[next.length - 1 - suffix]) suffix += 1;
    const oldEnd = previous.length - suffix;
    const newEnd = next.length - suffix;
    const origin = trace.pending || (event?.inputType === 'insertFromPaste' ? 'pasted'
      : event?.isTrusted && /^insert/i.test(event?.inputType || '') ? 'typed' : '');
    trace.sources = [
      ...trace.sources.slice(0, prefix),
      ...Array(newEnd - prefix).fill(origin),
      ...trace.sources.slice(oldEnd),
    ];
    trace.text = next; trace.pending = '';
  }
  function inputSourceRuns(source, text) {
    const trace = inputProvenance[source];
    const offset = trace.text.indexOf(text);
    if (!text || offset < 0) return [];
    const runs = [];
    let active = '';
    let start = 0;
    for (let index = 0; index < text.length; index += 1) {
      const origin = trace.sources[offset + index] || '';
      if (origin === active) continue;
      if (active) runs.push([start, index, active]);
      active = origin; start = index;
    }
    if (active) runs.push([start, text.length, active]);
    return runs;
  }
  function sliceInputSourceRuns(runs, start, end) {
    return (runs || []).flatMap(([from, to, source]) => {
      const left = Math.max(from, start), right = Math.min(to, end);
      return right > left ? [[left - start, right - start, source]] : [];
    });
  }
  function markInputMethod(source, method) {
    const methods = editMetrics[source].inputMethods;
    if (method === 'file' || method === 'history_recall') methods.clear();
    if (method === 'history_recall') editMetrics[source].hasExternalInput = true;
    methods.add(method);
  }
  function currentInputMethod(source) {
    const methods = [...editMetrics[source].inputMethods];
    if (methods.includes('history_recall')) return 'history_recall';
    if (methods.includes('internal_copy')) return 'internal_copy';
    return methods.length > 1 ? 'mixed' : methods[0] || '';
  }
  function recordPaste(source, characterCount) {
    const metrics = editMetrics[source];
    metrics.pasteCount += 1;
    metrics.pastedChars += Math.max(0, Number(characterCount) || 0);
    metrics.hasExternalInput = true;
    markInputMethod(source, 'pasted');
  }
  document.addEventListener('copy', () => {
    const selection = window.getSelection();
    const selectedNode = selection?.anchorNode;
    if (selectedNode && $('#terminalOutput')?.contains(selectedNode)) rememberInternalCopy(selection.toString());
  });
  function trackManualEdit(source, event) {
    if (!event.isTrusted || !event.inputType || /Paste/i.test(event.inputType)) return;
    if (!/^(insert|delete|historyUndo|historyRedo)/i.test(event.inputType)) return;
    const metrics = editMetrics[source];
    if (event.inputType.startsWith('insert') || event.inputType.startsWith('delete')) markInputMethod(source, 'typed');
    if (event.inputType.startsWith('insert')) metrics.typedChars += event.data?.length || (/LineBreak|Paragraph/i.test(event.inputType) ? 1 : 0);
    if (metrics.hasExternalInput) metrics.editsAfterPaste += 1;
  }
  function takeEditMetrics(sqlplus, inputSourceMap = null) {
    const metrics = editMetrics[sqlplus ? 'sqlplus' : 'plsql'];
    const source = sqlplus ? 'sqlplus' : 'plsql';
    const snapshot = { typedChars: metrics.typedChars, pastedChars: metrics.pastedChars, pasteCount: metrics.pasteCount, editsAfterPaste: metrics.editsAfterPaste, inputMethod: currentInputMethod(source), inputSourceMap: inputSourceMap || inputSourceRuns(source, sqlplus ? terminalInput.value : editor.value) };
    metrics.typedChars = metrics.pastedChars = metrics.pasteCount = metrics.editsAfterPaste = 0;
    metrics.hasExternalInput = false;
    metrics.inputMethods.clear();
    return snapshot;
  }
  const editorResizer = $('#editorResizer');
  const compilerLayout = editorResizer?.closest('.compiler-layout');
  const ratioKey = `oracle-pane-ratio:${document.body.dataset.userId || 'local'}`;
  let editorPaneRatio = 0.56;
  try { const saved=Number(localStorage.getItem(ratioKey)); if(saved>=0.2&&saved<=0.8)editorPaneRatio=saved; } catch {}
  function applyEditorPaneRatio() {
    if (!compilerLayout) return;
    compilerLayout.style.gridTemplateRows = `minmax(0, ${editorPaneRatio}fr) 10px minmax(0, ${1-editorPaneRatio}fr)`;
    const percentage=Math.round(editorPaneRatio*100);
    editorResizer.setAttribute('aria-valuenow', String(percentage));
    editorResizer.setAttribute('aria-valuetext', `Editor ${percentage}%, results ${100-percentage}%`);
    const slider=$('#editorPanePercent'),label=$('#editorPanePercentValue');
    if(slider)slider.value=String(percentage);
    if(label)label.textContent=`Editor ${percentage}% / Results ${100-percentage}%`;
  }
  function setPaneRatio(ratio) {
    editorPaneRatio=Math.max(0.2,Math.min(0.8,ratio));applyEditorPaneRatio();
    try{localStorage.setItem(ratioKey,String(editorPaneRatio));}catch{}
  }
  function resizeEditorPane(clientY) {
    if(!compilerLayout)return;
    const bounds=compilerLayout.getBoundingClientRect();
    if(bounds.height>10)setPaneRatio((clientY-bounds.top)/(bounds.height-10));
  }
  if(editorResizer&&compilerLayout){
    editorResizer.addEventListener('pointerdown',event=>{event.preventDefault();editorResizer.setPointerCapture(event.pointerId);resizeEditorPane(event.clientY);});
    editorResizer.addEventListener('pointermove',event=>{if(editorResizer.hasPointerCapture(event.pointerId))resizeEditorPane(event.clientY);});
    editorResizer.addEventListener('pointerup',event=>{if(editorResizer.hasPointerCapture(event.pointerId))editorResizer.releasePointerCapture(event.pointerId);});
    editorResizer.addEventListener('keydown',event=>{
      if(!['ArrowUp','ArrowDown','Home','End'].includes(event.key))return;
      event.preventDefault();setPaneRatio(event.key==='Home'?0.2:event.key==='End'?0.8:editorPaneRatio+(event.key==='ArrowDown'?0.03:-0.03));
    });
    $('#editorPanePercent')?.addEventListener('input',event=>setPaneRatio(Number(event.target.value)/100));
    applyEditorPaneRatio();
  }
  const outputStyleKey=`oracle-output-style:${document.body.dataset.userId || 'local'}`;
  let outputStyle='website';
  try{if(localStorage.getItem(outputStyleKey)==='sqlplus')outputStyle='sqlplus';}catch{}
  const outputStyleSelect=$('#outputStyle');
  if(outputStyleSelect){
    outputStyleSelect.value=outputStyle;
    outputStyleSelect.addEventListener('change',()=>{
      outputStyle=outputStyleSelect.value==='sqlplus'?'sqlplus':'website';
      try{localStorage.setItem(outputStyleKey,outputStyle);}catch{}
      setResultTab($('.result-tabs [data-result].active')?.dataset.result||'data');
      const entries=[...document.querySelectorAll('#terminalOutput > .terminal-entry')];
      const snapshots=entries.map(entry=>entry._outputSnapshot).filter(Boolean);
      entries.forEach(entry=>entry.remove());
      snapshots.forEach(item=>appendTerminalResult(item.sql,item.data,item.error));
    });
  }
  const terminalInput = $('#terminalInput');
  inputProvenance.sqlplus.text = terminalInput?.value || '';
  inputProvenance.sqlplus.sources = Array(inputProvenance.sqlplus.text.length).fill('');
  const terminalOutput = $('#terminalOutput');
  const terminalPrompt = $('.terminal-prompt');
  if (terminalOutput && terminalPrompt) terminalOutput.append(terminalPrompt);
  const tree = $('#databaseObjectTree');
  document.addEventListener('click', (event) => {
    if (event.target.closest('.object-tree-row')) return;
    $$('.object-action-menu', tree).forEach((menu) => { menu.hidden = true; });
    $$('.object-actions-toggle', tree).forEach((toggle) => toggle.setAttribute('aria-expanded', 'false'));
  });
  const connectDialog = $('#connectDialog');
  const connectForm = $('#connectForm');
  const historyKey = `oracle-sql-ide-history-v2:${document.body.dataset.userId || 'local'}`;
  const settingPrefix = 'oracle-sql-ide-';
  const objectTypes = [
    ['TABLE', '▦', 'Tables'], ['VIEW', '◉', 'Views'], ['MATERIALIZED VIEW', '◈', 'Materialized views'],
    ['INDEX', '⌗', 'Indexes'], ['SEQUENCE', '↗', 'Sequences'], ['PROCEDURE', 'ƒ', 'Procedures'],
    ['FUNCTION', 'ƒ', 'Functions'], ['PACKAGE', '◇', 'Packages'], ['TRIGGER', 'ϟ', 'Triggers'],
    ['TYPE', 'τ', 'Types'], ['SYNONYM', '↪', 'Synonyms'], ['CONSTRAINT', '⊞', 'Constraints']
  ];
  const objectCacheKey = `oracle-navigator-cache-${document.body.dataset.userId || 'local'}`;
  let navigatorCacheId = '';
  let objectRows = [];
  let currentSchema = '';
  let currentView = 'sqlplus';
  let sharedCompilerTabAppearance = {
    sqlplus: { label: 'SQLCL', visible: true, order: 1 },
    compiler: { label: 'SQL WorkSheet', visible: false, order: 2 },
  };
  const requestedView = new URLSearchParams(window.location.search).get('view');
  let resultData = null;
  let history = [];
  let historyRecords = [];
  let historyStudents = [];
  let historyStudentScope = 'assigned';
  let historyPage = 1;
  let historyPagination = { total: 0, pages: 1 };
  let historySort = { key: 'executedAt', direction: 'desc' };
  const historyPageSize = 1000;
  let terminalFontSize = 14;
  let editorFontSize = 18;
  let terminalBuffer = '';
  let terminalSqlBuffer = '';
  let terminalSpool = null;
  let terminalCommandHistory = [];
  let terminalHistoryIndex = 0;
  let terminalHistoryDraft = '';
  let legacyCredentials = null;
  const isTeacher = document.body.dataset.teacher === 'true';
  const isStudent = document.body.dataset.student === 'true';
  let teacherEditorKind = 'plsql';
  let sharedEditor = null;
  let shareSessionToken = sessionStorage.getItem('compiler-editor-share-session') || (crypto.randomUUID ? crypto.randomUUID().replaceAll('-', '') : `${Date.now()}${Math.random().toString(36).slice(2)}`);
  sessionStorage.setItem('compiler-editor-share-session', shareSessionToken);
  let shareSyncTimer = 0;
  let sharePollTimer = 0;
  let lastSharedEditorText = '';
  let lastSharedSqlplusText = '';
  let lastEditPermissionPrompt = '';
  let editorShareConflict = false;

  try { history = JSON.parse(localStorage.getItem(historyKey) || '[]'); } catch { history = []; }
  if (!Array.isArray(history)) history = [];

  function showToast(message) {
    const toast = $('#toast');
    if (!toast) return;
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => { toast.hidden = true; }, 3200);
  }

  let lastCompilerInteraction = Date.now();
  function compilerCsrfToken() {
    const cookie = document.cookie.split('; ').find((part) => part.startsWith('ora_csrf='));
    return cookie ? decodeURIComponent(cookie.slice('ora_csrf='.length)) : '';
  }
  function markCompilerInteraction() { lastCompilerInteraction = Date.now(); }
  function reportCompilerClientEvent(kind, source = 'Workspace') {
    if (!isStudent) return;
    fetch('/api/compiler/activity-events', {
      method: 'POST', credentials: 'same-origin', keepalive: true,
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': compilerCsrfToken() },
      body: JSON.stringify({ kind, source }),
    }).catch(() => {});
  }
  function reportCompilerPaste(source, text) {
    if (!isStudent || !text) return;
    fetch('/api/compiler/paste-events', {
      method: 'POST', credentials: 'same-origin', keepalive: true,
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': compilerCsrfToken() },
      body: JSON.stringify({ source, text, truncated: text.length > 65536 }),
    }).catch(() => {});
  }
  if (isStudent) {
    let lastVisibility = document.visibilityState;
    let lastWindowFocus = document.hasFocus();
    reportCompilerClientEvent(lastVisibility === 'visible' ? 'page_visible' : 'page_hidden');
    document.addEventListener('visibilitychange', () => {
      const next = document.visibilityState;
      if (next === lastVisibility) return;
      lastVisibility = next;
      reportCompilerClientEvent(next === 'visible' ? 'page_visible' : 'page_hidden');
      sendCompilerHeartbeat();
    });
    window.addEventListener('blur', () => {
      if (!lastWindowFocus) return;
      lastWindowFocus = false;
      reportCompilerClientEvent('window_blur');
    });
    window.addEventListener('focus', () => {
      if (lastWindowFocus) return;
      lastWindowFocus = true;
      reportCompilerClientEvent('window_focus');
    });
  }
  ['pointerdown', 'keydown', 'touchstart', 'input'].forEach((name) => document.addEventListener(name, markCompilerInteraction, { passive: true }));
  async function sendCompilerHeartbeat() {
    const csrfToken = compilerCsrfToken();
    try {
      await fetch('/api/portal/activity/heartbeat', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
        body: JSON.stringify({ page: 'compiler', visible: document.visibilityState === 'visible', active: document.visibilityState === 'visible' && Date.now() - lastCompilerInteraction < 60000, labId: $('#compilerLabSelect')?.value || '' })
      });
    } catch { /* The next heartbeat will restore live presence. */ }
  }
  async function loadCompilerLabs() {
    const context = $('#compilerLabContext');
    const select = $('#compilerLabSelect');
    if (!context || !select || !isStudent) return;
    try {
      const response = await fetch('/api/compiler/labs', { credentials: 'same-origin' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Labs are unavailable.');
      const saved = localStorage.getItem('compiler-lab-context') || '';
      select.replaceChildren(new Option('No lab selected', ''));
      (data.labs || []).forEach((lab) => select.add(new Option(`${lab.className} · ${lab.name}`, String(lab.id))));
      context.hidden = !(data.labs || []).length;
      select.value = (data.labs || []).some((lab) => String(lab.id) === saved) ? saved : '';
      sendCompilerHeartbeat();
    } catch { context.hidden = true; }
  }
  $('#compilerLabSelect')?.addEventListener('change', () => {
    localStorage.setItem('compiler-lab-context', $('#compilerLabSelect').value);
    markCompilerInteraction();
    sendCompilerHeartbeat();
  });
  if (isStudent) loadCompilerLabs();
  // The workspace itself is available only to authenticated portal users.
  // Keep presence alive even when a multi-role account has no active role
  // resolved on this page; otherwise an open, signed-in student workspace can
  // appear offline to the teacher roster.
  sendCompilerHeartbeat();
  window.setInterval(sendCompilerHeartbeat, 15000);
  document.addEventListener('visibilitychange', sendCompilerHeartbeat);

  function setView(view) {
    const allowedViews = ['sqlplus', 'compiler', 'history', 'settings'];
    if (isStudent) allowedViews.push('sharedCode');
    if (!allowedViews.includes(view)) return;
    if (['sqlplus', 'compiler'].includes(view) && !sharedCompilerTabAppearance[view]?.visible) {
      view = sharedCompilerTabAppearance.sqlplus?.visible ? 'sqlplus' : 'compiler';
    }
    currentView = view;
    main.dataset.view = view;
    $$('.ide-nav-item, .ide-tabs [data-view]').forEach((button) => {
      const active = button.dataset.view === view;
      button.classList.toggle('active', active);
      if (button.matches('[role="tab"]')) button.setAttribute('aria-selected', String(active));
    });
    $$('[data-panel]').forEach((panel) => {
      const active = panel.dataset.panel === view;
      panel.hidden = !active;
      panel.classList.toggle('active', active);
    });
    if (view === 'history') { loadCompilerHistory(); if (isTeacher && $('#historyStudentFilter')?.value) loadTeacherEditor(); }
    if (view === 'settings' && isStudent) loadStudentEditorSharing();
    if (view === 'sharedCode' && isStudent) loadStudentSharedCode();
    if (view === 'compiler') updateHighlight();
  }

  $$('.ide-nav-item, .ide-tabs [data-view]').forEach((button) => button.addEventListener('click', () => setView(button.dataset.view)));

  function objectField(item, upper, lower) { return item?.[upper] ?? item?.[lower] ?? ''; }
  async function showObjectDetails(owner, name, view) {
    const dialog = document.createElement('dialog');
    dialog.className = 'object-details-dialog';
    const header = document.createElement('div');header.className='object-details-dialog-heading';
    const heading = document.createElement('h2');heading.id='objectDetailsTitle';
    heading.textContent=`${owner}.${name} — ${view[0].toUpperCase()}${view.slice(1)}`;
    dialog.setAttribute('aria-labelledby',heading.id);
    const close = document.createElement('button');close.type='button';close.className='tool-button object-details-close';close.textContent='\u00d7';close.title='Close';close.setAttribute('aria-label','Close object details');
    const subtitle=document.createElement('p');subtitle.className='object-details-dialog-status';subtitle.setAttribute('role','status');subtitle.textContent='Loading object details…';
    const body=document.createElement('div');body.className='object-details-dialog-body';body.tabIndex=0;body.setAttribute('aria-label','Scrollable object details');
    header.append(heading,close);dialog.append(header,subtitle,body);shell.append(dialog);
    const controller=new AbortController();
    close.addEventListener('click',()=>dialog.close());
    dialog.addEventListener('close',()=>{controller.abort();dialog.remove();});
    dialog.showModal();
    try {
      const response=await fetch(`/api/objects/${encodeURIComponent(owner)}/${encodeURIComponent(name)}?view=${encodeURIComponent(view)}`,{credentials:'same-origin',signal:controller.signal});
      const data=await response.json();
      if(!response.ok)throw new Error(data.error||'Could not load object details.');
      if(!dialog.open)return;
      const records=view==='constraints'?data.constraints||[]:data.columns||[];
      if(view==='data'&&data.columns?.length){
        body.append(renderTable(data.columns,data.rows||[]));
      }else if(records.length){
        const keys=Object.keys(records[0]);
        const columns=keys.map(key=>({name:key.replaceAll('_',' ').toLowerCase().replace(/\b\w/g,letter=>letter.toUpperCase())}));
        body.append(renderTable(columns,records.map(record=>keys.map(key=>record[key]))));
      }else{
        body.textContent=view==='constraints'?'No constraints found for this object.':'No object details found.';
      }
      subtitle.textContent=data.truncated?'Showing the first 100 rows. Additional rows are not included.':`${view[0].toUpperCase()}${view.slice(1)} loaded.`;
    }catch(error){
      if(error.name==='AbortError')return;
      subtitle.textContent='Object details unavailable.';
      const message=document.createElement('pre');message.className='result-output error';message.textContent=error.message||'Could not load object details.';body.append(message);
    }
  }
  function renderDatabaseObjects() {
    tree.replaceChildren();
    if (!objectRows.length) {
      const empty = document.createElement('div');
      empty.className = 'navigator-empty';
      empty.textContent = $('#connectionDot').classList.contains('connected') ? 'No objects found in the selected schema.' : 'Connect to Oracle to browse database objects.';
      tree.append(empty);
      return;
    }
    const query = $('#objectSearch').value.trim().toLowerCase();
    const schema = $('#navSchema').value;
    const selectedType = $('#objectTypeFilter').value;
    const visible = objectRows.filter((item) => {
      const owner = String(objectField(item, 'OWNER', 'owner')).toUpperCase();
      const name = String(objectField(item, 'OBJECT_NAME', 'object_name')).toUpperCase();
      const type = String(objectField(item, 'OBJECT_TYPE', 'object_type')).toUpperCase();
      return (!schema || owner === schema) && (!selectedType || type === selectedType) && (!query || name.toLowerCase().includes(query) || owner.toLowerCase().includes(query) || type.toLowerCase().includes(query));
    });
    if (!visible.length) {
      const empty = document.createElement('div');
      empty.className = 'navigator-empty';
      empty.textContent = 'No matching database objects.';
      tree.append(empty);
      return;
    }
    for (const [type, icon, label] of objectTypes) {
      const matches = visible.filter((item) => String(objectField(item, 'OBJECT_TYPE', 'object_type')).toUpperCase() === type);
      if (!matches.length) continue;
      const group = document.createElement('section');
      group.className = 'object-group';
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'object-group-toggle';
      toggle.setAttribute('aria-expanded', 'true');
      toggle.innerHTML = `<span class="group-chevron">⌄</span><span class="group-icon">${icon}</span><span></span><span class="group-count"></span>`;
      toggle.querySelectorAll('span')[2].textContent = label;
      toggle.querySelector('.group-count').textContent = `(${matches.length})`;
      const items = document.createElement('div');
      items.className = 'object-group-items';
      items.hidden = false;
      toggle.addEventListener('click', () => {
        const open = toggle.getAttribute('aria-expanded') !== 'true';
        toggle.setAttribute('aria-expanded', String(open));
        toggle.querySelector('.group-chevron').textContent = open ? '⌄' : '›';
        items.hidden = !open;
      });
      for (const item of matches) {
        const owner = String(objectField(item, 'OWNER', 'owner')).toUpperCase();
        const name = String(objectField(item, 'OBJECT_NAME', 'object_name')).toUpperCase();
        const status = String(objectField(item, 'STATUS', 'status') || '');
        const row = document.createElement('div');
        row.className = 'object-tree-row';
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'object-tree-item';
        button.title = `${owner}.${name}${status ? ` · ${status}` : ''}`;
        button.innerHTML = `<span class="object-symbol">${icon}</span><span></span>`;
        button.querySelector('span:nth-child(2)').textContent = name;
        button.addEventListener('click', () => {
          $$('.object-tree-item', tree).forEach((node) => node.classList.remove('selected'));
          button.classList.add('selected');
          const isTable = ['TABLE', 'VIEW', 'MATERIALIZED VIEW'].includes(type);
          editor.value = isTable ? `SELECT * FROM "${owner}"."${name}";` : `-- ${label.replace(/s$/, '')} ${owner}.${name}\n`;
          updateLineNumbers();
          setView('compiler');
          editor.focus();
        });
        const menuToggle = document.createElement('button');
        menuToggle.type = 'button';
        menuToggle.className = 'object-actions-toggle';
        menuToggle.textContent = '•••';
        menuToggle.title = `Actions for ${name}`;
        menuToggle.setAttribute('aria-label', `Actions for ${name}`);
        menuToggle.setAttribute('aria-expanded', 'false');
        const menu = document.createElement('div');
        menu.className = 'object-action-menu';
        menu.setAttribute('role', 'menu');
        menu.hidden = true;
        const supportsStructure = ['TABLE', 'VIEW', 'MATERIALIZED VIEW', 'INDEX'].includes(type);
        const supportsTableActions = ['TABLE', 'VIEW', 'MATERIALIZED VIEW'].includes(type);
        [['structure', 'Structure', supportsStructure], ['data', 'Data', supportsTableActions], ['constraints', 'Constraints', supportsTableActions]].forEach(([view, label, enabled]) => {
          const action = document.createElement('button');
          action.type = 'button';
          action.setAttribute('role', 'menuitem');
          action.textContent = label;
          action.disabled = !enabled;
          action.addEventListener('click', (event) => {
            event.stopPropagation();
            menu.hidden = true;
            menuToggle.setAttribute('aria-expanded', 'false');
            showObjectDetails(owner, name, view);
          });
          menu.append(action);
        });
        menuToggle.addEventListener('click', (event) => {
          event.stopPropagation();
          const shouldOpen = menu.hidden;
          $$('.object-action-menu', tree).forEach((otherMenu) => { otherMenu.hidden = true; });
          $$('.object-actions-toggle', tree).forEach((otherToggle) => otherToggle.setAttribute('aria-expanded', 'false'));
          menu.hidden = !shouldOpen;
          menuToggle.setAttribute('aria-expanded', String(shouldOpen));
          if (shouldOpen) {
            const rowBounds = row.getBoundingClientRect();
            const treeBounds = tree.getBoundingClientRect();
            row.classList.toggle('menu-up', rowBounds.bottom + 116 > treeBounds.bottom && rowBounds.top - 116 > treeBounds.top);
          }
        });
        menu.addEventListener('click', (event) => event.stopPropagation());
        row.append(button, menuToggle, menu);
        items.append(row);
      }
      group.append(toggle, items);
      tree.append(group);
    }
  }

  function applyNavigatorObjects(data) {
      const schemaSelect = $('#navSchema');
      const previous = schemaSelect.value;
      schemaSelect.replaceChildren();
      const owners = [...new Set(objectRows.map((item) => String(objectField(item, 'OWNER', 'owner')).toUpperCase()))].sort();
      if (!data.defaultSchemaOnly && owners.length) {
        const all = document.createElement('option');
        all.value = '';
        all.textContent = 'All schemas';
        schemaSelect.append(all);
      }
      owners.forEach((owner) => {
        const option = document.createElement('option');
        option.value = owner;
        option.textContent = owner;
        schemaSelect.append(option);
      });
      currentSchema = data.user || owners[0] || '';
      schemaSelect.value = owners.includes(previous) ? previous : (owners.includes(currentSchema) ? currentSchema : (data.defaultSchemaOnly ? owners[0] || '' : ''));
      $('#navigatorStatus').textContent = `${objectRows.length} objects`;
      renderDatabaseObjects();
  }
  function restoreNavigatorObjects() {
    try {
      const cached = JSON.parse(sessionStorage.getItem(objectCacheKey) || 'null');
      if (!navigatorCacheId || cached?.id !== navigatorCacheId || !Array.isArray(cached.data?.objects)) return false;
      objectRows = cached.data.objects;
      applyNavigatorObjects(cached.data);
      return true;
    } catch { return false; }
  }

  async function refreshDatabaseNavigator(plan = null) {
    const names=plan?.names || [];
    const partial=!plan?.full && names.length>0;
    try {
      $('#navigatorStatus').textContent = 'Loading objects…';
      const response = await fetch('/api/objects'+(partial?'?names='+encodeURIComponent(JSON.stringify(names)):''), { credentials: 'same-origin' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Could not load database objects.');
      objectRows = partial ? window.CompilerMergeObjects(objectRows,data.objects||[],names) : data.objects || [];
      applyNavigatorObjects(data);
      if (navigatorCacheId) {
        try { sessionStorage.setItem(objectCacheKey, JSON.stringify({ id: navigatorCacheId, data: { ...data, objects: objectRows } })); } catch {}
      }
    } catch (error) {
      // Keep the last usable navigator when metadata refresh fails.
      $('#navigatorStatus').textContent = error.message || 'Could not load objects';
    }
  }

  const connectionChoiceKey = `oracle-connection-choice-${document.body.dataset.userId || 'current'}-${document.body.dataset.loginId || 'current-login'}`;
  let manualOracleDisconnect = false;
  try { manualOracleDisconnect = sessionStorage.getItem(connectionChoiceKey) === 'manual'; } catch {}
  function requireManualConnection() {
    manualOracleDisconnect = true;
    try { sessionStorage.setItem(connectionChoiceKey, 'manual'); } catch {}
  }

  function setStatus(connected, label) {
    const wasConnected=$('#connectionDot').classList.contains('connected');
    $('#connectionDot').classList.toggle('connected', connected);
    const [status, ...serviceParts] = label.split('·');
    $('#connectionLabel').textContent = status.trim();
    $('#connectionService').textContent = connected && serviceParts.length ? serviceParts.join('·').trim() : 'Oracle Database';
    $('#navigatorStatusLed').classList.toggle('online', connected);
    if(!connected || !wasConnected) $('#navigatorStatus').textContent = connected ? 'Objects ready' : 'Navigator offline';
    $('#terminalLed').classList.toggle('online', connected);
    $('#terminalStatus').textContent = connected ? 'Connected to Oracle' : 'Awaiting connection';
    const control = $('#openConnection');
    control.textContent = connected ? 'Disconnect' : 'Connect';
    control.dataset.connected = String(connected);
    control.setAttribute('aria-label', connected ? 'Disconnect from Oracle' : 'Connect to Oracle');
    if (connected && !wasConnected && !restoreNavigatorObjects()) refreshDatabaseNavigator();
    else if(!connected) { objectRows = []; renderDatabaseObjects(); }
  }

  function saveHistory() {
    try { localStorage.setItem(historyKey, JSON.stringify(history)); } catch { /* The current session remains usable. */ }
    if (currentView === 'history') loadCompilerHistory();
  }
  async function readHistoryResponse(response) {
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.toLocaleLowerCase().includes('application/json')) {
      const body = await response.text();
      if (response.redirected || /<form[^>]+login|<title>.*login/i.test(body)) {
        throw new Error('Your session has expired. Sign in again to view SQL history.');
      }
      throw new Error(`SQL History service failed (HTTP ${response.status}). Restart the workbench server after applying the latest update, then reload this page.`);
    }
    try { return await response.json(); }
    catch { throw new Error('The SQL History API returned invalid data. Refresh the page and try again.'); }
  }
  function addHistory(sql, source, response, error) {
    const statements = response?.statementResults?.length ? response.statementResults : [{ statement: sql, ...response, error }];
    statements.forEach(item => history.unshift({ sql: item.statement, source, executedAt: new Date().toISOString(),
      status: item.error ? 'error' : 'success', durationMs: response?.durationMs || 0,
      rows: item.columns?.length ? item.rows?.length || 0 : Math.max(0, item.rowsAffected || 0), message: item.error || item.message || '' }));
    saveHistory();
    loadOwnHistory();
  }
  let ownHistoryRevision = 0;
  async function loadOwnHistory() {
    const revision = ++ownHistoryRevision;
    try {
      const response = await fetch('/api/compiler/history?own=1', { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) return;
      const data = await readHistoryResponse(response);
      if (revision !== ownHistoryRevision) return;
      history = data.records || [];
      if (isStudent && data.todayCounts) {
        $('#todaySuccessCount').textContent = String(data.todayCounts.successful ?? 0);
        $('#todayFailedCount').textContent = String(data.todayCounts.failed ?? 0);
        $('#todayStatementCounts').hidden = false;
      }
      try { localStorage.setItem(historyKey, JSON.stringify(history)); } catch {}
      if ($('.result-tabs [data-result="history"]')?.classList.contains('active')) setResultTab('history', false);
    } catch { /* Keep the last loaded history while offline. */ }
  }

  function safeCell(value) {
    if (value === null || value === undefined) return 'NULL';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }
  function renderTable(columns, rows) {
    const table = document.createElement('table');
    table.className = 'result-table';
    const thead = document.createElement('thead');
    const headRow = document.createElement('tr');
    columns.forEach((column) => {
      const th = document.createElement('th');
      th.textContent = column.name || String(column);
      headRow.append(th);
    });
    thead.append(headRow);
    const tbody = document.createElement('tbody');
    rows.forEach((row) => {
      const tr = document.createElement('tr');
      row.forEach((value) => {
        const td = document.createElement('td');
        td.textContent = safeCell(value);
        tr.append(td);
      });
      tbody.append(tr);
    });
    table.append(thead, tbody);
    return table;
  }
  function renderResult() {
    const body = $('#resultBody');
    body.replaceChildren();
    if (!resultData) {
      const empty = document.createElement('div');
      empty.className = 'result-empty';
      empty.innerHTML = '<span>⌁</span><strong>Ready to execute</strong><small>Query results will appear here.</small>';
      body.append(empty);
      return;
    }
    if (resultData.error) {
      const message = document.createElement('pre');
      message.className = 'result-output error';
      message.textContent = resultData.error;
      body.append(message);
      return;
    }
    if (outputStyle === 'sqlplus') {
      const output=document.createElement('pre');output.className='result-output';
      output.textContent=window.CompilerWorkspace.outputText(resultData.sourceSql || '',resultData);body.append(output);return;
    }
    if (resultData.columns?.length) body.append(renderTable(resultData.columns, resultData.rows || []));
    if (resultData.outputLines?.length || resultData.message || resultData.rowsAffected !== null) {
      const output = document.createElement('pre');
      output.className = 'result-output';
      output.textContent = [...(resultData.outputLines || []), resultData.message || '', resultData.rowsAffected !== null && resultData.rowsAffected !== undefined ? `${resultData.rowsAffected} rows affected.` : ''].filter(Boolean).join('\n');
      body.append(output);
    }
    if (!body.childElementCount) body.textContent = 'Statement executed successfully.';
  }

  let executionBusy = false;
  async function collectBinds(source, { sequential = false, substitutionNames = [] } = {}) {
    const names = [...new Set(substitutionNames.map(name=>'&'+name.toUpperCase())), ...window.CompilerWorkspace.binds(source)];
    if (!names.length) return {};
    const dialog = document.createElement('dialog'); dialog.className = 'ide-dialog';
    const form = document.createElement('form');
    const heading = document.createElement('h2'); heading.id = 'compilerBindTitle'; heading.textContent = 'Enter variable values'; dialog.setAttribute('aria-labelledby', heading.id); form.append(heading);
    const fields = [];
    for (const name of names) {
      const label = document.createElement('label'); label.textContent = name.startsWith('&') ? name : ':' + name;
      const type = document.createElement('select');
      for (const [value,text] of [['text','Text'],['number','Number'],['null','NULL']]) {
        const option = document.createElement('option'); option.value=value;option.textContent=text;type.append(option);
      }
      const input = document.createElement('input'); input.autocomplete='off'; input.setAttribute('aria-label', ':'+name+' value');
      type.setAttribute('aria-label', ':'+name+' type');
      type.addEventListener('change',()=>{input.disabled=type.value==='null';});
      label.append(type,input);form.append(label);fields.push({name,type,input,label});
    }
    const error = document.createElement('p');error.className='dialog-error';form.append(error);
    const cancel = document.createElement('button');cancel.type='button';cancel.className='tool-button';cancel.textContent='Cancel';
    const run = document.createElement('button');run.type='submit';run.className='run-button';run.textContent='Execute';
    form.append(cancel,run);dialog.append(form);shell.append(dialog);
    let currentField = 0;
    const collectedValues = {};
    function showField() {
      if (!sequential) return;
      fields.forEach((field,index)=>{field.label.hidden=index!==currentField;});
      heading.textContent=`Enter value for :${fields[currentField].name} (${currentField+1} of ${fields.length})`;
      run.textContent=currentField===fields.length-1?'Execute':'Next';
      error.textContent='';fields[currentField].input.focus();
    }
    showField();
    return new Promise(resolve=>{
      let settled=false;
      const finish=value=>{if(settled)return;settled=true;dialog.close();dialog.remove();resolve(value);};
      cancel.addEventListener('click',()=>finish(null));
      dialog.addEventListener('cancel',event=>{event.preventDefault();finish(null);});
      form.addEventListener('submit',event=>{
        event.preventDefault();const values=collectedValues;
        for(const field of (sequential ? [fields[currentField]] : fields)){
          if(field.type.value==='number' && (!field.input.value.trim() || !Number.isFinite(Number(field.input.value)))) {
            error.textContent='Enter a valid number for :'+field.name;field.input.focus();return;
          }
          values[field.name]=field.type.value==='null'?null:field.type.value==='number'?Number(field.input.value):field.input.value;
        }
        if(sequential && currentField<fields.length-1){currentField++;showField();return;}
        finish(values);
      });
      dialog.showModal();fields[0].input.focus();
    });
  }
  const terminalQueueKey = `oracle-terminal-queue:${document.body.dataset.userId || 'local'}:${location.pathname}`;
  const terminalBufferKey = `${terminalQueueKey}:buffer-size`;
  const terminalTranscriptKey = `${terminalQueueKey}:transcript`;
  let defaultTerminalBufferEntries = 5000;
  let personalTerminalBufferEntries = null;
  try {
    const saved = Number(localStorage.getItem(terminalBufferKey));
    if (Number.isInteger(saved) && saved >= 100 && saved <= 50000) personalTerminalBufferEntries = saved;
  } catch {}
  function trimTerminalBuffer() {
    const limit = personalTerminalBufferEntries || defaultTerminalBufferEntries;
    const entries = [...$('#terminalOutput').querySelectorAll('.terminal-entry')];
    entries.slice(0, Math.max(0, entries.length-limit)).forEach(entry => entry.remove());
    if (terminalCommandHistory.length > limit) terminalCommandHistory.splice(0, terminalCommandHistory.length-limit);
    terminalHistoryIndex = Math.min(terminalHistoryIndex, terminalCommandHistory.length);
  }
  function restoreTerminalCommandHistory(entries) {
    terminalCommandHistory = (entries || [])
      .filter(item => typeof item?.sql === 'string' && item.sql.trim() && !item.data?.commentOnly)
      .map(item => item.sql);
    const limit = personalTerminalBufferEntries || defaultTerminalBufferEntries;
    if (terminalCommandHistory.length > limit) terminalCommandHistory = terminalCommandHistory.slice(-limit);
    terminalHistoryIndex = terminalCommandHistory.length;
    terminalHistoryDraft = '';
  }
  window.addEventListener('pagehide', () => {
    try {
      trimTerminalBuffer();
      const entries = [...$('#terminalOutput').querySelectorAll('.terminal-entry')]
        .map(entry => entry._outputSnapshot).filter(item => item && !item.data?.pending);
      sessionStorage.setItem(terminalTranscriptKey, JSON.stringify(entries));
    } catch { /* Current output remains available if browser storage is full. */ }
  });
  let terminalQueue = null;
  let terminalBatchCancelled = false;
  document.addEventListener('keydown', event => {
    if (!(event.ctrlKey && event.key.toLowerCase() === 'c') || (!terminalQueue && !terminalInput.readOnly)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    terminalBatchCancelled = true;
    try { sessionStorage.removeItem(terminalQueueKey); } catch {}
    $('.terminal-substitution')?.dispatchEvent(new Event('terminal-cancel'));
    $('#terminalStatus').textContent = 'Stopping pasted statements…';
  }, true);
  function saveTerminalQueue() {
    if (terminalBatchCancelled) { sessionStorage.removeItem(terminalQueueKey); return; }
    if (!terminalQueue) { sessionStorage.removeItem(terminalQueueKey); return; }
    terminalQueue.transcript = [...$('#terminalOutput').querySelectorAll('.terminal-entry')]
      .map(entry => entry._outputSnapshot).filter(item => item && !item.data?.pending);
    sessionStorage.setItem(terminalQueueKey, JSON.stringify(terminalQueue));
  }
  async function waitForTerminalConnection() {
    while ($('#openConnection').dataset.connected !== 'true') {
      if (terminalBatchCancelled) return false;
      $('#terminalStatus').textContent = 'Waiting for database connection…';
      if (!manualOracleDisconnect && !reconnectPromise && Date.now() - lastReconnectAttempt >= 15000) {
        await reconnectSavedCredentials();
      }
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    return !terminalBatchCancelled;
  }
  async function execute(sql, { sqlplus = false, script = false, executionMode, inputSourceMap = null } = {}) {
    let statement = sql.trim();
    if (sqlplus && statement === '/') {
      if (!terminalSqlBuffer) {
        appendTerminalResult('/', null, 'SP2-0103: Nothing in SQL buffer to run.');
        return { error: 'Nothing in SQL buffer to run.' };
      }
      statement = terminalSqlBuffer;
    }
    if (!statement) return null;
    if (executionBusy) { showToast('An execution is already in progress.'); return null; }
    executionBusy = true;
    const originalStatement = statement;
    const substitutionLines = [];
    let substitutionEntry = null;
    if (!sqlplus || !terminalQueue?.request) {
      const comments = window.CompilerWorkspace.scan(statement).tokens.filter(token => token.kind === 'comment');
      const matches = [...statement.matchAll(/&&?([A-Za-z][A-Za-z0-9_$#]*)/g)]
        .filter(match => !comments.some(token => match.index >= token.start && match.index < token.end));
      if (matches.length) {
        if (sqlplus) substitutionEntry = appendTerminalResult(originalStatement, { pending: true }, null);
        const replacements = [];
        for (const match of matches) {
          const value = await (sqlplus ? promptTerminalSubstitution(match[1]) : promptPlsqlSubstitution(match[1]));
          if (value === null) { substitutionEntry?.remove(); executionBusy = false; return null; }
          replacements.push({start:match.index,end:match.index+match[0].length,value});
          substitutionLines.push(`Enter value for ${match[1].toLowerCase()}: ${value}`);
        }
        for (const replacement of replacements.reverse()) statement = statement.slice(0,replacement.start)+replacement.value+statement.slice(replacement.end);
        originalStatement.split('\n').forEach((line,index) => {
          const updated = statement.split('\n')[index];
          if (line !== updated) substitutionLines.push(`old ${String(index+1).padStart(3)}: ${line}`, `new ${String(index+1).padStart(3)}: ${updated}`);
        });
      }
    }
    const binds = sqlplus ? (terminalQueue?.request ? JSON.parse(terminalQueue.request.body).binds : {}) : await collectBinds(statement);
    if (binds === null) { executionBusy = false; if (!sqlplus) $('#resultRuntime').textContent = 'Execution cancelled'; return null; }
    if (sqlplus && !isSqlPlusClientInput(statement)) {
      const buffered = window.CompilerWorkspace.ranges(statement).at(-1)?.sql;
      if (buffered) terminalSqlBuffer = buffered;
    }
    if (sqlplus && /^CLEAR\s+SQL\s*;?$/i.test(statement)) terminalSqlBuffer = '';
    const editorPosition = sqlplus ? null : {
      selectionStart: editor.selectionStart,
      selectionEnd: editor.selectionEnd,
      scrollTop: editor.scrollTop,
      scrollLeft: editor.scrollLeft
    };
    if (isStudent) await shareBeforeStatement(sqlplus, statement);
    const editSource = takeEditMetrics(sqlplus, inputSourceMap || inputSourceRuns(sqlplus ? 'sqlplus' : 'plsql', originalStatement));
    const started = performance.now();
    if (!sqlplus) $('#resultRuntime').textContent = 'Running…';
    const terminalEntry = substitutionEntry || (sqlplus ? appendTerminalResult(statement, { pending: true }, null) : null);
    let responseReceived = false;
    try {
      const body = JSON.stringify({ sql: statement, sqlplus, autoCommit: sqlplus ? $('#sqlplusAutoCommit').checked : $('#plsqlAutoCommit').checked, script, executionMode, binds, editSource });
      if (sqlplus && terminalQueue && !terminalQueue.request) {
        terminalQueue.request = { id: crypto.randomUUID(), body };
        saveTerminalQueue();
      }
      const queuedRequest = sqlplus ? terminalQueue?.request : null;
      const response = await fetch('/api/execute', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', ...(queuedRequest ? { 'X-Terminal-Request-Id': queuedRequest.id } : {}) },
        body: queuedRequest?.body || body
      });
      const data = await response.json();
      responseReceived = true;
      if (substitutionLines.length) {
        data.outputLines = [...substitutionLines, ...(data.outputLines || [])];
        if (data.statementResults?.length) data.statementResults[0].outputLines = [...substitutionLines, ...(data.statementResults[0].outputLines || [])];
      }
      if (sqlplus && terminalQueue && (response.status === 401 || data.connectionUnavailable)) {
        setStatus(false, 'Not connected');
        terminalEntry?.remove();
        return { retryPending: true };
      }
      if (!response.ok) {
        if (response.status === 401 || data.disconnected) setStatus(false, 'Not connected');
        throw new Error(data.error || 'SQL execution failed.');
      }
      if (!sqlplus) {
        resultData = { ...data, sourceSql: statement };
        $('#resultRuntime').textContent = `${data.durationMs ?? Math.round(performance.now() - started)} ms${data.rows?.length ? ` · ${data.rows.length} rows` : ''}`;
        renderResult();
      }
      const batchFailure = data.executionErrors?.length ? data.executionErrors.map((item) => `${item.statement}: ${item.error}`).join('\n') : null;
      addHistory(statement, sqlplus ? 'SQL*Plus' : 'PL/SQL', data, batchFailure);
      if (sqlplus) {
        if (data.sqlPlusSpool?.action === 'start') {
          terminalSpool = { filename: data.sqlPlusSpool.filename, mode: data.sqlPlusSpool.mode, offset: $('#terminalOutput').innerText.length };
        } else if (data.sqlPlusSpool?.action === 'stop' && terminalSpool) {
          const transcript = $('#terminalOutput').innerText.slice(terminalSpool.offset).trim();
          const link = document.createElement('a');
          link.href = URL.createObjectURL(new Blob([transcript], { type: 'text/plain;charset=utf-8' }));
          link.download = terminalSpool.filename;
          link.click();
          URL.revokeObjectURL(link.href);
          terminalSpool = null;
        }
        if (data.clearTerminal) $('#terminalOutput').replaceChildren(terminalPrompt);
        else if (Array.isArray(data.statementResults) && data.statementResults.length) {
          terminalEntry?.remove();
          data.statementResults.forEach((item) => appendTerminalResult(item.statement, { ...data, ...item }, item.error || null));
        } else appendTerminalResult(statement, data, null, terminalEntry);
        if (data.sqlPlusDisconnected || data.disconnected) setStatus(false, 'Not connected');
        if (typeof data.sqlPlusAutoCommit === 'boolean') {
          sqlplusAutoCommit.checked = data.sqlPlusAutoCommit;
          localStorage.setItem('ora-sqlplus-autocommit', data.sqlPlusAutoCommit ? 'ON' : 'OFF');
        }
        if (typeof data.sqlPlusPrompt === 'string') $('.terminal-prompt label').textContent = data.sqlPlusPrompt;
      }
      const refreshPlan=data.metadataRefresh;
      if (refreshPlan?.full || refreshPlan?.names?.length) {
        const refresh=refreshDatabaseNavigator(refreshPlan);
        if ($('#waitNavigatorRefresh')?.checked) await refresh;
      }
      return data;
    } catch (error) {
      if (sqlplus && terminalQueue && !responseReceived) {
        terminalEntry?.remove();
        setStatus(false, 'Waiting for connection');
        return { retryPending: true };
      }
      const failure = { error: error.message || 'SQL execution failed.' };
      if (!sqlplus) {
        resultData = failure;
        $('#resultRuntime').textContent = 'Failed';
        renderResult();
      }
      addHistory(statement, sqlplus ? 'SQL*Plus' : 'PL/SQL', null, failure.error);
      if (sqlplus) appendTerminalResult(statement, null, failure.error, terminalEntry);
      return failure;
    } finally {
      executionBusy = false;
      if (editorPosition) {
        editor.focus({ preventScroll: true });
        editor.setSelectionRange(
          Math.min(editorPosition.selectionStart, editor.value.length),
          Math.min(editorPosition.selectionEnd, editor.value.length)
        );
        editor.scrollTop = editorPosition.scrollTop;
        editor.scrollLeft = editorPosition.scrollLeft;
        updateHighlight();
        updateEditorCursorPosition();
      }
    }
  }

  async function promptPlsqlSubstitution(name) {
    const dialog = document.createElement('dialog');
    dialog.className = 'ide-dialog';
    const form = document.createElement('form');
    const title = document.createElement('h2');title.textContent='Substitution variable';
    const label = document.createElement('label');label.textContent=`Enter value for ${name.toLowerCase()}`;
    const input = document.createElement('input');input.type='text';input.autocomplete='off';label.append(input);
    const submit = document.createElement('button');submit.type='submit';submit.className='run-button';submit.textContent='Next';
    const cancel = document.createElement('button');cancel.type='button';cancel.className='tool-button';cancel.textContent='Cancel';
    form.append(title,label,submit,cancel);dialog.append(form);shell.append(dialog);
    return new Promise(resolve => {
      let settled=false;
      const finish=value=>{if(settled)return;settled=true;dialog.close();dialog.remove();resolve(value);};
      form.addEventListener('submit',event=>{event.preventDefault();finish(input.value);});
      cancel.addEventListener('click',()=>finish(null));
      dialog.addEventListener('cancel',event=>{event.preventDefault();finish(null);});
      dialog.showModal();input.focus();
    });
  }

  async function promptTerminalSubstitution(name) {
    const form = document.createElement('form');
    form.className = 'terminal-substitution';
    const label = document.createElement('label');
    label.textContent = `Enter value for ${name.toLowerCase()}: `;
    const input = document.createElement('input');
    input.type = 'text'; input.autocomplete = 'off';
    label.append(input); form.append(label);
    $('#terminalOutput').insertBefore(form, terminalPrompt);
    form.scrollIntoView({block:'nearest'});input.focus();
    return new Promise(resolve => {
      form.addEventListener('submit', event => {event.preventDefault();const value=input.value;form.remove();resolve(value);});
      form.addEventListener('terminal-cancel', () => {form.remove();resolve(null);});
      input.addEventListener('keydown', event => {
        if (event.key === 'Escape') {event.preventDefault();form.remove();resolve(null);}
      });
    });
  }

  function appendTerminalResult(sql, data, error, existingEntry = null) {
    // Apply prompt boundaries at rendering too: script/server results can bypass
    // the interactive input queue. Inline comments stay inside their statement.
    if(!existingEntry && !data?.commentOnly){
      const leading=window.CompilerWorkspace.leadingComments(sql);
      if(leading.comments.length){
        leading.comments.forEach(comment=>appendTerminalResult(comment,{commentOnly:true},null));
        sql=leading.sql;
        if(!sql.trim())return;
      }
    }
    const output = $('#terminalOutput');
    $('.terminal-welcome', output)?.remove();
    const entry = existingEntry || document.createElement('article');
    entry.className = data?.commentOnly ? 'terminal-entry terminal-comment' : 'terminal-entry';
    entry._outputSnapshot = { sql, data, error };
    if(existingEntry) {
      // The command was already echoed before the request; append only its result.
    } else if(data?.commentOnly){
      // Each physical comment line has a decorative prompt, never a line number.
      for(const line of sql.split(/\r?\n/)){
        const command=document.createElement('pre');command.className='terminal-command';
        command.innerHTML=renderSyntaxHighlight(line || ' ');entry.append(command);
      }
    } else {
      const command = document.createElement('pre');
      command.className = 'terminal-command';
      command.innerHTML = renderSyntaxHighlight(sql || ' ');
      if (sql.includes('\n')) {
        command.replaceChildren(...sql.split('\n').map((line, index) => {
          const row = document.createElement('span');
          row.className = 'terminal-command-line';
          row.dataset.lineNumber = index ? String(index + 1) : '';
          row.innerHTML = renderSyntaxHighlight(line || ' ');
          return row;
        }));
      }
      entry.append(command);
    }
    if (data?.pending || data?.commentOnly) {
      // Comments advance the prompt without an Oracle execution/result block.
    } else if (error) {
      const errorBlock = document.createElement('pre');
      errorBlock.className = 'terminal-output-text error';
      errorBlock.textContent = error;
      entry.append(errorBlock);
    } else if (outputStyle === 'sqlplus') {
      const text=document.createElement('pre');text.className='terminal-output-text';text.style.whiteSpace='pre';
      text.textContent=window.CompilerWorkspace.outputText(sql,{...data,statementResults:undefined});entry.append(text);
    } else if (data?.columns?.length) {
      const table = renderTable(data.columns, data.rows || []);
      if (data.sqlPlusHeading === false) $('thead', table)?.remove();
      entry.append(table);
      const command = window.CompilerWorkspace.scan(sql).tokens.find(token=>token.kind!=='comment')?.text.toUpperCase();
      if (data.sqlPlusFeedback !== false && !['DESC','DESCRIBE'].includes(command)) {
        const count = document.createElement('p');
        count.className = 'terminal-success';
        const selectedRows = data.rows?.length || 0;
        count.textContent = `${selectedRows} row${selectedRows === 1 ? '' : 's'} selected.`;
        entry.append(count);
      }
    } else {
      const result = document.createElement('pre');
      result.className = 'terminal-output-text';
      const lines = data?.outputLines || [];
      result.textContent = [...lines, data?.message || (lines.length ? '' : 'Statement executed successfully.')].filter(Boolean).join('\n');
      entry.append(result);
    }
    if (!existingEntry) output.insertBefore(entry, $('.terminal-prompt', output));
    output.scrollTop = output.scrollHeight;
    trimTerminalBuffer();
    return entry;
  }

  function connectionDiagnosis(message, online = navigator.onLine, transport = false) {
    const detail = String(message || 'Connection failed.');
    if (!online) return 'This device appears offline. Check Wi-Fi/Ethernet, the network adapter, and your router. Internet availability cannot be confirmed until connectivity returns.';
    if (/session.*expired|sign in again/i.test(detail)) return 'Your portal login expired. Sign in again before connecting to Oracle.';
    if (/AbortError|TimeoutError|timed? out|timeout/i.test(detail)) return 'Connection timed out. The server or database may be slow or unreachable. Check the network, VPN, firewall, and database availability.';
    if (/ORA-01017|invalid.*(?:username|password)|invalid credential/i.test(detail)) return 'Oracle rejected the username or password. Check your credentials or choose another database.';
    if (/ORA-28000|account.*locked/i.test(detail)) return 'The Oracle account is locked. Contact the database administrator or choose another database.';
    if (/ORA-28001|password.*expired/i.test(detail)) return 'The Oracle password has expired. Update it with your database administrator.';
    if (/ORA-12541|no listener|connection refused/i.test(detail)) return 'The database endpoint refused the connection or its listener is unavailable. Verify the host and port, or choose another database.';
    if (/ORA-12514|service.*not registered|service.*not known/i.test(detail)) return 'The Oracle service is unavailable or the service name is incorrect. Check the connect string or choose another database.';
    if (/ORA-12154|name.*resolution|name or service not known|getaddrinfo|nodename|host.*not found/i.test(detail)) return 'The database address could not be resolved. Check the hostname, connect string, DNS, and VPN.';
    if (/certificate|SSL|TLS|wallet/i.test(detail)) return 'A secure database connection could not be established. Check the certificate, wallet, and TLS configuration.';
    if (transport || /failed to fetch|networkerror|network request failed/i.test(detail)) return 'The workbench server could not be reached. It may be stopped, or the network, VPN, firewall, or browser certificate checks may be blocking access. Browser online status alone does not confirm internet access.';
    return 'Oracle could not connect. Check the database availability and connect string, or choose another database. Details: ' + detail;
  }
  let connectionProblem = null;
  function showConnectionProblem(message, transport = false) {
    if (!connectionProblem) {
      connectionProblem = document.createElement('div');
      connectionProblem.className = 'connection-diagnostic';
      connectionProblem.setAttribute('role', 'status');
      connectionProblem.setAttribute('aria-live', 'polite');
      const text = document.createElement('span');
      const options = document.createElement('button');
      options.type = 'button';
      options.className = 'tool-button';
      options.textContent = 'Connection options';
      options.addEventListener('click', async () => {
        await loadSavedCredentialDetails();
        if (!connectDialog.open) connectDialog.showModal();
      });
      connectionProblem.append(text, options);
      $('#ideWorkspace').before(connectionProblem);
    }
    connectionProblem.firstElementChild.textContent = connectionDiagnosis(message, navigator.onLine, transport);
    connectionProblem.dataset.transport = String(transport || !navigator.onLine);
    connectionProblem.hidden = false;
  }
  function clearConnectionProblem() { if (connectionProblem) connectionProblem.hidden = true; }
  window.addEventListener('offline', () => showConnectionProblem('Device offline', true));
  window.addEventListener('online', () => refreshStatus());

  async function readConnectionResponse(response) {
    if (response.redirected || response.status === 401) {
      throw new Error('Your portal session has expired. Sign in again, then reconnect to Oracle.');
    }
    if (!(response.headers.get('content-type') || '').includes('application/json')) {
      throw new Error('The connection service returned an unexpected response. Refresh the page and try again.');
    }
    return response.json();
  }

  async function connect(payload, { save = false } = {}) {
    const error = $('#connectError');
    const button = $('.dialog-submit', connectForm);
    error.textContent = 'Connecting…';
    button.disabled = true;
    try {
      const response = await fetch('/api/connect', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, saveCredentials: save })
      });
      const data = await readConnectionResponse(response);
      if (!response.ok) throw new Error(data.error || 'Could not connect.');
      manualOracleDisconnect = false;
      try { sessionStorage.removeItem(connectionChoiceKey); } catch {}
      navigatorCacheId = data.navigatorCacheId || '';
      clearConnectionProblem();
      setStatus(true, `Connected · ${data.service || data.user || payload.user}`);
      $('#connectionService').textContent = data.service || data.user || 'Oracle Database';
      $('#terminalService').textContent = 'Oracle session';
      connectDialog.close();
      connectForm.elements.password.value = '';
      showToast(save ? 'Connected. Credentials saved to your profile.' : 'Connected to Oracle.');
      return true;
    } catch (errorValue) {
      error.textContent = connectionDiagnosis(errorValue.message);
      showConnectionProblem(errorValue.message);
      if (!connectDialog.open) connectDialog.showModal();
      return false;
    } finally { button.disabled = false; }
  }

  function fillCredentials(data) {
    if (data.saved && data.user) {
      connectForm.dataset.savedOracleUser = data.user;
      connectForm.dataset.savedOracleConnectString = data.connectString || '';
    }
    if (data.user) connectForm.elements.user.value = data.user;
    if (data.connectString) connectForm.elements.connectString.value = data.connectString;
    $('#saveCredentials').checked = Boolean(data.saved);
    connectForm.elements.password.value = '';
    connectForm.elements.password.placeholder = data.saved ? 'Saved password stays securely on the server' : '';
    $('#savedCredentialsTitle').textContent = data.saved ? 'Use saved credentials' : 'Save credentials to my profile';
    $('#savedCredentialsHelp').textContent = data.saved
      ? 'Leave the password blank to connect with the encrypted password stored on the server.'
      : 'Automatically reconnect with these details after signing in.';
  }
  async function loadSavedCredentialDetails() {
    try {
      const response = await fetch('/api/connect-saved', { credentials: 'same-origin', cache: 'no-store' });
      const data = await readConnectionResponse(response);
      if (response.ok) {
        if (data.saved) fillCredentials(data);
        else {
          $('#saveCredentials').checked = false;
          $('#savedCredentialsTitle').textContent = 'Save credentials to my profile';
          $('#savedCredentialsHelp').textContent = 'Automatically reconnect with these details after signing in.';
          connectForm.elements.password.placeholder = '';
        }
      }
    } catch { /* The user can still enter credentials manually. */ }
  }
  async function connectWithSavedCredentials() {
    const error = $('#connectError');
    const button = $('.dialog-submit', connectForm);
    error.textContent = 'Connecting with saved credentials…';
    button.disabled = true;
    try {
      const response = await fetch('/api/connect-saved', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      const data = await readConnectionResponse(response);
      if (!response.ok || !data.connected) {
        if (data.saved) fillCredentials(data);
        throw new Error(data.error || 'No saved Oracle credentials are available.');
      }
      fillCredentials({ ...data, saved: true });
      manualOracleDisconnect = false;
      try { sessionStorage.removeItem(connectionChoiceKey); } catch {}
      navigatorCacheId = data.navigatorCacheId || '';
      clearConnectionProblem();
      setStatus(true, `Connected · ${data.service || data.user || 'Oracle'}`);
      $('#connectionService').textContent = data.service || data.user || 'Oracle Database';
      $('#terminalService').textContent = 'Oracle session';
      connectDialog.close();
      showToast('Connected using saved credentials.');
      return true;
    } catch (errorValue) {
      error.textContent = connectionDiagnosis(errorValue.message);
      showConnectionProblem(errorValue.message);
      if (!connectDialog.open) connectDialog.showModal();
      return false;
    } finally { button.disabled = false; }
  }
  let reconnectPromise = null;
  let lastReconnectAttempt = 0;
  async function reconnectSavedCredentials() {
    if (reconnectPromise) return reconnectPromise;
    if (manualOracleDisconnect) return false;
    lastReconnectAttempt = Date.now();
    reconnectPromise = (async () => {
      setStatus(false, 'Connecting to Oracle…');
      try {
        const response = await fetch('/api/connect-saved', { method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:'{}' });
        const data = await readConnectionResponse(response);
        if (response.ok && data.connected) {
          navigatorCacheId = data.navigatorCacheId || '';
      clearConnectionProblem();
      setStatus(true, `Connected · ${data.service || data.user || 'Oracle'}`);
          $('#connectionService').textContent = data.service || data.user || 'Oracle Database';
          fillCredentials({...data,saved:true});
          if (connectDialog.open) connectDialog.close();
          return true;
        }
        if (data.saved) {
          fillCredentials(data);
          setStatus(false, 'Saved Oracle connection failed');
          $('#connectError').textContent = connectionDiagnosis(data.error);
          showConnectionProblem(data.error);
          showToast(data.error || 'Saved Oracle connection failed. Check the credentials in your profile.');
          return false;
        }
        if (!response.ok) throw new Error(data.error || 'Could not check saved credentials.');
        if (legacyCredentials) {
          fillCredentials(legacyCredentials);
        }
        $('#connectError').textContent='';
        return false;
      } catch(error) {
        setStatus(false, 'Oracle connection unavailable');
        await loadSavedCredentialDetails();
        $('#connectError').textContent = connectionDiagnosis(error.message);
        showConnectionProblem(error.message);
        showToast(error.message || 'Could not reconnect to Oracle.');
        return false;
      }
    })();
    try { return await reconnectPromise; } finally { reconnectPromise=null; }
  }
  let statusRequest = null;
  async function refreshStatus() {
    if (statusRequest) return statusRequest;
    statusRequest = (async () => {
      try {
        const response=await fetch('/api/status',{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(10000)});
        if (!response.ok) { showConnectionProblem('Workbench status request failed (HTTP ' + response.status + ')', true); return null; }
        const data=await response.json();
        if (connectionProblem?.dataset.transport === 'true') clearConnectionProblem();
        if(data.connected){
          navigatorCacheId = data.navigatorCacheId || '';
      clearConnectionProblem();
      setStatus(true,`Connected · ${data.service||data.user||data.loginUser||'Oracle'}`);
          $('#connectionService').textContent=data.service||data.user||'Oracle Database';
          return true;
        }
        const sessionEnded=$('#connectionDot').classList.contains('connected');
        setStatus(false,'Not connected');
        if(sessionEnded&&!manualOracleDisconnect)showToast('Oracle session ended. Any uncommitted changes were rolled back.');
        return false;
      } catch (error) {
        // Keep the last known Oracle state: a failed web request does not prove it disconnected.
        showConnectionProblem(error.name + ': ' + error.message, true);
        return null;
      }
    })();
    try{return await statusRequest;}finally{statusRequest=null;}
  }
  function updateEditorCursorPosition() {
    const beforeCursor = editor.value.slice(0, editor.selectionStart);
    const line = beforeCursor.split('\n').length;
    const column = beforeCursor.length - beforeCursor.lastIndexOf('\n');
    $('#editorCursorPosition').textContent = `Ln ${line}, Col ${column}`;
  }
  function updateLineNumbers() {
    const lines = editor.value.split('\n').length;
    $('#editorLineCount').textContent = `${lines} ${lines === 1 ? 'line' : 'lines'}`;
    $('#lineNumbers').textContent = Array.from({ length: lines }, (_, index) => index + 1).join('\n');
  }
  function escapeHtml(value) { return value.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]); }
  function normalizePastedSql(text) { return window.CompilerWorkspace.clean(text); }
  function cleanEditorNow(input) {
    const text = normalizePastedSql(input.value);
    if (text === input.value) return;
    const start = normalizePastedSql(input.value.slice(0, input.selectionStart)).length;
    const end = normalizePastedSql(input.value.slice(0, input.selectionEnd)).length;
    window.CompilerWorkspace.edit(input, text);
    input.setSelectionRange(start, end);
  }
  function renderSyntaxHighlight(source) {
    const tokens = window.CompilerWorkspace.scan(source).tokens;
    let html = '', offset = 0;
    for (const token of tokens) {
      html += escapeHtml(source.slice(offset, token.start));
      const type = token.kind === 'comment' ? 'comment' : token.kind === 'literal' ? 'string' : token.kind === 'number' ? 'number' : token.kind === 'word' && window.CompilerEditorAssist.keywords.includes(token.text.toUpperCase()) ? 'keyword' : '';
      html += type ? `<span class="sql-${type}">${escapeHtml(token.text)}</span>` : escapeHtml(token.text);
      offset = token.end;
    }
    return html + escapeHtml(source.slice(offset));
  }
  function updateHighlight() {
    $('#syntaxHighlight').innerHTML = renderSyntaxHighlight(editor.value || ' ');
  }
  function updateTerminalInputHighlight() {
    const highlight = $('#terminalInputHighlight');
    if (highlight) highlight.innerHTML = renderSyntaxHighlight(terminalInput.value || ' ');
  }
  plsqlCleanCode.checked = localStorage.getItem('ora-editor-clean-code') !== 'false';
  plsqlAutoCommit.checked = localStorage.getItem('ora-editor-autocommit') !== 'OFF';
  plsqlStarterToggle.checked = localStorage.getItem('ora-show-sql-starters') === 'true';
  plsqlCleanCode.addEventListener('change', () => { localStorage.setItem('ora-editor-clean-code', String(plsqlCleanCode.checked)); if (plsqlCleanCode.checked) cleanEditorNow(editor); });
  plsqlAutoCommit.addEventListener('change', () => localStorage.setItem('ora-editor-autocommit', plsqlAutoCommit.checked ? 'ON' : 'OFF'));
  plsqlStarterToggle.addEventListener('change', () => {
    localStorage.setItem('ora-show-sql-starters', String(plsqlStarterToggle.checked));
    $('#sqlplusStarterPopupToggle').checked = plsqlStarterToggle.checked;
    plsqlStarterMenu.hidden = !plsqlStarterToggle.checked || Boolean(editor.value.trim());
  });
  editor.addEventListener('focus', () => { plsqlStarterMenu.hidden = !plsqlStarterToggle.checked || Boolean(editor.value.trim()); });
  editor.addEventListener('paste', (event) => {
    if (!event.clipboardData) return;
    const pastedText = event.clipboardData.getData('text');
    const internal = consumeInternalCopy(pastedText);
    if (!internal) {
      reportCompilerPaste('PL/SQL', pastedText);
      recordPaste('plsql', pastedText.length);
    } else markInputMethod('plsql', 'internal_copy');
    if (internal) inputProvenance.plsql.pending = 'internal';
    if (!plsqlCleanCode.checked) return;
    event.preventDefault();
    const normalized = normalizePastedSql(pastedText);
    editor.setRangeText(normalized, editor.selectionStart, editor.selectionEnd, 'end');
    inputProvenance.plsql.pending = internal ? 'internal' : 'pasted';
    editor.dispatchEvent(new Event('input', { bubbles: true }));
  });
  editor.addEventListener('input', (event) => { trackInputProvenance('plsql', editor, event); trackManualEdit('plsql', event); });
  plsqlStarterMenu.querySelectorAll('[data-plsql-starter]').forEach((button) => button.addEventListener('click', () => {
    editor.value = button.dataset.plsqlStarter;
    plsqlStarterMenu.hidden = true;
    editor.focus();
    editor.setSelectionRange(editor.value.length, editor.value.length);
    editor.dispatchEvent(new Event('input', { bubbles: true }));
  }));
  editor.addEventListener('input', () => { updateLineNumbers(); updateHighlight(); updateEditorCursorPosition(); scheduleEditorShareSync(); if (editor.value.trim()) plsqlStarterMenu.hidden = true; });
  ['click', 'keyup', 'select'].forEach((eventName) => editor.addEventListener(eventName, updateEditorCursorPosition));
  editor.addEventListener('scroll', () => { $('#syntaxHighlight').scrollTop = editor.scrollTop; $('#syntaxHighlight').scrollLeft = editor.scrollLeft; $('#lineNumbers').scrollTop = editor.scrollTop; });
  updateLineNumbers();
  updateHighlight();
  updateEditorCursorPosition();

  $('#formatSql').addEventListener('click', () => {
    const start = editor.selectionStart, end = editor.selectionEnd;
    // Tokenize the full source so a partial selection cannot alter literal data.
    if (start !== end) {
      const protectedTokens = window.CompilerWorkspace.scan(editor.value).tokens.filter(t => ['literal','identifier','comment'].includes(t.kind));
      if (protectedTokens.some(t => (start > t.start && start < t.end) || (end > t.start && end < t.end))) {
        showToast('Select complete SQL tokens before formatting.'); return;
      }
    }
    const from = start === end ? 0 : start, to = start === end ? editor.value.length : end;
    window.CompilerWorkspace.edit(editor, window.CompilerWorkspace.format(editor.value.slice(from, to)), from, to);
  });
  function applyEditorFontSize() {
    const font = `${editorFontSize}px`;
    editor.style.fontSize = font;
    $('#syntaxHighlight').style.fontSize = font;
    $('#lineNumbers').style.fontSize = font;
    $('#editorFontValue').textContent = String(editorFontSize);
  }
  function changeEditorFont(delta) {
    editorFontSize = Math.max(10, Math.min(30, editorFontSize + delta));
    applyEditorFontSize();
    localStorage.setItem('ora-plsql-font-size', String(editorFontSize));
  }
  $('#editorFontDown').addEventListener('click', () => changeEditorFont(-1));
  $('#editorFontUp').addEventListener('click', () => changeEditorFont(1));
  try {
    const savedEditorFontSize = Number(localStorage.getItem('ora-plsql-font-size'));
    if (Number.isFinite(savedEditorFontSize) && savedEditorFontSize >= 10 && savedEditorFontSize <= 30) editorFontSize = savedEditorFontSize;
  } catch { /* Use the default editor font size. */ }
  applyEditorFontSize();

  const convertCaseMenu = $('#convertCaseMenu');
  $('#convertCaseToggle').addEventListener('click', (event) => {
    event.stopPropagation();
    const opening = convertCaseMenu.hidden;
    convertCaseMenu.hidden = !opening;
    $('#convertCaseToggle').setAttribute('aria-expanded', String(opening));
  });
  convertCaseMenu.querySelectorAll('[data-case]').forEach((button) => button.addEventListener('click', () => {
    const start = editor.selectionStart;
    const end = editor.selectionEnd;
    const hasSelection = start !== end;
    const source = hasSelection ? editor.value.slice(start, end) : editor.value;
    const mode = button.dataset.case;
    const converted = window.CompilerWorkspace.convert(editor.value, mode, hasSelection ? start : 0, hasSelection ? end : editor.value.length);
    window.CompilerWorkspace.edit(editor, converted);
    editor.setSelectionRange(start, end);
    convertCaseMenu.hidden = true;
    $('#convertCaseToggle').setAttribute('aria-expanded', 'false');
    editor.dispatchEvent(new Event('input', { bubbles: true }));
    editor.focus();
  }));
  document.addEventListener('pointerdown', (event) => {
    if (!event.target.closest('.plsql-case-anchor')) {
      convertCaseMenu.hidden = true;
      $('#convertCaseToggle').setAttribute('aria-expanded', 'false');
    }
  });
  function runEditor(script) {
    const selected = window.CompilerWorkspace.target(editor.value, editor.selectionStart, editor.selectionEnd, script);
    const executionMode = script ? 'script' : editor.selectionStart !== editor.selectionEnd ? 'selection' : 'statement';
    if (!script && selected.sql.trim() && !executionBusy) {
      editor.focus({ preventScroll: true });
      editor.setSelectionRange(selected.start, selected.end);
      updateHighlight();
      updateEditorCursorPosition();
    }
    return execute(selected.sql, { script, executionMode });
  }
  $('#runStatement').addEventListener('click', () => runEditor(false));
  $('#runScript').addEventListener('click', async () => {
    const selectionStart = editor.selectionStart;
    const selectionEnd = editor.selectionEnd;
    const scrollTop = editor.scrollTop;
    const scrollLeft = editor.scrollLeft;
    await runEditor(true);
    editor.focus({ preventScroll: true });
    editor.setSelectionRange(selectionStart, selectionEnd);
    editor.scrollTop = scrollTop;
    editor.scrollLeft = scrollLeft;
    updateHighlight();
    updateEditorCursorPosition();
  });
  editor.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && event.ctrlKey) { event.preventDefault(); runEditor(event.shiftKey); }
    if (event.key === 'Tab') { event.preventDefault(); const start = editor.selectionStart; window.CompilerWorkspace.edit(editor, '  ', start, editor.selectionEnd, 'insertText'); }
  });

  $('#sqlFileInput').addEventListener('change', async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    window.CompilerWorkspace.edit(editor, await file.text());
    if (editor.value.trim()) markInputMethod('plsql', 'file');
  });
  $('#sqlplusFileInput').addEventListener('change', async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    terminalInput.value = await file.text();
    if (terminalInput.value.trim()) markInputMethod('sqlplus', 'file');
    terminalInput.dispatchEvent(new Event('input', { bubbles: true }));
    terminalInput.focus();
    event.target.value = '';
  });
  $('#saveSqlFile').addEventListener('click', () => {
    const blob = new Blob([editor.value], { type: 'text/sql;charset=utf-8' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'query.sql';
    link.click();
    URL.revokeObjectURL(link.href);
  });
  function filterExportEntries(entries, scope) {
    if (scope === 'last') return entries.slice(-1);
    if (scope === 'success') return entries.filter(entry => !entry.failed);
    if (scope === 'failed') return entries.filter(entry => entry.failed);
    return entries;
  }
  function scopedEditorExport(terminal, scope) {
    if (!terminal) {
      if (scope === 'all') return editor.value;
      const entries = history.filter(entry => entry.source === 'PL/SQL').slice().reverse()
        .map(entry => ({ text: entry.sql || '', failed: entry.status === 'error' }));
      return filterExportEntries(entries, scope).map(entry => entry.text).join('\n\n');
    }
    const entries = [...$('#terminalOutput').querySelectorAll('.terminal-entry')]
      .filter(node => node._outputSnapshot && !node._outputSnapshot.data?.pending && !node._outputSnapshot.data?.commentOnly)
      .map(node => ({ text: node.innerText, failed: Boolean(node._outputSnapshot.error || node._outputSnapshot.data?.executionErrors?.length) }));
    return filterExportEntries(entries, scope).map(entry => entry.text).join('\n\n');
  }
  const exportScopeMenu = document.createElement('div');
  exportScopeMenu.className = 'export-scope-menu';
  exportScopeMenu.setAttribute('popover', 'auto');
  exportScopeMenu.setAttribute('aria-label', 'Choose statements to export');
  let exportScopeTrigger = null;
  for (const [scope, label] of [['all', 'All'], ['success', 'Success'], ['failed', 'Failed'], ['last', 'Last']]) {
    const option = document.createElement('button');
    option.type = 'button';
    option.textContent = label;
    option.addEventListener('click', async () => {
      const trigger = exportScopeTrigger;
      exportScopeMenu.hidePopover();
      if (!trigger) return;
      const terminal = Boolean(trigger.dataset.terminalAction);
      const action = trigger.dataset.terminalAction || trigger.dataset.plsqlAction;
      const text = scopedEditorExport(terminal, scope);
      if (!text.trim()) { showToast(`No ${label.toLowerCase()} statements available.`); return; }
      try {
        if (action === 'download') {
          const url = URL.createObjectURL(new Blob([text], { type: terminal ? 'text/plain;charset=utf-8' : 'text/sql;charset=utf-8' }));
          const link = document.createElement('a');
          link.href = url;
          link.download = `${terminal ? 'sqlplus-session' : 'query'}-${scope}.${terminal ? 'txt' : 'sql'}`;
          link.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        } else if (action === 'share' && navigator.share) {
          await navigator.share({ title: terminal ? 'SQLCL session' : 'SQL WorkSheet statements', text });
        } else {
          await navigator.clipboard.writeText(text);
          showToast(`${label} statements copied${action === 'share' ? ' for sharing' : ''}.`);
        }
      } catch (error) { if (error.name !== 'AbortError') showToast(error.message || 'Export failed.'); }
    });
    exportScopeMenu.append(option);
  }
  document.body.append(exportScopeMenu);
  exportScopeMenu.addEventListener('toggle', () => {
    exportScopeTrigger?.setAttribute('aria-expanded', String(exportScopeMenu.matches(':popover-open')));
  });
  $$('[data-plsql-action], [data-terminal-action]').forEach(trigger => {
    const action = trigger.dataset.plsqlAction || trigger.dataset.terminalAction;
    if (!['copy', 'download', 'share'].includes(action)) return;
    trigger.setAttribute('aria-haspopup', 'true');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.addEventListener('click', event => {
      event.stopImmediatePropagation();
      const same = exportScopeTrigger === trigger && exportScopeMenu.matches(':popover-open');
      exportScopeMenu.hidePopover();
      if (same) return;
      exportScopeTrigger?.setAttribute('aria-expanded', 'false');
      exportScopeTrigger = trigger;
      const rect = trigger.getBoundingClientRect();
      exportScopeMenu.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - 148))}px`;
      exportScopeMenu.style.top = `${Math.max(8, Math.min(rect.bottom + 6, innerHeight - 184))}px`;
      exportScopeMenu.showPopover();
      trigger.setAttribute('aria-expanded', 'true');
      exportScopeMenu.firstElementChild.focus();
    }, true);
  });

  $$('[data-plsql-action]').forEach((button) => button.addEventListener('click', async () => {
    const action = button.dataset.plsqlAction;
    const text = editor.value;
    if (action === 'copy') {
      await navigator.clipboard.writeText(text);
      showToast('SQL WorkSheet editor copied.');
    }
    if (action === 'download') {
      const link = document.createElement('a');
      link.href = URL.createObjectURL(new Blob([text], { type: 'text/sql;charset=utf-8' }));
      link.download = 'query.sql'; link.click(); URL.revokeObjectURL(link.href);
    }
    if (action === 'open') $('#sqlFileInput').click();
    if (action === 'share') {
      if (!text.trim()) { showToast('There is no SQL WorkSheet code to share.'); return; }
      if (navigator.share) await navigator.share({ title: 'SQL WorkSheet editor', text });
      else { await navigator.clipboard.writeText(text); showToast('SQL WorkSheet code copied for sharing.'); }
    }
  }));

  function setResultTab(tab, refreshHistory = true) {
    $$('.result-tabs [data-result]').forEach((button) => button.classList.toggle('active', button.dataset.result === tab));
    if (tab === 'history') {
      const body = $('#resultBody');
      body.replaceChildren();
      const list = document.createElement('div');
      list.className = 'history-list compact-history';
      history.forEach((entry) => list.append(makeHistoryRow(entry, true)));
      body.append(list);
      if (refreshHistory) loadOwnHistory();
    } else if (tab === 'output') {
      const body = $('#resultBody');
      body.replaceChildren();
      const output = document.createElement('pre');
      output.className = 'result-output';
      output.textContent = outputStyle === 'sqlplus' && resultData ? window.CompilerWorkspace.outputText(resultData.sourceSql || '', resultData) : resultData?.error || (resultData ? [...(resultData.outputLines || []), resultData.message || 'Statement completed.'].filter(Boolean).join('\n') : 'Run a statement to see output.');
      body.append(output);
    } else renderResult();
  }
  $$('.result-tabs [data-result]').forEach((button) => button.addEventListener('click', () => setResultTab(button.dataset.result)));

  function resultTabExport() {
    const tab = $('.result-tabs [data-result].active')?.dataset.result || 'data';
    const body = $('#resultBody');
    const table = body.querySelector('.result-table');
    if (tab === 'data' && table) {
      const rows = [...table.rows].map((row) => [...row.cells].map((cell) => cell.textContent));
      const csv = rows.map((row) => row.map((value) => `"${String(value ?? '').replaceAll('"', '""')}"`).join(',')).join('\r\n');
      const text = rows.map((row) => row.join('\t')).join('\n');
      return { tab, text, downloadText: csv, filename: 'query-results.csv', type: 'text/csv;charset=utf-8' };
    }
    if (tab === 'history') {
      const rows = [['Time', 'Source', 'Status', 'SQL'], ...history.map((entry) => [new Date(entry.executedAt).toLocaleString(), compilerSourceLabel(entry.source), entry.status === 'error' ? 'Failed' : 'Success', entry.sql || ''])];
      const csv = rows.map((row) => row.map((value) => `"${String(value ?? '').replaceAll('"', '""')}"`).join(',')).join('\r\n');
      const text = rows.map((row) => row.join('\t')).join('\n');
      return { tab, text, downloadText: csv, filename: 'sql-history.csv', type: 'text/csv;charset=utf-8' };
    }
    const text = [...body.querySelectorAll('.result-output')].map((node) => node.innerText).join('\n\n').trim() || body.innerText.trim();
    return { tab, text, downloadText: text, filename: 'query-output.txt', type: 'text/plain;charset=utf-8' };
  }
  $$('[data-result-action]').forEach((button) => button.addEventListener('click', async () => {
    const action = button.dataset.resultAction;
    const exported = resultTabExport();
    if (!exported.text.trim()) { showToast('There is no content in the selected tab.'); return; }
    try {
      if (action === 'copy') {
        await navigator.clipboard.writeText(exported.text);
        showToast(`${exported.tab === 'history' ? 'SQL history' : exported.tab === 'output' ? 'Output' : 'Data'} copied.`);
      } else if (action === 'download') {
        const link = document.createElement('a');
        link.href = URL.createObjectURL(new Blob([exported.downloadText], { type: exported.type }));
        link.download = exported.filename; link.click();
        setTimeout(() => URL.revokeObjectURL(link.href), 1000);
      } else if (action === 'share') {
        const title = exported.tab === 'history' ? 'SQL History' : exported.tab === 'output' ? 'SQL Output' : 'Query Data';
        if (navigator.share) await navigator.share({ title, text: exported.text });
        else { await navigator.clipboard.writeText(exported.text); showToast(`${title} copied for sharing.`); }
      }
    } catch (error) {
      if (error.name !== 'AbortError') showToast(error.message || 'Could not complete this action.');
    }
  }));

  function makeHistoryRow(entry, compact = false) {
    const row = document.createElement('article');
    row.className = `history-entry${compact ? ' compact' : ''}`;
    const meta = document.createElement('div');
    meta.className = 'history-meta';
    const source = document.createElement('span');
    source.textContent = compilerSourceLabel(entry.source);
    const status = document.createElement('span');
    status.textContent = entry.status === 'error' ? 'Failed' : 'Success';
    status.className = entry.status === 'error' ? 'history-error' : 'history-success';
    const date = document.createElement('time');
    date.textContent = new Date(entry.executedAt).toLocaleString();
    meta.append(source, status, date);
    const sql = document.createElement('pre');
    sql.textContent = entry.sql;
    const actions = document.createElement('div');
    actions.className = 'history-actions';
    const run = document.createElement('button');
    run.type = 'button'; run.className = 'tool-button'; run.textContent = 'Run again';
    run.addEventListener('click', () => { editor.value = entry.sql; markInputMethod('plsql', 'history_recall'); updateLineNumbers(); updateHighlight(); setView('compiler'); execute(entry.sql); });
    const copy = document.createElement('button');
    copy.type = 'button'; copy.className = 'tool-button'; copy.textContent = 'Copy';
    copy.addEventListener('click', async () => { if (entry.source === 'SQL*Plus') rememberInternalCopy(entry.sql); await navigator.clipboard.writeText(entry.sql); showToast('SQL copied.'); });
    actions.append(run, copy);
    row.append(meta, sql, actions);
    return row;
  }
  async function loadCompilerHistory() {
    const body = $('#historyTableBody');
    if (!body) return;
    const studentStatus = $('#historyStudentStatus')?.value || 'all';
    const selectedStudent = $('#historyStudentFilter')?.value || '';
    const params = historyRequestParams();
    params.set('studentStatus', studentStatus);
    params.set('page', String(historyPage));
    params.set('pageSize', String(historyPageSize));
    if (selectedStudent) params.set('student', selectedStudent);
    body.innerHTML = '<tr><td class="history-table-empty" colspan="10">Loading SQL history…</td></tr>';
    try {
      const response = await fetch(`/api/compiler/history?${params}`, { credentials: 'same-origin', cache: 'no-store' });
      const data = await readHistoryResponse(response);
      if (!response.ok) throw new Error(data.error || 'Could not load SQL history.');
      historyStudents = data.students || [];
      const clearClass = $('#historyClearClass');
      if (clearClass) {
        const selectedClass = clearClass.value;
        clearClass.replaceChildren(new Option('Choose a class', ''), ...(data.classes || []).map(item => new Option(item.name, item.id)));
        clearClass.value = selectedClass;
      }
      historyStudentScope = data.studentScope || 'assigned';
      const studentFilter = $('#historyStudentFilter');
      if (studentFilter) {
        const validSelected = historyStudents.some((student) => String(student.id) === selectedStudent);
        if (selectedStudent && !validSelected) studentFilter.value = '';
        renderHistoryStudentOptions(validSelected ? selectedStudent : '');
      }
      historyRecords = data.records || [];
      historyPagination = data.pagination || { total: historyRecords.length, pages: 1 };
      historyPage = historyPagination.page || historyPage;
      renderHistory();
    } catch (error) {
      body.innerHTML = '';
      const row = document.createElement('tr'); const cell = document.createElement('td');
      cell.colSpan = isStudent ? 9 : 10; cell.className = 'history-table-empty'; cell.textContent = error.message || 'Could not load SQL history.';
      row.append(cell); body.append(row);
    }
  }
  function historyRequestParams() {
    const params = new URLSearchParams();
    const values = {
      source: $('#historySourceFilter')?.value || 'all',
      status: $('#historyStatusFilter')?.value || 'all',
      from: $('#historyDateFrom')?.value || '', to: $('#historyDateTo')?.value || '',
      search: ($('#historySearch')?.value || '').trim(), sort: historySort.key, direction: historySort.direction,
    };
    Object.entries(values).forEach(([key, value]) => { if (value && value !== 'all') params.set(key, value); });
    return params;
  }
  function renderHistoryStudentOptions(selectedId = $('#historyStudentFilter')?.value || '') {
    const studentFilter = $('#historyStudentFilter');
    if (!studentFilter) return;
    const search = ($('#historyStudentSearch')?.value || '').trim().toLocaleLowerCase();
    const matches = historyStudents.filter((student) => `${student.name} ${student.number || ''}`.toLocaleLowerCase().includes(search));
    const selectedStudent = historyStudents.find((student) => String(student.id) === selectedId);
    if (selectedStudent && !matches.some((student) => student.id === selectedStudent.id)) matches.unshift(selectedStudent);
    studentFilter.replaceChildren(new Option(historyStudentScope === 'all' ? 'All students' : 'All assigned students', ''));
    matches.forEach((student) => studentFilter.add(new Option(`${student.name}${student.number ? ` · ${student.number}` : ''}${student.isActive ? '' : ' · Inactive'}`, String(student.id))));
    studentFilter.value = selectedStudent ? selectedId : '';
  }
  async function changeHistoryRecord(entry, patch) {
    const response = await fetch(`/api/compiler/history/${entry.id}`, {
      method: patch ? 'PATCH' : 'DELETE', credentials: 'same-origin',
      headers: patch ? { 'Content-Type': 'application/json' } : {},
      body: patch ? JSON.stringify(patch) : undefined,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not update SQL history.');
    await loadCompilerHistory();
  }
  function filteredHistoryRecords() { return historyRecords; }
  function appendColoredHistorySql(container, sql, sourceMap, inputMethod = '') {
    const text = String(sql || '');
    const runs = Array.isArray(sourceMap) ? sourceMap : [];
    if (!runs.length) {
      const span = document.createElement('span');
      span.textContent = text;
      if (inputMethod === 'typed') span.className = 'input-typed';
      else if (inputMethod === 'pasted') span.className = 'input-pasted';
      else if (inputMethod === 'mixed') span.className = 'input-mixed';
      container.append(span);
      return;
    }
    let cursor = 0;
    for (const run of runs) {
      if (!Array.isArray(run) || run.length !== 3) continue;
      const start = Math.max(cursor, Math.min(text.length, Number(run[0]) || 0));
      const end = Math.max(start, Math.min(text.length, Number(run[1]) || 0));
      const source = run[2] === 'typed' || run[2] === 'pasted' ? run[2] : '';
      if (start > cursor) container.append(document.createTextNode(text.slice(cursor, start)));
      if (end > start) {
        const span = document.createElement('span');
        span.className = source ? `input-${source}` : '';
        span.textContent = text.slice(start, end);
        container.append(span);
      }
      cursor = end;
    }
    if (cursor < text.length) container.append(document.createTextNode(text.slice(cursor)));
  }
  function renderHistory() {
    const body = $('#historyTableBody');
    if (!body) return;
    const filtered = historyRecords;
    $$('[data-history-sort]').forEach((header) => {
      header.setAttribute('aria-sort', header.dataset.historySort === historySort.key
        ? (historySort.direction === 'asc' ? 'ascending' : 'descending') : 'none');
    });
    const pageCount = Math.max(1, historyPagination.pages || 1);
    const total = Number(historyPagination.total) || 0;
    const start = total ? (historyPage - 1) * historyPageSize : 0;
    const pageEntries = filtered;
    body.replaceChildren();
    if (!pageEntries.length) {
      const row = document.createElement('tr'); const cell = document.createElement('td');
      cell.colSpan = isStudent ? 8 : 10; cell.className = 'history-table-empty';
      cell.textContent = total ? 'No executions match these filters.' : 'No SQL history yet.';
      row.append(cell); body.append(row);
    }
    pageEntries.forEach((entry) => {
      const row = document.createElement('tr');
      const timeCell = document.createElement('td');
      const time = new Date(entry.executedAt); const date = document.createElement('time');
      if (!Number.isNaN(time.getTime())) {
        date.dateTime = time.toISOString();
        const dateText = document.createTextNode(new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' }).format(time));
        const clockText = document.createElement('small');
        clockText.textContent = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(time);
        date.append(dateText, clockText);
      } else date.textContent = 'Unknown';
      timeCell.append(date);
      const studentCell = document.createElement('td');
      const studentName = document.createElement('span'); studentName.className = 'history-student-name';
      studentName.textContent = entry.studentName || 'You'; studentCell.append(studentName);
      if (entry.studentNumber) { const number = document.createElement('small'); number.textContent = entry.studentNumber; studentCell.append(number); }
      const sourceCell = document.createElement('td'); const badge = document.createElement('span');
      badge.className = `history-source-badge ${entry.source === 'SQL*Plus' ? 'sqlplus' : 'compiler'}`;
      badge.textContent = compilerSourceLabel(entry.source); sourceCell.append(badge);
      const queryCell = document.createElement('td'); queryCell.className = 'history-query-preview';
      const queryText = document.createElement('span');
      queryText.className = 'history-query-text';
      const inputMethod = entry.inputMethod || (Number(entry.pastedChars) > 0 && Number(entry.typedChars) > 0 ? 'mixed' : Number(entry.pastedChars) > 0 ? 'pasted' : Number(entry.typedChars) > 0 ? 'typed' : '');
      appendColoredHistorySql(queryText, entry.sql || '', entry.inputSourceMap, inputMethod);
      queryText.title = entry.sql || '';
      if (inputMethod === 'mixed' && !entry.inputSourceMap?.length) {
        queryText.title = `${entry.sql || ''}\nMixed typed and pasted input; exact character positions were not recorded.`;
        const inputLegend = document.createElement('small'); inputLegend.className = 'history-query-input-legend';
        inputLegend.innerHTML = '<span class="input-typed">Typed</span><span class="input-pasted">Pasted</span>';
        queryCell.append(inputLegend);
      }
      queryCell.append(queryText);
      if (inputMethod === 'file' || inputMethod === 'history_recall') queryCell.title = `${inputMethod === 'file' ? 'Loaded from file' : 'Recalled from history'} · ${entry.sql || ''}`;
      const resultCell = document.createElement('td'); const resultBadge = document.createElement('span');
      const failed = entry.status === 'error'; resultBadge.className = `history-status-badge ${failed ? 'failed' : 'success'}`;
      resultBadge.textContent = failed ? 'Failed' : 'Success';
      if (failed && entry.message) resultBadge.title = entry.message;
      resultCell.append(resultBadge);
      const accessCell = document.createElement('td'); const accessBadge = document.createElement('span');
      accessBadge.className = `history-access-badge ${entry.isActive ? 'active' : 'inactive'}`;
      accessBadge.textContent = entry.isActive ? 'Active' : 'Inactive'; accessCell.append(accessBadge);
      const durationCell = document.createElement('td'); durationCell.textContent = `${Number(entry.durationMs) || 0} ms`;
      const rowsCell = document.createElement('td'); rowsCell.textContent = String(Number(entry.rows) || 0);
      const editSourceCell = document.createElement('td');
      if ([entry.typedChars, entry.pastedChars, entry.editsAfterPaste].some((value) => value != null)) {
        editSourceCell.textContent = `T ${Number(entry.typedChars) || 0} · P ${Number(entry.pastedChars) || 0} · E ${Number(entry.editsAfterPaste) || 0}`;
        editSourceCell.title = `Manually typed characters: ${Number(entry.typedChars) || 0}; pasted characters: ${Number(entry.pastedChars) || 0}; edits after paste or history recall: ${Number(entry.editsAfterPaste) || 0}; paste actions: ${Number(entry.pasteCount) || 0}`;
      } else {
        editSourceCell.textContent = 'Not tracked';
        editSourceCell.title = 'Editing source counts were not collected for this execution.';
      }
      const actionsCell = document.createElement('td'); const actions = document.createElement('div'); actions.className = 'history-table-actions';
      const makeAction = (label, icon, handler) => {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'history-icon-action'; button.title = label; button.setAttribute('aria-label', label); button.textContent = icon;
        button.addEventListener('click', async () => { try { await handler(); } catch (error) { showToast(error.message || 'History action failed.'); } });
        actions.append(button);
      };
      makeAction('Run query', '↗', async () => {
        const sqlplus = entry.source === 'SQL*Plus';
        markInputMethod(sqlplus ? 'sqlplus' : 'plsql', 'history_recall');
        if (!sqlplus) { editor.value = entry.sql; updateLineNumbers(); updateHighlight(); updateEditorCursorPosition(); }
        setView(sqlplus ? 'sqlplus' : 'compiler'); await execute(entry.sql, { sqlplus });
      });
      makeAction('Copy query', '⧉', async () => { if (entry.source === 'SQL*Plus') rememberInternalCopy(entry.sql || ''); await navigator.clipboard.writeText(entry.sql || ''); showToast('SQL copied.'); });
      if ($('#historyStudentFilter')) {
        makeAction(entry.isActive ? 'Deactivate for student' : 'Activate for student', entry.isActive ? '◉' : '○', () => changeHistoryRecord(entry, { isActive: !entry.isActive }));
        makeAction('Delete query', '×', () => changeHistoryRecord(entry, null));
      }
      actionsCell.append(actions);
      row.append(timeCell, studentCell, sourceCell, queryCell, resultCell, ...(!isStudent ? [accessCell] : []), durationCell, rowsCell, ...(!isStudent ? [editSourceCell] : []), actionsCell);
      body.append(row);
    });
    const shownStart = total ? start + 1 : 0;
    $('#historySummary').textContent = `Showing ${shownStart}–${Math.min(start + pageEntries.length, total)} of ${total} executions`;
    $('#historyPageLabel').textContent = `${historyPage} / ${pageCount}`;
    $('#historyPrev').disabled = historyPage <= 1; $('#historyNext').disabled = historyPage >= pageCount;
    const clearButton = $('#clearHistory');
    if (clearButton) clearButton.disabled = false;
  }
  let historySearchTimer;
  ['historySourceFilter', 'historyStatusFilter', 'historyDateFrom', 'historyDateTo', 'historySearch'].forEach((id) => {
    const control = $(`#${id}`);
    control?.addEventListener(control.tagName === 'INPUT' && control.type === 'search' ? 'input' : 'change', () => {
      historyPage = 1;
      if (control.id === 'historySearch') { clearTimeout(historySearchTimer); historySearchTimer = setTimeout(loadCompilerHistory, 250); }
      else loadCompilerHistory();
    });
  });
  $$('[data-history-sort]').forEach((header) => {
    const sortByHeader = () => {
      const key = header.dataset.historySort;
      historySort = { key, direction: historySort.key === key && historySort.direction === 'asc' ? 'desc' : 'asc' };
      historyPage = 1;
      loadCompilerHistory();
    };
    header.addEventListener('click', sortByHeader);
    header.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); sortByHeader(); }
    });
  });
  $('#historyStudentStatus')?.addEventListener('change', () => { historyPage = 1; $('#historyStudentFilter').value = ''; loadCompilerHistory(); });
  $('#historyStudentFilter')?.addEventListener('change', () => { historyPage = 1; loadCompilerHistory(); });
  $('#historyStudentSearch')?.addEventListener('input', () => renderHistoryStudentOptions());
  $$('[data-history-action]').forEach((button) => button.addEventListener('click', async () => {
    const action = button.dataset.historyAction;
    let records;
    try { records = await fetchAllCompilerHistory(); }
    catch (error) { showToast(error.message || 'Could not load SQL history for export.'); return; }
    if (!records.length) { showToast('There is no SQL history matching these filters.'); return; }
    const headers = ['Time', 'Student', 'Source', 'Query', 'Result', 'Access', 'Duration ms', 'Rows', 'Typed characters', 'Pasted characters', 'Paste actions', 'Edits after paste'];
    const rows = [headers, ...records.map((entry) => [entry.executedAt, entry.studentName || 'You', compilerSourceLabel(entry.source), entry.sql || '', entry.status === 'error' ? 'Failed' : 'Success', entry.isActive ? 'Active' : 'Inactive', entry.durationMs ?? 0, entry.rows ?? 0, entry.typedChars ?? 'Not tracked', entry.pastedChars ?? 'Not tracked', entry.pasteCount ?? 'Not tracked', entry.editsAfterPaste ?? 'Not tracked'])];
    if (isStudent) { rows.forEach(row => { row.splice(8, 4); row.splice(5, 1); }); }
    const csv = rows.map((row) => row.map((value) => `"${String(value ?? '').replaceAll('"', '""')}"`).join(',')).join('\r\n');
    const text = rows.map((row) => row.join('\t')).join('\n');
    try {
      if (action === 'copy') { await navigator.clipboard.writeText(text); showToast('Filtered SQL history copied.'); }
      else if (action === 'download') {
        const link = document.createElement('a');
        link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
        link.download = 'sql-history.csv'; link.click();
        setTimeout(() => URL.revokeObjectURL(link.href), 1000);
      } else if (action === 'share') {
        if (navigator.share) await navigator.share({ title: 'SQL History', text });
        else { await navigator.clipboard.writeText(text); showToast('Filtered SQL history copied for sharing.'); }
      }
    } catch (error) {
      if (error.name !== 'AbortError') showToast(error.message || 'Could not complete this action.');
    }
  }));
  async function fetchAllCompilerHistory() {
    const base = historyRequestParams();
    base.set('studentStatus', $('#historyStudentStatus')?.value || 'all');
    const selectedStudent = $('#historyStudentFilter')?.value || '';
    if (selectedStudent) base.set('student', selectedStudent);
    base.set('pageSize', '1000');
    const first = await fetch(`/api/compiler/history?${new URLSearchParams(base)}`, { credentials: 'same-origin', cache: 'no-store' });
    const firstData = await readHistoryResponse(first);
    if (!first.ok) throw new Error(firstData.error || 'Could not export SQL history.');
    const rows = [...(firstData.records || [])];
    for (let p = 2; p <= (firstData.pagination?.pages || 1); p += 1) {
      const params = new URLSearchParams(base); params.set('page', String(p));
      const response = await fetch(`/api/compiler/history?${params}`, { credentials: 'same-origin', cache: 'no-store' });
      const data = await readHistoryResponse(response);
      if (!response.ok) throw new Error(data.error || 'Could not export SQL history.');
      rows.push(...(data.records || []));
    }
    return rows;
  }
  $('#historyPrev')?.addEventListener('click', () => { historyPage = Math.max(1, historyPage - 1); loadCompilerHistory(); });
  $('#historyNext')?.addEventListener('click', () => { historyPage += 1; loadCompilerHistory(); });
  $('#historyClearScope')?.addEventListener('change', event => {
    $('#historyClearClass').hidden = event.target.value !== 'class';
  });
  $('#clearHistory')?.addEventListener('click', async () => {
    const studentId = $('#historyStudentFilter')?.value;
    const scope = $('#historyClearScope').value;
    const classId = $('#historyClearClass').value;
    if (scope === 'student' && !studentId) return showToast('Choose a student from the Student filter.');
    if (scope === 'class' && !classId) return showToast('Choose a class before clearing history.');
    const target = scope === 'student' ? $('#historyStudentFilter').selectedOptions[0].textContent : scope === 'class' ? $('#historyClearClass').selectedOptions[0].textContent : 'all students in your activity scope';
    if (!window.confirm(`Permanently clear all SQLCL and SQL WorkSheet history for ${target}? This includes history outside the current filters.`)) return;
    try {
      const response = await fetch('/api/compiler/history/clear', { method: 'DELETE', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope, studentId, classId }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Could not clear SQL history.');
      if (!studentId) { history = []; try { localStorage.removeItem(historyKey); } catch { /* Ignore unavailable storage. */ } }
      showToast('SQL history cleared.');
      await loadCompilerHistory();
    } catch (error) { showToast(error.message || 'Could not clear SQL history.'); }
  });

  async function editorShareRequest(url, options = {}) {
    const response = await fetch(url, { credentials: 'same-origin', ...options, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) } });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not access editor sharing.');
    return data;
  }
  function selectedEditorStudent() { return $('#historyStudentFilter')?.value || ''; }
  function teacherEditorCanEdit(data) {
    if (!data) return false;
    if (data.mode === 'always') return true;
    const fresh = Date.now() - Date.parse(data.updatedAt) <= 120000;
    return fresh && (data.mode === 'session' || (data.mode === 'statement' && data.statementSnapshotReady));
  }
  function ensureTeacherPermissionRequestButton() {
    const actions = $('.teacher-editor-actions');
    if (!actions || $('#requestTeacherEditorPermission')) return $('#requestTeacherEditorPermission');
    const button = document.createElement('button');
    button.type = 'button';
    button.id = 'requestTeacherEditorPermission';
    button.className = 'tool-button';
    button.textContent = 'Request edit permission';
    button.hidden = true;
    button.addEventListener('click', async () => {
      const studentId = selectedEditorStudent();
      if (!studentId || !sharedEditor || teacherEditorCanEdit(sharedEditor)) return;
      button.disabled = true;
      try {
        sharedEditor = await editorShareRequest(`/api/compiler/editor-share/${studentId}`, { method: 'POST', body: JSON.stringify({ action: 'request_edit_permission' }) });
        $('#teacherEditorStatus').textContent = 'Permission request sent. The student will see a popup in the compiler.';
        button.textContent = 'Permission request sent';
      } catch (error) {
        $('#teacherEditorStatus').textContent = error.message;
        button.disabled = false;
      }
    });
    $('#saveTeacherEditor')?.before(button);
    return button;
  }
  async function loadTeacherEditor() {
    const studentId = selectedEditorStudent();
    if (!isTeacher) return;
    const panel = $('#teacherEditorPanel');
    if (!panel) return;
    panel.hidden = false;
    if (!studentId) {
      sharedEditor = null;
      $('#teacherStudentEditor').value = '';
      $('#teacherStudentEditor').readOnly = true;
      $('#saveTeacherEditor').disabled = true;
      const requestButton = ensureTeacherPermissionRequestButton(); if (requestButton) requestButton.hidden = true;
      $('#teacherEditorTitle').textContent = 'Student editor';
      $('#teacherEditorAccess').textContent = 'Choose one student from the filter above.';
      $('#teacherEditorRevision').textContent = '';
      $('#teacherEditorStatus').textContent = 'Select a student to check their sharing permission.';
      return;
    }
    $('#teacherStudentEditor').value = '';
    $('#teacherStudentEditor').readOnly = true;
    $('#saveTeacherEditor').disabled = true;
    $('#teacherEditorStatus').textContent = 'Checking student sharing permission…';
    try {
      sharedEditor = await editorShareRequest(`/api/compiler/editor-share/${studentId}`);
      $('#teacherEditorTitle').textContent = `${sharedEditor.studentName || 'Student'} · ${teacherEditorKind === 'plsql' ? 'SQL WorkSheet Editor' : 'SQLCL Editor'}`;
      const canEdit = teacherEditorCanEdit(sharedEditor);
      $('#teacherEditorAccess').textContent = canEdit ? `Access: ${sharedEditor.mode === 'statement' ? 'one statement' : sharedEditor.mode === 'session' ? 'this session' : 'always allow'}` : 'View only · student permission is required to edit';
      $('#teacherEditorRevision').textContent = `Revision ${sharedEditor.revision}`;
      $('#teacherStudentEditor').value = teacherEditorKind === 'plsql' ? sharedEditor.editorText : sharedEditor.sqlplusText;
      $('#teacherStudentEditor').readOnly = !canEdit;
      $('#saveTeacherEditor').disabled = !canEdit;
      const requestButton = ensureTeacherPermissionRequestButton();
      requestButton.hidden = canEdit;
      requestButton.disabled = Boolean(sharedEditor.editPermissionRequest);
      requestButton.textContent = sharedEditor.editPermissionRequest ? 'Permission request sent' : 'Request edit permission';
      $('#teacherEditorStatus').textContent = canEdit ? 'Changes are saved directly to the selected student editor.' : 'Editor contents are visible. Request permission to edit; the student will receive a popup.';
    } catch (error) {
      sharedEditor = null;
      $('#teacherEditorTitle').textContent = 'Student editor';
      $('#teacherEditorAccess').textContent = 'Choose a student who has granted editor access.';
      $('#teacherEditorRevision').textContent = '';
      $('#teacherEditorStatus').textContent = error.message;
    }
  }
  if (isTeacher) {
    $$('[data-teacher-history-view]').forEach((button) => button.addEventListener('click', () => {
      teacherEditorKind = button.dataset.teacherHistoryView;
      $$('[data-teacher-history-view]').forEach((item) => item.classList.toggle('active', item === button));
      const isHistory = teacherEditorKind === 'history';
      $$('.history-table-wrap, .history-pagination').forEach((element) => { element.hidden = !isHistory; });
      $('#clearHistory').hidden = !isHistory;
      $('#teacherEditorPanel').hidden = isHistory;
      if (!isHistory) loadTeacherEditor();
    }));
    $('#historyStudentFilter')?.addEventListener('change', () => { if (teacherEditorKind !== 'history') loadTeacherEditor(); });
    $('#historyStudentStatus')?.addEventListener('change', () => { if (teacherEditorKind !== 'history') setTimeout(loadTeacherEditor, 0); });
    $('#saveTeacherEditor')?.addEventListener('click', async () => {
      if (!sharedEditor || !selectedEditorStudent()) return;
      const text = $('#teacherStudentEditor').value;
      const body = { editorText: teacherEditorKind === 'plsql' ? text : sharedEditor.editorText, sqlplusText: teacherEditorKind === 'sqlplus' ? text : sharedEditor.sqlplusText, revision: sharedEditor.revision };
      try {
        sharedEditor = await editorShareRequest(`/api/compiler/editor-share/${selectedEditorStudent()}`, { method: 'POST', body: JSON.stringify(body) });
        $('#teacherEditorRevision').textContent = `Revision ${sharedEditor.revision}`;
        $('#teacherEditorAccess').textContent = sharedEditor.mode === 'private' ? 'One-statement access has ended.' : `Access: ${sharedEditor.mode === 'session' ? 'this session' : 'always allow'}`;
        $('#teacherEditorStatus').textContent = 'Student editor updated.';
        showToast('Student editor updated.');
      } catch (error) { $('#teacherEditorStatus').textContent = error.message; showToast(error.message); }
    });
  }
  async function loadStudentEditorSharing() {
    if (!isStudent || !$('#teacherEditorAccess')) return;
    const privateOption = $('#teacherEditorAccess option[value="private"]');
    if (privateOption) privateOption.textContent = 'View only';
    const explanation = $('#teacherEditorAccess').closest('.settings-card')?.querySelector('small');
    if (explanation) explanation.textContent = 'Teachers can always view your current SQL WorkSheet and SQLCL code. Choose whether your assigned teacher may edit it. View only blocks editing; this session expires after two minutes without activity.';
    try {
      const data = await editorShareRequest('/api/compiler/editor-share');
      $('#teacherEditorAccess').value = data.mode;
      $('#teacherEditorShareStatus').textContent = data.mode === 'private' ? 'View only · teacher cannot edit' : data.mode === 'statement' ? (data.statementSnapshotReady ? 'Statement shared' : 'Waiting for next statement') : data.mode === 'session' ? 'Sharing this session' : 'Always sharing';
      sharedEditor = data;
      syncStudentEditorSharing();
    } catch (error) { $('#teacherEditorShareStatus').textContent = error.message; }
  }
  async function setStudentShareMode(mode) {
    const body = { mode, sessionToken: shareSessionToken, editorText: editor.value, sqlplusText: terminalInput.value, revision: sharedEditor?.revision };
    const data = await editorShareRequest('/api/compiler/editor-share', { method: 'POST', body: JSON.stringify(body) });
    sharedEditor = data;
    editorShareConflict = false;
    lastSharedEditorText = editor.value;
    lastSharedSqlplusText = terminalInput.value;
    $('#teacherEditorShareStatus').textContent = mode === 'private' ? 'View only · teacher cannot edit' : mode === 'statement' ? 'Waiting for next statement' : mode === 'session' ? 'Sharing this session' : 'Always sharing';
    syncStudentEditorSharing();
  }
  async function syncStudentEditorSharing() {
    if (!isStudent || !$('#teacherEditorAccess') || editorShareConflict) return;
    try {
      const data = await editorShareRequest('/api/compiler/editor-share', { method: 'POST', body: JSON.stringify({ action: 'sync_view_snapshot', editorText: editor.value, sqlplusText: terminalInput.value, revision: sharedEditor?.revision }) });
      sharedEditor = data;
      lastSharedEditorText = editor.value;
      lastSharedSqlplusText = terminalInput.value;
    } catch { /* Sharing will retry on the next edit or heartbeat. */ }
  }
  function dataMode() { return $('#teacherEditorAccess')?.value || 'private'; }
  function scheduleEditorShareSync() {
    if (!isStudent || !$('#teacherEditorAccess')) return;
    clearTimeout(shareSyncTimer);
    shareSyncTimer = setTimeout(syncStudentEditorSharing, 500);
  }
  async function shareBeforeStatement(sqlplus = false, command = '') {
    if (!isStudent || dataMode() !== 'statement' || sharedEditor?.statementSnapshotReady) return;
    try {
      sharedEditor = await editorShareRequest('/api/compiler/editor-share', { method: 'POST', body: JSON.stringify({ mode: 'statement', statementExecution: true, editorText: editor.value, sqlplusText: sqlplus ? command : terminalInput.value, revision: sharedEditor?.revision }) });
      lastSharedEditorText = editor.value;
      lastSharedSqlplusText = sqlplus ? '' : terminalInput.value;
      $('#teacherEditorShareStatus').textContent = 'Statement shared with your teacher';
    } catch (error) { showToast(error.message); }
  }
  async function pollTeacherEditorChanges() {
    if (!isStudent || !$('#teacherEditorAccess') || document.hidden) return;
    try {
      const data = await editorShareRequest('/api/compiler/editor-share');
      const permissionRequest = data.editPermissionRequest;
      if (permissionRequest?.id && permissionRequest.id !== lastEditPermissionPrompt) {
        lastEditPermissionPrompt = permissionRequest.id;
        const approved = await askTeacherEditorPermission(permissionRequest.teacherName);
        const response = await editorShareRequest('/api/compiler/editor-share', {
          method: 'POST',
          body: JSON.stringify({
            action: 'respond_edit_permission', requestId: permissionRequest.id, approved,
            sessionToken: shareSessionToken, editorText: editor.value, sqlplusText: terminalInput.value,
          }),
        });
        sharedEditor = response;
        if (approved) $('#teacherEditorAccess').value = 'session';
        lastSharedEditorText = editor.value;
        lastSharedSqlplusText = terminalInput.value;
        editorShareConflict = false;
        $('#teacherEditorShareStatus').textContent = approved ? 'Sharing this session' : 'Edit request declined · view only';
        if (approved) showToast('Your teacher can edit your shared editors for this session.');
        return;
      }
      if (sharedEditor && data.revision > sharedEditor.revision) {
        const editorChangedLocally = editor.value !== lastSharedEditorText;
        const editorChangedRemotely = (data.editorText || '') !== lastSharedEditorText;
        const terminalChangedLocally = terminalInput.value !== lastSharedSqlplusText;
        const terminalChangedRemotely = (data.sqlplusText || '') !== lastSharedSqlplusText;
        const conflict = (editorChangedLocally && editorChangedRemotely && editor.value !== data.editorText)
          || (terminalChangedLocally && terminalChangedRemotely && terminalInput.value !== data.sqlplusText);
        if (conflict) {
          editorShareConflict = true;
          showToast('Your teacher and you edited the same code at once. Copy your local edits before reloading the shared editor.');
        }
        if (!conflict && editor.value === lastSharedEditorText && editorChangedRemotely) { window.CompilerWorkspace.edit(editor, data.editorText, 0, editor.value.length, 'insertReplacementText'); }
        if (!conflict && !terminalInput.readOnly && terminalInput.value === lastSharedSqlplusText && terminalChangedRemotely) {
          terminalInput.value = data.sqlplusText;
          terminalInput.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertReplacementText' }));
        }
        if (!conflict) showToast('Your teacher updated a shared editor.');
      }
      sharedEditor = data;
      if (!editorShareConflict) {
        lastSharedEditorText = editor.value;
        lastSharedSqlplusText = terminalInput.value;
      }
      if (data.mode === 'private') $('#teacherEditorAccess').value = 'private';
      $('#teacherEditorShareStatus').textContent = data.mode === 'private' ? 'View only · teacher cannot edit' : data.mode === 'statement' ? (data.statementSnapshotReady ? 'Statement shared' : 'Waiting for next statement') : data.mode === 'session' ? 'Sharing this session' : 'Always sharing';
    } catch { /* Keep the student's editor available if the polling request fails. */ }
  }
  function askTeacherEditorPermission(teacherName) {
    return new Promise(resolve => {
      const dialog = document.createElement('dialog');
      dialog.className = 'help-dialog';
      const heading = document.createElement('div');
      heading.className = 'dialog-heading';
      const title = document.createElement('h2');
      title.textContent = 'Teacher edit request';
      heading.append(title);
      const message = document.createElement('p');
      message.className = 'dialog-note';
      message.textContent = `${teacherName || 'Your teacher'} is asking to edit both your SQLCL Terminal and SQL WorkSheet code for this session.`;
      const actions = document.createElement('div');
      actions.className = 'profile-form-actions';
      const reject = document.createElement('button');
      reject.type = 'button'; reject.className = 'register-cancel'; reject.textContent = 'Reject';
      const allow = document.createElement('button');
      allow.type = 'button'; allow.className = 'submit-button'; allow.textContent = 'Allow for this session';
      actions.append(reject, allow);
      dialog.append(heading, message, actions);
      document.body.append(dialog);
      if (typeof dialog.showModal !== 'function') {
        const approved = window.confirm(`${message.textContent} Allow?`);
        dialog.remove();
        resolve(approved);
        return;
      }
      const finish = approved => { dialog.dataset.approved = approved ? 'true' : 'false'; dialog.close(); };
      reject.addEventListener('click', () => finish(false));
      allow.addEventListener('click', () => finish(true));
      dialog.addEventListener('close', () => { const approved = dialog.dataset.approved === 'true'; dialog.remove(); resolve(approved); }, { once: true });
      dialog.addEventListener('cancel', () => { dialog.dataset.approved = 'false'; }, { once: true });
      dialog.showModal();
    });
  }
  $('#teacherEditorAccess')?.addEventListener('change', async (event) => {
    try { await setStudentShareMode(event.target.value); }
    catch (error) { showToast(error.message); await loadStudentEditorSharing(); }
  });
  function resizeTerminalInput() {
    if (!terminalInput || !terminalInput.getClientRects().length) return;
    // The outer terminal scrolls; the input must contain every pasted line.
    terminalInput.style.height = '0px';
    terminalInput.style.height = `${Math.max(25, terminalInput.scrollHeight)}px`;
    terminalInput.scrollTop = 0;
  }
  let terminalViewportWidth = -1;
  if (terminalOutput && window.ResizeObserver) {
    new ResizeObserver(() => {
      if (terminalOutput.clientWidth === terminalViewportWidth) return;
      terminalViewportWidth = terminalOutput.clientWidth;
      requestAnimationFrame(resizeTerminalInput);
    }).observe(terminalOutput);
  }
  document.addEventListener('fullscreenchange', () => requestAnimationFrame(resizeTerminalInput));
  terminalInput?.addEventListener('input', () => {
    resizeTerminalInput();
    scheduleEditorShareSync();
    terminalOutput.scrollTop = terminalOutput.scrollHeight;
  });
  if (isTeacher) {
    setInterval(async () => {
      if (teacherEditorKind === 'history' || !$('#historyStudentFilter')?.value || document.hidden) return;
      try { await editorShareRequest(`/api/compiler/editor-share/${$('#historyStudentFilter').value}`); }
      catch (error) {
        sharedEditor = null;
        $('#teacherStudentEditor').value = '';
        $('#teacherStudentEditor').readOnly = true;
        $('#saveTeacherEditor').disabled = true;
        $('#teacherEditorStatus').textContent = error.message || 'Student editor access is unavailable.';
      }
    }, 4000);
  }
  if (isStudent) {
    sharePollTimer = setInterval(pollTeacherEditorChanges, 4000);
    setInterval(syncStudentEditorSharing, 25000);
    setInterval(pollRemoteTerminalCommand, 1200);
    loadStudentEditorSharing();
  }

  function lastSqlTerminatorIndex(text) {
    return window.CompilerWorkspace.scan(text).tokens.filter(token => token.kind === 'symbol' && token.text === ';').at(-1)?.start ?? -1;
  }
  function isSqlPlusClientInput(text) {
    const commandLine = /^(?:DESC(?:RIBE)?|SET|SHOW|DEFINE|UNDEFINE|PROMPT|REM(?:ARK)?|HELP|HISTORY|TIMING|CLEAR|LIST|L|APPEND|CHANGE|C|INPUT|DEL|RUN|R|EXIT|QUIT|DISCONNECT|CONNECT|CONN|START|GET|SAVE|SPOOL)\b|^@@?\S+$/i;
    const lines = text.split(/\r?\n/).map((line) => line.trim()).filter((line) => line && !line.startsWith('--'));
    return lines.length > 0 && lines.every((line) => commandLine.test(line) || line === '/' || line === '?');
  }
  function terminalInputIsComplete(text) {
    const source = text.trim();
    if (!source) return false;
    if (window.CompilerWorkspace.commentOnly(source)) return true;
    const isPlsql = /^(?:DECLARE|BEGIN|CREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+)?(?:PROCEDURE|FUNCTION|PACKAGE|TRIGGER|TYPE)\b)/i.test(source);
    if (isPlsql) {
      const slash = source.match(/(?:^|\n)\s*\/\s*(?:\n|$)/);
      if (!slash) return false;
      const remainder = source.slice(slash.index + slash[0].length).trim();
      return !remainder || terminalInputIsComplete(remainder);
    }
    const terminator = lastSqlTerminatorIndex(source);
    if (terminator < 0) return isSqlPlusClientInput(source);
    const remainder = source.slice(terminator + 1).trim();
    return !remainder || isSqlPlusClientInput(remainder);
  }
  let remoteTerminalPolling = false;
  async function pollRemoteTerminalCommand() {
    if (!isStudent || document.hidden || remoteTerminalPolling || executionBusy || terminalQueue || !terminalInput || $('#openConnection').dataset.connected !== 'true') return;
    remoteTerminalPolling = true;
    try {
      const pending = await editorShareRequest('/api/compiler/remote-terminal/pending');
      const job = pending.command;
      if (!job) return;
      let executionFinished = false;
      try {
        const sqlplus = job.editor !== 'worksheet';
        const result = await execute(job.text, { sqlplus, script: Boolean(job.script) });
        if (!result || result.retryPending) throw new Error('The student Oracle connection was lost before a result was returned.');
        executionFinished = true;
        const output = sqlplus ? window.CompilerWorkspace.outputText(job.text, result) : JSON.stringify(result, null, 2);
        const error = result.error || (result.executionErrors?.length ? result.executionErrors.map(item => item.error || item.message || String(item)).join('\n') : '');
        await editorShareRequest(`/api/compiler/remote-terminal/${encodeURIComponent(job.id)}/complete`, {
          method: 'POST', body: JSON.stringify({ output, error, executed: executionFinished }),
        });
        if (sqlplus && terminalInput.value.trim() === job.text.trim()) {
          terminalInput.value = '';
          terminalInput.style.height = '';
          terminalInput.dispatchEvent(new Event('input', { bubbles: true }));
        }
      } catch (error) {
        try {
          await editorShareRequest(`/api/compiler/remote-terminal/${encodeURIComponent(job.id)}/complete`, {
            method: 'POST', body: JSON.stringify({ error: error.message || 'Remote SQLCL execution failed.', executed: executionFinished }),
          });
        } catch { /* Teacher will see the command expire if its result cannot be reported. */ }
      }
    } catch { /* Retry after the next student heartbeat if the portal is briefly unavailable. */ }
    finally { remoteTerminalPolling = false; }
  }
  function advanceTerminalComments(source) {
    const leading=window.CompilerWorkspace.leadingComments(source);
    leading.comments.forEach(comment=>appendTerminalResult(comment,{commentOnly:true},null));
    return leading.sql.trim();
  }
  async function runTerminalCommand() {
    const submitted = terminalInput.value.trim();
    let command = submitted;
    if (!command || $('#terminalRun').disabled) return;
    if (window.CompilerWorkspace.pastedBatch(command).commands.length || terminalInputIsComplete(command)) {
      await runPastedTerminalBatch(command);
      return;
    }
    if (!terminalInputIsComplete(command)) {
      showToast('Finish the SQL statement with ; before running it.');
      return;
    }
    command = advanceTerminalComments(command);
    if(!command){
      terminalInput.value='';terminalInput.dispatchEvent(new Event('input',{bubbles:true}));terminalInput.focus();return;
    }
    terminalBuffer = '';
    $('#terminalRun').disabled = true;
    let completed = false;
    try {
      const result = await execute(command, { sqlplus: true });
      if (result === null) return;
      completed = true;
      const executedCommands = result?.statementResults?.map((item) => item.statement).filter(Boolean);
      terminalCommandHistory.push(...(executedCommands?.length ? executedCommands : [command]));
      terminalHistoryIndex = terminalCommandHistory.length;
      terminalHistoryDraft = '';
    } finally {
      if (completed && terminalInput.value.trim() === submitted) {
        terminalInput.value = '';
        terminalInput.style.height = '';
        terminalInput.dispatchEvent(new Event('input', { bubbles:true }));
      }
      $('#terminalRun').disabled = false;
      terminalInput.focus();
    }
  }
  terminalInput.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      // Alt recalls history anywhere; plain arrows do so only at line boundaries.
      if (event.shiftKey || event.ctrlKey || event.metaKey) return;
      if (!event.altKey) {
        if (terminalInput.selectionStart !== terminalInput.selectionEnd) return;
        const caret = terminalInput.selectionStart;
        const hasPreviousLine = terminalInput.value.slice(0, caret).includes('\n');
        const hasNextLine = terminalInput.value.slice(caret).includes('\n');
        if ((event.key === 'ArrowUp' && hasPreviousLine)
          || (event.key === 'ArrowDown' && hasNextLine)) return;
      }
      if (terminalCommandHistory.length) {
        event.preventDefault();
        event.stopImmediatePropagation();
        const text = terminalInput.value;
        if (event.key === 'ArrowUp' && terminalHistoryIndex > 0) {
          if (terminalHistoryIndex === terminalCommandHistory.length) terminalHistoryDraft = text;
          terminalHistoryIndex -= 1;
          terminalInput.value = terminalCommandHistory[terminalHistoryIndex];
          markInputMethod('sqlplus', 'history_recall');
        } else if (event.key === 'ArrowDown' && terminalHistoryIndex < terminalCommandHistory.length) {
          terminalHistoryIndex += 1;
          terminalInput.value = terminalHistoryIndex === terminalCommandHistory.length ? terminalHistoryDraft : terminalCommandHistory[terminalHistoryIndex];
          if (terminalHistoryIndex < terminalCommandHistory.length) markInputMethod('sqlplus', 'history_recall');
        }
        const historyCaret = event.key === 'ArrowUp' ? terminalInput.value.length : 0;
        terminalInput.setSelectionRange(historyCaret, historyCaret);
        terminalInput.dispatchEvent(new Event('input', { bubbles: true }));
        terminalInput.scrollTop = event.key === 'ArrowUp' ? terminalInput.scrollHeight : 0;
      }
      return;
    }
    if (event.key !== 'Enter') return;
    if (event.shiftKey) {
      event.preventDefault();
      const start = terminalInput.selectionStart;
      terminalInput.setRangeText('\n', start, terminalInput.selectionEnd, 'end');
      return;
    }
    event.preventDefault();
    if (!terminalInputIsComplete(terminalInput.value)) {
      const start = terminalInput.selectionStart;
      terminalInput.setRangeText('\n', start, terminalInput.selectionEnd, 'end');
      terminalInput.dispatchEvent(new Event('input', { bubbles: true }));
      return;
    }
    runTerminalCommand();
  });
  const sqlplusStarterMenu = $('#sqlplusStarterMenu');
  const sqlplusStarterToggle = $('#sqlplusStarterPopupToggle');
  const sqlplusCleanPaste = $('#sqlplusCleanPaste');
  const sqlplusAutoCommit = $('#sqlplusAutoCommit');
  sqlplusCleanPaste.checked = localStorage.getItem('ora-sqlplus-clean-paste') !== 'false';
  sqlplusAutoCommit.checked = localStorage.getItem('ora-sqlplus-autocommit') !== 'OFF';
  sqlplusStarterToggle.checked = localStorage.getItem('ora-show-sql-starters') === 'true';
  sqlplusCleanPaste.addEventListener('change', () => { localStorage.setItem('ora-sqlplus-clean-paste', String(sqlplusCleanPaste.checked)); if (sqlplusCleanPaste.checked) cleanEditorNow(terminalInput); });
  sqlplusAutoCommit.addEventListener('change', () => localStorage.setItem('ora-sqlplus-autocommit', sqlplusAutoCommit.checked ? 'ON' : 'OFF'));
  sqlplusStarterToggle.addEventListener('change', () => {
    localStorage.setItem('ora-show-sql-starters', String(sqlplusStarterToggle.checked));
    plsqlStarterToggle.checked = sqlplusStarterToggle.checked;
    sqlplusStarterMenu.hidden = !sqlplusStarterToggle.checked || Boolean(terminalInput.value.trim());
  });
  terminalInput.addEventListener('focus', () => { sqlplusStarterMenu.hidden = !sqlplusStarterToggle.checked || Boolean(terminalInput.value.trim()); });
  terminalInput.addEventListener('input', () => { updateTerminalInputHighlight(); if (terminalInput.value.trim()) sqlplusStarterMenu.hidden = true; });
  terminalInput.addEventListener('scroll', () => {
    const highlight = $('#terminalInputHighlight');
    if (highlight) { highlight.scrollTop = terminalInput.scrollTop; highlight.scrollLeft = terminalInput.scrollLeft; }
  });
  async function runPastedTerminalBatch(snapshot, recovered = null, sourceMapOverride = null) {
    terminalBatchCancelled = false;
    const batch = recovered || window.CompilerWorkspace.pastedBatch(snapshot);
    const batchSourceMap = sourceMapOverride || inputSourceRuns('sqlplus', snapshot);
    const batchInputMethod = recovered?.inputMethod || currentInputMethod('sqlplus');
    if (!batch.commands.length && snapshot.trim() && terminalInputIsComplete(snapshot)) {
      batch.commands.push(snapshot.trim()); batch.remaining = '';
    }
    if (!batch.commands.length) return;
    let remaining = batch.remaining;
    if (remaining.trim() && terminalInputIsComplete(remaining)) { batch.commands.push(remaining.trim()); remaining = ''; }
    terminalInput.readOnly = true;
    terminalInput.compilerPendingDraft = snapshot;
    terminalInput.value = '';
    terminalInput.dispatchEvent(new Event('input', { bubbles:true }));
    let nextIndex = 0;
    try {
      // Keep pasted work out of the input even while another execution or
      // navigator refresh is finishing. Never fall back to painting the batch.
      while(executionBusy || $('#terminalRun').disabled) {
        if (terminalBatchCancelled) return;
        await new Promise(resolve=>setTimeout(resolve,50));
      }
      $('#terminalRun').disabled = true;
      terminalQueue = recovered || { commands: batch.commands, remaining, index: 0, request: null, inputMethod: batchInputMethod };
      saveTerminalQueue();
      let provenanceSearchOffset = 0;
      for (let index=terminalQueue.index; index<batch.commands.length; index++) {
        if (terminalBatchCancelled) { remaining=''; break; }
        nextIndex = index;
        terminalInput.compilerPendingDraft = batch.commands.slice(index).join('\n')+'\n'+remaining;
        const command=advanceTerminalComments(batch.commands[index]);
        if(!command){nextIndex=index+1;continue;}
        const commandOffset = snapshot.indexOf(command, provenanceSearchOffset);
        if (commandOffset >= 0) provenanceSearchOffset = commandOffset + command.length;
        if (await waitForTerminalConnection() === false) { remaining=''; break; }
        if (batchInputMethod) editMetrics.sqlplus.inputMethods.add(batchInputMethod);
        const result=await execute(command, { sqlplus:true, inputSourceMap: commandOffset >= 0 ? sliceInputSourceRuns(batchSourceMap, commandOffset, commandOffset + command.length) : [] });
        if (terminalBatchCancelled) { remaining=''; break; }
        if (result?.retryPending) { saveTerminalQueue(); index--; await new Promise(resolve=>setTimeout(resolve,1000)); continue; }
        if (result === null) { remaining=batch.commands.slice(index).join('\n')+'\n'+remaining; break; }
        terminalCommandHistory.push(command);
        nextIndex = index + 1;
        terminalQueue.index = nextIndex;
        terminalQueue.request = null;
        saveTerminalQueue();
        if (result.disconnected || result.sqlPlusDisconnected) setStatus(false, 'Waiting for database connection');
      }
      terminalHistoryIndex=terminalCommandHistory.length;
      terminalHistoryDraft='';
      terminalInput.value=remaining;
      terminalQueue = null;
      saveTerminalQueue();
      terminalInput.dispatchEvent(new Event('input', { bubbles:true }));
    } catch (error) {
      terminalInput.value = batch.commands.slice(nextIndex).join('\n')+'\n'+remaining;
      throw error;
    } finally {
      if (terminalBatchCancelled) {
        terminalQueue = null;
        terminalInput.value = '';
        try { sessionStorage.removeItem(terminalQueueKey); } catch {}
        appendTerminalResult('^C', { message:'Pasted statement execution stopped. Remaining statements were not executed.' }, null);
        $('#terminalStatus').textContent = $('#openConnection').dataset.connected === 'true' ? 'Connected to Oracle' : 'Awaiting connection';
      }
      delete terminalInput.compilerPendingDraft;
      terminalInput.dispatchEvent(new Event('input', { bubbles:true }));
      terminalInput.readOnly=false;
      $('#terminalRun').disabled=false;
      terminalInput.focus();
    }
  }
  terminalInput.addEventListener('paste', (event) => {
    if (!event.clipboardData) return;
    event.preventDefault();
    if (terminalInput.readOnly) return;
    const pastedText = event.clipboardData.getData('text/plain');
    const internal = consumeInternalCopy(pastedText);
    if (!internal) {
      reportCompilerPaste('SQL*Plus', pastedText);
      recordPaste('sqlplus', pastedText.length);
    } else markInputMethod('sqlplus', 'internal_copy');
    const transcriptSql = window.CompilerWorkspace.cleanSqlPlusTranscript(pastedText);
    const withoutPrompts = window.CompilerWorkspace.stripSqlPlusPrompts(transcriptSql);
    const text = sqlplusCleanPaste.checked ? normalizePastedSql(withoutPrompts) : withoutPrompts.replace(/\r\n?/g, '\n');
    const replaceStart = terminalInput.selectionStart, replaceEnd = terminalInput.selectionEnd;
    const snapshot=terminalInput.value.slice(0,replaceStart)+text+terminalInput.value.slice(replaceEnd);
    const trace = inputProvenance.sqlplus;
    trace.text = snapshot;
    trace.sources = [...trace.sources.slice(0, replaceStart), ...Array(text.length).fill(internal ? 'internal' : 'pasted'), ...trace.sources.slice(replaceEnd)];
    if (window.CompilerWorkspace.pastedBatch(snapshot).commands.length) {
      // Queue before painting: the entire pasted batch never enters the textarea.
      runPastedTerminalBatch(snapshot).catch(error => showToast(error.message || 'Pasted batch failed.'));
    } else {
      const leading=window.CompilerWorkspace.leadingComments(snapshot);
      if(!executionBusy && !$('#terminalRun').disabled && leading.comments.length){
        terminalInput.value=advanceTerminalComments(snapshot);
        terminalInput.setSelectionRange(terminalInput.value.length,terminalInput.value.length);
      } else terminalInput.setRangeText(text, terminalInput.selectionStart, terminalInput.selectionEnd, 'end');
      inputProvenance.sqlplus.pending = internal ? 'internal' : 'pasted';
      terminalInput.dispatchEvent(new InputEvent('input', { bubbles:true, inputType:'insertFromPaste' }));
    }
  });
  terminalInput.addEventListener('input', (event) => trackManualEdit('sqlplus', event));
  sqlplusStarterMenu.querySelectorAll('[data-sqlplus-starter]').forEach((button) => button.addEventListener('click', () => {
    terminalInput.value = button.dataset.sqlplusStarter;
    sqlplusStarterMenu.hidden = true;
    terminalInput.focus();
    terminalInput.setSelectionRange(terminalInput.value.length, terminalInput.value.length);
    terminalInput.dispatchEvent(new Event('input', { bubbles: true }));
  }));
  $('#terminalRun').addEventListener('click', runTerminalCommand);
  $$('[data-terminal-action]').forEach((button) => button.addEventListener('click', async () => {
    const action = button.dataset.terminalAction;
    const output = $('#terminalOutput');
    if (action === 'clear') { output.replaceChildren(terminalPrompt); terminalInput.value = ''; terminalInput.style.height = ''; }
    if (action === 'copy') { rememberInternalCopy(output.innerText); await navigator.clipboard.writeText(output.innerText); showToast('Terminal copied.'); }
    if (action === 'download') {
      const link = document.createElement('a');
      link.href = URL.createObjectURL(new Blob([output.innerText], { type: 'text/plain' }));
      link.download = 'sqlplus-session.txt'; link.click(); URL.revokeObjectURL(link.href);
    }
    if (action === 'open') $('#sqlplusFileInput').click();
    if (action === 'share') {
      const text = output.innerText.trim();
      if (!text) { showToast('There is no SQLCL session output to share.'); return; }
      if (navigator.share) await navigator.share({ title: 'SQLCL session', text });
      else { await navigator.clipboard.writeText(text); showToast('SQLCL session copied for sharing.'); }
    }
    if (action === 'shortcuts') { const dialog = $('#shortcutDialog'); if (!dialog.open) dialog.showModal(); }
  }));

  function applyTerminalFont() {
    $$('.terminal-output').forEach((node) => { node.style.fontSize = `${terminalFontSize}px`; });
    const prompt = $('.terminal-prompt');
    if (prompt) prompt.style.fontSize = `${terminalFontSize}px`;
    if (terminalInput) { terminalInput.style.fontSize = `${terminalFontSize}px`; requestAnimationFrame(resizeTerminalInput); }
    $('#terminalFontValue').textContent = terminalFontSize;
    $('#fontValue').textContent = terminalFontSize;
  }
  function adjustFont(delta) { terminalFontSize = Math.max(10, Math.min(22, terminalFontSize + delta)); applyTerminalFont(); localStorage.setItem(`${settingPrefix}font`, String(terminalFontSize)); }
  $('#terminalFontDown').addEventListener('click', () => adjustFont(-1));
  $('#terminalFontUp').addEventListener('click', () => adjustFont(1));
  $('#fontDown').addEventListener('click', () => adjustFont(-1));
  $('#fontUp').addEventListener('click', () => adjustFont(1));

  let fullscreenNavExpanded = false;
  try { fullscreenNavExpanded = localStorage.getItem(`${settingPrefix}fullscreen-nav-expanded`) === 'true'; } catch {}
  function syncNavExpandControl() {
    shell.classList.toggle('fullscreen-nav-expanded', fullscreenNavExpanded);
    const fullscreen = shell.classList.contains('fullscreen-mode');
    const collapsed = fullscreen ? !fullscreenNavExpanded : shell.classList.contains('icon-nav-left');
    const button = $('#navModeBtn');
    button.title = collapsed ? 'Expand navigation' : 'Collapse navigation';
    button.setAttribute('aria-label', button.title);
    button.setAttribute('aria-expanded', String(!collapsed));
    button.textContent = collapsed ? '»' : '«';
  }
  const exitFullscreenButton = document.createElement('button');
  exitFullscreenButton.type = 'button';
  exitFullscreenButton.className = 'ide-exit-fullscreen';
  exitFullscreenButton.textContent = '↙ Exit full screen';
  exitFullscreenButton.setAttribute('aria-label', 'Exit full screen');
  exitFullscreenButton.hidden = true;
  shell.append(exitFullscreenButton);
  exitFullscreenButton.addEventListener('click', toggleFullscreen);
  function syncFullscreenControl() {
    syncNavExpandControl();
    const active = shell.classList.contains('fullscreen-mode');
    exitFullscreenButton.hidden = !active;
    $('#navFullscreenBtn').title = active ? 'Restore workspace' : 'Maximize workspace';
    $('#navFullscreenBtn').setAttribute('aria-label', active ? 'Restore workspace' : 'Maximize workspace');
    $('#navFullscreenBtn').textContent = active ? '↙' : '⛶';
  }
  async function toggleFullscreen() {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (shell.classList.contains('fullscreen-mode')) {
        shell.classList.remove('fullscreen-mode');
        syncFullscreenControl();
      } else await shell.requestFullscreen();
    } catch {
      shell.classList.toggle('fullscreen-mode');
      syncFullscreenControl();
    }
  }
  $('#fullscreenBtn').addEventListener('click', toggleFullscreen);
  $('#navFullscreenBtn').addEventListener('click', toggleFullscreen);
  document.addEventListener('fullscreenchange', () => {
    shell.classList.toggle('fullscreen-mode', Boolean(document.fullscreenElement));
    syncFullscreenControl();
  });
  function syncNavigationMode(collapsed) {
    shell.classList.toggle('icon-nav-left', collapsed);
    $('#iconNavigation').checked = collapsed;
    $('#navModeBtn').title = collapsed ? 'Expand navigation labels' : 'Collapse navigation labels';
    $('#navModeBtn').setAttribute('aria-label', $('#navModeBtn').title);
    syncNavExpandControl();
  }
  $('#navModeBtn').addEventListener('click', () => {
    if (shell.classList.contains('fullscreen-mode')) {
      fullscreenNavExpanded = !fullscreenNavExpanded;
      syncNavExpandControl();
      localStorage.setItem(`${settingPrefix}fullscreen-nav-expanded`, String(fullscreenNavExpanded));
      return;
    }
    const collapsed = !shell.classList.contains('icon-nav-left');
    syncNavigationMode(collapsed);
    localStorage.setItem(`${settingPrefix}icon-nav`, String(collapsed));
  });
  $('#collapseNavigator').addEventListener('click', () => {
    const collapsed = main.classList.toggle('navigator-collapsed');
    $('#collapseNavigator').textContent = collapsed ? '»' : '«';
    $('#collapseNavigator').title = collapsed ? 'Expand navigator' : 'Collapse navigator';
  });

  $('#iconNavigation').addEventListener('change', (event) => {
    syncNavigationMode(event.target.checked);
    localStorage.setItem(`${settingPrefix}icon-nav`, String(event.target.checked));
  });
  $('#defaultFullscreen').addEventListener('change', (event) => localStorage.setItem(`${settingPrefix}default-fullscreen`, String(event.target.checked)));
  $('#objectSearch').addEventListener('input', renderDatabaseObjects);
  $('#navSchema').addEventListener('change', renderDatabaseObjects);
  $('#objectTypeFilter').addEventListener('change', renderDatabaseObjects);
  const waitForNavigatorRefresh = $('#waitNavigatorRefresh');
  waitForNavigatorRefresh.checked = localStorage.getItem(`${settingPrefix}wait-navigator-refresh`) !== 'false';
  waitForNavigatorRefresh.addEventListener('change', () => localStorage.setItem(`${settingPrefix}wait-navigator-refresh`, String(waitForNavigatorRefresh.checked)));
  $('#refreshObjects').addEventListener('click', refreshDatabaseNavigator);

  $('#openConnection').addEventListener('click', async () => {
    if ($('#openConnection').dataset.connected === 'true') {
      requireManualConnection();
      // Finish any in-flight initial connection before closing its server session.
      if (reconnectPromise) await reconnectPromise;
      try {
        await fetch('/api/disconnect', { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': document.cookie.split('; ').find((part) => part.startsWith('ora_csrf='))?.split('=').slice(1).join('=') || '' } });
      } finally { setStatus(false, 'Not connected'); showToast('Disconnected from Oracle.'); }
      return;
    }
    $('#connectError').textContent = '';
    requireManualConnection();
    await loadSavedCredentialDetails();
    if (!connectDialog.open) connectDialog.showModal();
    connectForm.elements.user.focus();
  });
  $$('[data-close-dialog]').forEach((button) => button.addEventListener('click', () => button.closest('dialog').close()));
  connectForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(connectForm).entries());
    const unchangedSavedConnection = values.user === connectForm.dataset.savedOracleUser
      && values.connectString === connectForm.dataset.savedOracleConnectString;
    if ($('#saveCredentials').checked && !values.password && unchangedSavedConnection) connectWithSavedCredentials();
    else connect(values, { save: $('#saveCredentials').checked });
  });

  $('#sqlEditor').addEventListener('paste', () => setTimeout(updateHighlight, 0));
  $('#sqlEditor').addEventListener('keyup', updateHighlight);
  document.addEventListener('keydown', (event) => {
    if (event.code === 'Space' && !event.altKey && !event.ctrlKey && !event.metaKey
      && event.target instanceof Element && event.target.closest('.terminal-frame')
      && !event.target.closest('button, a, input, select, textarea, [contenteditable="true"]')
      && terminalInput && !terminalInput.readOnly) {
      event.preventDefault();
      const start = terminalInput.selectionStart ?? terminalInput.value.length;
      const end = terminalInput.selectionEnd ?? start;
      terminalInput.focus({ preventScroll: true });
      terminalInput.setRangeText(' ', start, end, 'end');
      terminalInput.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: ' ' }));
      return;
    }
    if (event.key === 'F11') { event.preventDefault(); toggleFullscreen(); }
    if (event.key === 'Escape' && shell.classList.contains('fullscreen-mode') && !document.fullscreenElement) {
      shell.classList.remove('fullscreen-mode'); syncFullscreenControl();
    }
  });

  try {
    const saved = Number(localStorage.getItem(`${settingPrefix}font`));
    if (Number.isFinite(saved) && saved >= 10 && saved <= 22) terminalFontSize = saved;
    const iconNav = localStorage.getItem(`${settingPrefix}icon-nav`) === 'true';
    const defaultFullscreen = localStorage.getItem(`${settingPrefix}default-fullscreen`) === 'true';
    $('#iconNavigation').checked = iconNav;
    $('#defaultFullscreen').checked = defaultFullscreen;
    syncNavigationMode(iconNav);
    shell.classList.toggle('fullscreen-mode', typeof workspaceResume?.fullscreen === 'boolean' ? workspaceResume.fullscreen : defaultFullscreen);
    applyTerminalFont();
  } catch { applyTerminalFont(); }

  try {
    const old = localStorage.getItem('oracle-sql-credentials');
    if (old) { legacyCredentials = JSON.parse(old); localStorage.removeItem('oracle-sql-credentials'); }
  } catch { legacyCredentials = null; }

  const assistColumnCache = new Map();
  async function assistColumns(sql) {
    const source = window.CompilerWorkspace.scan(sql).tokens.map(token => ['literal','comment'].includes(token.kind) ? ' '.repeat(token.end-token.start) : token.text).join(' ');
    const names = [...source.matchAll(/\b(?:FROM|JOIN|UPDATE|INTO)\s+([A-Za-z][\w$#]*)(?:\s*\.\s*([A-Za-z][\w$#]*))?/gi)].slice(0,6);
    const groups = await Promise.all(names.map(async (match) => {
      const owner = match[2] ? match[1] : currentSchema;
      const name = match[2] || match[1];
      const key = `${owner}.${name}`.toUpperCase();
      if (!owner) return [];
      const cached = assistColumnCache.get(key);
      if (cached && Date.now() - cached.at < 60000) return cached.promise;
      const promise = (async () => {
      try {
        const response = await fetch(`/api/objects/${encodeURIComponent(owner)}/${encodeURIComponent(name)}?view=structure`, {credentials:'same-origin'});
        if (!response.ok) return [];
        const data = await response.json();
        const columns = (data.columns || []).map(row => row.COLUMN_NAME || row.column_name || row.name).filter(Boolean);
        return columns;
      } catch { return []; }
      })();
      assistColumnCache.set(key, { at: Date.now(), promise });
      return promise;
    }));
    return groups.flat();
  }
  const draftOptions = { user: document.body.dataset.userId, workspace: location.pathname };
  window.CompilerWorkspace.draft(editor, { ...draftOptions, statusHost: $('.editor-status') });
  window.CompilerWorkspace.draft(terminalInput, { ...draftOptions, statusHost: $('.terminal-foot'),
    runRecovered: () => runTerminalCommand().catch(error => showToast(error.message || 'Recovered statements failed.')) });
  window.CompilerWorkspace.history(editor);
  window.CompilerWorkspace.history(terminalInput);
  updateLineNumbers(); updateHighlight(); updateEditorCursorPosition();
  terminalInput.dispatchEvent(new Event('input', { bubbles:true }));
  const assistOptions = { getObjects: () => objectRows, getColumns: assistColumns };
  window.CompilerEditorAssist?.attach(editor, { ...assistOptions, statusHost: $('.editor-status') });
  window.CompilerEditorAssist?.attach(terminalInput, { ...assistOptions, terminal:true, isComplete:terminalInputIsComplete, statusHost:$('.terminal-foot') });
  function installFontSetting(id, kind) {
    const select=$('#'+id);
    const allowed=new Set(['','Consolas','Cascadia Code','Courier New','Lucida Console','DM Mono']);
    const key=`compiler-font:${document.body.dataset.userId || 'local'}:${kind}`;
    function apply() {
      const choice=allowed.has(select.value)?select.value:'';
      const family=choice?`"${choice}", monospace`:'';
      const nodes=kind==='plsql'?[editor,$('#syntaxHighlight'),$('#lineNumbers'),$('#sqlEditor-completions')]:[terminalOutput,terminalInput,terminalPrompt,$('#terminalInput-completions')];
      nodes.filter(Boolean).forEach(node=>{node.style.fontFamily=family;});
      if(kind==='sqlplus') {
        if(family)terminalOutput.style.setProperty('--compiler-terminal-font',family);
        else terminalOutput.style.removeProperty('--compiler-terminal-font');
        requestAnimationFrame(resizeTerminalInput);
      } else updateHighlight();
    }
    try { const saved=localStorage.getItem(key)||'';select.value=allowed.has(saved)?saved:''; } catch {}
    apply();
    select.addEventListener('change',()=>{apply();try{localStorage.setItem(key,select.value);}catch{}});
  }
  installFontSetting('plsqlFontFamily','plsql');
  installFontSetting('sqlplusFontFamily','sqlplus');
  const availableViews = ['sqlplus', 'compiler', 'history', 'settings', ...(isStudent ? ['sharedCode'] : [])];
  const initialView = availableViews.includes(requestedView)
    ? requestedView
    : availableViews.includes(workspaceResume?.view) ? workspaceResume.view : 'sqlplus';
  setView(initialView);
  syncFullscreenControl();
  document.documentElement.classList.remove('workspace-restoring');
  loadOwnHistory();
  const bufferInput = $('#terminalBufferEntries');
  function updateBufferSetting() {
    bufferInput.value = personalTerminalBufferEntries || defaultTerminalBufferEntries;
    $('#terminalBufferStatus').textContent = personalTerminalBufferEntries ? 'Personal buffer size' : 'Using shared default';
    trimTerminalBuffer();
  }
  bufferInput.addEventListener('change', () => {
    const value = Number(bufferInput.value);
    if (!Number.isInteger(value) || value < 100 || value > 50000) {
      updateBufferSetting(); showToast('Choose 100–50,000 entries.'); return;
    }
    personalTerminalBufferEntries = value;
    try { localStorage.setItem(terminalBufferKey, String(value)); } catch {}
    updateBufferSetting();
  });
  $('#useDefaultTerminalBuffer').addEventListener('click', () => {
    personalTerminalBufferEntries = null;
    try { localStorage.removeItem(terminalBufferKey); } catch {}
    updateBufferSetting();
  });
  function applyCompilerTabAppearance() {
    const entries = ['sqlplus', 'compiler'].map(key => ({ key, ...sharedCompilerTabAppearance[key] }));
    for (const entry of entries) {
      const navItem = $(`.ide-nav-item[data-view=\"${entry.key}\"]`);
      const panel = $(`[data-panel=\"${entry.key}\"]`);
      if (navItem) {
        navItem.hidden = !entry.visible;
        navItem.title = entry.label;
        navItem.setAttribute('aria-label', entry.label);
        const text = $('b', navItem); if (text) text.textContent = entry.label;
      }
      if (panel) {
        panel.dataset.tabVisible = String(entry.visible);
        panel.setAttribute('aria-label', entry.label);
      }
    }
    const nav = $('#ideNav');
    const historyTab = $('.ide-nav-item[data-view=\"history\"]', nav);
    entries.sort((a, b) => a.order - b.order).forEach(entry => {
      const item = $(`.ide-nav-item[data-view=\"${entry.key}\"]`, nav);
      if (item && historyTab) nav.insertBefore(item, historyTab);
    });
    const title = $('.terminal-titlebar > span:nth-child(2)');
    if (title?.firstChild) title.firstChild.textContent = `${sharedCompilerTabAppearance.sqlplus.label} Terminal `;
    const sourceLabels = { 'SQL*Plus': sharedCompilerTabAppearance.sqlplus.label, 'PL/SQL': sharedCompilerTabAppearance.compiler.label };
    $$('option[value=\"SQL*Plus\"], option[value=\"PL/SQL\"]').forEach(option => {
      if (sourceLabels[option.value]) option.textContent = sourceLabels[option.value];
    });
    if (['sqlplus', 'compiler'].includes(currentView) && !sharedCompilerTabAppearance[currentView].visible) {
      setView(sharedCompilerTabAppearance.sqlplus.visible ? 'sqlplus' : 'compiler');
    }
  }
  function fillCompilerTabAppearanceControls() {
    const fields = {
      sqlplus: { label: $('#tabLabelSqlplus'), visible: $('#tabVisibleSqlplus'), order: $('#tabOrderSqlplus') },
      compiler: { label: $('#tabLabelCompiler'), visible: $('#tabVisibleCompiler'), order: $('#tabOrderCompiler') },
    };
    for (const [key, controls] of Object.entries(fields)) {
      if (!controls.label) continue;
      controls.label.value = sharedCompilerTabAppearance[key].label;
      controls.visible.checked = sharedCompilerTabAppearance[key].visible;
      controls.order.value = String(sharedCompilerTabAppearance[key].order);
    }
  }
  function fillCompilerTabTargets(data) {
    const students = $('#compilerTabStudents');
    const labs = $('#compilerTabLabs');
    if (!students || !labs) return;
    students.replaceChildren();
    (data.students || []).forEach(student => {
      const option = document.createElement('option');
      option.value = String(student.id);
      option.textContent = `${student.name} · ${student.identifier}`;
      students.append(option);
    });
    labs.replaceChildren();
    (data.labs || []).forEach(lab => {
      const option = document.createElement('option');
      option.value = String(lab.id);
      option.textContent = `${lab.className} · ${lab.name}`;
      labs.append(option);
    });
  }
  function updateCompilerTargetScope() {
    const scope = $('#compilerTabTargetScope')?.value;
    if ($('#compilerTabStudentsWrap')) $('#compilerTabStudentsWrap').hidden = scope !== 'students';
    if ($('#compilerTabLabsWrap')) $('#compilerTabLabsWrap').hidden = scope !== 'labs';
  }
  $('#compilerTabTargetScope')?.addEventListener('change', updateCompilerTargetScope);
  updateCompilerTargetScope();
  async function loadBufferDefaults() {
    try {
      const response = await fetch('/api/compiler/settings', { credentials:'same-origin', cache:'no-store', signal:AbortSignal.timeout(5000) });
      if (!response.ok) throw new Error('Could not load shared compiler settings.');
      const data = await response.json();
      defaultTerminalBufferEntries = data.bufferEntries;
      $('#defaultTerminalBufferEntries').value = data.bufferEntries;
      $('#terminalBufferDefaultCard').hidden = !data.canManageDefault;
      if (data.effectiveTabs || data.tabAppearance) sharedCompilerTabAppearance = data.effectiveTabs || data.tabAppearance;
      applyCompilerTabAppearance();
      fillCompilerTabAppearanceControls();
      fillCompilerTabTargets(data);
      updateBufferSetting();
    } catch (error) { $('#terminalBufferStatus').textContent = error.message; }
  }
  async function loadStudentSharedCode() {
    const list = $('#studentSharedCodeList');
    const status = $('#studentSharedCodeStatus');
    if (!isStudent || !list || !status) return;
    try {
      const response = await fetch('/api/compiler/shared-code', { credentials: 'same-origin', cache: 'no-store' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Could not load code shared with you.');
      list.replaceChildren();
      const items = Array.isArray(data.items) ? data.items : [];
      if (!items.length) {
        status.textContent = 'No code has been shared with you yet.';
        return;
      }
      for (const item of items) {
        const card = document.createElement('article');
        card.className = 'student-shared-code-item';
        const header = document.createElement('header');
        const heading = document.createElement('div');
        const title = document.createElement('strong');
        title.textContent = item.title || 'Shared code';
        const meta = document.createElement('small');
        const created = item.createdAt ? new Date(item.createdAt).toLocaleString() : '';
        meta.textContent = [item.editorLabel, item.teacher, created].filter(Boolean).join(' · ');
        heading.append(title, meta);
        const copy = document.createElement('button');
        copy.type = 'button'; copy.className = 'tool-button'; copy.textContent = 'Copy code';
        const details = document.createElement('details');
        const summary = document.createElement('summary'); summary.textContent = 'View code';
        const code = document.createElement('pre'); code.textContent = item.code || '';
        details.append(summary, code);
        copy.addEventListener('click', async () => {
          try { await navigator.clipboard.writeText(item.code || ''); copy.textContent = 'Copied'; }
          catch { copy.textContent = 'Copy unavailable'; }
          window.setTimeout(() => { copy.textContent = 'Copy code'; }, 1600);
        });
        header.append(heading, copy);
        card.append(header, details);
        list.append(card);
      }
      status.textContent = `${items.length} shared item${items.length === 1 ? '' : 's'}`;
    } catch (error) {
      status.textContent = error.message || 'Could not load code shared with you.';
    }
  }
  const saveTabAppearance = $('#saveCompilerTabAppearance');
  if (saveTabAppearance) saveTabAppearance.addEventListener('click', async () => {
    const status = $('#compilerTabAppearanceStatus');
    const tabAppearance = {
      sqlplus: { label: $('#tabLabelSqlplus').value.trim(), visible: $('#tabVisibleSqlplus').checked, order: Number($('#tabOrderSqlplus').value) },
      compiler: { label: $('#tabLabelCompiler').value.trim(), visible: $('#tabVisibleCompiler').checked, order: Number($('#tabOrderCompiler').value) },
    };
    const scope = $('#compilerTabTargetScope').value;
    const target = { scope };
    if (scope === 'students') target.studentIds = [...$('#compilerTabStudents').selectedOptions].map(option => Number(option.value));
    if (scope === 'labs') target.labIds = [...$('#compilerTabLabs').selectedOptions].map(option => Number(option.value));
    try {
      const response = await fetch('/api/compiler/settings', { method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:JSON.stringify({tabAppearance, target}) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Could not save tab appearance.');
      if (scope === 'all' && document.body.dataset.canManageCompilerTabs === 'true' && !isTeacher) {
        sharedCompilerTabAppearance = data.tabAppearance;
        applyCompilerTabAppearance(); fillCompilerTabAppearanceControls();
      }
      status.textContent = `Tab appearance saved for ${data.affectedCount} student${data.affectedCount === 1 ? '' : 's'}.`;
    } catch (error) { status.textContent = error.message; }
  });
  $('#saveDefaultTerminalBuffer').addEventListener('click', async () => {
    const status = $('#terminalBufferDefaultStatus');
    try {
      const response = await fetch('/api/compiler/settings', { method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({bufferEntries:Number($('#defaultTerminalBufferEntries').value)}) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Could not save default.');
      defaultTerminalBufferEntries = data.bufferEntries;
      updateBufferSetting(); status.textContent = 'Shared default saved.';
    } catch(error) { status.textContent = error.message; }
  });
  updateBufferSetting();
  await loadBufferDefaults();
  if (isStudent) {
    loadStudentSharedCode();
    window.setInterval(loadStudentSharedCode, 30000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) loadStudentSharedCode(); });
  }
  try {
    const recovered = JSON.parse(sessionStorage.getItem(terminalQueueKey) || 'null');
    if (recovered?.commands?.length && Number.isInteger(recovered.index)) {
      restoreTerminalCommandHistory(recovered.transcript || []);
      (recovered.transcript || []).forEach(item => appendTerminalResult(item.sql, item.data, item.error));
      runPastedTerminalBatch('', recovered).catch(error => showToast(error.message || 'Terminal queue could not resume.'));
    } else {
      const transcript = JSON.parse(sessionStorage.getItem(terminalTranscriptKey) || '[]');
      restoreTerminalCommandHistory(transcript);
      transcript.slice(-(personalTerminalBufferEntries || defaultTerminalBufferEntries)).forEach(item => appendTerminalResult(item.sql, item.data, item.error));
    }
  } catch { /* Leave ordinary draft recovery available if queue storage is unreadable. */ }
  refreshStatus().then(connected => { if (connected === false) reconnectSavedCredentials(); });
  window.setInterval(async () => {
    if(document.hidden || executionBusy || reconnectPromise)return;
    const connected=await refreshStatus();
    if (connected === false && !manualOracleDisconnect && Date.now() - lastReconnectAttempt >= 30000) {
      await reconnectSavedCredentials();
    }
  },15000);
})();
