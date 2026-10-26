(() => {
  const $ = (selector) => document.querySelector(selector);
  const studentSelect = $('#remoteCodeStudent');
  const textarea = $('#remoteCodeText');
  const saveButton = $('#remoteCodeSave');
  const refreshButton = $('#remoteCodeRefresh');
  const status = $('#remoteCodeStatus');
  if (!studentSelect || !textarea) return;

  let activeEditor = 'sqlplus';
  let shared = null;
  let drafts = { sqlplus: '', plsql: '' };
  let dirty = false;
  let conflict = false;
  let loading = false;
  let loadSequence = 0;
  let currentStudentId = studentSelect.value;

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
    return Boolean(data && ['statement', 'session', 'always'].includes(data.mode));
  }
  function setEditorContent() {
    const selectedName = studentSelect.selectedOptions[0]?.textContent.split(' · ')[0] || 'Student editor';
    const terminal = activeEditor === 'sqlplus';
    textarea.value = drafts[activeEditor] || '';
    textarea.disabled = !hasAccess(shared) || loading;
    textarea.readOnly = !hasAccess(shared) || loading;
    textarea.setAttribute('aria-label', `${selectedName} ${terminal ? 'SQLCL terminal' : 'SQL WorkSheet editor'} code`);
    $('#remoteCodeLabel').textContent = terminal ? 'SQLCL Terminal code' : 'SQL WorkSheet Editor code';
    $('#remoteCodeStudentHeading').textContent = selectedName;
    document.querySelectorAll('[data-remote-editor]').forEach((button) => {
      const active = button.dataset.remoteEditor === activeEditor;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
    });
    saveButton.disabled = !hasAccess(shared) || !dirty || conflict || loading;
    refreshButton.disabled = !studentSelect.value || loading;
  }

  function accessLabel(mode) {
    return mode === 'always' ? 'Always allow' : mode === 'session' ? 'This session' : mode === 'statement' ? 'One statement' : 'Private';
  }

  async function load({ discard = false } = {}) {
    const studentId = studentSelect.value;
    const sequence = ++loadSequence;
    if (dirty && !discard) {
      if (!window.confirm('Discard your unsaved remote code changes and reload the student editor?')) return;
    }
    if (!studentId) {
      shared = null; drafts = { sqlplus: '', plsql: '' }; dirty = conflict = false;
      $('#remoteCodeAccess').textContent = 'Select a student. Students must allow teacher editor access in compiler Settings.';
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
      $('#remoteCodeAccess').textContent = `Student sharing: ${accessLabel(data.mode)}. Changes you save are sent to the student compiler.`;
      $('#remoteCodeRevision').textContent = `Revision ${data.revision}`;
      status.textContent = data.mode === 'statement'
        ? 'This shared statement can be viewed or edited once. Saving ends this one-statement access.'
        : 'Read and write access is active. Student changes refresh here automatically.';
      setEditorContent();
    } catch (error) {
      if (sequence !== loadSequence || studentSelect.value !== studentId) return;
      shared = null; drafts = { sqlplus: '', plsql: '' }; dirty = conflict = false;
      $('#remoteCodeAccess').textContent = 'Ask the student to enable editor sharing in compiler Settings.';
      $('#remoteCodeRevision').textContent = '';
      status.textContent = error.message;
      setEditorContent();
    } finally {
      loading = false;
      setEditorContent();
    }
  }

  async function poll() {
    if (!studentSelect.value || !shared || loading) return;
    try {
      const data = await request(`/api/compiler/editor-share/${encodeURIComponent(studentSelect.value)}`);
      if (!shared || data.revision === shared.revision) return;
      if (dirty) {
        conflict = true;
        status.textContent = 'The student editor changed. Refresh to reload before saving so you do not overwrite their update.';
        setEditorContent();
        return;
      }
      shared = data;
      drafts = { sqlplus: data.sqlplusText || '', plsql: data.editorText || '' };
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

  studentSelect.addEventListener('change', () => {
    const nextStudentId = studentSelect.value;
    if (dirty && !window.confirm('Discard your unsaved remote code changes and switch students?')) {
      studentSelect.value = currentStudentId;
      return;
    }
    currentStudentId = nextStudentId;
    load({ discard: true });
  });
  refreshButton.addEventListener('click', () => load());
  document.querySelectorAll('[data-remote-editor]').forEach((button) => button.addEventListener('click', () => {
    drafts[activeEditor] = textarea.value;
    activeEditor = button.dataset.remoteEditor;
    setEditorContent();
  }));
  textarea.addEventListener('input', () => {
    drafts[activeEditor] = textarea.value;
    dirty = Boolean(shared && (drafts.sqlplus !== (shared.sqlplusText || '') || drafts.plsql !== (shared.editorText || '')));
    status.textContent = dirty ? 'Unsaved changes. Save to update the student editor.' : 'No unsaved changes.';
    setEditorContent();
  });
  saveButton.addEventListener('click', async () => {
    if (!shared || !studentSelect.value || !dirty || conflict) return;
    drafts[activeEditor] = textarea.value;
    loading = true; setEditorContent(); status.textContent = 'Saving to student editor…';
    try {
      shared = await request(`/api/compiler/editor-share/${encodeURIComponent(studentSelect.value)}`, {
        method: 'POST', body: JSON.stringify({ editorText: drafts.plsql, sqlplusText: drafts.sqlplus, revision: shared.revision }),
      });
      drafts = { sqlplus: shared.sqlplusText || '', plsql: shared.editorText || '' };
      dirty = conflict = false;
      $('#remoteCodeRevision').textContent = `Revision ${shared.revision}`;
      $('#remoteCodeAccess').textContent = `Student sharing: ${accessLabel(shared.mode)}.`;
      status.textContent = shared.mode === 'private'
        ? 'Student editor updated. One-statement access has ended; the student must share again for further edits.'
        : 'Student editor updated.';
    } catch (error) {
      status.textContent = `${error.message} Refresh the student editor before trying again.`;
      conflict = error.message.includes('changed') || error.message.includes('revision');
    } finally {
      loading = false; setEditorContent();
    }
  });

  setEditorContent();
  window.setInterval(poll, 5000);
})();
