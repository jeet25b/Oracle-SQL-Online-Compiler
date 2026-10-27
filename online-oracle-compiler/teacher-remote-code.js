(() => {
  const $ = (selector) => document.querySelector(selector);
  const studentSelect = $('#remoteCodeStudent');
  const textarea = $('#remoteCodeText');
  const refreshButton = $('#remoteCodeRefresh');
  const requestButton = $('#remoteCodeRequest');
  const worksheetActions = $('#remoteWorksheetActions');
  const worksheetRunStatement = $('#remoteWorksheetRunStatement');
  const worksheetRunScript = $('#remoteWorksheetRunScript');
  const status = $('#remoteCodeStatus');
  if (!studentSelect || !textarea) return;

  let activeEditor = 'sqlplus';
  let shared = null;
  let drafts = { sqlplus: '', plsql: '' };
  let dirty = false;
  let conflict = false;
  let loading = false;
  let saving = false;
  let switching = false;
  let sendingRemoteCommand = false;
  let lastClearedRemoteCommand = '';
  let saveTimer = 0;
  let loadSequence = 0;
  let currentStudentId = studentSelect.value;
  let terminalStatementHistory = [];
  let terminalHistoryIndex = 0;
  let terminalHistoryDraft = '';

  async function request(url, options = {}) {
    const response = await fetch(url, {
      credentials: 'same-origin', cache: 'no-store', ...options,
      headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) },
    });
    let data;
    try { data = await response.json(); }
    catch { throw new Error(`Remote code service returned an invalid response (HTTP ${response.status}).`); }
    if (!response.ok) throw new Error(data.error || 'Could not access the student editor.');
    return data;
  }

  function hasAccess(data) {
    if (!data) return false;
    if (data.mode === 'always') return true;
    if (data.mode === 'statement') return Boolean(data.statementSnapshotReady && Date.now() - Date.parse(data.updatedAt) <= 120000);
    return data.mode === 'session' && Date.now() - Date.parse(data.updatedAt) <= 120000;
  }
  function setEditorContent() {
    const selectedName = studentSelect.selectedOptions[0]?.textContent.split(' · ')[0] || 'Student editor';
    const terminal = activeEditor === 'sqlplus';
    textarea.value = drafts[activeEditor] || '';
    textarea.disabled = !shared || loading;
    textarea.readOnly = !hasAccess(shared) || loading;
    textarea.setAttribute('aria-label', `${selectedName} ${terminal ? 'SQLCL terminal' : 'SQL WorkSheet editor'} code`);
    $('#remoteCodeLabel').textContent = terminal ? 'SQLCL Terminal code' : 'SQL WorkSheet Editor code';
    worksheetActions.hidden = terminal;
    worksheetRunStatement.disabled = !studentSelect.value || !hasAccess(shared) || loading || saving || sendingRemoteCommand;
    worksheetRunScript.disabled = worksheetRunStatement.disabled;
    $('#remoteCodeStudentHeading').textContent = selectedName;
    document.querySelectorAll('[data-remote-editor]').forEach((button) => {
      const active = button.dataset.remoteEditor === activeEditor;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
    });
    refreshButton.disabled = !studentSelect.value || loading;
    if (requestButton) {
      const canEdit = hasAccess(shared);
      requestButton.hidden = !shared || canEdit;
      requestButton.disabled = loading || switching;
      requestButton.textContent = shared?.editPermissionRequest ? 'Request sent · awaiting student' : 'Request edit access for both editors';
      requestButton.disabled = requestButton.disabled || Boolean(shared?.editPermissionRequest);
    }
  }

  function accessLabel(mode) {
    return mode === 'always' ? 'Always allow' : mode === 'session' ? 'This session' : mode === 'statement' ? 'One statement' : 'View only';
  }

  function showRemoteCommand(command) {
    if (!command) return;
    $('#remoteTerminalCommandText').textContent = command.text || '';
    $('#remoteTerminalResultTitle').textContent = `Last remote ${command.editor === 'worksheet' ? 'SQL WorkSheet' : 'SQLCL'} ${command.script ? 'script' : 'command'}`;
    const labels = { queued: 'Waiting for student terminal', running: 'Running in student compiler', completed: 'Completed', failed: 'Failed', expired: 'Expired' };
    $('#remoteTerminalCommandStatus').textContent = labels[command.status] || command.status;
    $('#remoteTerminalCommandOutput').textContent = command.error || formatRemoteOutput(command) || (command.status === 'queued' ? 'Waiting for the student compiler to connect and receive this command…' : command.status === 'running' ? 'The student compiler is executing this command…' : 'No output returned.');
    if (command.editor === 'sqlplus' && command.executed && activeEditor === 'sqlplus'
      && textarea.value.trim() === String(command.text || '').trim() && lastClearedRemoteCommand !== command.id) {
      lastClearedRemoteCommand = command.id;
      drafts.sqlplus = '';
      textarea.value = '';
      dirty = Boolean(shared && (drafts.sqlplus !== (shared.sqlplusText || '') || drafts.plsql !== (shared.editorText || '')));
      textarea.dispatchEvent(new Event('input', { bubbles: true }));
      status.textContent = 'Statement executed. The SQLCL editor was cleared.';
    }
  }

  function formatRemoteOutput(command) {
    if (!command.output) return '';
    if (command.editor !== 'worksheet') return command.output;
    try {
      const result = JSON.parse(command.output);
      const lines = [];
      if (result.columns?.length) lines.push(result.columns.map(column => typeof column === 'string' ? column : column.name || column.label || '').join(' | '));
      for (const row of result.rows || []) lines.push((Array.isArray(row) ? row : Object.values(row)).map(value => value == null ? 'NULL' : String(value)).join(' | '));
      if (result.message) lines.push(result.message);
      if (result.rowsAffected !== null && result.rowsAffected !== undefined) lines.push(`${result.rowsAffected} rows affected.`);
      if (result.outputLines?.length) lines.push(...result.outputLines);
      if (!lines.length) lines.push('Statement executed successfully.');
      return lines.join('\n');
    } catch { return command.output; }
  }

  async function pollRemoteCommand(studentId = studentSelect.value) {
    if (!studentId || switching) return;
    try {
      const data = await request(`/api/compiler/remote-terminal/${encodeURIComponent(studentId)}`);
      if (studentSelect.value === studentId && data.command) showRemoteCommand(data.command);
    } catch (error) {
      if (error.message.includes('permission')) status.textContent = error.message;
    }
  }

  async function loadTerminalStatementHistory(studentId) {
    terminalStatementHistory = [];
    terminalHistoryIndex = 0;
    terminalHistoryDraft = '';
    if (!studentId) return;
    try {
      const data = await request(`/api/compiler/history?student=${encodeURIComponent(studentId)}&source=${encodeURIComponent('SQL*Plus')}&sort=executedAt&direction=asc&page=1&pageSize=1000`);
      if (studentSelect.value !== studentId) return;
      terminalStatementHistory = (data.records || []).map(record => record.sql).filter(statement => typeof statement === 'string' && statement.trim());
      terminalHistoryIndex = terminalStatementHistory.length;
    } catch {
      // History recall is optional; keep the editor usable if history is unavailable.
    }
  }

  function recallTerminalStatement(event) {
    if (activeEditor !== 'sqlplus' || event.shiftKey || event.ctrlKey || event.metaKey) return;
    if (!event.altKey) {
      if (textarea.selectionStart !== textarea.selectionEnd) return;
      const caret = textarea.selectionStart;
      const hasPreviousLine = textarea.value.slice(0, caret).includes('\n');
      const hasNextLine = textarea.value.slice(caret).includes('\n');
      if ((event.key === 'ArrowUp' && hasPreviousLine) || (event.key === 'ArrowDown' && hasNextLine)) return;
    }
    if (!terminalStatementHistory.length) return;
    if (event.key === 'ArrowUp' && terminalHistoryIndex <= 0) return;
    if (event.key === 'ArrowDown' && terminalHistoryIndex >= terminalStatementHistory.length) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (event.key === 'ArrowUp') {
      if (terminalHistoryIndex === terminalStatementHistory.length) terminalHistoryDraft = textarea.value;
      terminalHistoryIndex -= 1;
      textarea.value = terminalStatementHistory[terminalHistoryIndex];
    } else {
      terminalHistoryIndex += 1;
      textarea.value = terminalHistoryIndex === terminalStatementHistory.length
        ? terminalHistoryDraft
        : terminalStatementHistory[terminalHistoryIndex];
    }
    const caret = event.key === 'ArrowUp' ? textarea.value.length : 0;
    textarea.setSelectionRange(caret, caret);
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    textarea.scrollTop = event.key === 'ArrowUp' ? textarea.scrollHeight : 0;
  }

  function remoteStatementComplete(source) {
    const text = source.trim();
    if (!text) return false;
    if (/^(?:DESC(?:RIBE)?|SET|SHOW|DEFINE|UNDEFINE|PROMPT|REM(?:ARK)?|HELP|HISTORY|TIMING|CLEAR|LIST|APPEND|CHANGE|INPUT|DEL|RUN|EXIT|QUIT|DISCONNECT|CONNECT|CONN|START|GET|SAVE|SPOOL)\b[^\n]*(?:\n|$)/i.test(text)) return true;
    if (/^(?:DECLARE|BEGIN|CREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+)?(?:PROCEDURE|FUNCTION|PACKAGE|TRIGGER|TYPE)\b)/i.test(text)) return /(?:^|\n)\s*\/\s*$/.test(text);
    return /;\s*$/.test(text);
  }

  async function executeRemoteCommand(command, editor = 'sqlplus', script = false) {
    const studentId = studentSelect.value;
    command = String(command || '').trim();
    if (!studentId || !command || !hasAccess(shared) || loading || saving || sendingRemoteCommand) return false;
    clearTimeout(saveTimer);
    sendingRemoteCommand = true;
    const requestId = crypto.randomUUID();
    $('#remoteTerminalResultTitle').textContent = `Last remote ${editor === 'worksheet' ? 'SQL WorkSheet' : 'SQLCL'} ${script ? 'script' : 'command'}`;
    $('#remoteTerminalCommandText').textContent = command;
    $('#remoteTerminalCommandStatus').textContent = 'Sending to student compiler';
    $('#remoteTerminalCommandOutput').textContent = 'Waiting for the student compiler…';
    setEditorContent();
    try {
      const data = await request(`/api/compiler/remote-terminal/${encodeURIComponent(studentId)}`, {
        method: 'POST', body: JSON.stringify({ command, requestId, editor, script }),
      });
      showRemoteCommand(data.command);
      status.textContent = `Sent to the student’s ${editor === 'worksheet' ? 'SQL WorkSheet' : 'SQLCL terminal'}. It will run when their compiler is connected to Oracle.`;
      return true;
    } catch (error) {
      $('#remoteTerminalCommandStatus').textContent = 'Not sent';
      $('#remoteTerminalCommandOutput').textContent = error.message;
      status.textContent = error.message;
      return false;
    } finally {
      sendingRemoteCommand = false;
      setEditorContent();
    }
  }

  function worksheetStatementAtCursor() {
    const source = textarea.value;
    if (textarea.selectionStart !== textarea.selectionEnd) return source.slice(textarea.selectionStart, textarea.selectionEnd).trim();
    const ranges = window.CompilerWorkspace?.ranges(source) || [];
    const range = ranges.find(item => textarea.selectionStart >= item.start && textarea.selectionStart < item.end) || ranges.find(item => item.start >= textarea.selectionStart) || ranges.at(-1);
    return range?.sql || '';
  }

  function worksheetStatementComplete(source) {
    const text = String(source || '').trim();
    return remoteStatementComplete(text)
      || (/^(?:DECLARE|BEGIN|CREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+)?(?:PROCEDURE|FUNCTION|PACKAGE|TRIGGER|TYPE)\b)/i.test(text) && /\bEND\s*;\s*$/i.test(text));
  }

  function runWorksheetStatement() {
    const statement = worksheetStatementAtCursor();
    if (!statement) { status.textContent = 'Place the cursor in a statement or select a statement to run.'; return; }
    executeRemoteCommand(statement, 'worksheet', false);
  }

  function runWorksheetScript() {
    const script = textarea.value.trim();
    if (!script) { status.textContent = 'The SQL WorkSheet editor is empty.'; return; }
    executeRemoteCommand(script, 'worksheet', true);
  }

  async function load({ discard = false } = {}) {
    const studentId = studentSelect.value;
    const sequence = ++loadSequence;
    if (dirty && !discard) {
      if (!window.confirm('Discard your unsaved remote code changes and reload the student editor?')) return;
    }
    if (!studentId) {
      shared = null; drafts = { sqlplus: '', plsql: '' }; dirty = conflict = false;
      $('#remoteCodeAccess').textContent = 'Select a student. Editors are viewable; student permission is required to edit.';
      $('#remoteCodeRevision').textContent = '';
      status.textContent = 'Student sharing permission is required to view or edit code.';
      setEditorContent();
      return;
    }
    loading = true;
    status.textContent = 'Checking student sharing permission…';
    setEditorContent();
    try {
      const data = await request(`/api/compiler/editor-share/${encodeURIComponent(studentId)}`);
      if (sequence !== loadSequence || studentSelect.value !== studentId) return;
      if (dirty && shared && data.revision !== shared.revision) {
        conflict = true;
        status.textContent = 'The student editor changed. Refresh to reload before saving so you do not overwrite their update.';
        return;
      }
      shared = data;
      drafts = { sqlplus: data.sqlplusText || '', plsql: data.editorText || '' };
      dirty = conflict = false;
      loadTerminalStatementHistory(studentId);
      $('#remoteCodeAccess').textContent = `Student sharing: ${accessLabel(data.mode)}. Teacher and student changes sync automatically.`;
      $('#remoteCodeRevision').textContent = `Revision ${data.revision}`;
      status.textContent = !hasAccess(data)
        ? 'View-only access. Request permission to edit; the student will receive a popup.'
        : data.mode === 'statement'
        ? 'This shared statement can be viewed or edited once. The first synced teacher edit ends this one-statement access.'
        : 'Read and write access is active. Changes from either editor sync automatically.';
      setEditorContent();
      pollRemoteCommand(studentId);
    } catch (error) {
      if (sequence !== loadSequence || studentSelect.value !== studentId) return;
      shared = null; drafts = { sqlplus: '', plsql: '' }; dirty = conflict = false;
      $('#remoteCodeAccess').textContent = 'This student editor is viewable. Student permission is required to edit.';
      $('#remoteCodeRevision').textContent = '';
      status.textContent = error.message;
      setEditorContent();
    } finally {
      loading = false;
      setEditorContent();
    }
  }

  async function poll() {
    if (!studentSelect.value || !shared || loading || switching) return;
    try {
      const pollingStudentId = studentSelect.value;
      const data = await request(`/api/compiler/editor-share/${encodeURIComponent(pollingStudentId)}`);
      if (studentSelect.value !== pollingStudentId) return;
      if (!shared) return;
      if (data.revision === shared.revision) {
        const requestWasPending = Boolean(shared.editPermissionRequest);
        shared = data;
        $('#remoteCodeAccess').textContent = `Student sharing: ${accessLabel(shared.mode)}. ${hasAccess(shared) ? 'Changes from either editor sync automatically.' : 'Editors are viewable; permission is required to edit.'}`;
        if (requestWasPending && !data.editPermissionRequest) status.textContent = 'The student declined or dismissed the edit request. You can still view the editor.';
        setEditorContent();
        return;
      }
      if (dirty) {
        conflict = true;
        status.textContent = 'The student editor changed. Refresh to reload before saving so you do not overwrite their update.';
        setEditorContent();
        return;
      }
      shared = data;
      drafts = { sqlplus: data.sqlplusText || '', plsql: data.editorText || '' };
      $('#remoteCodeAccess').textContent = `Student sharing: ${accessLabel(shared.mode)}. ${hasAccess(shared) ? 'Changes from either editor sync automatically.' : 'Editors are viewable; permission is required to edit.'}`;
      $('#remoteCodeRevision').textContent = `Revision ${data.revision}`;
      status.textContent = 'Student editor updated.';
      setEditorContent();
    } catch (error) {
      if (error.message.includes('no longer active') || error.message.includes('not allowed')) {
        shared = null; dirty = conflict = false;
        status.textContent = error.message;
        setEditorContent();
      }
    }
  }

  async function saveDraft(studentId = currentStudentId) {
    if (!shared || !studentId || !dirty || conflict || loading || saving) return false;
    const snapshot = { sqlplus: drafts.sqlplus, plsql: drafts.plsql };
    saving = true;
    status.textContent = 'Syncing changes to the student editor…';
    try {
      const result = await request(`/api/compiler/editor-share/${encodeURIComponent(studentId)}`, {
        method: 'POST', body: JSON.stringify({ editorText: snapshot.plsql, sqlplusText: snapshot.sqlplus, revision: shared.revision }),
      });
      if (studentSelect.value !== studentId && currentStudentId !== studentId) return true;
      shared = result;
      $('#remoteCodeRevision').textContent = `Revision ${shared.revision}`;
      dirty = drafts.sqlplus !== (shared.sqlplusText || '') || drafts.plsql !== (shared.editorText || '');
      conflict = false;
      status.textContent = dirty ? 'Syncing latest edits…' : 'Changes synced automatically to the student editor.';
      return true;
    } catch (error) {
      conflict = error.message.includes('changed') || error.message.includes('revision');
      status.textContent = conflict
        ? 'The student editor changed at the same time. Refresh to load the latest shared editor.'
        : error.message;
      return false;
    } finally {
      saving = false;
      setEditorContent();
      if (dirty && !conflict && !loading) {
        clearTimeout(saveTimer);
        saveTimer = window.setTimeout(() => saveDraft(currentStudentId), 250);
      }
    }
  }

  function scheduleAutosave() {
    clearTimeout(saveTimer);
    if (!dirty || conflict || !hasAccess(shared)) return;
    status.textContent = 'Changes will sync automatically…';
    saveTimer = window.setTimeout(() => saveDraft(currentStudentId), 500);
  }

  studentSelect.addEventListener('change', () => {
    const nextStudentId = studentSelect.value;
    const previousStudentId = currentStudentId;
    clearTimeout(saveTimer);
    const switchStudent = async () => {
      switching = true;
      while (saving) await new Promise(resolve => window.setTimeout(resolve, 40));
      if (dirty && conflict) {
        studentSelect.value = previousStudentId;
        switching = false;
        return;
      }
      if (dirty && !conflict) {
        const saved = await saveDraft(previousStudentId);
        if (!saved) {
          studentSelect.value = previousStudentId;
          switching = false;
          return;
        }
      }
      currentStudentId = nextStudentId;
      switching = false;
      load({ discard: true });
    };
    if (dirty && conflict) {
      studentSelect.value = currentStudentId;
      return;
    }
    switchStudent();
  });
  refreshButton.addEventListener('click', async () => {
    clearTimeout(saveTimer);
    if (dirty && !conflict) {
      const synced = await saveDraft(currentStudentId);
      if (synced) status.textContent = 'Changes synced automatically to the student editor.';
      return;
    }
    load();
  });
  requestButton?.addEventListener('click', async () => {
    if (!studentSelect.value || !shared || hasAccess(shared)) return;
    requestButton.disabled = true;
    status.textContent = 'Sending permission request…';
    try {
      shared = await request(`/api/compiler/editor-share/${encodeURIComponent(studentSelect.value)}`, {
        method: 'POST', body: JSON.stringify({ action: 'request_edit_permission' }),
      });
      status.textContent = 'Edit request sent for both SQLCL and SQL WorkSheet. The student can allow or reject it in the compiler popup.';
      setEditorContent();
    } catch (error) {
      status.textContent = error.message;
      requestButton.disabled = false;
    }
  });
  document.querySelectorAll('[data-remote-editor]').forEach((button) => button.addEventListener('click', () => {
    drafts[activeEditor] = textarea.value;
    activeEditor = button.dataset.remoteEditor;
    setEditorContent();
  }));
  textarea.addEventListener('input', () => {
    drafts[activeEditor] = textarea.value;
    dirty = Boolean(shared && (drafts.sqlplus !== (shared.sqlplusText || '') || drafts.plsql !== (shared.editorText || '')));
    status.textContent = dirty ? 'Changes will sync automatically…' : 'All changes are synced.';
    setEditorContent();
    scheduleAutosave();
  });
  textarea.addEventListener('keydown', async (event) => {
    if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      recallTerminalStatement(event);
      return;
    }
    if (event.key !== 'Enter' || event.shiftKey || event.ctrlKey || event.altKey || event.isComposing) return;
    if (activeEditor === 'plsql') {
      const statement = worksheetStatementAtCursor();
      if (!worksheetStatementComplete(statement)) return;
      event.preventDefault();
      await executeRemoteCommand(statement, 'worksheet', false);
      return;
    }
    if (!remoteStatementComplete(textarea.value)) return;
    event.preventDefault();
    await executeRemoteCommand(textarea.value, 'sqlplus', false);
  });
  worksheetRunStatement.addEventListener('click', runWorksheetStatement);
  worksheetRunScript.addEventListener('click', runWorksheetScript);

  setEditorContent();
  window.setInterval(() => { poll(); pollRemoteCommand(); }, 1500);
})();
