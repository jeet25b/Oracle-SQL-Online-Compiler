const compilerSourceLabel = source => ({'SQL*Plus': 'SQLCL', 'PL/SQL': 'SQL WorkSheet'}[source] || source || 'SQL WorkSheet');
(async () => {
  'use strict';

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  function installNavigatorPicker(selectId) {
    const select = $(`#${selectId}`);
    if (!select || select.dataset.themeSelectEnhanced === 'true') return;
    const picker = document.createElement('div');
    picker.className = 'navigator-picker';
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.id = `${selectId}Picker`;
    trigger.className = 'navigator-picker-trigger';
    trigger.setAttribute('role', 'combobox');
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('aria-label', select.getAttribute('aria-label') || 'Choose an option');
    const value = document.createElement('span');
    value.className = 'navigator-picker-value';
    const arrow = document.createElement('span');
    arrow.className = 'navigator-picker-arrow';
    arrow.setAttribute('aria-hidden', 'true');
    arrow.textContent = '⌄';
    trigger.append(value, arrow);
    const menu = document.createElement('div');
    menu.className = 'navigator-picker-menu';
    menu.id = `${selectId}PickerOptions`;
    menu.setAttribute('role', 'listbox');
    trigger.setAttribute('aria-controls', menu.id);
    select.parentNode.insertBefore(picker, select);
    picker.append(select, trigger, menu);
    select.classList.add('navigator-picker-native');
    select.setAttribute('aria-hidden', 'true');
    select.tabIndex = -1;
    const label = $(`label[for="${selectId}"]`);
    if (label) label.htmlFor = trigger.id;

    const close = (restoreFocus = false) => {
      menu.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
      if (restoreFocus) trigger.focus();
    };
    const sync = () => {
      value.textContent = select.selectedOptions[0]?.textContent?.trim() || '';
      trigger.disabled = select.disabled;
      menu.replaceChildren();
      [...select.options].forEach((option, index) => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'navigator-picker-option';
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', String(option.selected));
        item.disabled = option.disabled;
        item.dataset.optionIndex = String(index);
        item.textContent = option.textContent.trim();
        item.addEventListener('click', () => {
          select.selectedIndex = index;
          select.dispatchEvent(new Event('change', { bubbles: true }));
          close(true);
        });
        menu.append(item);
      });
    };
    const open = (focusSelected = false) => {
      if (trigger.disabled) return;
      menu.hidden = false;
      trigger.setAttribute('aria-expanded', 'true');
      if (focusSelected) {
        const selected = $('.navigator-picker-option[aria-selected="true"]', menu);
        (selected || $('.navigator-picker-option:not(:disabled)', menu))?.focus();
      }
    };
    trigger.addEventListener('click', () => menu.hidden ? open() : close());
    trigger.addEventListener('keydown', (event) => {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
        event.preventDefault();
        open(true);
      }
    });
    menu.addEventListener('keydown', (event) => {
      const options = $$('.navigator-picker-option:not(:disabled)', menu);
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
      if (!picker.contains(event.target)) close();
    });
    select.addEventListener('change', sync);
    new MutationObserver(sync).observe(select, { childList: true, subtree: true, attributes: true });
    sync();
  }
  installNavigatorPicker('navSchema');
  installNavigatorPicker('objectTypeFilter');
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
    plsql: { typedChars: 0, pastedChars: 0, pasteCount: 0, pasteTexts: [], editsAfterPaste: 0, hasExternalInput: false, inputMethods: new Set() },
    sqlplus: { typedChars: 0, pastedChars: 0, pasteCount: 0, pasteTexts: [], editsAfterPaste: 0, hasExternalInput: false, inputMethods: new Set() },
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
    if (method === 'file' || method === 'history_recall' || method === 'daily_task') methods.clear();
    if (method === 'history_recall' || method === 'daily_task') editMetrics[source].hasExternalInput = true;
    methods.add(method);
  }
  function currentInputMethod(source) {
    const methods = [...editMetrics[source].inputMethods];
    if (methods.includes('daily_task')) return 'daily_task';
    if (methods.includes('history_recall')) return 'history_recall';
    if (methods.includes('internal_copy')) return 'internal_copy';
    return methods.length > 1 ? 'mixed' : methods[0] || '';
  }
  function recordPaste(source, characterCount, pastedText) {
    const metrics = editMetrics[source];
    metrics.pasteCount += 1;
    metrics.pastedChars += Math.max(0, Number(characterCount) || 0);
    if (typeof pastedText === 'string' && pastedText && metrics.pasteTexts.length < 100) metrics.pasteTexts.push(pastedText);
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
    const snapshot = { typedChars: metrics.typedChars, pastedChars: metrics.pastedChars, pasteCount: metrics.pasteCount, pasteTexts: metrics.pasteTexts.slice(0, 100), editsAfterPaste: metrics.editsAfterPaste, inputMethod: currentInputMethod(source), inputSourceMap: inputSourceMap || inputSourceRuns(source, sqlplus ? terminalInput.value : editor.value) };
    metrics.typedChars = metrics.pastedChars = metrics.pasteCount = metrics.editsAfterPaste = 0;
    metrics.pasteTexts = [];
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
  const sessionUI = window.WorkspaceSessionUI?.create({editor, form: connectForm, onRestore: () => { markInputMethod('plsql','file'); updateLineNumbers(); updateHighlight(); updateEditorCursorPosition(); }});

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
  const objectSearchSuggestions = window.PortalSearchSuggestions?.register($('#objectSearch'));
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
  const historyPageSize = 50;
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
  let teacherEditorKind = 'history';
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

  function compilerCsrfToken() {
    const cookie = document.cookie.split('; ').find((part) => part.startsWith('ora_csrf='));
    return cookie ? decodeURIComponent(cookie.slice('ora_csrf='.length)) : '';
  }
  async function compilerApiJson(response) {
    try { return await response.json(); }
    catch { return { error: `The server returned an unexpected response (HTTP ${response.status}).` }; }
  }
  function removeSubmittedDailyTask(tasks, currentIndex, taskId) {
    const submittedIndex = tasks.findIndex((task) => String(task.id) === String(taskId));
    if (submittedIndex < 0) return { index: currentIndex, removed: false };
    tasks.splice(submittedIndex, 1);
    const index = submittedIndex < currentIndex ? currentIndex - 1 : Math.min(currentIndex, Math.max(0, tasks.length - 1));
    return { index, removed: true };
  }
  function announceDailyTaskSubmitted(taskId, result = {}) {
    // Incorrect automatic evaluations remain available for an immediate revision.
    if (result.evaluated && result.isCorrect === false) return;
    document.dispatchEvent(new CustomEvent('daily-task-submitted', { detail: { taskId } }));
  }
  const dailyTaskUiStateKey = `oracle-daily-task-ui:${document.body.dataset.userId || 'local'}`;
  function readDailyTaskUiState() {
    try { return JSON.parse(localStorage.getItem(dailyTaskUiStateKey) || '{}') || {}; }
    catch { return {}; }
  }
  function saveDailyTaskUiState(patch) {
    const state = { ...readDailyTaskUiState(), ...patch };
    try { localStorage.setItem(dailyTaskUiStateKey, JSON.stringify(state)); }
    catch { /* The current page remains usable when browser storage is unavailable. */ }
    return state;
  }
  function saveDailyTaskPosition(task, notify = true) {
    const taskId = task ? String(task.id) : '';
    const previousId = String(readDailyTaskUiState().taskId || '');
    saveDailyTaskUiState({ taskId });
    if (notify && taskId && taskId !== previousId) {
      document.dispatchEvent(new CustomEvent('daily-task-position-changed', { detail: { taskId } }));
    }
  }
  function restoreDailyTaskIndex(tasks) {
    const selectedId = String(readDailyTaskUiState().taskId || '');
    const foundIndex = tasks.findIndex((task) => String(task.id) === selectedId);
    return foundIndex >= 0 ? foundIndex : 0;
  }
  function testQuestionKey(item) { return item ? `${item.testId}:${item.questionId}` : ''; }
  function restoreTestQuestionIndex(items) {
    const selectedKey = String(readDailyTaskUiState().testQuestionKey || '');
    const foundIndex = items.findIndex((item) => testQuestionKey(item) === selectedKey);
    return foundIndex >= 0 ? foundIndex : 0;
  }
  function saveTestQuestionPosition(item, notify = true) {
    const key = testQuestionKey(item);
    saveDailyTaskUiState({ testQuestionKey: key });
    if (notify && key) document.dispatchEvent(new CustomEvent('test-question-position-changed', { detail: { key } }));
  }
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
  async function sendCompilerHeartbeat() {
    const csrfToken = compilerCsrfToken();
    try {
      const response = await fetch('/api/portal/activity/heartbeat', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
        body: JSON.stringify({ page: 'compiler', visible: document.visibilityState === 'visible', active: Date.now() - (window.portalLastActivityAt || Date.now()) < 30000 })
      });
      if (response.status === 401) window.location.replace('/portal/');
    } catch { /* The next heartbeat will restore live presence. */ }
  }
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
    if (['sqlplus', 'compiler'].includes(view) && !(isTeacher && view === 'compiler') && !sharedCompilerTabAppearance[view]?.visible) {
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
    if (view === 'history') {
      if (isTeacher) {
        teacherEditorKind = 'history';
        $$('[data-teacher-history-view]').forEach((button) => button.classList.toggle('active', button.dataset.teacherHistoryView === 'history'));
        $$('.history-table-wrap, .history-pagination').forEach((element) => { element.hidden = false; });
        if ($('#clearHistory')) $('#clearHistory').hidden = false;
        if ($('#teacherEditorPanel')) $('#teacherEditorPanel').hidden = true;
      }
      loadCompilerHistory();
    }
    if (view === 'settings' && isStudent) loadStudentEditorSharing();
    if (view === 'sharedCode' && isStudent) loadStudentSharedCode();
    if (view === 'compiler') updateHighlight();
  }

  $$('.ide-nav-item[data-view], .ide-tabs [data-view]').forEach((button) => button.addEventListener('click', () => setView(button.dataset.view)));

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
      objectSearchSuggestions?.setItems([]);
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
    objectSearchSuggestions?.setItems(visible.map((item) => {
      const owner = String(objectField(item, 'OWNER', 'owner'));
      const name = String(objectField(item, 'OBJECT_NAME', 'object_name'));
      const type = String(objectField(item, 'OBJECT_TYPE', 'object_type'));
      return { label: name, detail: `${owner} · ${type}`, value: name };
    }));
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

  let navigatorRefreshGeneration = 0;
  let navigatorRefreshBusy = false;
  async function refreshDatabaseNavigator(plan = null) {
    const overlappingRefresh = navigatorRefreshBusy;
    navigatorRefreshBusy = true;
    const generation = ++navigatorRefreshGeneration;
    const refreshPlan = overlappingRefresh ? { full: true, names: [] } : plan;
    const names=refreshPlan?.names || [];
    const partial=!refreshPlan?.full && names.length>0;
    try {
      $('#navigatorStatus').textContent = 'Loading objects…';
      const response = await fetch('/api/objects'+(partial?'?names='+encodeURIComponent(JSON.stringify(names)):''), { credentials: 'same-origin' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Could not load database objects.');
      if (generation !== navigatorRefreshGeneration) return;
      objectRows = partial ? window.CompilerMergeObjects(objectRows,data.objects||[],names) : data.objects || [];
      applyNavigatorObjects(data);
      if (navigatorCacheId) {
        try { sessionStorage.setItem(objectCacheKey, JSON.stringify({ id: navigatorCacheId, data: { ...data, objects: objectRows } })); } catch {}
      }
    } catch (error) {
      // Keep the last usable navigator when metadata refresh fails.
      if (generation === navigatorRefreshGeneration) {
        const message = error.message || 'Could not load objects';
        $('#navigatorStatus').textContent = message;
        if (!objectRows.length) {
          const empty = document.createElement('div');
          empty.className = 'navigator-empty';
          empty.textContent = `Could not load database objects: ${message}`;
          tree.replaceChildren(empty);
        }
      }
    } finally {
      if (generation === navigatorRefreshGeneration) navigatorRefreshBusy = false;
    }
  }

  const connectionChoiceKey = `oracle-connection-choice-${document.body.dataset.userId || 'current'}-${document.body.dataset.loginId || 'current-login'}`;
  let manualOracleDisconnect = false;
  // A fresh page load should always resume or recreate the Oracle session.
  // Manual disconnect only suppresses retries until this page is refreshed.
  try { sessionStorage.removeItem(connectionChoiceKey); } catch {}
  function requireManualConnection() {
    manualOracleDisconnect = true;
  }
  function pauseReconnectUntilRefresh() {
    manualOracleDisconnect = true;
    try { sessionStorage.removeItem(connectionChoiceKey); } catch {}
  }

  function setStatus(connected, label, state) {
    state = state || (connected ? 'connected' : 'disconnected');
    const wasConnected=$('#connectionDot').classList.contains('connected');
    $('#connectionDot').classList.toggle('connected', state === 'connected');
    $('#connectionDot').classList.toggle('connecting', state === 'connecting');
    $('#connectionDot').classList.toggle('disconnected', state === 'disconnected');
    const [status, ...serviceParts] = label.split('·');
    $('#connectionLabel').textContent = state === 'connected' ? 'Connected' : status.trim();
    $('#connectionService').textContent = connected && serviceParts.length ? serviceParts.join('·').trim() : 'Oracle Database';
    const navigatorHeading = $('.navigator-heading');
    if (navigatorHeading && !$('#collapsedConnectionIndicator')) {
      const indicator = document.createElement('span');
      indicator.id = 'collapsedConnectionIndicator'; indicator.className = 'navigator-collapsed-connection';
      indicator.setAttribute('aria-label', 'Database connection status'); indicator.innerHTML = '<i></i>';
      navigatorHeading.insertBefore(indicator, $('#collapseNavigator'));
    }
    $('#collapsedConnectionIndicator')?.classList.toggle('connected', state === 'connected');
    $('#collapsedConnectionIndicator')?.classList.toggle('connecting', state === 'connecting');
    $('#collapsedConnectionIndicator')?.classList.toggle('disconnected', state === 'disconnected');
    for (const led of [$('#navigatorStatusLed'), $('#terminalLed')]) { led.classList.toggle('online', state === 'connected'); led.classList.toggle('connecting', state === 'connecting'); led.classList.toggle('offline', state === 'disconnected'); }
    if(state !== 'connected' || !wasConnected) $('#navigatorStatus').textContent = state === 'connecting' ? 'Connecting…' : state === 'connected' ? 'Objects ready' : 'Navigator offline';
    $('#terminalStatus').textContent = state === 'connected' ? 'Connected to Oracle' : state === 'connecting' ? 'Connecting to Oracle…' : 'Awaiting connection';
    const control = $('#openConnection');
    control.textContent = connected ? 'Disconnect' : 'Connect';
    control.dataset.connected = String(connected);
    control.setAttribute('aria-label', connected ? 'Disconnect from Oracle' : 'Connect to Oracle');
    sessionUI?.setConnection(connected,label);
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
    return window.WorkbenchPro?.grid ? window.WorkbenchPro.grid(table, columns, rows) : table;
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
    const activeResultTab = $('.result-tabs [data-result].active')?.dataset.result;
    if (activeResultTab === 'plan' && (resultData.queryDiagnostics || resultData.queryPlanTree)) {
      renderPlanPanel(body);
      return;
    }
    if (resultData.queryDiagnostics) {
      const empty = document.createElement('div');
      empty.className = 'result-empty';
      empty.textContent = 'Plan diagnostics are available in the Explain Plan tab.';
      body.append(empty);
      return;
    }
    if (resultData.queryPlanTree && !resultData.columns?.length) {
      const empty = document.createElement('div');
      empty.className = 'result-empty';
      empty.textContent = 'Execution plan is available in the Explain Plan tab.';
      body.append(empty);
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

  function renderPlanPanel(body = $('#resultBody')) {
    body.replaceChildren();
    if (!resultData) {
      const empty = document.createElement('div');
      empty.className = 'result-empty';
      empty.textContent = 'Run Explain Plan to inspect the selected SQL statement.';
      body.append(empty);
      return;
    }
    if (resultData.error) {
      const error = document.createElement('pre');
      error.className = 'result-output error';
      error.textContent = resultData?.error || 'Run Explain Plan to inspect the selected SQL statement.';
      body.append(error);
      return;
    }
    const panel = document.createElement('section');
    panel.className = 'query-diagnostics';
    const heading = document.createElement('div');
    heading.className = 'query-diagnostics-heading';
    const title = document.createElement('h2');
    title.textContent = 'Execution plan';
    const note = document.createElement('span');
    note.textContent = 'Estimated plan';
    heading.append(title, note);
    panel.append(heading);
    if (resultData.diagnostics?.length) {
      const diagnosticList = document.createElement('ul');
      diagnosticList.className = 'query-diagnostics-list';
      resultData.diagnostics.forEach((item) => {
        const li = document.createElement('li');
        li.dataset.severity = item.severity || 'info';
        li.textContent = item.message;
        diagnosticList.append(li);
      });
      panel.append(diagnosticList);
    }
    const treeHeading = document.createElement('h3');
    treeHeading.textContent = 'Plan operations';
    panel.append(treeHeading);
    const nodes = resultData.planTree || resultData.queryPlanTree || [];
    if (nodes.length) {
      const makeTree = (items) => {
        const list = document.createElement('ul');
        list.className = 'query-plan-tree';
        items.forEach((item) => {
          const li = document.createElement('li');
          const row = document.createElement('div');
          row.className = 'query-plan-node';
          const op = document.createElement('strong');
          op.textContent = item.operation || 'Operation';
          row.append(op);
          const estimate = [];
          if (item.estimatedRows !== null && item.estimatedRows !== undefined) estimate.push(`Rows ${item.estimatedRows}`);
          if (item.cost !== null && item.cost !== undefined) estimate.push(`Cost ${item.cost}`);
          if (estimate.length) {
            const meta = document.createElement('span');
            meta.textContent = estimate.join(' · ');
            row.append(meta);
          }
          li.append(row);
          if (item.children?.length) li.append(makeTree(item.children));
          list.append(li);
        });
        return list;
      };
      panel.append(makeTree(nodes));
    } else {
      const empty = document.createElement('p');
      empty.className = 'muted-note';
      empty.textContent = resultData.plan?.length ? 'Oracle did not return structured plan rows; see the plan text below.' : 'No plan is available. Run Explain Plan for a SQL statement.';
      panel.append(empty);
    }
    if (resultData.plan?.length) {
      const rawDetails = document.createElement('details');
      rawDetails.className = 'query-plan-raw';
      const summary = document.createElement('summary');
      summary.textContent = 'Oracle plan text';
      const raw = document.createElement('pre');
      raw.className = 'query-plan-output';
      raw.textContent = resultData.plan.join('\n');
      rawDetails.append(summary, raw);
      panel.append(rawDetails);
    }
    body.append(panel);
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
  const terminalCommandHistoryKey = terminalQueueKey + ':command-history';
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
  function persistTerminalCommandHistory() {
    try { sessionStorage.setItem(terminalCommandHistoryKey, JSON.stringify(terminalCommandHistory)); } catch {}
  }
  function clearTerminalDisplay() {
    persistTerminalCommandHistory();
    $('#terminalOutput').replaceChildren(terminalPrompt);
    terminalInput.value = '';
    terminalInput.style.height = '';
    terminalHistoryIndex = terminalCommandHistory.length;
    terminalHistoryDraft = '';
    updateTerminalInputHighlight();
    try { sessionStorage.setItem(terminalTranscriptKey, '[]'); } catch {}
    saveTerminalQueue();
    terminalInput.focus();
  }
  function restoreTerminalCommandHistory(entries) {
    terminalCommandHistory = (entries || [])
      .filter(item => typeof item?.sql === 'string' && item.sql.trim() && !item.data?.commentOnly)
      .map(item => item.sql);
    try {
      const saved = JSON.parse(sessionStorage.getItem(terminalCommandHistoryKey) || 'null');
      if (Array.isArray(saved)) terminalCommandHistory = saved.filter(sql => typeof sql === 'string' && sql.trim());
    } catch {}
    const limit = personalTerminalBufferEntries || defaultTerminalBufferEntries;
    if (terminalCommandHistory.length > limit) terminalCommandHistory = terminalCommandHistory.slice(-limit);
    terminalHistoryIndex = terminalCommandHistory.length;
    terminalHistoryDraft = '';
  }
  window.addEventListener('pagehide', () => {
    try {
      trimTerminalBuffer();
      persistTerminalCommandHistory();
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
        if (data.queryPlanTree) setResultTab('plan');
        else renderResult();
      }
      sessionUI?.updateTransactions({[sqlplus ? 'sqlcl' : 'worksheet']: data.transactionPending ?? null});
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
          data.statementResults.forEach((item) => appendTerminalResult(item.statement, {
            ...data, ...item,
            durationMs: item.durationMs ?? (data.statementResults.length === 1 ? data.durationMs : undefined),
          }, item.error || null));
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
        const refresh = refreshDatabaseNavigator(refreshPlan);
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
      text.textContent=window.CompilerWorkspace.outputText(sql,{...data,statementResults:undefined});entry.append(text);if(data?.columns?.length){const rich=document.createElement('details'),summary=document.createElement('summary');summary.textContent='Interactive data grid';rich.append(summary,renderTable(data.columns,data.rows||[]));entry.append(rich);}
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
    if (!data?.pending && !data?.commentOnly && typeof data?.durationMs === 'number' && Number.isFinite(data.durationMs) && data.durationMs >= 0) {
      const timing = document.createElement('div');
      timing.className = 'terminal-execution-time';
      timing.textContent = `Execution time: ${Math.round(data.durationMs)} ms`;
      entry.append(timing);
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
    const data = await response.json();
    sessionUI?.setDetails(data);
    return data;
  }

  async function connect(payload, { save = false } = {}) {
    const error = $('#connectError');
    const button = $('.dialog-submit', connectForm);
    if (!String(payload.user || '').trim() || !String(payload.password || '').trim() || !String(payload.connectString || '').trim()) {
      error.textContent = 'Enter your Oracle username, password, and connect string.';
      if (!connectDialog.open) connectDialog.showModal();
      return false;
    }
    error.textContent = 'Connecting…';
    button.disabled = true;
    try {
      const response = await fetch('/api/connect', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, saveCredentials: save })
      });
      const data = await readConnectionResponse(response);
      sessionUI?.updateTransactions(data.transactions);
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

  let savedOracleConnections = [];
  const savedSelector = $('#savedOracleConnections');
  const saveCredentialsToggle = $('#saveCredentials');
  if (saveCredentialsToggle && !savedSelector?.value) saveCredentialsToggle.checked = true;
  const savedLabel = savedSelector?.closest('label');
  const deleteSavedConnection = document.createElement('button');
  deleteSavedConnection.type = 'button'; deleteSavedConnection.className = 'tool-button'; deleteSavedConnection.textContent = 'Delete selected saved connection'; deleteSavedConnection.disabled = true;
  if (savedSelector?.parentElement) { const savedControls=document.createElement('div'); savedControls.className='saved-connection-controls'; savedSelector.parentElement.insertBefore(savedControls,savedSelector); savedControls.append(savedSelector,deleteSavedConnection); }
  const connectionSettingsPanel=document.querySelector('[data-panel="settings"]');
  if (savedLabel && connectionSettingsPanel) {
    savedLabel.hidden = true;
    const manageCard=document.createElement('article'); manageCard.className='settings-card saved-connections-settings'; manageCard.innerHTML='<div><strong>Saved Oracle connections</strong><small>Manage saved database profiles without cluttering the connection form.</small></div><button type="button" class="tool-button">Manage connections</button>';
    manageCard.querySelector('button').addEventListener('click',async()=>{savedLabel.hidden=false;await loadSavedCredentialDetails();connectDialog.showModal();savedSelector.focus();});
    const cards=connectionSettingsPanel.querySelector('.settings-cards'); (cards||connectionSettingsPanel).prepend(manageCard);
    connectDialog.addEventListener('close',()=>{savedLabel.hidden=true;});
  }
  savedSelector?.addEventListener('input', () => { deleteSavedConnection.disabled = !savedSelector.value; });
  const usernameInput = connectForm.elements.user;
  usernameInput?.addEventListener('input', () => { if (savedSelector?.value) { savedSelector.value = ''; deleteSavedConnection.disabled = true; $('#saveCredentials').checked = true; } });
  deleteSavedConnection.addEventListener('click', async () => {
    const id = Number(savedSelector?.value); if (!id || !confirm('Delete this saved Oracle connection?')) return;
    deleteSavedConnection.disabled = true;
    try {
      const response = await fetch('/api/connect-saved', {method:'DELETE',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({connectionId:id})});
      const data = await readConnectionResponse(response); if (!response.ok) throw new Error(data.error || 'Unable to delete saved connection.');
      savedOracleConnections = savedOracleConnections.filter(item => item.id !== id); savedSelector.replaceChildren(new Option('New connection','')); savedOracleConnections.forEach(item => savedSelector.append(new Option(`${item.name} · ${item.user}`, String(item.id))));
      fillCredentials({saved:false}); showToast('Saved Oracle connection deleted.');
    } catch (errorValue) { showToast(errorValue.message); deleteSavedConnection.disabled = false; }
  });
  $('#savedOracleConnections')?.addEventListener('change', () => {
    deleteSavedConnection.disabled = !savedSelector.value;
    const saved = savedOracleConnections.find(item => String(item.id) === $('#savedOracleConnections').value);
    if (saved) {
      fillCredentials({...saved, saved: true});
      connectForm.elements.connectionName.value = saved.name;
    } else {
      fillCredentials({saved: false});
      connectForm.elements.user.value = '';
      connectForm.elements.connectString.value = '';
      connectForm.elements.connectionName.value = '';
      delete connectForm.dataset.savedOracleUser;
      delete connectForm.dataset.savedOracleConnectString;
    }
  });
  function fillCredentials(data) {
    const help = $('#freeSqlConnectionHelp');
    if (help) help.open = false;
    if (data.saved && data.user) {
      connectForm.dataset.savedOracleUser = data.user;
      connectForm.dataset.savedOracleConnectString = data.connectString || '';
    }
    if (data.user) connectForm.elements.user.value = data.user;
    if (data.connectString) connectForm.elements.connectString.value = data.connectString;
    $('#saveCredentials').checked = true;
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
        savedOracleConnections = data.connections || [];
        const selector = $('#savedOracleConnections');
        const previous = selector.value;
        selector.replaceChildren(new Option('New connection', ''), ...savedOracleConnections.map(item => new Option(`${item.name} · ${item.user}`, String(item.id))));
        const selected = savedOracleConnections.find(item => String(item.id) === previous)
          || savedOracleConnections.find(item => item.user === data.user && item.connectString === data.connectString);
        if (selected) {
          selector.value = String(selected.id);
          fillCredentials({...selected, saved: true});
          connectForm.elements.connectionName.value = selected.name;
          return;
        }
        if (data.saved) fillCredentials(data);
        else {
      $('#saveCredentials').checked = true;
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
      const response = await fetch('/api/connect-saved', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify($('#savedOracleConnections')?.value ? {connectionId: Number($('#savedOracleConnections').value)} : {}) });
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
      setStatus(false, 'Connecting to Oracle…', 'connecting');
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
          // Any failed automatic reconnect needs an actionable login dialog.  A
          // connection can fail for network, TLS, service-name, or database
          // availability reasons too; leaving the workspace in a disconnected
          // state hides the only useful recovery path from the user.
          pauseReconnectUntilRefresh();
          setStatus(false, 'Saved Oracle connection failed', 'disconnected');
          $('#connectError').textContent = connectionDiagnosis(data.error);
          showConnectionProblem(data.error);
          await loadSavedCredentialDetails();
          if (!connectDialog.open) connectDialog.showModal();
          showToast(data.error || 'Saved Oracle connection failed. Check the credentials in your profile.');
          return false;
        }
        if (!response.ok) throw new Error(data.error || 'Could not check saved credentials.');
        if (legacyCredentials) {
          fillCredentials(legacyCredentials);
        }
        pauseReconnectUntilRefresh();
        $('#connectError').textContent='';
        await loadSavedCredentialDetails();
        if (!connectDialog.open) connectDialog.showModal();
        return false;
      } catch(error) {
        pauseReconnectUntilRefresh();
        setStatus(false, 'Oracle connection unavailable');
        await loadSavedCredentialDetails();
        $('#connectError').textContent = connectionDiagnosis(error.message);
        showConnectionProblem(error.message);
        if (!connectDialog.open) connectDialog.showModal();
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
        sessionUI?.setDetails(data);
        sessionUI?.updateTransactions(data.transactions);
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
    const tokens = window.CompilerEditorAssist.syntaxTokens(source);
    let html = '', offset = 0;
    for (const token of tokens) {
      html += escapeHtml(source.slice(offset, token.start));
      const type = token.syntax;
      html += type ? `<span class="sql-${type}">${escapeHtml(token.text)}</span>` : escapeHtml(token.text);
      offset = token.end;
    }
    return html + escapeHtml(source.slice(offset));
  }
  function renderBracketHighlight(input) {
    const source=input.value||' ',caret=input.selectionStart;
    const {active,unmatched}=window.CompilerEditorAssist.bracketPairs(source,caret);
    const tokens=window.CompilerEditorAssist.syntaxTokens(source);
    const marks=new Map();
    if(active){marks.set(active.start,'sql-bracket-match');marks.set(active.end,'sql-bracket-match');}
    for(const position of unmatched)if(position===caret||position===caret-1)marks.set(position,'sql-bracket-unmatched');
    let offset=0,html='';
    function segment(text,start,type){
      let result='';
      for(let i=0;i<text.length;i++){
        const escaped=escapeHtml(text[i]),mark=marks.get(start+i);
        result+=mark?`<span class="${mark}">${escaped}</span>`:escaped;
      }
      return type?result.split('\n').map(line=>`<span class="sql-${type}">${line}</span>`).join('\n'):result;
    }
    for(const token of tokens){html+=segment(source.slice(offset,token.start),offset,'');html+=segment(token.text,token.start,token.syntax);offset=token.end;}
    html+=segment(source.slice(offset),offset,'');
    if(active){
      const first=source.slice(0,active.start).split('\n').length-1,last=source.slice(0,active.end).split('\n').length-1;
      if(last>first){
        // The guide is painted in the existing overlay and adds no text width.
        const lineStart=source.lastIndexOf('\n',active.start-1)+1,column=active.start-lineStart;
        html=html.split('\n').map((line,index)=>`<span class="sql-bracket-guide-line"${index>first&&index<=last?` style="--bracket-column:${column}ch"`:''}>${line||'&#8203;'}</span>`).join('');
      }
    }
    return html;
  }
  function updateHighlight() {
    $('#syntaxHighlight').innerHTML = renderBracketHighlight(editor);
  }
  function updateTerminalInputHighlight() {
    const highlight = $('#terminalInputHighlight');
    if (highlight) highlight.innerHTML = renderBracketHighlight(terminalInput);
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
      recordPaste('plsql', pastedText.length, pastedText);
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
  ['click', 'keyup', 'select'].forEach((eventName) => editor.addEventListener(eventName, () => {updateEditorCursorPosition();updateHighlight();}));
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
    const maxFontSize = shell.classList.contains('fullscreen-mode') ? 36 : 30;
    editorFontSize = Math.max(10, Math.min(maxFontSize, editorFontSize + delta));
    applyEditorFontSize();
    localStorage.setItem('ora-plsql-font-size', String(editorFontSize));
  }
  $('#editorFontDown').addEventListener('click', () => changeEditorFont(-1));
  $('#editorFontUp').addEventListener('click', () => changeEditorFont(1));
  try {
    const savedEditorFontSize = Number(localStorage.getItem('ora-plsql-font-size'));
    if (Number.isFinite(savedEditorFontSize) && savedEditorFontSize >= 10 && savedEditorFontSize <= 36) editorFontSize = savedEditorFontSize;
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
  $('#explainPlan').addEventListener('click', async () => {
    const selected = window.CompilerWorkspace.target(editor.value, editor.selectionStart, editor.selectionEnd, false);
    const sql = selected.sql.trim();
    if (!sql) { showToast('Write or select one SQL statement first.'); return; }
    const binds = await collectBinds(sql);
    if (binds === null) return;
    const button = $('#explainPlan');
    const started = performance.now();
    button.disabled = true;
    button.textContent = '⌕ Analyzing…';
    $('#resultRuntime').textContent = 'Analyzing…';
    try {
      const response = await fetch('/api/explain', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sql, binds })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Could not explain this query.');
      resultData = { ...data, queryDiagnostics: true };
      $('#resultRuntime').textContent = `Plan · ${Math.round(performance.now() - started)} ms`;
      setResultTab('plan');
      renderResult();
    } catch (error) {
      resultData = { error: error.message };
      $('#resultRuntime').textContent = 'Plan unavailable';
      setResultTab('plan');
      renderResult();
    } finally {
      button.disabled = false;
      button.textContent = '⌕ Explain plan';
    }
  });
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
  function scopedEditorExport(terminal, mode, scope = 'all') {
    if (!terminal) {
      const sql = editor.value || '';
      if (mode === 'statements') return sql.replace(/;[ \t]+(?=\S)/g, ';\r\n');
      const output = resultData ? (resultData.error || [...(resultData.outputLines || []), resultData.message || 'Statement completed.'].filter(Boolean).join('\n')) : '';
      return output ? `${sql.trim()}\n\n-- Output / Error\n${output}` : sql;
    }
    let entries = [...$('#terminalOutput').querySelectorAll('.terminal-entry')]
      .filter(node => node._outputSnapshot && !node._outputSnapshot.data?.pending && !node._outputSnapshot.data?.commentOnly)
      .map(node => {
        const snapshot = node._outputSnapshot;
        const sql = snapshot.sql || '';
        if (mode === 'statements') return sql;
        const rendered = node.innerText || '';
        const detail = rendered.startsWith(sql) ? rendered.slice(sql.length).replace(/^\s+/, '') : rendered;
        return detail ? `${sql}\n\n-- Output / Error\n${detail}` : sql;
      });
    if (scope === 'last') entries = entries.slice(-1);
    if (scope === 'success') entries = entries.filter(text => !/error|failed/i.test(text));
    if (scope === 'failed') entries = entries.filter(text => /error|failed/i.test(text));
    return entries.map(value => mode === 'statements' ? String(value).replace(/;\s+(?=(?:select|with|insert|update|delete|merge|create|alter|drop|begin|declare)\b)/gi, ';\r\n') : value).join('\r\n');
  }
  const exportScopeMenu = document.createElement('div');
  exportScopeMenu.className = 'export-scope-menu';
  exportScopeMenu.setAttribute('popover', 'auto');
  exportScopeMenu.setAttribute('aria-label', 'Choose statements to export');
  let exportScopeTrigger = null;
  function escapeExportHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[char]));
  }
  function formattedExportHtml(text) {
    const lines = String(text || '').split(/\r?\n/);
    const outputStart = lines.findIndex(line => /^-- Output \/ Error$/.test(line));
    const sqlLines = outputStart >= 0 ? lines.slice(0, outputStart) : lines;
    const outputLines = outputStart >= 0 ? lines.slice(outputStart + 1) : [];
    const sqlHtml = renderSyntaxHighlight(sqlLines.join('\n'));
    const outputHtml = outputLines.map(line => {
      if (/^\s*(select|with|insert|update|delete|merge|create|alter|drop|begin|declare)\b/i.test(line)) return renderSyntaxHighlight(line);
      const kind = /^\s*(ORA-\d+|SP2-|PLS-|Help:)/i.test(line) ? 'error-line' : 'output-line';
      return `<span class="${kind}">${escapeExportHtml(line)}</span>`;
    }).join('\n');
    return `<!doctype html><meta charset="utf-8"><title>SQL export</title><style>@page{margin:18mm}*{-webkit-print-color-adjust:exact;print-color-adjust:exact}body{font:14px ui-monospace,Consolas,monospace;background:#fff;color:#172033;padding:24px;white-space:pre-wrap}.sql{color:#172033}.sql-keyword{color:#0757c9}.sql-string{color:#16863d}.sql-number{color:#b35c00}.sql-comment{color:#6f7785}.output{color:#172033;border-top:1px solid #b9c2d0;margin-top:18px;padding-top:14px}.output-line{color:#172033}.error-line{color:#d11f3a;font-weight:700}</style><div class="sql">${sqlHtml}</div>${outputLines.length ? `<div class="output">${outputHtml}</div>` : ''}`;
  }
  function inlineSqlClipboardHtml(text) {
    return `<div style="font-family:Consolas,'Courier New',monospace;font-size:11pt;white-space:pre-wrap;color:#172033">${renderSyntaxHighlight(String(text || '').replace(/\r\n?/g, '\n')).replace(/\n/g, '<br>').replace(/\t/g, '&nbsp;&nbsp;&nbsp;&nbsp;').replace(/  /g, '&nbsp; ').replace(/class="sql-keyword"/g, 'style="color:#0757c9"').replace(/class="sql-string"/g, 'style="color:#16863d"').replace(/class="sql-number"/g, 'style="color:#b35c00"').replace(/class="sql-comment"/g, 'style="color:#6f7785"')}</div>`;
  }
  async function writeRichSqlClipboard(text, html) {
    if (navigator.clipboard?.write && window.ClipboardItem) {
      await navigator.clipboard.write([new ClipboardItem({
        'text/plain': new Blob([text], { type: 'text/plain' }),
        'text/html': new Blob([html], { type: 'text/html' })
      })]);
      return;
    }
    // The LAN hostname is often an insecure browser context, where the async
    // Clipboard API is unavailable. A trusted Copy click can still use copy.
    const holder = document.createElement('div');
    holder.contentEditable = 'true';
    holder.style.cssText = 'position:fixed;left:-10000px;top:0;opacity:0';
    holder.innerHTML = html;
    document.body.append(holder);
    const selection = window.getSelection();
    const previous = selection.rangeCount ? selection.getRangeAt(0).cloneRange() : null;
    const range = document.createRange();
    range.selectNodeContents(holder);
    selection.removeAllRanges();
    selection.addRange(range);
    const copied = document.execCommand('copy');
    selection.removeAllRanges();
    if (previous) selection.addRange(previous);
    holder.remove();
    if (!copied) throw new Error('Browser could not copy formatted text.');
  }
  document.addEventListener('copy', event => {
    if (!event.clipboardData) return;
    let text = '';
    if (document.activeElement === editor) {
      text = editor.value.slice(editor.selectionStart, editor.selectionEnd);
    } else if (document.activeElement === terminalInput) {
      text = terminalInput.value.slice(terminalInput.selectionStart, terminalInput.selectionEnd);
    } else {
      const selection = window.getSelection();
      if (selection?.rangeCount && terminalOutput.contains(selection.getRangeAt(0).commonAncestorContainer)) text = selection.toString();
    }
    if (!text) return;
    event.clipboardData.setData('text/plain', text);
    event.clipboardData.setData('text/html', inlineSqlClipboardHtml(text));
    event.preventDefault();
  });
  terminalInput.addEventListener('copy', event => {
    if (!event.clipboardData) return;
    const text = terminalInput.value.slice(terminalInput.selectionStart, terminalInput.selectionEnd);
    if (!text) return;
    event.clipboardData.setData('text/plain', text);
    event.clipboardData.setData('text/html', inlineSqlClipboardHtml(text));
    rememberInternalCopy(text);
    event.preventDefault();event.stopImmediatePropagation();
  });
  async function copyFormatted(text, title) {
    text = String(text ?? '').replace(/\r?\n/g, '\r\n');
    if (/Statements only/i.test(title)) text = text.replace(/;[ \t]+(?=\S)/g, ';\r\n');
    await writeRichSqlClipboard(text, inlineSqlClipboardHtml(text));
    showToast(`${title} copied with formatting.`);
  }
  async function saveExportFile(blob, filename) {
    if (window.showSaveFilePicker) {
      const handle = await window.showSaveFilePicker({ suggestedName: filename, types: [{ description: 'Export file', accept: { [blob.type.split(';')[0]]: [`.${filename.split('.').pop()}`] } }] });
      const writable = await handle.createWritable(); await writable.write(blob); await writable.close();
      return;
    }
    const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function chooseExportOption(title, options, anchor = exportScopeTrigger) {
    return new Promise(resolve => {
      const dialog = document.createElement('dialog'); dialog.className = 'export-choice-dialog';
      dialog.innerHTML = `<form method="dialog"><h3>${title}</h3><div class="export-choice-list">${options.map(([value, label]) => `<button type="button" data-value="${value}">${label}</button>`).join('')}</div><button type="button" data-cancel>Cancel</button></form>`;
      dialog.querySelectorAll('[data-value]').forEach(button => button.addEventListener('click', () => { dialog.close(button.dataset.value); dialog.remove(); resolve(button.dataset.value); }));
      dialog.querySelector('[data-cancel]').addEventListener('click', () => { dialog.close(); dialog.remove(); resolve(null); });
      dialog.addEventListener('cancel', () => { dialog.remove(); resolve(null); }, { once: true });
      document.body.append(dialog); dialog.showModal();
      if (anchor) {
        const rect = anchor.getBoundingClientRect();
        const width = dialog.getBoundingClientRect().width;
        dialog.style.position = 'fixed';
        dialog.style.margin = '0';
        dialog.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - width - 8))}px`;
        dialog.style.top = `${Math.max(8, Math.min(rect.bottom + 6, innerHeight - dialog.getBoundingClientRect().height - 8))}px`;
      }
    });
  }
  const exportDefaultFormat = (() => { try { const value = localStorage.getItem('oracle-export-default-format') || 'sql'; return ['sql','pdf','rtf','md'].includes(value) ? value : 'sql'; } catch { return 'sql'; } })();
  const settingsPanel = document.querySelector('[data-panel="settings"]');
  if (settingsPanel && !$('#exportDefaultFormat')) {
    const card = document.createElement('article'); card.className = 'settings-card';
    card.innerHTML = '<div><strong>Export default format</strong><small>Default format for Copy, Download, and Share exports.</small></div><select id="exportDefaultFormat" aria-label="Export default format"><option value="sql">SQL</option><option value="pdf">PDF</option><option value="rtf">RTF</option><option value="md">Markdown</option></select>';
    card.querySelector('select').value = exportDefaultFormat;
    card.querySelector('select').addEventListener('change', event => { try { localStorage.setItem('oracle-export-default-format', event.target.value); } catch {} });
    (settingsPanel.querySelector('.settings-cards') || settingsPanel).prepend(card);
  }
  for (const [mode, label] of [['statements', 'Statements only'], ['full', 'Statements + output/errors']]) {
    const option = document.createElement('button');
    option.type = 'button';
    option.textContent = label;
    option.addEventListener('click', async () => {
      const trigger = exportScopeTrigger;
      exportScopeMenu.hidePopover();
      if (!trigger) return;
      const scope = await chooseExportOption('Choose records to export', [['last','Last'],['success','Success'],['failed','Failed'],['all','All']]);
      if (!['last','success','failed','all'].includes(scope)) return;
      const action = trigger.dataset.terminalAction || trigger.dataset.plsqlAction || trigger.dataset.historyAction;
      const format = action === 'download' ? await chooseExportOption('Choose file format', [['sql',`SQL${exportDefaultFormat === 'sql' ? ' · Default' : ''}`],['pdf',`PDF${exportDefaultFormat === 'pdf' ? ' · Default' : ''}`],['rtf',`RTF${exportDefaultFormat === 'rtf' ? ' · Default' : ''}`],['md',`Markdown${exportDefaultFormat === 'md' ? ' · Default' : ''}`]]) : 'sql';
      const normalizedFormat = format === 'markdown' ? 'md' : format;
      if (!['sql','pdf','rtf','md'].includes(normalizedFormat)) return;
      const terminal = Boolean(trigger.dataset.terminalAction);
      if (trigger.dataset.historyAction) {
        let records;
        try { records = await fetchAllCompilerHistory(); }
        catch (error) { showToast(error.message || 'Could not load SQL history for export.'); return; }
        if (!records.length) { showToast('There is no SQL history matching these filters.'); return; }
        if (scope === 'last') records = records.slice(-1);
        if (scope === 'success') records = records.filter(entry => entry.status !== 'error');
        if (scope === 'failed') records = records.filter(entry => entry.status === 'error');
        const headers = ['Time', 'Student', 'Source', 'Query', 'Result', 'Output / Error'];
        const rows = [headers, ...records.map(entry => {
          const detail = entry.error || entry.output || entry.message || (Array.isArray(entry.outputLines) ? entry.outputLines.join('\n') : '');
          return [entry.executedAt, entry.studentName || 'You', compilerSourceLabel(entry.source), entry.sql || '', entry.status === 'error' ? 'Failed' : 'Success', detail];
        })];
        const text = mode === 'statements' ? records.map(entry => entry.sql || '').join('\n\n') : rows.map(row => row.join('\t')).join('\n');
        if (!text.trim()) { showToast('Nothing available to export.'); return; }
        try {
          if (action === 'download') {
            if (format === 'pdf') { const printWindow = window.open('', '_blank'); if (printWindow) { printWindow.document.write(formattedExportHtml(text)); printWindow.document.close(); printWindow.focus(); setTimeout(() => printWindow.print(), 100); } return; }
            const csv = rows.map(row => row.map(value => `"${String(value ?? '').replaceAll('"', '""')}"`).join(',')).join('\r\n');
            const payload = format === 'md' ? `\n\`\`\`sql\n${text}\n\`\`\`\n` : format === 'rtf' ? `{\\rtf1\\ansi ${text.replace(/[\\{}]/g, '\\$&').replace(/\r?\n/g, '\\line ')}}` : format === 'docx' ? formattedExportHtml(text) : mode === 'statements' ? text : formattedExportHtml(text);
            const blob = new Blob([payload], { type: format === 'rtf' ? 'application/rtf' : format === 'md' ? 'text/markdown;charset=utf-8' : format === 'sql' ? 'text/sql;charset=utf-8' : 'text/html;charset=utf-8' });
            await saveExportFile(blob, `sql-history-${mode}.${format}`);
          } else if (action === 'share') {
            if (navigator.share) await navigator.share({ title: 'SQL History', text });
            else { await navigator.clipboard.writeText(text); showToast(`${label} copied for sharing.`); }
          }
          else { await copyFormatted(text, label); }
        } catch (error) { if (error.name !== 'AbortError') showToast(error.message || 'Export failed.'); }
        return;
      }
      const text = scopedEditorExport(terminal, mode, scope);
      if (!text.trim()) { showToast('Nothing available to export.'); return; }
      try {
        if (action === 'download') {
          if (format === 'pdf') { const printWindow = window.open('', '_blank'); if (printWindow) { printWindow.document.write(formattedExportHtml(text)); printWindow.document.close(); printWindow.focus(); setTimeout(() => printWindow.print(), 100); } return; }
          const formatted = mode === 'full';
          const payload = format === 'md' ? `\
\n\`\`\`sql\n${text}\n\`\`\`\n` : format === 'rtf' ? `{\\rtf1\\ansi\\deff0 ${text.replace(/[\\{}]/g, '\\$&').replace(/\r?\n/g, '\\line ')}}` : (formatted ? formattedExportHtml(text) : text);
          const mime = format === 'rtf' ? 'application/rtf' : format === 'md' ? 'text/markdown;charset=utf-8' : formatted ? 'text/html;charset=utf-8' : (terminal ? 'text/plain;charset=utf-8' : 'text/sql;charset=utf-8');
          await saveExportFile(new Blob([payload], { type: mime }), `${terminal ? 'sqlplus-session' : 'query'}-${mode}.${format}`);
        } else if (action === 'share') {
          if (navigator.share) await navigator.share({ title: terminal ? 'SQLCL session' : 'SQL WorkSheet statements', text });
          else { await navigator.clipboard.writeText(text); showToast(`${label} copied for sharing.`); }
        } else {
          await copyFormatted(text, label);
        }
      } catch (error) { if (error.name !== 'AbortError') showToast(error.message || 'Export failed.'); }
    });
    exportScopeMenu.append(option);
  }
  document.body.append(exportScopeMenu);
  exportScopeMenu.addEventListener('toggle', () => {
    exportScopeTrigger?.setAttribute('aria-expanded', String(exportScopeMenu.matches(':popover-open')));
  });
  $$('[data-plsql-action], [data-terminal-action], [data-history-action]').forEach(trigger => {
      const action = trigger.dataset.plsqlAction || trigger.dataset.terminalAction || trigger.dataset.historyAction;
    if (!['copy', 'download', 'share'].includes(action)) return;
    trigger.setAttribute('aria-haspopup', 'true');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.addEventListener('click', event => {
      event.stopImmediatePropagation();
      if (action === 'share') {
        event.preventDefault();
        const title = trigger.dataset.historyAction ? 'SQL History' : trigger.dataset.terminalAction ? 'SQLCL session' : 'SQL WorkSheet statements';
        let text = trigger.dataset.terminalAction ? $('#terminalOutput')?.innerText?.trim() : trigger.dataset.plsqlAction ? editor.value : history.map(entry => entry.sql || '').filter(Boolean).join('\n\n');
        if (trigger.dataset.historyAction) text = historyRecords.map(entry => entry.sql || '').filter(Boolean).join('\n\n');
        if (!text) { showToast('There is no content available to share.'); return; }
        if (navigator.share) {
          try {
            const payload = { title, text };
            if (navigator.canShare && !navigator.canShare(payload)) throw new Error('This browser cannot share this text.');
            const pendingShare = navigator.share(payload);
            pendingShare.catch(error => { if (error.name !== 'AbortError') showToast(error.message || 'Could not open the Share panel.'); });
          } catch (error) { showToast(error.message || 'Could not open the Share panel.'); }
        }
        else navigator.clipboard.writeText(text).then(() => showToast('Content copied for sharing.')).catch(error => showToast(error.message || 'Could not copy content.'));
        return;
      }
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
    if (action === 'share') return;
    const text = editor.value;
    if (action === 'copy') {
      await writeRichSqlClipboard(text, inlineSqlClipboardHtml(text));
      showToast('SQL WorkSheet editor copied with formatting.');
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
    $$('.result-tabs [data-result]').forEach((button) => {
      const active = button.dataset.result === tab;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
    });
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
    } else if (tab === 'plan') renderPlanPanel();
    else renderResult();
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
    if (selectedStudent && selectedStudent !== 'self') params.set('student', selectedStudent);
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
        const validSelected = selectedStudent === 'self' || historyStudents.some((student) => String(student.id) === selectedStudent);
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
    if (isTeacher && $('#historyStudentFilter')?.value === 'self') params.set('own', '1');
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
    const matches = [...historyStudents];
    const selectedStudent = historyStudents.find((student) => String(student.id) === selectedId);
    if (selectedStudent && !matches.some((student) => student.id === selectedStudent.id)) matches.unshift(selectedStudent);
    studentFilter.replaceChildren(new Option(historyStudentScope === 'all' ? 'All students' : 'All assigned students', ''));
    if (isTeacher) studentFilter.add(new Option('Teacher · Self history', 'self'));
    matches.forEach((student) => studentFilter.add(new Option(`${student.name}${student.number ? ` · ${student.number}` : ''}${student.isActive ? '' : ' · Inactive'}`, String(student.id))));
    studentFilter.value = selectedId === 'self' || selectedStudent ? selectedId : '';
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
      window.HistorySql.render(queryText, entry.sql || '');
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
      makeAction('Copy query', '⧉', async () => { if (entry.source === 'SQL*Plus') rememberInternalCopy(entry.sql || ''); const copied = await window.HistorySql.copy(entry.sql || ''); if (entry.source === 'SQL*Plus') rememberInternalCopy(copied); showToast('Formatted SQL copied.'); });
      if ($('#historyStudentFilter') && $('#historyStudentFilter').value !== 'self') {
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
  $$('[data-history-action]').forEach((button) => button.addEventListener('click', async () => {
    const action = button.dataset.historyAction;
    if (action === 'share') return;
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
      if (action === 'copy') { await window.HistorySql.copy(records.map(entry => entry.sql || '').join('\n\n')); showToast('Formatted SQL history copied.'); }
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
    if (selectedStudent && selectedStudent !== 'self') base.set('student', selectedStudent);
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
    if (scope === 'student' && (!studentId || studentId === 'self')) return showToast('Choose a student from the Student filter.');
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
    if (isStudent && typeof data.syncConnected === 'boolean') {
      for (const indicator of document.querySelectorAll('[data-remote-sync-indicator]')) {
        indicator.hidden = !data.syncConnected;
        indicator.textContent = data.syncConnected ? `● Synced with teacher: ${(data.syncTeachers || []).join(', ')}` : '';
        indicator.style.color = data.syncConnected ? 'var(--accent)' : 'var(--muted)';
      }
    }
    return data;
  }
  function selectedEditorStudent() { const value = $('#historyStudentFilter')?.value || ''; return value === 'self' ? '' : value; }
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
    if (teacherEditorKind === 'history') { panel.hidden = true; return; }
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
      dialog.className = 'ide-dialog teacher-permission-dialog';
      dialog.setAttribute('aria-label', 'Teacher edit request');
      const heading = document.createElement('div');
      heading.className = 'dialog-heading';
      const title = document.createElement('h2');
      title.textContent = 'Teacher edit request';
      heading.append(title);
      const message = document.createElement('p');
      message.className = 'dialog-note';
      message.textContent = `${teacherName || 'Your teacher'} is asking to edit both your SQLCL Terminal and SQL WorkSheet code for this session.`;
      const actions = document.createElement('div');
      actions.className = 'dialog-actions';
      const reject = document.createElement('button');
      reject.type = 'button'; reject.className = 'theme-button'; reject.textContent = 'Reject';
      const allow = document.createElement('button');
      allow.type = 'button'; allow.className = 'theme-button theme-button-primary'; allow.textContent = 'Allow for this session';
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
    if (!terminalInput || !terminalInput.getClientRects().length || terminalInput.parentElement.classList.contains('pro-editor-mounted')) return;
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
    setInterval(() => { if (!document.hidden) pollRemoteTerminalCommand(); }, 2000);
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
  let pendingRemoteResult = null;
  async function deliverRemoteResult() {
    if (!pendingRemoteResult) return;
    const { job, payload } = pendingRemoteResult;
    await editorShareRequest(`/api/compiler/remote-terminal/${encodeURIComponent(job.id)}/complete`, {
      method: 'POST', body: JSON.stringify(payload),
    });
    if (job.editor !== 'worksheet' && terminalInput.value.trim() === job.text.trim()) {
      terminalInput.value = '';
      terminalInput.style.height = '';
      terminalInput.dispatchEvent(new Event('input', { bubbles: true }));
    }
    pendingRemoteResult = null;
  }
  async function pollRemoteTerminalCommand() {
    if (!isStudent || remoteTerminalPolling || !terminalInput) return;
    if (dataMode() === 'private') return;
    if (!pendingRemoteResult && (executionBusy || terminalQueue || $('#openConnection').dataset.connected !== 'true')) return;
    remoteTerminalPolling = true;
    try {
      if (pendingRemoteResult) { await deliverRemoteResult(); return; }
      const pending = await editorShareRequest('/api/compiler/remote-terminal/pending');
      const job = pending.command;
      if (!job) return;
      let executionFinished = false;
      try {
        const sqlplus = job.editor !== 'worksheet';
        if (sqlplus && terminalInput.value.trim() === job.text.trim()) {
          terminalInput.value = '';
          terminalInput.style.height = '';
          terminalInput.dispatchEvent(new Event('input', { bubbles: true }));
        }
        // Remote teacher code is programmatic input, so record it explicitly instead
        // of relying on the browser's trusted input events.
        markInputMethod(sqlplus ? 'sqlplus' : 'plsql', 'remote_code');
        const result = await execute(job.text, { sqlplus, script: Boolean(job.script) });
        if (!result || result.retryPending) throw new Error('The student Oracle connection was lost before a result was returned.');
        executionFinished = true;
        const output = sqlplus ? window.CompilerWorkspace.outputText(job.text, result) : JSON.stringify(result, null, 2);
        const error = result.error || (result.executionErrors?.length ? result.executionErrors.map(item => item.error || item.message || String(item)).join('\n') : '');
        pendingRemoteResult = { job, payload: { output, error, executed: executionFinished } };
        await deliverRemoteResult();
      } catch (error) {
        if (!pendingRemoteResult) pendingRemoteResult = { job, payload: { error: error.message || 'Remote SQLCL execution failed.', executed: executionFinished } };
        // Retain the original output when delivery fails; retry without executing again.
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
    if (!command) return;
    if (terminalQueue) {
      const queuedBatch = window.CompilerWorkspace.pastedBatch(command);
      const queuedCommands = queuedBatch.commands.length ? queuedBatch.commands : (terminalInputIsComplete(command) ? [command] : []);
      if (!queuedCommands.length) {
        showToast('Finish the SQL statement with ; before running it.');
        return;
      }
      const inputMethod = currentInputMethod('sqlplus');
      terminalQueue.commands.push(...queuedCommands);
      terminalQueue.inputMethods ||= Array(terminalQueue.commands.length - queuedCommands.length).fill(terminalQueue.inputMethod || '');
      terminalQueue.inputMethods.push(...queuedCommands.map(() => inputMethod));
      terminalQueue.remaining = queuedBatch.remaining || terminalQueue.remaining || '';
      terminalInput.value = '';
      terminalInput.dispatchEvent(new Event('input', { bubbles: true }));
      saveTerminalQueue();
      return;
    }
    if ($('#terminalRun').disabled && !executionBusy) return;
    if (window.CompilerWorkspace.pastedBatch(command).commands.length || terminalInputIsComplete(command) || executionBusy) {
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
  window.WorkspaceDailyTask?.attach({isStudent, editor, compilerApiJson, markInputMethod, updateLineNumbers, updateHighlight, updateEditorCursorPosition, saveDailyTaskPosition, setView, compilerCsrfToken, announceDailyTaskSubmitted});
  const studentDailyTaskToggle = $('#studentDailyTaskToggle');
  const studentDailyTaskPanel = $('#studentDailyTaskPanel');
  const studentTestToggle = $('#studentTestToggle');
  const studentTestPanel = $('#studentTestPanel');
  if (studentDailyTaskToggle && studentDailyTaskPanel) {
    const savedCompilerAssessment = readDailyTaskUiState();
    studentDailyTaskToggle.checked = Boolean(savedCompilerAssessment.sqlplusVisible) && !Boolean(savedCompilerAssessment.sqlplusTestVisible);
    studentDailyTaskPanel.hidden = !studentDailyTaskToggle.checked;
    const previousButton = $('#studentDailyTaskPrevious');
    const nextButton = $('#studentDailyTaskNext');
    const submitButton = $('#studentDailyTaskSubmit');
    const positionLabel = $('#studentDailyTaskPosition');
    const questionNode = $('#studentDailyTaskQuestion');
    const statusNode = $('#studentDailyTaskStatus');
    let studentDailyTasks = [];
    let selectedDailyTask = 0;
    let dailyTasksRequest = null;
    const renderDailyTask = () => {
      const task = studentDailyTasks[selectedDailyTask];
      const count = studentDailyTasks.length;
      positionLabel.textContent = count ? `${selectedDailyTask + 1} / ${count}` : '0 / 0';
      questionNode.textContent = task?.instructions || (count ? '' : 'No pending daily tasks.');
      previousButton.disabled = !count || selectedDailyTask === 0;
      nextButton.disabled = !count || selectedDailyTask >= count - 1;
      submitButton.disabled = !task;
    };
    const loadDailyTasks = async () => {
      dailyTasksRequest?.abort();
      dailyTasksRequest = new AbortController();
      statusNode.textContent = 'Loading your daily tasks…';
      submitButton.disabled = true;
      try {
        const response = await fetch('/api/portal/daily-tasks/available', {
          signal: dailyTasksRequest.signal,
          headers: { Accept: 'application/json' },
          cache: 'no-store',
        });
        if (response.redirected) throw new Error('Sign in again to load your daily tasks.');
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not load daily tasks.');
        studentDailyTasks = Array.isArray(data.tasks) ? data.tasks : [];
        selectedDailyTask = studentDailyTasks.length ? restoreDailyTaskIndex(studentDailyTasks) : 0;
        renderDailyTask();
        if (studentDailyTasks[selectedDailyTask]) saveDailyTaskPosition(studentDailyTasks[selectedDailyTask], false);
        statusNode.textContent = studentDailyTasks.length
          ? ''
          : 'No enabled daily tasks are assigned to your enrolled classes.';
      } catch (error) {
        if (error.name !== 'AbortError') {
          studentDailyTasks = [];
          renderDailyTask();
          statusNode.textContent = error.message || 'Could not load daily tasks.';
        }
      }
    };
    studentDailyTaskToggle.addEventListener('change', () => {
      if (studentDailyTaskToggle.checked && studentTestToggle) {
        studentTestToggle.checked = false;
        if (studentTestPanel) studentTestPanel.hidden = true;
      }
      studentDailyTaskPanel.hidden = !studentDailyTaskToggle.checked;
      saveDailyTaskUiState({ sqlplusVisible: studentDailyTaskToggle.checked, ...(studentDailyTaskToggle.checked ? { sqlplusTestVisible: false } : {}) });
      if (studentDailyTaskToggle.checked) loadDailyTasks();
      else dailyTasksRequest?.abort();
    });
    previousButton.addEventListener('click', () => {
      if (selectedDailyTask > 0) { selectedDailyTask -= 1; renderDailyTask(); saveDailyTaskPosition(studentDailyTasks[selectedDailyTask]); }
    });
    nextButton.addEventListener('click', () => {
      if (selectedDailyTask < studentDailyTasks.length - 1) { selectedDailyTask += 1; renderDailyTask(); saveDailyTaskPosition(studentDailyTasks[selectedDailyTask]); }
    });
    document.addEventListener('daily-task-position-changed', (event) => {
      const index = studentDailyTasks.findIndex((task) => String(task.id) === String(event.detail?.taskId));
      if (index < 0 || index === selectedDailyTask) return;
      selectedDailyTask = index;
      renderDailyTask();
    });
      document.addEventListener('daily-task-submitted', (event) => {
        const result = removeSubmittedDailyTask(studentDailyTasks, selectedDailyTask, event.detail?.taskId);
        if (!result.removed) return;
        selectedDailyTask = result.index;
        renderDailyTask();
        saveDailyTaskPosition(studentDailyTasks[selectedDailyTask], false);
        statusNode.textContent = studentDailyTasks.length ? 'Showing the next pending daily task.' : 'No pending daily tasks.';
      });
    submitButton.addEventListener('click', async () => {
      const task = studentDailyTasks[selectedDailyTask];
      if (!task) return;
      submitButton.disabled = true;
      statusNode.textContent = 'Submitting your latest successful SQLCL statement…';
      try {
        const response = await fetch('/api/portal/daily-tasks/submit-latest-sqlcl', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-CSRFToken': compilerCsrfToken() },
          body: JSON.stringify({ taskId: task.id }),
        });
        if (response.redirected) throw new Error('Please sign in again to submit your answer.');
        const data = await compilerApiJson(response);
        if (!response.ok) throw new Error(data.error || 'Could not submit the SQLCL statement.');
        announceDailyTaskSubmitted(task.id, data);
        statusNode.textContent = data.message || 'Your answer was submitted.';
        showToast(data.message);
      } catch (error) {
        statusNode.textContent = error.message || 'Could not submit the SQLCL statement.';
        showToast(statusNode.textContent);
      } finally {
        submitButton.disabled = !studentDailyTasks[selectedDailyTask];
      }
    });
    if (studentDailyTaskToggle.checked) loadDailyTasks();
  }
  if (studentTestToggle && studentTestPanel) {
    const savedCompilerAssessment = readDailyTaskUiState();
    studentTestToggle.checked = Boolean(savedCompilerAssessment.sqlplusTestVisible) && !Boolean(savedCompilerAssessment.sqlplusVisible);
    studentTestPanel.hidden = !studentTestToggle.checked;
    const previousButton = $('#studentTestPrevious');
    const nextButton = $('#studentTestNext');
    const submitButton = $('#studentTestSubmit');
    const positionLabel = $('#studentTestPosition');
    const titleNode = $('#studentTestTitle');
    const questionNode = $('#studentTestQuestion');
    const statusNode = $('#studentTestStatus');
    let items = [];
    let selectedIndex = 0;
    const renderTestQuestion = () => {
      const item = items[selectedIndex];
      positionLabel.textContent = items.length ? `${selectedIndex + 1} / ${items.length}` : '0 / 0';
      titleNode.textContent = item ? item.testTitle : '';
      questionNode.textContent = item ? `${item.position}. ${item.prompt}` : (items.length ? '' : 'No pending test questions.');
      previousButton.disabled = !item || selectedIndex === 0;
      nextButton.disabled = !item || selectedIndex >= items.length - 1;
      submitButton.disabled = !item;
    };
    const loadTestQuestions = async ({ showLoading = true } = {}) => {
      if (showLoading) statusNode.textContent = 'Loading available test questions…';
      try {
        const response = await fetch('/api/portal/tests/available', { credentials: 'same-origin', headers: { Accept: 'application/json' }, cache: 'no-store' });
        const data = await compilerApiJson(response);
        if (!response.ok) throw new Error(data.error || 'Could not load test questions.');
        items = Array.isArray(data.items) ? data.items : [];
        selectedIndex = items.length ? restoreTestQuestionIndex(items) : 0;
        renderTestQuestion();
        if (items[selectedIndex]) saveTestQuestionPosition(items[selectedIndex], false);
        if (showLoading) statusNode.textContent = items.length ? '' : 'No available test questions.';
      } catch (error) {
        items = [];
        renderTestQuestion();
        statusNode.textContent = error.message || 'Could not load test questions.';
      }
    };
    studentTestToggle.addEventListener('change', () => {
      if (studentTestToggle.checked && studentDailyTaskToggle) {
        studentDailyTaskToggle.checked = false;
        if (studentDailyTaskPanel) studentDailyTaskPanel.hidden = true;
        saveDailyTaskUiState({ sqlplusVisible: false, sqlplusTestVisible: true });
      } else {
        saveDailyTaskUiState({ sqlplusTestVisible: studentTestToggle.checked });
      }
      studentTestPanel.hidden = !studentTestToggle.checked;
      if (studentTestToggle.checked) loadTestQuestions();
    });
    previousButton.addEventListener('click', () => {
      if (selectedIndex > 0) { selectedIndex--; renderTestQuestion(); saveTestQuestionPosition(items[selectedIndex]); }
    });
    nextButton.addEventListener('click', () => {
      if (selectedIndex < items.length - 1) { selectedIndex++; renderTestQuestion(); saveTestQuestionPosition(items[selectedIndex]); }
    });
    document.addEventListener('test-question-position-changed', (event) => {
      const index = items.findIndex((item) => testQuestionKey(item) === String(event.detail?.key || ''));
      if (index < 0 || index === selectedIndex) return;
      selectedIndex = index;
      renderTestQuestion();
    });
    document.addEventListener('test-question-submitted', async (event) => {
      if (event.detail?.origin === 'sqlplus') return;
      const wasCurrent = testQuestionKey(items[selectedIndex]) === String(event.detail?.key || '');
      if (studentTestToggle.checked) {
        await loadTestQuestions({ showLoading: false });
        if (wasCurrent && items.length) selectedIndex = Math.min(selectedIndex, items.length - 1);
        renderTestQuestion();
      }
    });
    submitButton.addEventListener('click', async () => {
      const item = items[selectedIndex];
      if (!item) return;
      submitButton.disabled = true;
      statusNode.textContent = 'Submitting your latest successful SQLCL statement…';
      try {
        const response = await fetch('/api/portal/tests/submit-latest-sqlcl', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-CSRFToken': compilerCsrfToken() },
          body: JSON.stringify({ testId: item.testId, questionId: item.questionId }),
        });
        const data = await compilerApiJson(response);
        if (!response.ok) throw new Error(data.error || 'Could not submit the SQLCL statement.');
        document.dispatchEvent(new CustomEvent('test-question-submitted', { detail: { key: testQuestionKey(item), origin: 'sqlplus' } }));
        await loadTestQuestions({ showLoading: false });
        statusNode.textContent = `✓ Answer saved successfully. ${data.message}`;
        showToast(statusNode.textContent);
      } catch (error) {
        statusNode.textContent = error.message || 'Could not submit the SQLCL statement.';
        showToast(statusNode.textContent);
      } finally {
        submitButton.disabled = !items[selectedIndex];
      }
    });
    if (studentTestToggle.checked) loadTestQuestions();
  }
  sqlplusCleanPaste.checked = localStorage.getItem('ora-sqlplus-clean-paste') !== 'false';
  sqlplusAutoCommit.checked = localStorage.getItem('ora-sqlplus-autocommit') === 'ON';
  sqlplusStarterToggle.checked = localStorage.getItem('ora-show-sql-starters') === 'true';
  sqlplusCleanPaste.addEventListener('change', () => { localStorage.setItem('ora-sqlplus-clean-paste', String(sqlplusCleanPaste.checked)); if (sqlplusCleanPaste.checked) cleanEditorNow(terminalInput); });
  sqlplusAutoCommit.addEventListener('change', () => localStorage.setItem('ora-sqlplus-autocommit', sqlplusAutoCommit.checked ? 'ON' : 'OFF'));
  sqlplusStarterToggle.addEventListener('change', () => {
    localStorage.setItem('ora-show-sql-starters', String(sqlplusStarterToggle.checked));
    plsqlStarterToggle.checked = sqlplusStarterToggle.checked;
    sqlplusStarterMenu.hidden = !sqlplusStarterToggle.checked || Boolean(terminalInput.value.trim());
  });
  terminalInput.addEventListener('focus', () => { sqlplusStarterMenu.hidden = !sqlplusStarterToggle.checked || Boolean(terminalInput.value.trim()); });
  ['click','keyup','select','focus'].forEach(name=>terminalInput.addEventListener(name,updateTerminalInputHighlight));
  terminalInput.addEventListener('input', () => { updateTerminalInputHighlight(); if (terminalInput.value.trim()) sqlplusStarterMenu.hidden = true; });
  terminalInput.addEventListener('scroll', () => {
    const highlight = $('#terminalInputHighlight');
    if (highlight) { highlight.scrollTop = terminalInput.scrollTop; highlight.scrollLeft = terminalInput.scrollLeft; }
  });
  async function runPastedTerminalBatch(snapshot, recovered = null, sourceMapOverride = null) {
    if (terminalQueue && !recovered) {
      const queued = window.CompilerWorkspace.pastedBatch(snapshot);
      const commands = queued.commands.length ? queued.commands : (terminalInputIsComplete(snapshot) ? [snapshot.trim()] : []);
      if (!commands.length) return;
      const inputMethod = currentInputMethod('sqlplus');
      terminalQueue.commands.push(...commands);
      terminalQueue.inputMethods ||= Array(terminalQueue.commands.length - commands.length).fill(terminalQueue.inputMethod || '');
      terminalQueue.inputMethods.push(...commands.map(() => inputMethod));
      terminalQueue.remaining = queued.remaining || terminalQueue.remaining || '';
      terminalInput.value = '';
      terminalInput.dispatchEvent(new Event('input', { bubbles: true }));
      saveTerminalQueue();
      return;
    }
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
    terminalInput.compilerPendingDraft = snapshot;
    terminalInput.value = '';
    terminalInput.dispatchEvent(new Event('input', { bubbles:true }));
    let nextIndex = 0;
    try {
      terminalQueue = recovered || {
        commands: batch.commands,
        remaining,
        index: 0,
        request: null,
        inputMethod: batchInputMethod,
        inputMethods: batch.commands.map(() => batchInputMethod),
      };
      terminalQueue.inputMethods ||= terminalQueue.commands.map(() => terminalQueue.inputMethod || '');
      saveTerminalQueue();
      // Keep pasted work out of the input even while another execution or
      // navigator refresh is finishing. The input stays editable; Enter appends
      // new statements to this queue so they run after the current batch.
      while(executionBusy || $('#terminalRun').disabled) {
        if (terminalBatchCancelled) return;
        await new Promise(resolve=>setTimeout(resolve,50));
      }
      $('#terminalRun').disabled = true;
      saveTerminalQueue();
      let provenanceSearchOffset = 0;
      for (let index=terminalQueue.index; index<terminalQueue.commands.length; index++) {
        if (terminalBatchCancelled) { remaining=''; break; }
        nextIndex = index;
        terminalInput.compilerPendingDraft = terminalQueue.commands.slice(index).join('\n')+'\n'+remaining;
        const command=advanceTerminalComments(terminalQueue.commands[index]);
        if(!command){nextIndex=index+1;continue;}
        const commandOffset = snapshot.indexOf(command, provenanceSearchOffset);
        if (commandOffset >= 0) provenanceSearchOffset = commandOffset + command.length;
        if (await waitForTerminalConnection() === false) {
          remaining = terminalQueue.commands.slice(index).join('\n') + (remaining ? `\n${remaining}` : '');
          break;
        }
        const commandInputMethod = terminalQueue.inputMethods[index] || batchInputMethod;
        if (commandInputMethod) editMetrics.sqlplus.inputMethods.add(commandInputMethod);
        const result=await execute(command, { sqlplus:true, inputSourceMap: commandOffset >= 0 ? sliceInputSourceRuns(batchSourceMap, commandOffset, commandOffset + command.length) : [] });
        if (terminalBatchCancelled) { remaining=''; break; }
        if (result?.retryPending) { saveTerminalQueue(); index--; await new Promise(resolve=>setTimeout(resolve,1000)); continue; }
        if (result === null) { remaining=terminalQueue.commands.slice(index).join('\n')+'\n'+remaining; break; }
        terminalCommandHistory.push(command);
        nextIndex = index + 1;
        terminalQueue.index = nextIndex;
        terminalQueue.request = null;
        saveTerminalQueue();
        if (result.disconnected || result.sqlPlusDisconnected) setStatus(false, 'Waiting for database connection');
      }
      terminalHistoryIndex=terminalCommandHistory.length;
      terminalHistoryDraft='';
      remaining = terminalQueue.remaining || remaining;
      terminalInput.value=[remaining, terminalInput.value].filter(value => value && value.trim()).join('\n');
      terminalQueue = null;
      saveTerminalQueue();
      terminalInput.dispatchEvent(new Event('input', { bubbles:true }));
    } catch (error) {
      const pendingCommands = terminalQueue?.commands.slice(nextIndex).join('\n') || '';
      terminalInput.value = [pendingCommands, remaining, terminalInput.value].filter(value => value && value.trim()).join('\n');
      terminalQueue = null;
      try { sessionStorage.removeItem(terminalQueueKey); } catch {}
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
      recordPaste('sqlplus', pastedText.length, pastedText);
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
    if (action === 'share') return;
    const output = $('#terminalOutput');
    if (action === 'clear') { clearTerminalDisplay(); return; }
    if (action === 'copy') {
      const text = output.innerText;
      rememberInternalCopy(text);
      if (navigator.clipboard?.write && window.ClipboardItem) {
        const clone = output.cloneNode(true);
        clone.querySelectorAll('button,input,textarea').forEach(node => node.remove());
        clone.querySelectorAll('*').forEach((node, index) => {
          const original = output.querySelectorAll('*')[index];
          const computed = getComputedStyle(original);
          node.style.color = computed.color;
          node.style.backgroundColor = computed.backgroundColor;
          node.style.fontFamily = computed.fontFamily;
          node.style.fontSize = computed.fontSize;
          node.style.fontWeight = computed.fontWeight;
          node.style.whiteSpace = computed.whiteSpace;
        });
        const html = `<div style="font-family:ui-monospace,Consolas,monospace;white-space:pre-wrap">${clone.innerHTML.replace(/\r\n?/g, '\n').replace(/\n/g, '<br>').replace(/\t/g, '&nbsp;&nbsp;&nbsp;&nbsp;')}</div>`;
        await writeRichSqlClipboard(text, html);
      } else await writeRichSqlClipboard(text, inlineSqlClipboardHtml(text));
      showToast('Terminal copied with formatting.');
    }
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
    shell.style.setProperty('--sqlcl-font-size', `${terminalFontSize}px`);
    $$('.terminal-output').forEach((node) => { node.style.fontSize = `${terminalFontSize}px`; });
    const prompt = $('.terminal-prompt');
    if (prompt) prompt.style.fontSize = `${terminalFontSize}px`;
    if (terminalInput) { terminalInput.style.fontSize = `${terminalFontSize}px`; requestAnimationFrame(resizeTerminalInput); }
    $('#terminalFontValue').textContent = terminalFontSize;
    $('#fontValue').textContent = terminalFontSize;
  }
  function adjustFont(delta) {
    const maxFontSize = shell.classList.contains('fullscreen-mode') ? 36 : 22;
    terminalFontSize = Math.max(10, Math.min(maxFontSize, terminalFontSize + delta));
    applyTerminalFont();
    localStorage.setItem(`${settingPrefix}font`, String(terminalFontSize));
  }
  function syncFontSizeBounds() {
    const fullscreen = shell.classList.contains('fullscreen-mode');
    const maxTerminalFont = fullscreen ? 36 : 22;
    const maxEditorFont = fullscreen ? 36 : 30;
    if (terminalFontSize > maxTerminalFont) {
      terminalFontSize = maxTerminalFont;
      localStorage.setItem(`${settingPrefix}font`, String(terminalFontSize));
      applyTerminalFont();
    }
    if (editorFontSize > maxEditorFont) {
      editorFontSize = maxEditorFont;
      localStorage.setItem('ora-plsql-font-size', String(editorFontSize));
      applyEditorFontSize();
    }
  }
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
    syncFontSizeBounds();
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
    if (!$('#collapsedConnectionIndicator')) {
      const indicator = document.createElement('span'); indicator.id = 'collapsedConnectionIndicator'; indicator.className = 'navigator-collapsed-connection'; indicator.setAttribute('aria-label', 'Database connection status'); indicator.innerHTML = '<i></i>';
      $('.navigator-heading')?.insertBefore(indicator, $('#collapseNavigator'));
    }
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
  waitForNavigatorRefresh.checked = localStorage.getItem(`${settingPrefix}wait-navigator-refresh`) === 'true';
  waitForNavigatorRefresh.addEventListener('change', () => localStorage.setItem(`${settingPrefix}wait-navigator-refresh`, String(waitForNavigatorRefresh.checked)));
  $('#refreshObjects').addEventListener('click', refreshDatabaseNavigator);

  $('#openConnection').addEventListener('click', async () => {
    if ($('#openConnection').dataset.connected === 'true') {
      if (sessionUI && !sessionUI.confirmDisconnect()) return;
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
      terminalInput.focus({ preventScroll: true });
      return;
    }
    if (event.key === 'F11') { event.preventDefault(); toggleFullscreen(); }
    if (event.key === 'Escape' && shell.classList.contains('fullscreen-mode') && !document.fullscreenElement) {
      shell.classList.remove('fullscreen-mode'); syncFullscreenControl();
    }
  });

  try {
    const saved = Number(localStorage.getItem(`${settingPrefix}font`));
    if (Number.isFinite(saved) && saved >= 10 && saved <= 36) terminalFontSize = saved;
    const iconNav = localStorage.getItem(`${settingPrefix}icon-nav`) === 'true';
    const defaultFullscreen = localStorage.getItem(`${settingPrefix}default-fullscreen`) === 'true';
    $('#iconNavigation').checked = iconNav;
    $('#defaultFullscreen').checked = defaultFullscreen;
    syncNavigationMode(iconNav);
    shell.classList.toggle('fullscreen-mode', typeof workspaceResume?.fullscreen === 'boolean' ? workspaceResume.fullscreen : defaultFullscreen);
    syncFontSizeBounds();
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
    const allowed=new Set(['','Hack','Consolas','Cascadia Code','Courier New','Lucida Console','DM Mono']);
    const key=`compiler-font:${document.body.dataset.userId || 'local'}:${kind}`;
    function apply() {
      const choice=allowed.has(select.value)?select.value:'';
      const family=choice?`"${choice}", monospace`:'Hack, Consolas, monospace';
      const nodes=kind==='plsql'?[editor,$('#syntaxHighlight'),$('#lineNumbers'),$('#sqlEditor-completions')]:[terminalOutput,terminalInput,terminalPrompt,$('#terminalInputHighlight'),$('#terminalInput-completions')];
      nodes.filter(Boolean).forEach(node=>{node.style.fontFamily=family;});
      if(kind==='sqlplus') {
        if(family)terminalOutput.style.setProperty('--compiler-terminal-font',family);
        else terminalOutput.style.removeProperty('--compiler-terminal-font');
        requestAnimationFrame(resizeTerminalInput);
      } else updateHighlight();
    }
    try { if(localStorage.getItem(key+':hack-default')!=='1'){localStorage.setItem(key,'Hack');localStorage.setItem(key+':hack-default','1');} const saved=localStorage.getItem(key)||'Hack';select.value=allowed.has(saved)?saved:'Hack'; } catch {select.value='Hack';}
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
  if (new URLSearchParams(location.search).get('promptOracleLogin') === '1' && !connectDialog.open) {
    connectDialog.showModal();
    requestAnimationFrame(() => connectForm.elements.user.focus());
  }
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
    if (isTeacher) sharedCompilerTabAppearance = {
      ...sharedCompilerTabAppearance,
      compiler: { ...sharedCompilerTabAppearance.compiler, visible: true }
    };
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
    if (!students) return;
    students.replaceChildren();
    (data.students || []).forEach(student => {
      const option = document.createElement('option');
      option.value = String(student.id);
      option.textContent = `${student.name} · ${student.identifier}`;
      students.append(option);
    });

  }
  function updateCompilerTargetScope() {
    const scope = $('#compilerTabTargetScope')?.value;
    if ($('#compilerTabStudentsWrap')) $('#compilerTabStudentsWrap').hidden = scope !== 'students';
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
      const independentSessions = $('#independentCompilerSessions');
      if (independentSessions) {
        independentSessions.checked = Boolean(data.independentCompilerSessions);
        independentSessions.closest('.settings-card')?.classList.toggle('is-disabled', !data.canManageIndependentSessions);
        independentSessions.disabled = !data.canManageIndependentSessions;
      }
    } catch (error) { $('#terminalBufferStatus').textContent = error.message; }
  }
  $('#independentCompilerSessions')?.addEventListener('change', async (event) => {
    const control = event.currentTarget;
    const status = $('#independentCompilerSessionsStatus');
    const requested = control.checked;
    control.disabled = true;
    status.textContent = 'Saving…';
    try {
      const response = await fetch('/api/compiler/settings', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ independentCompilerSessions: requested }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Could not update Oracle session access.');
      control.checked = Boolean(data.independentCompilerSessions);
      status.textContent = data.independentCompilerSessions
        ? `Enabled for ${Number(data.affectedCount).toLocaleString()} students in your teaching scope.`
        : `Disabled for ${Number(data.affectedCount).toLocaleString()} students in your teaching scope.`;
    } catch (error) {
      control.checked = !requested;
      status.textContent = error.message || 'Could not update Oracle session access.';
    } finally { control.disabled = false; }
  });
  async function loadStudentSharedCode() {
    const list = $('#studentSharedCodeList');
    const status = $('#studentSharedCodeStatus');
    if (!isStudent || !list || !status) return;
    try {
      const response = await fetch('/api/compiler/shared-code', { credentials: 'same-origin', cache: 'no-store' });
      if (!response.headers.get('content-type')?.toLowerCase().includes('application/json')) {
        throw new Error(response.ok
          ? 'The server returned an unexpected response while loading shared code.'
          : `Could not load shared code (server returned HTTP ${response.status}).`);
      }
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
  // A browser refresh may outlive the server's in-memory Oracle session.
  // Reuse it when status succeeds; otherwise create a fresh Oracle session
  // from the saved profile for this portal login.
  refreshStatus().then(connected => {
    if (connected !== true && !manualOracleDisconnect) reconnectSavedCredentials();
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) return;
    refreshStatus().then(connected => {
      if (connected === false && !manualOracleDisconnect) reconnectSavedCredentials();
    });
  });
  window.setInterval(async () => {
    if(executionBusy || reconnectPromise)return;
    const connected=await refreshStatus();
    if (connected === false && !manualOracleDisconnect && Date.now() - lastReconnectAttempt >= 30000) {
      await reconnectSavedCredentials();
    }
  },15000);
  window.PortalSqlBridge = {
    editor, terminal: terminalInput,
    runWorkbook: script => runEditor(script),
    runTerminal: () => runTerminalCommand(),
    isTerminalComplete: text => terminalInputIsComplete(text),
    terminalHistory: direction => {
      // Reuse the established history handler, including draft recovery,
      // provenance tracking and end/start cursor placement.
      terminalInput.dispatchEvent(new KeyboardEvent('keydown', {
        key: direction < 0 ? 'ArrowUp' : 'ArrowDown', altKey: true,
        cancelable: true, bubbles: false
      }));
    },
    schema: () => currentSchema,
    objects: () => objectRows,
    clipboardHtml: text => inlineSqlClipboardHtml(text),
    output: () => resultData?.columns ? [resultData.columns.map(col=>col.name||String(col)).join('\t'),...(resultData.rows||[]).map(row=>row.map(safeCell).join('\t'))].join('\n') : resultData?.error || (resultData?.outputLines||[]).join('\n'),
  };
  document.dispatchEvent(new Event('portal-sql-ready'));
})();
