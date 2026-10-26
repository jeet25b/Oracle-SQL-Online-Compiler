(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const classFilter = $('#attendanceClass');
  const labFilter = $('#attendanceLab');
  const dateFrom = $('#attendanceDateFrom');
  const dateTo = $('#attendanceDateTo');
  const search = $('#attendanceSearch');
  const body = $('#attendanceTable');
  let report = null;
  let sort = { key: 'studentName', direction: 1 };
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
      const tr = document.createElement('tr'); const td = document.createElement('td'); td.colSpan = 10; td.className = 'activity-empty'; td.textContent = report.students.length ? 'No students match this search.' : 'No students found for this class or lab.'; tr.append(td); body.append(tr);
    } else rows.forEach((student) => {
      const tr = document.createElement('tr');
      const studentCell = document.createElement('td'); studentCell.className = 'activity-student'; const name = document.createElement('strong'); name.textContent = student.studentName; studentCell.append(name); const id = document.createElement('small'); id.textContent = student.studentNumber || ''; studentCell.append(id);
      const present = document.createElement('td'); present.textContent = `${student.presentDays} / ${student.eligibleDays}`;
      const absent = document.createElement('td'); absent.textContent = student.absentDays;
      const percent = document.createElement('td'); percent.className = 'attendance-percent'; percent.textContent = `${student.attendancePercent}%`;
      const first = document.createElement('td'); first.textContent = formatTime(student.firstLogin);
      const logout = document.createElement('td'); logout.textContent = student.openSession ? 'Session open' : `${formatTime(student.lastLogout)}${student.logoutEstimated && student.lastLogout ? ' · estimated' : ''}`;
      const arrival = document.createElement('td'); arrival.textContent = student.arrivalStatus === 'late' ? 'Late' : student.arrivalStatus === 'on_time' ? 'On time' : 'Set start time';
      const counts = document.createElement('td'); counts.textContent = `${student.onTimeDays} / ${student.lateDays}`;
      const duration = document.createElement('td'); duration.textContent = formatDuration(student.durationSeconds);
      const last = document.createElement('td'); last.textContent = student.lastPresent || '—';
      tr.append(studentCell, present, absent, percent, first, logout, arrival, counts, duration, last); body.append(tr);
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
    try { report = await readJson(await fetch(`/api/teacher/attendance-report?${params}`, { credentials: 'same-origin', cache: 'no-store' })); render(); }
    catch (error) { body.replaceChildren(); const tr = document.createElement('tr'); const td = document.createElement('td'); td.colSpan = 10; td.className = 'activity-empty error'; td.textContent = error.message; tr.append(td); body.append(tr); $('#attendanceUpdated').textContent = 'Attendance could not be loaded.'; }
  }
  document.querySelectorAll('[data-attendance-sort]').forEach((button) => button.addEventListener('click', () => {
    if (sort.key === button.dataset.attendanceSort) sort.direction *= -1; else sort = { key: button.dataset.attendanceSort, direction: 1 };
    document.querySelectorAll('[data-attendance-sort]').forEach((item) => { const active = item === button; item.closest('th').setAttribute('aria-sort', active ? (sort.direction > 0 ? 'ascending' : 'descending') : 'none'); });
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
  $('#attendanceRefresh').addEventListener('click', async () => { try { await loadLabs(); } catch {} await refresh(); });
  $('#attendanceExport').addEventListener('click', () => {
    if (!report) return;
    const lines = [['Student','Student ID','Present days','Eligible weekdays','Absent weekdays','Attendance %','First login','Last logout','Arrival','On time days','Late days','Duration seconds','Last present'], ...(report.students || []).map((student) => [student.studentName,student.studentNumber,student.presentDays,student.eligibleDays,student.absentDays,student.attendancePercent,student.firstLogin,student.lastLogout,student.arrivalStatus,student.onTimeDays,student.lateDays,student.durationSeconds,student.lastPresent])];
    const csv = lines.map((line) => line.map((value) => `"${String(value ?? '').replaceAll('"','""')}"`).join(',')).join('\r\n');
    const link = document.createElement('a'); link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' })); link.download = `student-attendance-${dateFrom.value}-${dateTo.value}.csv`; link.click(); URL.revokeObjectURL(link.href);
  });
  $('#scheduleScope').value = 'class';
  (async () => { try { await loadLabs(); } catch {} await refresh(); })();
})();
