(() => {
  document.querySelectorAll('[data-select-all-classes], [data-clear-classes]').forEach(button => {
    button.addEventListener('click', () => {
      const scope = button.dataset.selectAllClasses || button.dataset.clearClasses;
      const selector = scope === 'test' ? '[data-test-class]' : '[data-daily-class-select]';
      const select = document.querySelector(selector);
      if (!select) return;
      const shouldSelect = Boolean(button.dataset.selectAllClasses);
      [...select.options].forEach(option => { option.selected = shouldSelect; });
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
  });

  const taskViews = document.querySelector('[data-daily-task-views]');
  if (taskViews) {
    const cards = [...document.querySelectorAll('[data-daily-task-card]')];
    const buttons = [...taskViews.querySelectorAll('[data-task-view]')];
    const emptyMessage = document.createElement('p');
    emptyMessage.className = 'daily-muted';
    emptyMessage.dataset.taskViewEmpty = '';
    emptyMessage.hidden = true;
    emptyMessage.setAttribute('role', 'status');
    taskViews.after(emptyMessage);
    const storageKey = `portal-daily-task-view:${taskViews.dataset.userId || 'student'}`;
    let activeView = 'pending';
    try {
      const savedView = localStorage.getItem(storageKey);
      if (['pending', 'submitted', 'revision', 'completed'].includes(savedView)) activeView = savedView;
    } catch {}
    const renderView = () => {
      let visibleCount = 0;
      const counts = { pending: 0, submitted: 0, revision: 0, completed: 0 };
      cards.forEach(card => { counts[card.dataset.taskState || 'pending'] += 1; });
      cards.forEach(card => {
        const visible = (card.dataset.taskState || 'pending') === activeView;
        card.hidden = !visible;
        if (visible) visibleCount += 1;
      });
      buttons.forEach(button => {
        const selected = button.dataset.taskView === activeView;
        button.setAttribute('aria-pressed', String(selected));
        const count = taskViews.querySelector(`[data-view-count="${button.dataset.taskView}"]`);
        if (count) count.textContent = String(counts[button.dataset.taskView]);
      });
      emptyMessage.hidden = visibleCount !== 0;
      emptyMessage.textContent = ({pending:'No new tasks are waiting. Choose another view to see your submitted work.',submitted:'No answers are awaiting review.',revision:'No tasks need revision.',completed:'Correct answers will appear here after evaluation.'})[activeView];
    };
    buttons.forEach(button => button.addEventListener('click', () => {
      activeView = button.dataset.taskView;
      try { localStorage.setItem(storageKey, activeView); } catch {}
      renderView();
    }));
    const focusedCard = cards.find(card => card.id === `daily-task-${taskViews.dataset.focusTask}`);
    if (focusedCard) activeView = focusedCard.dataset.taskState || 'pending';
    renderView();
  }

  const bulkForm = document.querySelector('#dailyTaskBulkActionForm');
  if (bulkForm) {
    const selections = [...document.querySelectorAll('[data-daily-task-select]')];
    const selectAll = bulkForm.querySelector('[data-daily-select-all]');
    const selectedCount = bulkForm.querySelector('[data-daily-selection-count]');
    const confirmSelected = [...bulkForm.querySelectorAll('[data-daily-confirm-selected]')];
    const syncSelection = () => {
      const count = selections.filter(input => input.checked).length;
      selectedCount.textContent = `${count} selected`;
      confirmSelected.forEach(button => { button.disabled = count === 0; });
      selectAll.checked = selections.length > 0 && count === selections.length;
      selectAll.indeterminate = count > 0 && count < selections.length;
    };
    selectAll.addEventListener('change', () => {
      selections.forEach(input => { input.checked = selectAll.checked; });
      syncSelection();
    });
    selections.forEach(input => input.addEventListener('change', syncSelection));
    syncSelection();
  }

  document.querySelectorAll('[data-daily-history]').forEach(root => {
    const form = root.closest('form');
    const answer = form.querySelector('textarea[name="answer"]');
    const historyId = form.querySelector('[name="history_id"]');
    const picker = root.querySelector('[data-history-picker]');
    const search = root.querySelector('[data-history-search]');
    const list = root.querySelector('[data-history-list]');
    const status = root.querySelector('[data-history-status]');
    const latest = root.querySelector('[data-use-latest]');
    const more = root.querySelector('[data-history-more]');
    let controller, timer, lastId;
    const select = record => {
      answer.value = record.sql;
      historyId.value = record.id;
      picker.open = false;
      status.textContent = 'Successful SQL added. Review it, then submit your answer.';
      answer.focus();
    };
    const explainPasteRule = event => {
      event.preventDefault();
      status.textContent = 'Pasting is limited to successfully executed SQL. Use the successful SQL history picker to add a statement.';
    };
    answer.addEventListener('paste', explainPasteRule);
    answer.addEventListener('drop', explainPasteRule);
    answer.addEventListener('input', () => {
      historyId.value = '';
      status.textContent = 'Answer edited. Your edited text will be submitted.';
    });
    async function load({ append = false, useLatest = false } = {}) {
      clearTimeout(timer);
      controller?.abort();
      const active = new AbortController();
      controller = active;
      latest.disabled = more.disabled = true;
      more.hidden = true;
      status.textContent = 'Loading successful SQL…';
      if (!append && !useLatest) list.replaceChildren();
      const query = new URLSearchParams({ task_id: root.dataset.taskId });
      if (!useLatest && search.value.trim()) query.set('q', search.value.trim());
      if (append && lastId) query.set('before', lastId);
      try {
        const response = await fetch(`/api/portal/daily-tasks/history?${query}`, {
          signal: active.signal, headers: { Accept: 'application/json' }, cache: 'no-store',
        });
        if (response.redirected) throw new Error('Please sign in again to load your history.');
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not load SQL history.');
        if (active.signal.aborted) return;
        if (useLatest) {
          if (data.statements.length) select(data.statements[0]);
          else status.textContent = 'No successful SQL yet. Run your answer in the compiler first, or type it below.';
          return;
        }
        data.statements.forEach(record => {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'daily-history-option';
          const label = document.createElement('small');
          label.textContent = `${record.source === 'SQL*Plus' ? 'SQLCL' : 'SQL WorkSheet'} · ${new Date(record.executed_at).toLocaleString()} · Use this statement`;
          const preview = document.createElement('pre');
          preview.textContent = record.sql.length > 400 ? `${record.sql.slice(0, 400)}…` : record.sql;
          button.append(label, preview);
          button.addEventListener('click', () => select(record));
          list.append(button);
        });
        lastId = data.statements.at(-1)?.id;
        more.hidden = !data.hasMore;
        status.textContent = list.childElementCount ? 'Choose a statement to fill your answer.' : 'No matching successful statements. Try another search or type your answer below.';
      } catch (error) {
        if (error.name !== 'AbortError') status.textContent = error.message || 'Could not load history. Try again.';
      } finally {
        if (controller === active) latest.disabled = more.disabled = false;
      }
    }
    latest.addEventListener('click', () => load({ useLatest: true }));
    picker.addEventListener('toggle', () => { if (picker.open) load(); });
    search.addEventListener('input', () => {
      controller?.abort();
      clearTimeout(timer);
      timer = setTimeout(() => load(), 250);
    });
    search.addEventListener('keydown', event => {
      if (event.key === 'Enter') { event.preventDefault(); load(); }
    });
    more.addEventListener('click', () => load({ append: true }));
  });
})();

// Keep daily-task answering on the assigned-tasks page instead of navigating away.
document.querySelectorAll('a[href^="/portal/sandbox/daily_task/"]').forEach(link => {
  const match = link.getAttribute('href').match(/daily_task\/(\d+)/); if (!match) return;
  const taskId = match[1], note = link.nextElementSibling;
  const panel = document.createElement('div'); panel.className = 'daily-inline-sandbox'; panel.dataset.taskId = taskId;
  panel.innerHTML = '<label>SQL answer<textarea rows="7" spellcheck="false" data-inline-answer></textarea></label><div class="daily-inline-actions"><button type="button" class="daily-button" data-inline-run>Run SQL</button><button type="button" class="daily-button" data-inline-save disabled>Save answer</button><button type="button" class="daily-button" data-inline-submit disabled>Submit</button></div><pre class="daily-inline-output" data-inline-output hidden></pre><p class="daily-muted" data-inline-status role="status" aria-live="polite"></p>';
  link.replaceWith(panel); note?.remove();
  let run = null; const answer = panel.querySelector('[data-inline-answer]'); const status = panel.querySelector('[data-inline-status]'); const output = panel.querySelector('[data-inline-output]');
  const buttons = {run:panel.querySelector('[data-inline-run]'),save:panel.querySelector('[data-inline-save]'),submit:panel.querySelector('[data-inline-submit]')};
  const csrf = () => document.cookie.split('; ').find(item => item.startsWith('ora_csrf='))?.split('=')[1] || '';
  const call = async (action, extra={}) => { const response = await fetch(`/api/portal/sandbox/daily_task/${taskId}`, {method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':decodeURIComponent(csrf())},body:JSON.stringify({action,runId:run?.id,inputMethod:'typed',...extra})}); const data=await response.json(); if(!response.ok) throw Error(data.error||'Sandbox request failed.'); return data; };
  const refreshRun = async () => { const response=await fetch(`/api/teacher/project-runs/${run.id}`,{credentials:'same-origin',cache:'no-store'}); run=await response.json(); return run; };
  const waitUntilReady = async () => { run=await call('start'); for(let attempt=0; attempt<60 && run.status!=='ready'; attempt++){ if(['paused','cancelled','completed'].includes(run.status)) break; await new Promise(resolve=>setTimeout(resolve,250)); await refreshRun(); } if(run.status!=='ready') throw Error('Sandbox is not ready yet. Try Run SQL again.'); };
  const waitForExecution = async () => { for(let attempt=0; attempt<80 && ['queued','running','pending'].includes(run.status); attempt++){ await new Promise(resolve=>setTimeout(resolve,250)); await refreshRun(); } const result=(run.results||[]).at(-1); output.hidden=!result || (!result.output && !result.error); output.textContent=result?.error || result?.output || ''; return result; };
  buttons.run.onclick = async () => { buttons.run.disabled=true; output.hidden=true; try { if(!run || ['cancelled','completed'].includes(run.status)) await waitUntilReady(); run=await call('run',{sql:answer.value}); const result=await waitForExecution(); const failed=run.status==='paused'||result?.status==='error'; buttons.save.disabled=buttons.submit.disabled=run.status!=='ready'||failed; status.textContent=failed?'Statement failed. Edit the statement and run SQL again.':result?.status==='success'?'Statement executed successfully.':'Execution did not complete.'; } catch(error){status.textContent=error.message;} finally {buttons.run.disabled=false;} };
  buttons.save.onclick = async () => { try { await call('save_answer',{index:(run.results||[]).findLast(item=>item.status==='success')?.index,questionId:Number(taskId)}); status.textContent='Answer saved.'; } catch(error){status.textContent=error.message;} };
  buttons.submit.onclick = async () => { try { await call('submit'); status.textContent='Daily task submitted.'; buttons.run.disabled=buttons.save.disabled=buttons.submit.disabled=true; } catch(error){status.textContent=error.message;} };
});

// Feedback choices use the same themed select controls as the rest of the portal.
document.querySelectorAll('[data-feedback-macro]').forEach(select => select.addEventListener('change', () => {
  const feedback = select.closest('form').querySelector('[name="feedback"]');
  if (select.value && feedback) { feedback.value = select.value; feedback.focus(); }
}));
