(() => {
  'use strict';
  const $ = (selector, root = document) => root.querySelector(selector);
  const setText = (selector, value) => { const element = $(selector); if (element) element.textContent = value; };
  const classFilter = $('#activityClass');
  const labFilter = $('#activityLab');
  const dateFilter = $('#activityDate');
  const presenceTimeout = $('#presenceOfflineTimeout');
  const presenceTimeoutStatus = $('#presenceTimeoutStatus');
  const attendanceFrom = $('#attendanceDateFrom');
  const attendanceTo = $('#attendanceDateTo');
  const roster = $('#activityRoster');
  const dialog = $('#createLabDialog');
  let latestData = null;
  let latestAttendance = null;
  let refreshTimer = 0;
  let rosterSort = { key: 'lastActivity', direction: -1 };
  let attendanceSort = { key: 'studentName', direction: 1 };

  function enableColumnResizing(table, storageKey) {
    if (!table?.tHead?.rows[0]) return;
    let colgroup = table.querySelector('colgroup');
    if (!colgroup) { colgroup = document.createElement('colgroup'); table.insertBefore(colgroup, table.firstChild); }
    const headers = [...table.tHead.rows[0].cells];
    const columns = headers.map((header, index) => {
      if (!colgroup.children[index]) colgroup.append(document.createElement('col'));
      return colgroup.children[index];
    });
    while (colgroup.children.length > columns.length) colgroup.lastElementChild.remove();
    let saved = [];
    try { saved = JSON.parse(localStorage.getItem(storageKey) || '[]'); } catch {}
    columns.forEach((column, index) => { const width = Number(saved[index]); if (width >= 72 && width <= 1000) column.style.width = `${width}px`; });
    headers.forEach((header, index) => {
      if (header.querySelector('.column-resize-handle')) return;
      header.classList.add('resizable-column-header');
      const handle = document.createElement('span');
      handle.className = 'column-resize-handle'; handle.title = 'Drag to resize this column';
      handle.setAttribute('role', 'separator'); handle.setAttribute('aria-orientation', 'vertical');
      handle.setAttribute('aria-label', `Resize ${header.textContent.trim()} column`);
      handle.addEventListener('pointerdown', (event) => {
        event.preventDefault();
        const startX = event.clientX;
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
  enableColumnResizing($('.attendance-table'), `teacher-attendance-columns:${document.body.dataset.userId || 'local'}:live-v3`);

  const localDate = () => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  };
  if (dateFilter) dateFilter.value = localDate();
  if (attendanceTo) attendanceTo.value = localDate();
  if (attendanceFrom) { const start = new Date(); start.setDate(start.getDate() - 29); attendanceFrom.value = `${start.getFullYear()}-${String(start.getMonth() + 1).padStart(2, '0')}-${String(start.getDate()).padStart(2, '0')}`; }
  const formatDateTime = (value, estimated = false) => {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '—';
    const formatted = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', ...(dateFilter.value !== localDate() ? { month: 'short', day: 'numeric' } : {}) }).format(date);
    return estimated ? `${formatted} · estimated` : formatted;
  };
  const formatDuration = (seconds) => {
    seconds = Math.max(0, Number(seconds) || 0);
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return hours ? `${hours}h ${minutes}m` : `${minutes}m`;
  };
  const formatCount = (value) => Number(value || 0).toLocaleString();
  function lastExecutionInputLabel(student) {
    const inputMethod = student.lastExecutionInputMethod;
    const typedChars = Number(student.lastExecutionTypedChars) || 0;
    const pastedChars = Number(student.lastExecutionPastedChars) || 0;
    const edited = Number(student.lastExecutionEditsAfterPaste) > 0;
    if (inputMethod === 'mixed' && typedChars > 0 && pastedChars === 0) return 'History recall + Edited';
    let label = inputMethod === 'history_recall' ? 'History recall' : edited && (pastedChars || inputMethod === 'mixed') ? 'Pasted' : ({ typed: 'Typed', pasted: 'Pasted', file: 'File', internal_copy: 'Internal copy', mixed: typedChars && pastedChars ? 'Typed + pasted' : 'Mixed' })[inputMethod] || (typedChars && pastedChars ? 'Typed + pasted' : pastedChars ? 'Pasted' : typedChars ? 'Typed' : '');
    if (edited) label = label ? `${label} + Edited` : 'Edited';
    return label;
  }
  function visibleRosterStudents(data) {
    const query = ($('#activityStudentSearch')?.value || '').trim().toLocaleLowerCase();
    const priority = { active: 0, idle: 1, portal: 2, offline: 3 };
    return [...(data.students || [])]
      .filter((student) => !query || `${student.studentName || ''} ${student.studentNumber || ''}`.toLocaleLowerCase().includes(query))
      .sort((a, b) => {
        let left, right;
        if (rosterSort.key === 'lastActivity') {
          const statusOrder = (priority[a.status] ?? 4) - (priority[b.status] ?? 4);
          if (statusOrder) return statusOrder;
          left = a.lastActivity ? new Date(a.lastActivity).getTime() : 0;
          right = b.lastActivity ? new Date(b.lastActivity).getTime() : 0;
        } else if (rosterSort.key === 'status') {
          left = priority[a.status] ?? 4; right = priority[b.status] ?? 4;
        } else if (['inTime', 'outTime', 'lastExecution'].includes(rosterSort.key)) {
          const field = rosterSort.key;
          left = a[field] ? new Date(a[field]).getTime() : 0;
          right = b[field] ? new Date(b[field]).getTime() : 0;
        } else if (rosterSort.key === 'studentName') {
          left = a.studentName || ''; right = b.studentName || '';
        } else {
          left = Number(a[rosterSort.key] || 0); right = Number(b[rosterSort.key] || 0);
        }
        const result = typeof left === 'string' ? left.localeCompare(right, undefined, { numeric: true, sensitivity: 'base' }) : left - right;
        return result * rosterSort.direction || (a.studentName || '').localeCompare(b.studentName || '', undefined, { sensitivity: 'base' });
      });
  }
  function updateRosterSortControls() {
    document.querySelectorAll('[data-roster-sort]').forEach((button) => {
      if (!button.dataset.label) button.dataset.label = button.textContent.trim();
      const active = button.dataset.rosterSort === rosterSort.key;
      button.textContent = `${button.dataset.label}${active ? (rosterSort.direction > 0 ? ' ↑' : ' ↓') : ' ↕'}`;
      button.closest('th')?.setAttribute('aria-sort', active ? (rosterSort.direction > 0 ? 'ascending' : 'descending') : 'none');
    });
    document.querySelectorAll('[data-attendance-sort]').forEach((button) => {
      if (!button.dataset.label) button.dataset.label = button.textContent.trim();
      const active = button.dataset.attendanceSort === attendanceSort.key;
      button.textContent = `${button.dataset.label}${active ? (attendanceSort.direction > 0 ? ' ↑' : ' ↓') : ' ↕'}`;
      button.closest('th')?.setAttribute('aria-sort', active ? (attendanceSort.direction > 0 ? 'ascending' : 'descending') : 'none');
    });
    document.querySelectorAll('.attendance-days-percent-header').forEach((header) => {
      const active = [...header.querySelectorAll('[data-attendance-sort]')].some((button) => button.dataset.attendanceSort === attendanceSort.key);
      header.setAttribute('aria-sort', active ? (attendanceSort.direction > 0 ? 'ascending' : 'descending') : 'none');
    });
  }
  const csrfToken = () => {
    const cookie = document.cookie.split('; ').find((part) => part.startsWith('ora_csrf='));
    return cookie ? decodeURIComponent(cookie.slice('ora_csrf='.length)) : '';
  };
  async function readJsonResponse(response, fallbackMessage) {
    const body = await response.text();
    let data;
    try { data = body ? JSON.parse(body) : {}; }
    catch {
      const status = response.status ? ` (HTTP ${response.status})` : '';
      throw new Error(`The server returned an unexpected response${status}. Please refresh and try again.`);
    }
    if (!response.ok) throw new Error(data.error || fallbackMessage);
    return data;
  }
  async function loadPresenceTimeout() {
    if (!presenceTimeout) return;
    try {
      const response = await fetch('/api/teacher/presence-timeout', { credentials: 'same-origin', cache: 'no-store' });
      const data = await readJsonResponse(response, 'Could not load the offline timeout.');
      presenceTimeout.value = String(data.seconds);
    } catch (error) {
      if (presenceTimeoutStatus) presenceTimeoutStatus.textContent = error.message;
    }
  }
  presenceTimeout?.addEventListener('change', async () => {
    if (presenceTimeoutStatus) presenceTimeoutStatus.textContent = 'Saving…';
    presenceTimeout.disabled = true;
    try {
      const response = await fetch('/api/teacher/presence-timeout', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ seconds: Number(presenceTimeout.value) }),
      });
      const data = await readJsonResponse(response, 'Could not save the offline timeout.');
      presenceTimeout.value = String(data.seconds);
      if (presenceTimeoutStatus) presenceTimeoutStatus.textContent = 'Saved for your student monitor.';
      refreshActivity();
    } catch (error) {
      if (presenceTimeoutStatus) presenceTimeoutStatus.textContent = error.message;
      loadPresenceTimeout();
    } finally { presenceTimeout.disabled = false; }
  });
  async function loadLabs() {
    const params = new URLSearchParams({ activity: '1' });
    if (classFilter.value) params.set('class', classFilter.value);
    const response = await fetch(`/api/compiler/labs?${params}`, { credentials: 'same-origin' });
    const data = await readJsonResponse(response, 'Could not load compiler labs.');
    const selected = labFilter.value;
    labFilter.replaceChildren(new Option('All labs', ''));
    (data.labs || []).forEach((lab) => labFilter.add(new Option(`${lab.name} · ${lab.className}`, String(lab.id))));
    labFilter.value = [...labFilter.options].some((option) => option.value === selected) ? selected : '';
  }
  function updateSummary(data) {
    const summary = data.summary || {};
    setText('#metricActive', summary.active ?? 0);
    setText('#metricIdle', (summary.idle || 0) + (summary.portal || 0));
    setText('#metricOffline', summary.offline ?? 0);
    setText('#metricSuccess', summary.success ?? 0);
    setText('#metricFailed', summary.failed ?? 0);
    setText('#metricEditSource', summary.trackedExecutions ? `${formatCount(summary.typedChars)} / ${formatCount(summary.pastedChars)}` : 'Not tracked');
    setText('#metricPasteEdits', summary.trackedExecutions ? `${formatCount(summary.pasteCount)} / ${formatCount(summary.editsAfterPaste)}` : 'Not tracked');
    setText('#metricPasteEvents', formatCount(summary.pasteEvents));
    setText('#metricSwitchEvents', `${formatCount(summary.pageHiddenEvents)} / ${formatCount(summary.windowBlurEvents)}`);
    setText('#metricEditSourceScope', `${formatCount(summary.trackedExecutions)} tracked executions`);
    setText('#metricActiveTime', formatDuration(summary.activeSeconds));
    setText('#metricIdleTime', formatDuration(summary.idleSeconds));
    setText('#metricAverageTime', formatDuration(summary.present ? Math.round((summary.activeSeconds || 0) / summary.present) : 0));
    const visibleCount = visibleRosterStudents(data).length;
    const totalCount = (data.students || []).length;
    setText('#compilerRosterCount', $('#activityStudentSearch')?.value.trim() ? `${visibleCount} of ${totalCount} students` : `${totalCount} students`);
    setText('#activityUpdated', `Updated ${formatDateTime(data.updatedAt)} · live refresh every 10 seconds`);
  }
  function renderRoster(data) {
    roster.replaceChildren();
    const students = visibleRosterStudents(data);
    if (!students.length) {
      const row = document.createElement('tr');
      const cell = document.createElement('td'); cell.colSpan = 7; cell.className = 'activity-empty'; cell.textContent = data.students?.length ? 'No students match this search.' : 'No active enrollments found for this filter.';
      row.append(cell); roster.append(row); return;
    }
    const totalCount = (data.students || []).length;
    setText('#compilerRosterCount', $('#activityStudentSearch')?.value.trim() ? `${students.length} of ${totalCount} students` : `${totalCount} students`);
    students.forEach((student) => {
      const row = document.createElement('tr');
      const name = document.createElement('td'); name.className = 'activity-student';
      const nameLine = document.createElement('span'); nameLine.className = 'activity-student-line';
      const dot = document.createElement('i'); dot.className = `activity-presence-dot ${student.status === 'active' ? 'active' : student.status === 'offline' ? 'offline' : 'online-idle'}`;
      dot.setAttribute('aria-label', student.status === 'active' ? 'Online and active' : student.status === 'offline' ? 'Offline' : 'Online and inactive');
      dot.title = dot.getAttribute('aria-label'); dot.setAttribute('role', 'img');
      const nameButton = document.createElement('button'); nameButton.type = 'button'; nameButton.textContent = student.studentName;
      nameLine.append(dot, nameButton); name.append(nameLine);
      const number = document.createElement('small'); number.textContent = student.studentNumber || student.classNames.join(', '); name.append(number);
      const lastActive = document.createElement('td');
      const activityTime = document.createElement('span'); activityTime.textContent = formatDateTime(student.lastActivity || student.lastSeen); lastActive.append(activityTime);
      if (student.status !== 'offline') {
        const location = document.createElement('small'); location.className = 'activity-current-page';
        location.textContent = student.currentPage === 'compiler' ? 'Online compiler' : 'Online portal';
        lastActive.append(location);
      }
      const duration = document.createElement('td'); duration.className = 'activity-duration'; duration.textContent = `${formatDuration(student.sessionActiveSeconds)} / ${formatDuration(student.sessionIdleSeconds)}`;
      const executions = document.createElement('td'); executions.className = 'activity-executions';
      const executionDetails = document.createElement('span'); executionDetails.className = 'activity-executions-inner';
      const total = document.createElement('strong'); total.textContent = String(student.totalExecutions || 0); executionDetails.append(total);
      const successes = document.createElement('span'); successes.textContent = `✓ ${student.successfulExecutions || 0}`; successes.className = 'success';
      const failures = document.createElement('span'); failures.textContent = `× ${student.failedExecutions || 0}`; failures.className = 'failure'; executionDetails.append(successes, failures); executions.append(executionDetails);
      const lastExec = document.createElement('td'); lastExec.className = 'activity-last-execution';
      const lastExecTime = document.createElement('span'); lastExecTime.textContent = student.lastExecution ? formatDateTime(student.lastExecution) : '—'; lastExec.append(lastExecTime);
      const methodLabel = lastExecutionInputLabel(student);
      if (methodLabel) { const method = document.createElement('small'); method.className = 'activity-last-execution-method'; method.textContent = methodLabel; lastExec.append(method); }
      if (student.lastExecutionStatement) { const statement = document.createElement('code'); statement.textContent = student.lastExecutionStatement; statement.title = student.lastExecutionStatement; lastExec.append(statement); }
      const editSource = document.createElement('td'); editSource.className = 'activity-edit-source';
      if (student.trackedExecutions || student.historyRecallCount) {
        editSource.textContent = `T ${formatCount(student.typedChars)} · P ${formatCount(student.pastedChars)} · E ${formatCount(student.editsAfterPaste)} · H ${formatCount(student.historyRecallCount)}`;
    editSource.title = `Typed characters · pasted characters · edits after paste or history recall · history recall executions; ${formatCount(student.pasteCount)} paste actions across ${formatCount(student.trackedExecutions)} tracked executions`;
      } else editSource.textContent = 'Not tracked';
      const clientActivity = document.createElement('td');
      clientActivity.textContent = `${formatCount(student.pasteEvents)} · ${formatCount(student.pageHiddenEvents)} · ${formatCount(student.windowBlurEvents)}`;
      clientActivity.title = 'Compiler paste events · times the compiler tab became hidden (tab change or minimized window) · times the browser window lost focus';
      row.append(name, lastActive, duration, executions, editSource, clientActivity, lastExec);
      row.addEventListener('click', () => openStudentDetails(student));
      nameButton.addEventListener('click', (event) => { event.stopPropagation(); openStudentDetails(student); });
      roster.append(row);
    });
  }
  function renderAttendance(data) {
    latestAttendance = data;
    const query = ($('#activityStudentSearch')?.value || '').trim().toLocaleLowerCase();
    const students = [...(data.students || [])].filter((student) => !query || `${student.studentName || ''} ${student.studentNumber || ''}`.toLocaleLowerCase().includes(query));
    students.sort((a, b) => {
      const left = a[attendanceSort.key] ?? ''; const right = b[attendanceSort.key] ?? '';
      const result = typeof left === 'string' ? left.localeCompare(right, undefined, { numeric: true, sensitivity: 'base' }) : left - right;
      return result * attendanceSort.direction || (a.studentName || '').localeCompare(b.studentName || '', undefined, { sensitivity: 'base' });
    });
    const body = $('#attendanceRoster'); body.replaceChildren();
    setText('#attendanceRosterCount', query ? `${students.length} of ${(data.students || []).length} students` : `${students.length} students`);
    const presentStudents = (data.students || []).filter((student) => student.presentDays > 0).length;
    setText('#metricStudents', (data.students || []).length);
    setText('#metricPresent', presentStudents);
    setText('#metricAbsent', Math.max(0, (data.students || []).length - presentStudents));
    if (!students.length) {
      const row = document.createElement('tr'); const cell = document.createElement('td'); cell.colSpan = 7; cell.className = 'activity-empty'; cell.textContent = data.students?.length ? 'No students match this search.' : 'No enrolled students found for this filter.'; row.append(cell); body.append(row);
    } else students.forEach((student) => {
      const row = document.createElement('tr');
      const name = document.createElement('td'); name.className = 'activity-student';
      const strong = document.createElement('strong'); strong.textContent = student.studentName; name.append(strong);
      const id = document.createElement('small'); id.textContent = student.studentNumber || ''; name.append(id);
      const presentPercent = document.createElement('td'); presentPercent.className = 'attendance-days-percent';
      const present = document.createElement('div'); present.textContent = `${student.presentDays} / ${student.eligibleDays}`;
      const percent = document.createElement('div'); percent.className = 'attendance-percent'; percent.textContent = `${student.attendancePercent}%`;
      presentPercent.append(present, percent);
      const loginLogout = document.createElement('td'); loginLogout.className = 'attendance-login-logout';
      const lastLogin = document.createElement('div'); lastLogin.textContent = student.lastLogin ? formatDateTime(student.lastLogin) : '—';
      const lastLogout = document.createElement('div'); lastLogout.textContent = student.openSession ? 'Session open' : student.lastLogout ? `${formatDateTime(student.lastLogout)}${student.logoutEstimated ? ' · est.' : ''}` : '—';
      loginLogout.append(lastLogin, lastLogout);
      const arrival = document.createElement('td'); arrival.textContent = student.arrivalStatus === 'late' ? 'Late' : student.arrivalStatus === 'on_time' ? 'On time' : 'Set start time';
      const timing = document.createElement('td'); timing.textContent = `${student.onTimeDays} / ${student.lateDays}`;
      const duration = document.createElement('td'); duration.textContent = formatDuration(student.durationSeconds);
      const last = document.createElement('td'); last.textContent = student.lastPresent || '—';
      row.append(name, presentPercent, loginLogout, arrival, timing, duration, last); body.append(row);
    });
    setText('#attendanceUpdated', `${data.scope || 'Selected scope'} · ${data.from} to ${data.to} · ${data.eligibleDays} weekdays counted. ${data.attendanceBasis || ''}`);
  }
  async function refreshAttendance() {
    const params = new URLSearchParams({ from: attendanceFrom.value || localDate(), to: attendanceTo.value || localDate() });
    if (classFilter.value) params.set('class', classFilter.value);
    if (labFilter.value) params.set('lab', labFilter.value);
    const body = $('#attendanceRoster');
    try {
      const response = await fetch(`/api/teacher/attendance-report?${params}`, { credentials: 'same-origin', cache: 'no-store' });
      renderAttendance(await readJsonResponse(response, 'Could not load attendance records.'));
    } catch (error) {
      body.replaceChildren(); const row = document.createElement('tr'); const cell = document.createElement('td');
      cell.colSpan = 7; cell.className = 'activity-empty error'; cell.textContent = error.message; row.append(cell);
      setText('#attendanceUpdated', 'Attendance report could not be loaded.');
    }
  }
  async function refreshActivity() {
    const params = new URLSearchParams({ date: dateFilter.value || localDate() });
    if (classFilter.value) params.set('class', classFilter.value);
    if (labFilter.value) params.set('lab', labFilter.value);
    try {
      const response = await fetch(`/api/teacher/compiler-activity?${params}`, { credentials: 'same-origin', cache: 'no-store' });
      const data = await readJsonResponse(response, 'Could not load activity data.');
      latestData = data;
      updateSummary(data);
      renderRoster(data);
    } catch (error) {
      roster.innerHTML = '';
      const row = document.createElement('tr'); const cell = document.createElement('td');
      cell.colSpan = 7; cell.className = 'activity-empty error'; cell.textContent = error.message;
      row.append(cell); roster.append(row);
      setText('#activityUpdated', 'Activity update failed');
    }
  }
  async function refreshAll() { try { await loadLabs(); } catch { /* Activity endpoint will report access errors. */ } await Promise.all([refreshActivity(), refreshAttendance()]); }
  function scheduleLiveRefresh() {
    clearInterval(refreshTimer);
    if ($('#activityLive').checked) refreshTimer = setInterval(() => { refreshActivity(); refreshAttendance(); }, 10000);
  }
  classFilter.addEventListener('change', refreshAll);
  $('#activityStudentSearch')?.addEventListener('input', () => { if (latestData) { updateSummary(latestData); renderRoster(latestData); } if (latestAttendance) renderAttendance(latestAttendance); });
  document.querySelectorAll('[data-roster-sort]').forEach((button) => button.addEventListener('click', () => {
    if (rosterSort.key === button.dataset.rosterSort) rosterSort.direction *= -1;
    else rosterSort = { key: button.dataset.rosterSort, direction: 1 };
    updateRosterSortControls();
    if (latestData) renderRoster(latestData);
  }));
  document.querySelectorAll('[data-attendance-sort]').forEach((button) => button.addEventListener('click', () => {
    if (attendanceSort.key === button.dataset.attendanceSort) attendanceSort.direction *= -1;
    else attendanceSort = { key: button.dataset.attendanceSort, direction: 1 };
    updateRosterSortControls();
    if (latestAttendance) renderAttendance(latestAttendance);
  }));
  updateRosterSortControls();
  loadPresenceTimeout();
  labFilter.addEventListener('change', refreshAll);
  dateFilter.addEventListener('change', refreshActivity);
  attendanceFrom.addEventListener('change', refreshAttendance);
  attendanceTo.addEventListener('change', refreshAttendance);
  $('#refreshAttendance').addEventListener('click', refreshAttendance);
  $('#refreshActivity').addEventListener('click', refreshAll);
  $('#activityLive').addEventListener('change', scheduleLiveRefresh);
  $('#showCreateLab').addEventListener('click', () => dialog.showModal());
  $('#cancelCreateLab').addEventListener('click', () => dialog.close());
  $('#createLabForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const status = $('#createLabStatus'); status.textContent = 'Saving…';
    try {
      const response = await fetch('/api/compiler/labs', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ classId: $('#newLabClass').value, name: $('#newLabName').value.trim() })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Could not save the lab.');
      status.textContent = 'Lab created.';
      $('#newLabName').value = '';
      await refreshAll();
      setTimeout(() => dialog.close(), 500);
    } catch (error) { status.textContent = error.message; }
  });

  async function openStudentDetails(student) {
    const detailDialog = $('#studentActivityDialog');
    if (!detailDialog) return;
    setText('#studentActivityTitle', `${student.studentName} · Daily detail`);
    const target = $('#studentActivityTimeline'); target.replaceChildren();
    const loading = document.createElement('p'); loading.textContent = 'Loading session and execution events…'; target.append(loading);
    detailDialog.showModal();
    try {
      const params = new URLSearchParams({ date: dateFilter.value || localDate() });
      if (classFilter.value) params.set('class', classFilter.value);
      if (labFilter.value) params.set('lab', labFilter.value);
      const response = await fetch(`/api/teacher/compiler-activity/student/${student.studentId}?${params}`, { credentials: 'same-origin', cache: 'no-store' });
      const data = await readJsonResponse(response, 'Could not load student details.');
      target.replaceChildren();
      if (!data.events?.length) { const empty = document.createElement('p'); empty.textContent = 'No session or execution events recorded for this date.'; target.append(empty); }
      (data.events || []).forEach((event) => {
        const item = document.createElement('article'); item.className = 'activity-timeline-item';
        const time = document.createElement('time'); time.textContent = formatDateTime(event.at, event.estimated);
        const title = document.createElement('strong'); title.textContent = event.title;
        const detail = document.createElement('small'); detail.textContent = event.detail;
        item.append(time, title, detail);
        if (event.preview) { const preview = document.createElement('pre'); preview.textContent = event.preview; item.append(preview); }
        target.append(item);
      });
    } catch (error) { target.replaceChildren(); const message = document.createElement('p'); message.textContent = error.message; target.append(message); }
  }
  $('#closeStudentActivity').addEventListener('click', () => $('#studentActivityDialog').close());
  $('#exportActivity').addEventListener('click', () => {
    if (!latestData) return;
    const columns = ['Student', 'Student ID', 'Status', 'Attendance', 'In time', 'Out time', 'Out estimated', 'Active seconds', 'Idle seconds', 'Executions', 'Success', 'Failed', 'Typed characters', 'Pasted characters', 'Paste actions', 'Edits after paste', 'History recall executions', 'Tracked executions', 'Editor paste events', 'Compiler tab hidden events', 'Browser window blur events', 'Last seen', 'Last execution input method', 'Last executed statement'];
    const rows = (latestData.students || []).map((student) => [student.studentName, student.studentNumber, student.status, student.attendance, student.inTime || '', student.outTime || '', student.outEstimated ? 'Yes' : 'No', student.sessionActiveSeconds, student.sessionIdleSeconds, student.totalExecutions, student.successfulExecutions, student.failedExecutions, student.typedChars ?? '', student.pastedChars ?? '', student.pasteCount ?? '', student.editsAfterPaste ?? '', student.historyRecallCount ?? '', student.trackedExecutions || 0, student.pasteEvents || 0, student.pageHiddenEvents || 0, student.windowBlurEvents || 0, student.lastSeen || '', lastExecutionInputLabel(student), student.lastExecutionStatement || '']);
    const csv = [columns, ...rows].map((line) => line.map((value) => `"${String(value ?? '').replaceAll('"', '""')}"`).join(',')).join('\r\n');
    const link = document.createElement('a'); link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' })); link.download = `compiler-activity-${dateFilter.value || localDate()}.csv`; link.click(); URL.revokeObjectURL(link.href);
  });
  $('#exportAttendance').addEventListener('click', () => {
    if (!latestAttendance) return;
    const columns = ['Student', 'Student ID', 'Present days', 'Eligible weekdays', 'Attendance %', 'Last login', 'Last logout', 'Arrival', 'On time days', 'Late days', 'Duration seconds', 'Last present'];
    const rows = (latestAttendance.students || []).map((student) => [student.studentName, student.studentNumber, student.presentDays, student.eligibleDays, student.attendancePercent, student.lastLogin, student.lastLogout, student.arrivalStatus, student.onTimeDays, student.lateDays, student.durationSeconds, student.lastPresent]);
    const csv = [columns, ...rows].map((line) => line.map((value) => `"${String(value ?? '').replaceAll('"', '""')}"`).join(',')).join('\r\n');
    const link = document.createElement('a'); link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' })); link.download = `student-attendance-${attendanceFrom.value}-${attendanceTo.value}.csv`; link.click(); URL.revokeObjectURL(link.href);
  });
  refreshAll();
  scheduleLiveRefresh();
})();
