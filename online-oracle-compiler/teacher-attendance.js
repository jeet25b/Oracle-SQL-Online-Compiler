(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const classFilter = $('#attendanceClass');
  const labFilter = $('#attendanceLab');
  const dateFrom = $('#attendanceDateFrom');
  const dateTo = $('#attendanceDateTo');
  const search = $('#attendanceSearch');
  const searchSuggestions = window.PortalSearchSuggestions?.register(search);
  const body = $('#attendanceTable');
  let report = null;
  async function loadSessionLimit() {
    const params = new URLSearchParams({ classId: classFilter.value, labId: labFilter.value });
    const target = $('#sessionResetStudent');
    const previous = target.value;
    target.replaceChildren(new Option('Choose student', ''));
    (report?.students || []).forEach(student => target.add(new Option(`${student.studentName} · ${student.studentNumber || ''}`, student.studentId)));
    target.value = previous;
    if (!classFilter.value && !labFilter.value) { $('#sessionLimitStatus').textContent = 'Choose a class or lab above.'; return; }
    try {
      const data = await readJson(await fetch(`/api/teacher/session-limits?${params}`, { credentials: 'same-origin', cache: 'no-store' }));
      $('#sessionLimitMinutes').value = data.limitMinutes;
      $('#sessionLimitStatus').textContent = `${data.limitMinutes} minutes per day${data.inherited ? ' · inherited from class' : ''}.`;
    } catch (error) { $('#sessionLimitStatus').textContent = error.message; }
  }
  async function changeSessionLimit(action) {
    const scope = $('#sessionResetScope').value;
    const payload = { action, classId: classFilter.value || labFilter.selectedOptions[0]?.dataset.classId, labId: labFilter.value, limitMinutes: $('#sessionLimitMinutes').value };
    if (action === 'reset') {
      payload.scope = scope;
      if (scope === 'lab' && !payload.labId) { $('#sessionLimitStatus').textContent = 'Choose a lab first.'; return; }
      if (scope === 'class') payload.labId = '';
      if (scope === 'student') payload.studentId = $('#sessionResetStudent').value;
    }
    try {
      const data = await readJson(await fetch('/api/teacher/session-limits', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() }, body: JSON.stringify(payload) }));
      $('#sessionLimitStatus').textContent = action === 'reset' ? `Today's allowance restored for ${data.resetCount} student(s).` : `Daily allowance saved: ${data.limitMinutes} minutes.`;
    } catch (error) { $('#sessionLimitStatus').textContent = error.message; }
  }
  $('#saveSessionLimit').addEventListener('click', () => changeSessionLimit('save'));
  $('#resetSessionLimit').addEventListener('click', () => changeSessionLimit('reset'));
  const view = $('#attendanceView');
  let dailySort = { key: 'date', direction: -1 };
  function filteredStudents() {
    const query = search.value.trim().toLocaleLowerCase();
    return (report?.students || []).filter((student) => !query || `${student.studentName} ${student.studentNumber || ''}`.toLocaleLowerCase().includes(query));
  }
  function dailyRows() {
    return filteredStudents().flatMap((student) => (student.dailyAttendance || []).map((day) => ({ ...day, studentName: student.studentName, studentNumber: student.studentNumber })))
      .sort((a, b) => {
        const left = a[dailySort.key] ?? ''; const right = b[dailySort.key] ?? '';
        const order = typeof left === 'number' ? left - right : String(left).localeCompare(String(right), undefined, { numeric: true, sensitivity: 'base' });
        return order * dailySort.direction || b.date.localeCompare(a.date) || a.studentName.localeCompare(b.studentName);
      });
  }
  function renderDaily() {
    const dailyBody = $('#attendanceDailyTable');
    dailyBody.replaceChildren();
    const fragment = document.createDocumentFragment();
    for (const row of dailyRows()) {
      const tr = document.createElement('tr');
      const values = [row.date, `${row.studentName} ${row.studentNumber || ''}`, row.status,
        formatTime(row.firstLogin), row.openSession ? 'Session open' : `${formatTime(row.lastLogout)}${row.logoutEstimated ? ' · estimated' : ''}`,
        row.status === 'Absent' ? '—' : row.arrivalStatus === 'late' ? 'Late' : row.arrivalStatus === 'on_time' ? 'On time' : 'Set start time',
        row.status === 'Absent' ? '—' : formatDuration(row.durationSeconds)];
      values.forEach((value) => { const td = document.createElement('td'); td.textContent = value; tr.append(td); });
      fragment.append(tr);
    }
    if (!fragment.childNodes.length) {
      const tr = document.createElement('tr'); const td = document.createElement('td'); td.colSpan = 7; td.textContent = 'No attendance records match the selected dates and students.'; tr.append(td); fragment.append(tr);
    }
    dailyBody.append(fragment);
    $('#attendanceDailyWrap').hidden = view.value !== 'daily';
    $('#attendanceSummaryWrap').hidden = view.value === 'daily';
  }
  let sort = { key: 'studentName', direction: 1 };
  function enableColumnResizing(table, storageKey) {
    if (!table?.tHead?.rows[0]) return;
    let colgroup = table.querySelector('colgroup');
    if (!colgroup) { colgroup = document.createElement('colgroup'); table.insertBefore(colgroup, table.firstChild); }
    const headers = [...table.tHead.rows[0].cells];
    const columns = headers.map((header, index) => { if (!colgroup.children[index]) colgroup.append(document.createElement('col')); return colgroup.children[index]; });
    while (colgroup.children.length > columns.length) colgroup.lastElementChild.remove();
    let saved = [];
    try { saved = JSON.parse(localStorage.getItem(storageKey) || '[]'); } catch {}
    columns.forEach((column, index) => { const width = Number(saved[index]); if (width >= 72 && width <= 1000) column.style.width = `${width}px`; });
    headers.forEach((header, index) => {
      if (header.querySelector('.column-resize-handle')) return;
      header.classList.add('resizable-column-header');
      const handle = document.createElement('span'); handle.className = 'column-resize-handle'; handle.title = 'Drag to resize this column';
      handle.setAttribute('role', 'separator'); handle.setAttribute('aria-orientation', 'vertical'); handle.setAttribute('aria-label', `Resize ${header.textContent.trim()} column`);
      handle.addEventListener('pointerdown', (event) => {
        event.preventDefault(); const startX = event.clientX;
        const startWidth = columns[index].getBoundingClientRect().width || header.getBoundingClientRect().width;
        handle.setPointerCapture(event.pointerId);
        const move = (moveEvent) => { columns[index].style.width = `${Math.max(72, Math.min(1000, startWidth + moveEvent.clientX - startX))}px`; };
        const finish = () => {
          handle.removeEventListener('pointermove', move); handle.removeEventListener('pointerup', finish); handle.removeEventListener('pointercancel', finish);
          try { localStorage.setItem(storageKey, JSON.stringify(columns.map((column) => Math.round(column.getBoundingClientRect().width)))); } catch {}
        };
        handle.addEventListener('pointermove', move); handle.addEventListener('pointerup', finish); handle.addEventListener('pointercancel', finish);
      });
      header.append(handle);
    });
    table.classList.add('resizable-table');
  }
  enableColumnResizing($('.attendance-table'), `teacher-attendance-columns:${document.body.dataset.userId || 'local'}:register-v3`);
  const today = new Date();
  const toInputDate = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  dateTo.value = toInputDate(today);
  const start = new Date(today); start.setDate(start.getDate() - 29); dateFrom.value = toInputDate(start);
  const csrfToken = () => { const item = document.cookie.split('; ').find((part) => part.startsWith('ora_csrf=')); return item ? decodeURIComponent(item.slice('ora_csrf='.length)) : ''; };
  async function readJson(response) {
    const text = await response.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; } catch { throw new Error(`Unexpected server response (HTTP ${response.status}).`); }
    if (!response.ok) throw new Error(data.error || 'Attendance request failed.');
    return data;
  }
  function formatTime(value) {
    if (!value) return '—';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '—' : new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date);
  }
  function formatDuration(value) {
    const seconds = Math.max(0, Number(value) || 0);
    const hours = Math.floor(seconds / 3600); const minutes = Math.floor((seconds % 3600) / 60);
    return hours ? `${hours}h ${minutes}m` : `${minutes}m`;
  }
  async function loadLabs() {
    const params = new URLSearchParams({ activity: '1' });
    if (classFilter.value) params.set('class', classFilter.value);
    const selected = labFilter.value;
    const data = await readJson(await fetch(`/api/compiler/labs?${params}`, { credentials: 'same-origin' }));
    labFilter.replaceChildren(new Option('All labs', ''));
    (data.labs || []).forEach((lab) => { const option = new Option(`${lab.name} · ${lab.className}`, String(lab.id)); option.dataset.classId = String(lab.classId); labFilter.add(option); });
    labFilter.value = [...labFilter.options].some((option) => option.value === selected) ? selected : '';
  }
  function render() {
    if (!report) return;
    renderDaily();
    const query = search.value.trim().toLocaleLowerCase();
    const rows = [...(report.students || [])].filter((student) => !query || `${student.studentName} ${student.studentNumber || ''}`.toLocaleLowerCase().includes(query));
    rows.sort((a, b) => {
      const left = a[sort.key] ?? ''; const right = b[sort.key] ?? '';
      const order = typeof left === 'string' ? left.localeCompare(right, undefined, { numeric: true, sensitivity: 'base' }) : left - right;
      return order * sort.direction || (a.studentName || '').localeCompare(b.studentName || '', undefined, { sensitivity: 'base' });
    });
    body.replaceChildren();
    $('#attendanceCount').textContent = query ? `${rows.length} of ${report.students.length} students` : `${rows.length} students`;
    if (!rows.length) {
      const tr = document.createElement('tr'); const td = document.createElement('td'); td.colSpan = 7; td.className = 'activity-empty'; td.textContent = report.students.length ? 'No students match this search.' : 'No students found for this class or lab.'; tr.append(td); body.append(tr);
    } else rows.forEach((student) => {
      const tr = document.createElement('tr');
      const studentCell = document.createElement('td'); studentCell.className = 'activity-student'; const name = document.createElement('strong'); name.textContent = student.studentName; studentCell.append(name); const id = document.createElement('small'); id.textContent = student.studentNumber || ''; studentCell.append(id);
      const presentPercent = document.createElement('td'); presentPercent.className = 'attendance-days-percent';
      const present = document.createElement('div'); present.textContent = `${student.presentDays} / ${student.eligibleDays}`;
      const percent = document.createElement('div'); percent.className = 'attendance-percent'; percent.textContent = `${student.attendancePercent}%`;
      presentPercent.append(present, percent);
      const loginLogout = document.createElement('td'); loginLogout.className = 'attendance-login-logout';
      const login = document.createElement('div'); login.textContent = formatTime(student.lastLogin);
      const logout = document.createElement('div'); logout.textContent = student.openSession ? 'Session open' : `${formatTime(student.lastLogout)}${student.logoutEstimated && student.lastLogout ? ' · estimated' : ''}`;
      loginLogout.append(login, logout);
      const arrival = document.createElement('td'); arrival.textContent = student.arrivalStatus === 'late' ? 'Late' : student.arrivalStatus === 'on_time' ? 'On time' : 'Set start time';
      const counts = document.createElement('td'); counts.textContent = `${student.onTimeDays} / ${student.lateDays}`;
      const duration = document.createElement('td'); duration.textContent = formatDuration(student.durationSeconds);
      const last = document.createElement('td'); last.textContent = student.lastPresent || '—';
      tr.append(studentCell, presentPercent, loginLogout, arrival, counts, duration, last); body.append(tr);
    });
    $('#attendanceUpdated').textContent = `${report.scope || 'Selected scope'} · ${report.from} to ${report.to} · ${report.eligibleDays} weekdays · ${report.scheduledStartTime ? `arrival time ${report.scheduledStartTime} (${report.scheduleLevel} schedule)` : 'expected start time not set'}`;
    const timeInput = $('#attendanceStartTime');
    if (document.activeElement !== timeInput) timeInput.value = report.scheduledStartTime || '';
    $('#attendanceScheduleStatus').textContent = report.scheduledStartTime ? `Current expected start: ${report.scheduledStartTime} (${report.scheduleLevel}).` : 'No expected start time set for this scope.';
  }
  async function refresh() {
    const params = new URLSearchParams({ from: dateFrom.value || toInputDate(today), to: dateTo.value || toInputDate(today) });
    if (classFilter.value) params.set('class', classFilter.value);
    if (labFilter.value) params.set('lab', labFilter.value);
    try { report = await readJson(await fetch(`/api/teacher/attendance-report?${params}`, { credentials: 'same-origin', cache: 'no-store' })); searchSuggestions?.setItems((report.students || []).map((student) => ({ label: student.studentName, detail: student.studentNumber, value: student.studentName }))); render(); await loadSessionLimit(); }
    catch (error) { report = null; for (const target of [body, $('#attendanceDailyTable')]) { target.replaceChildren(); const tr = document.createElement('tr'); const td = document.createElement('td'); td.colSpan = 7; td.className = 'activity-empty error'; td.textContent = error.message; tr.append(td); target.append(tr); } $('#attendanceUpdated').textContent = 'Attendance could not be loaded.'; }
  }
  document.querySelectorAll('[data-attendance-sort]').forEach((button) => button.addEventListener('click', () => {
    if (sort.key === button.dataset.attendanceSort) sort.direction *= -1; else sort = { key: button.dataset.attendanceSort, direction: 1 };
    document.querySelectorAll('[data-attendance-sort]').forEach((item) => { const active = item === button; item.closest('th').setAttribute('aria-sort', active ? (sort.direction > 0 ? 'ascending' : 'descending') : 'none'); });
    const statsHeader = button.closest('.attendance-days-percent-header');
    if (statsHeader) statsHeader.setAttribute('aria-sort', sort.direction > 0 ? 'ascending' : 'descending');
    render();
  }));
  $('#saveAttendanceSchedule').addEventListener('click', async () => {
    const labOption = labFilter.selectedOptions[0];
    const classId = classFilter.value || labOption?.dataset.classId;
    const labId = $('#scheduleScope').value === 'lab' ? labFilter.value : '';
    const status = $('#attendanceScheduleStatus');
    if (!classId || ($('#scheduleScope').value === 'lab' && !labId)) { status.textContent = 'Choose a class or lab first.'; return; }
    status.textContent = 'Saving…';
    try {
      const response = await fetch('/api/teacher/attendance-schedule', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() }, body: JSON.stringify({ classId, labId, startTime: $('#attendanceStartTime').value }) });
      const data = await readJson(response); status.textContent = data.startTime ? `Expected start saved: ${data.startTime}.` : 'Expected start time cleared.'; await refresh();
    } catch (error) { status.textContent = error.message; }
  });
  $('#scheduleScope').addEventListener('change', () => { if ($('#scheduleScope').value === 'lab' && !labFilter.value) $('#attendanceScheduleStatus').textContent = 'Choose a lab first.'; });
  classFilter.addEventListener('change', async () => { try { await loadLabs(); } catch {} await refresh(); });
  labFilter.addEventListener('change', refresh);
  dateFrom.addEventListener('change', refresh); dateTo.addEventListener('change', refresh);
  search.addEventListener('input', render);
  view.addEventListener('change', render);
  document.querySelectorAll('[data-attendance-daily-sort]').forEach((button) => button.addEventListener('click', () => {
    const key = button.dataset.attendanceDailySort;
    dailySort = { key, direction: dailySort.key === key ? -dailySort.direction : 1 };
    document.querySelectorAll('[data-attendance-daily-sort]').forEach((item) => {
      const active = item === button;
      item.closest('th').setAttribute('aria-sort', active ? (dailySort.direction > 0 ? 'ascending' : 'descending') : 'none');
      item.textContent = `${item.dataset.label}${active ? (dailySort.direction > 0 ? ' ↑' : ' ↓') : ''}`;
    });
    render();
  }));
  $('#attendanceRefresh').addEventListener('click', async () => { try { await loadLabs(); } catch {} await refresh(); });
  $('#attendanceExport').addEventListener('click', () => {
    if (!report) return;
    const lines = view.value === 'daily'
      ? [['Date','Student','Student ID','Status','First login','Last logout','Session open','Logout estimated','Arrival','Duration seconds'], ...dailyRows().map((row) => [row.date,row.studentName,row.studentNumber,row.status,row.firstLogin,row.lastLogout,row.openSession,row.logoutEstimated,row.arrivalStatus,row.durationSeconds])]
      : [['Student','Student ID','Present days','Eligible weekdays','Attendance %','Last login','Last logout','Arrival','On time days','Late days','Duration seconds','Last present'], ...filteredStudents().map((student) => [student.studentName,student.studentNumber,student.presentDays,student.eligibleDays,student.attendancePercent,student.lastLogin,student.lastLogout,student.arrivalStatus,student.onTimeDays,student.lateDays,student.durationSeconds,student.lastPresent])];
    const csv = lines.map((line) => line.map((value) => `"${String(value ?? '').replaceAll('"','""')}"`).join(',')).join('\r\n');
    const link = document.createElement('a'); link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' })); link.download = `student-attendance-${dateFrom.value}-${dateTo.value}.csv`; link.click(); URL.revokeObjectURL(link.href);
  });
  $('#scheduleScope').value = 'class';
  (async () => { try { await loadLabs(); } catch {} await refresh(); })();
})();
