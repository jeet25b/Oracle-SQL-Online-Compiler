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
  let page = 1;
  let pagination = { total: 0, pages: 1 };
  let sort = { key: 'executedAt', direction: 'desc' };
  const pageSize = 50;
  function inputMethodLabel(item) {
    const labels = { typed: 'Typed', pasted: 'Pasted', file: 'File', history_recall: 'History recall', mixed: 'Mixed' };
    if (labels[item.inputMethod]) return labels[item.inputMethod];
    const pastedChars = Number(item.pastedChars) || 0;
    const pasteCount = Number(item.pasteCount) || 0;
    const typedChars = Number(item.typedChars) || 0;
    if (pastedChars > 0 || pasteCount > 0) return typedChars > 0 ? 'Typed + pasted' : 'Pasted';
    if (typedChars > 0) return 'Typed';
    return 'Not tracked';
  }
  function showMessage(message, isError = false) {
    rowsNode.replaceChildren();
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 9;
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
      pagination = data.pagination || { page: 1, pageSize, total: (data.records || []).length, pages: 1 };
      page = pagination.page || page;
      const suggestions = $('#teacherHistoryStudentSuggestions');
      if (suggestions) {
        suggestions.replaceChildren(...students.map((student) => {
          const option = document.createElement('option');
          option.value = student.name || '';
          option.label = student.number || '';
          return option;
        }).filter((option) => option.value));
      }
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
      const sql = document.createElement('td'); sql.className = 'teacher-history-sql'; sql.textContent = item.sql || '';
      const input = document.createElement('td'); input.className = 'teacher-history-input';
      const pastedChars = Number(item.pastedChars) || 0;
      const pasteCount = Number(item.pasteCount) || 0;
      const typedChars = Number(item.typedChars) || 0;
      const inputLabel = inputMethodLabel(item);
      if (['Pasted', 'Typed + pasted', 'Mixed'].includes(inputLabel)) {
        const badge = document.createElement('span'); badge.className = 'paste-badge';
        badge.textContent = inputLabel;
        badge.title = `${pasteCount} paste event${pasteCount === 1 ? '' : 's'} · ${pastedChars.toLocaleString()} pasted characters${Number(item.editsAfterPaste) ? ` · ${Number(item.editsAfterPaste).toLocaleString()} edits after paste` : ''}. Browser cannot identify the source tab or app.`;
        input.append(badge);
      } else if (inputLabel !== 'Not tracked') {
        const badge = document.createElement('span'); badge.className = 'typed-badge'; badge.textContent = inputLabel;
        badge.title = inputLabel === 'Typed' ? `${typedChars.toLocaleString()} typed characters` : inputLabel === 'File' ? 'Statement was loaded using Open file.' : 'Statement was recalled from SQL history.';
        input.append(badge);
      } else {
        input.textContent = 'Not tracked';
        input.title = 'Input method was not recorded for this statement.';
      }
      const result = document.createElement('td'); result.className = `teacher-history-result ${item.status === 'success' ? 'success' : 'error'}`;
      result.textContent = item.status === 'success' ? 'Success' : 'Failed'; result.title = item.message || '';
      const duration = document.createElement('td'); duration.textContent = `${Number(item.durationMs || 0).toLocaleString()} ms`;
      const count = document.createElement('td'); count.textContent = Number(item.rows || 0).toLocaleString();
      const action = document.createElement('td'); const copy = document.createElement('button'); copy.type = 'button'; copy.className = 'teacher-history-copy'; copy.textContent = 'Copy'; copy.title = 'Copy statement';
      copy.addEventListener('click', async () => { try { await navigator.clipboard.writeText(item.sql || ''); copy.textContent = 'Copied'; setTimeout(() => { copy.textContent = 'Copy'; }, 1200); } catch { $('#teacherHistoryUpdated').textContent = 'Clipboard access is unavailable.'; } });
      action.append(copy); row.append(student, time, source, sql, input, result, duration, count, action); rowsNode.append(row);
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
    const columns = ['Student', 'Student ID', 'Executed at', 'Compiler', 'Statement', 'Input method', 'Typed characters', 'Pasted characters', 'Paste events', 'Edits after paste', 'Result', 'Duration ms', 'Rows', 'Message'];
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
      const rows = records.map((item) => [item.studentName, item.studentNumber, item.executedAt, compilerSourceLabel(item.source), item.sql, inputMethodLabel(item), item.typedChars, item.pastedChars, item.pasteCount, item.editsAfterPaste, item.status, item.durationMs, item.rows, item.message]);
      const csv = [columns, ...rows].map((values) => values.map((value) => `"${String(value ?? '').replaceAll('"', '""')}"`).join(',')).join('\r\n');
      const link = document.createElement('a'); link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' })); link.download = 'student-sql-history.csv'; link.click(); URL.revokeObjectURL(link.href);
    } catch (error) { showMessage(error.message || 'Could not export student SQL history.', true); }
    finally { button.disabled = false; }
  });
  loadHistory();
})();
