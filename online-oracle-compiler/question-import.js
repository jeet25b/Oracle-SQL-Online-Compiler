(() => {
  document.querySelectorAll('[data-question-file]').forEach(input => {
    const form = input.closest('form');
    const list = form?.querySelector('[data-question-list]');
    if (!form || !list) return;
    const parsed = form.querySelector('[name="question_file_parsed"]');
    const status = form.querySelector('[data-question-import-status]');
    const submit = form.querySelector('[type="submit"]');
    let controller;
    const selector = '.test-question-row, .test-question-card';
    const cardStyle = !!list.querySelector('.test-question-card');
    const field = (label, name, value, required = false) => {
      const wrapper = document.createElement('label'); wrapper.append(label);
      const textarea = document.createElement('textarea');
      textarea.name = name; textarea.rows = 3; textarea.value = value || ''; textarea.required = required;
      wrapper.append(textarea); return wrapper;
    };
    input.addEventListener('change', async () => {
      controller?.abort();
      if (parsed) parsed.value = '0';
      if (!input.files?.length) { if (submit) submit.disabled = false; return; }
      controller = new AbortController();
      const current = controller;
      const data = new FormData(); data.append('question_file', input.files[0]);
      data.append('csrfmiddlewaretoken', form.querySelector('[name="csrfmiddlewaretoken"]')?.value || '');
      if (status) status.textContent = 'Extracting questions and reference SQL answers…';
      if (submit) submit.disabled = true;
      try {
        const response = await fetch('/api/portal/questions/parse', {method:'POST', body:data, credentials:'same-origin', signal:current.signal});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Could not read that question file.');
        const records = result.questions;
        if (!Array.isArray(records) || !records.length) throw new Error('No questions were found.');
        const manual = [...list.querySelectorAll(':is(.test-question-row, .test-question-card):not([data-imported-question])')].filter(row => [...row.querySelectorAll('textarea')].some(textarea => textarea.value.trim()));
        if (manual.length + records.length > 200) throw new Error('Import at most 200 questions in total.');
        list.querySelectorAll('[data-imported-question]').forEach(row => row.remove());
        list.querySelectorAll(selector).forEach(row => { if (![...row.querySelectorAll('textarea')].some(textarea => textarea.value.trim())) row.remove(); });
        for (const record of records) {
          const row = document.createElement('div'); row.className = cardStyle ? 'test-question-card' : 'test-question-row'; row.dataset.importedQuestion = '';
          const number = document.createElement('span'); number.className = cardStyle ? 'q-card-num' : '';
          row.append(number, field('Question','question',record.prompt,true), field('Reference SQL answer','expected_sql',record.expected_sql), field('Verification SQL / test calls','verification_sql',record.verification_sql));
          const markLabel = document.createElement('label'); markLabel.append('Marks');
          const mark = document.createElement('input'); mark.type = 'number'; mark.name = 'question_marks'; mark.className = 'q-mark-input'; mark.min = '0.5'; mark.step = '0.5'; mark.required = true; mark.value = form.querySelector('[name="default_question_marks"]')?.value || '1'; markLabel.append(mark); row.append(markLabel);
          list.append(row);
        }
        [...list.querySelectorAll(selector)].forEach((row,index) => { const number=row.querySelector('span'); if(number) number.textContent=(cardStyle ? 'Question #' : '')+String(index+1)+(cardStyle ? '' : '.'); });
        if (parsed) parsed.value = '1';
        const answers = records.filter(record => record.expected_sql?.trim()).length;
        if (status) status.textContent = `${records.length} questions and ${answers} reference SQL answers extracted. Review and edit the fields below before saving.`;
      } catch (error) {
        if (error.name === 'AbortError') return;
        if (status) status.textContent = error.message || 'Could not read that question file.';
        input.value = ''; input.dispatchEvent(new Event('change', {bubbles:true}));
      } finally {
        if (controller === current && submit) submit.disabled = false;
      }
    });
  });
})();
