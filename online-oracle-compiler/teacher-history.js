const compilerSourceLabel = source => ({'SQL*Plus': 'SQLCL', 'PL/SQL': 'SQL WorkSheet'}[source] || source || '—');
(() => {
  const $ = (selector) => document.querySelector(selector);
  const rowsNode = $('#teacherHistoryRows');
  if (!rowsNode) return;
  const filters = {
    student: $('#teacherHistoryStudent'), source: $('#teacherHistorySource'), status: $('#teacherHistoryStatus'),
    from: $('#teacherHistoryFrom'), to: $('#teacherHistoryTo'), search: $('#teacherHistorySearch'),
  };
  let students = [];
  const searchSuggestions = window.PortalSearchSuggestions?.register(filters.search);
  let page = 1;
  let pagination = { total: 0, pages: 1 };
  let sort = { key: 'executedAt', direction: 'desc' };
  const pageSize = 50;
  function inputMethodLabel(item) {
    const labels = { typed: 'Typed', pasted: 'Pasted', file: 'File', history_recall: 'History recall', internal_copy: 'Internal copy', mixed: 'Mixed' };
    const pastedChars = Number(item.pastedChars) || 0;
    const pasteCount = Number(item.pasteCount) || 0;
    const typedChars = Number(item.typedChars) || 0;
    const edited = Number(item.editsAfterPaste) > 0;
    const recalledAndEdited = item.inputMethod === 'mixed' && typedChars > 0 && pastedChars === 0 && pasteCount === 0;
    if (recalledAndEdited) return 'History recall + Edited';
    let label = item.inputMethod === 'history_recall' ? 'History recall' : edited && (pastedChars > 0 || pasteCount > 0 || item.inputMethod === 'mixed') ? 'Pasted' : labels[item.inputMethod] || (pastedChars > 0 || pasteCount > 0 ? typedChars > 0 ? 'Typed + pasted' : 'Pasted' : typedChars > 0 ? 'Typed' : 'Not tracked');
    if (edited && label !== 'Not tracked') label = `${label} + Edited`;
    return label;
  }
  function appendColoredSql(cell, sql, sourceMap, inputLabel) {
    const text = String(sql || '');
    const runs = Array.isArray(sourceMap) ? sourceMap : [];
    if (inputLabel.startsWith('History recall')) {
      const span = document.createElement('span'); span.className = 'input-history-recall'; span.textContent = text; cell.append(span); return;
    }
    if (!runs.length) {
      const span = document.createElement('span'); span.textContent = text;
      if (inputLabel.startsWith('Typed')) span.className = 'input-typed';
      else if (inputLabel.startsWith('Pasted')) span.className = 'input-pasted';
      else if (inputLabel === 'Typed + pasted' || inputLabel === 'Mixed') span.className = 'input-mixed';
      cell.append(span); return;
    }
    let cursor = 0;
    runs.forEach((run) => {
      if (!Array.isArray(run) || run.length !== 3) return;
      const start = Math.max(cursor, Math.min(text.length, Number(run[0]) || 0));
      const end = Math.max(start, Math.min(text.length, Number(run[1]) || 0));
      if (start > cursor) cell.append(document.createTextNode(text.slice(cursor, start)));
      if (end > start) {
        const span = document.createElement('span');
        span.className = run[2] === 'typed' || run[2] === 'pasted' ? `input-${run[2]}` : '';
        span.textContent = text.slice(start, end); cell.append(span);
      }
      cursor = end;
    });
    if (cursor < text.length) cell.append(document.createTextNode(text.slice(cursor)));
  }
  function showMessage(message, isError = false) {
    rowsNode.replaceChildren();
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 10;
    cell.className = `activity-empty${isError ? ' error' : ''}`;
    cell.textContent = message;
    row.append(cell);
    rowsNode.append(row);
  }
  async function loadHistory() {
    $('#teacherHistoryRefresh').disabled = true;
    showMessage('Loading student history…');
    try {
      const params = requestParams();
      const response = await fetch(`/api/compiler/history?${params}`, { credentials: 'same-origin', cache: 'no-store' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Student SQL history could not be loaded.');
      students = data.students || [];
      searchSuggestions?.setItems(students.map((student) => ({ label: student.name || student.number, detail: student.number, value: student.name || '' })));
      pagination = data.pagination || { page: 1, pageSize, total: (data.records || []).length, pages: 1 };
      page = pagination.page || page;
      const selected = filters.student.value;
      filters.student.replaceChildren(new Option(data.studentScope === 'all' ? 'All students' : 'All assigned students', ''));
      students.forEach((student) => filters.student.add(new Option(`${student.name}${student.number ? ` · ${student.number}` : ''}${student.isActive ? '' : ' · Inactive'}`, String(student.id))));
      filters.student.value = selected;
      $('#teacherHistoryScope').textContent = data.studentScope === 'all'
        ? 'Showing history for all students available to your teacher account.'
        : 'Showing history for students assigned to your classes.';
      render(data.records || []);
      $('#teacherHistoryUpdated').textContent = `Updated ${new Date().toLocaleString()}.`;
    } catch (error) {
      showMessage(error.message || 'Student SQL history could not be loaded.', true);
      $('#teacherHistoryCount').textContent = 'Unavailable';
      $('#teacherHistorySummary').textContent = 'History could not be loaded.';
      $('#teacherHistoryUpdated').textContent = '';
    } finally { $('#teacherHistoryRefresh').disabled = false; }
  }
  function requestParams() {
    const params = new URLSearchParams({
      studentStatus: 'all', page: String(page), pageSize: String(pageSize),
      sort: sort.key, direction: sort.direction,
    });
    Object.entries({ student: filters.student.value, source: filters.source.value, status: filters.status.value,
      from: filters.from.value, to: filters.to.value, search: filters.search.value.trim() })
      .forEach(([key, value]) => { if (value && value !== 'all') params.set(key, value); });
    return params;
  }
  function render(visible) {
    const total = Number(pagination.total) || 0;
    const pages = Math.max(1, Number(pagination.pages) || 1);
    const start = (page - 1) * pageSize;
    $('#teacherHistoryCount').textContent = `${total.toLocaleString()} ${total === 1 ? 'statement' : 'statements'}`;
    $('#teacherHistorySummary').textContent = total ? `Showing ${start + 1}–${Math.min(start + pageSize, total)} of ${total.toLocaleString()} statements` : 'No statements match these filters.';
    $('#teacherHistoryPrev').disabled = page <= 1;
    $('#teacherHistoryNext').disabled = page >= pages;
    rowsNode.replaceChildren();
    if (!visible.length) { showMessage(total ? 'No statements match these filters.' : 'No student compiler history has been recorded yet.'); return; }
    visible.forEach((item) => {
      const row = document.createElement('tr');
      const student = document.createElement('td'); student.className = 'teacher-history-student';
      student.append(document.createTextNode(item.studentName || 'Student'));
      const studentNumber = document.createElement('small'); studentNumber.textContent = item.studentNumber || '—'; student.append(studentNumber);
      const time = document.createElement('td');
      const timeMain = document.createElement('span'); timeMain.textContent = new Date(item.executedAt).toLocaleDateString();
      const timeSmall = document.createElement('small'); timeSmall.textContent = new Date(item.executedAt).toLocaleTimeString(); time.append(timeMain, timeSmall);
      const source = document.createElement('td'); source.textContent = compilerSourceLabel(item.source);
      const sql = document.createElement('td'); sql.className = 'teacher-history-sql';
      const inputLabel = inputMethodLabel(item);
      appendColoredSql(sql, item.sql || '', item.inputSourceMap, inputLabel);
      if ((inputLabel === 'Typed + pasted' || inputLabel === 'Mixed') && !item.inputSourceMap?.length) {
        sql.classList.add('input-mixed');
        sql.title = 'Mixed typed and pasted input; exact character positions were not recorded.';
      }
      const input = document.createElement('td'); input.className = 'teacher-history-input';
      const pastedChars = Number(item.pastedChars) || 0;
      const pasteCount = Number(item.pasteCount) || 0;
      const typedChars = Number(item.typedChars) || 0;
      if (inputLabel.startsWith('Pasted') || ['Typed + pasted', 'Mixed'].includes(inputLabel)) {
        const badge = document.createElement('span'); badge.className = 'paste-badge';
        badge.textContent = inputLabel;
        badge.title = `${pasteCount} paste event${pasteCount === 1 ? '' : 's'} · ${pastedChars.toLocaleString()} pasted characters${Number(item.editsAfterPaste) ? ` · ${Number(item.editsAfterPaste).toLocaleString()} edits after paste` : ''}.`;
        input.append(badge);
      } else if (inputLabel !== 'Not tracked') {
        const badge = document.createElement('span'); badge.className = 'typed-badge'; badge.textContent = inputLabel;
        badge.title = inputLabel.startsWith('History recall') ? 'Statement was recalled from SQL history and then edited before execution.' : inputLabel === 'File' ? 'Statement was loaded using Open file.' : `${typedChars.toLocaleString()} typed characters`;
        input.append(badge);
      } else {
        input.textContent = 'Not tracked';
        input.title = 'Input method was not recorded for this statement.';
      }
      const pastedText = document.createElement('td'); pastedText.className = 'teacher-history-pasted-text-cell';
      const pasteTexts = Array.isArray(item.pastedTexts) ? item.pastedTexts.filter((text) => typeof text === 'string' && text.length) : [];
      if (pasteTexts.length) {
        pastedText.textContent = pasteTexts.join(', ');
        pastedText.classList.add('teacher-history-pasted-text');
      } else if (pasteCount > 0) {
        pastedText.textContent = 'Not captured for this statement';
        pastedText.classList.add('teacher-history-pasted-text-unavailable');
      } else {
        pastedText.textContent = '—';
      }
      const result = document.createElement('td'); result.className = `teacher-history-result ${item.status === 'success' ? 'success' : 'error'}`;
      result.textContent = item.status === 'success' ? 'Success' : 'Failed'; result.title = item.message || '';
      const duration = document.createElement('td'); duration.textContent = `${Number(item.durationMs || 0).toLocaleString()} ms`;
      const count = document.createElement('td'); count.textContent = Number(item.rows || 0).toLocaleString();
      const action = document.createElement('td'); const copy = document.createElement('button'); copy.type = 'button'; copy.className = 'teacher-history-copy'; copy.textContent = 'Copy'; copy.title = 'Copy statement';
      copy.addEventListener('click', async () => { try { await navigator.clipboard.writeText(item.sql || ''); copy.textContent = 'Copied'; setTimeout(() => { copy.textContent = 'Copy'; }, 1200); } catch { $('#teacherHistoryUpdated').textContent = 'Clipboard access is unavailable.'; } });
      action.append(copy); row.append(student, time, source, sql, input, pastedText, result, duration, count, action); rowsNode.append(row);
    });
  }
  let searchTimer;
  Object.values(filters).forEach((control) => control.addEventListener(control === filters.search ? 'input' : 'change', () => {
    page = 1;
    if (control === filters.search) { clearTimeout(searchTimer); searchTimer = setTimeout(loadHistory, 250); }
    else loadHistory();
  }));
  document.querySelectorAll('[data-history-sort]').forEach((button) => button.addEventListener('click', () => {
    const key = button.dataset.historySort;
    sort = { key, direction: sort.key === key && sort.direction === 'asc' ? 'desc' : 'asc' };
    loadHistory();
  }));
  $('#teacherHistoryPrev').addEventListener('click', () => { page = Math.max(1, page - 1); loadHistory(); });
  $('#teacherHistoryNext').addEventListener('click', () => { page += 1; loadHistory(); });
  $('#teacherHistoryRefresh').addEventListener('click', loadHistory);
  $('#teacherHistoryExport').addEventListener('click', async () => {
    const columns = ['Student', 'Student ID', 'Executed at', 'Compiler', 'Statement', 'Input method', 'Pasted text', 'Typed characters', 'Pasted characters', 'Paste events', 'Edits after paste', 'Result', 'Duration ms', 'Rows', 'Message'];
    const button = $('#teacherHistoryExport'); button.disabled = true;
    try {
      const base = requestParams(); base.set('page', '1'); base.set('pageSize', '1000');
      const records = [];
      let pages = 1;
      for (let current = 1; current <= pages; current += 1) {
        base.set('page', String(current));
        const response = await fetch(`/api/compiler/history?${base}`, { credentials: 'same-origin', cache: 'no-store' });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not export student SQL history.');
        records.push(...(data.records || [])); pages = data.pagination?.pages || 1;
      }
      const rows = records.map((item) => [item.studentName, item.studentNumber, item.executedAt, compilerSourceLabel(item.source), item.sql, inputMethodLabel(item), (Array.isArray(item.pastedTexts) ? item.pastedTexts : []).join(', '), item.typedChars, item.pastedChars, item.pasteCount, item.editsAfterPaste, item.status, item.durationMs, item.rows, item.message]);
      const csv = [columns, ...rows].map((values) => values.map((value) => `"${String(value ?? '').replaceAll('"', '""')}"`).join(',')).join('\r\n');
      const link = document.createElement('a'); link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' })); link.download = 'student-sql-history.csv'; link.click(); URL.revokeObjectURL(link.href);
    } catch (error) { showMessage(error.message || 'Could not export student SQL history.', true); }
    finally { button.disabled = false; }
  });
  loadHistory();
})();
