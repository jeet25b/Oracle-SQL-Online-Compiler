(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const csrf = () => decodeURIComponent(document.cookie.split('; ').find(v => v.startsWith('ora_csrf='))?.slice(9) || '');
  async function api(path, data) {
    const response = await fetch(path, {credentials:'same-origin', cache:'no-store',
      ...(data ? {method:'POST', headers:{'Content-Type':'application/json','X-CSRFToken':csrf()}, body:JSON.stringify(data)} : {})});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || 'Setup help is temporarily unavailable.');
    return value;
  }
  const controls = $('[data-guide-teacher-controls]');
  if (controls) {
    const enabled = controls.querySelector('[data-guide-enabled]');
    const restart = controls.querySelector('[data-guide-restart]');
    const copy = controls.querySelector('[data-guide-copy]');
    const status = controls.querySelector('[data-guide-control-status]');
    let settings;
    async function save(action) {
      [enabled,restart,copy].forEach(el => el.disabled = true);
      try {
        settings = await api('/api/teacher/setup-guide', action ? {action} : null);
        enabled.checked = settings.enabled;
        status.textContent = action === 'restart' ? 'Setup help restarted. Assigned students will see it on their next page or within 30 seconds.' : settings.enabled ? 'Setup help is enabled for your students.' : 'Setup help is disabled.';
      } catch (error) { status.textContent = error.message; }
      finally { [enabled,restart,copy].forEach(el => el.disabled = !settings); }
    }
    enabled.addEventListener('change', () => save(enabled.checked ? 'enable' : 'disable'));
    restart.addEventListener('click', () => save('restart'));
    copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(new URL(settings.registrationPath,location.origin).href); status.textContent = 'Guided registration link copied. Share it with new students.'; }
      catch { status.textContent = `Registration link: ${new URL(settings.registrationPath,location.origin).href}`; }
    });
    save();
  }
  const registration = !!$('#accessLink');
  const compiler = !!$('#openConnection');
  const choosingClass = location.pathname === '/portal/choose-class/';
  const stage = registration ? 'registration' : choosingClass ? 'class-selection' : compiler ? 'connection' : 'portal';
  const query = new URLSearchParams(location.search);
  let teacherId = query.get('guide');
  try { if (teacherId) localStorage.setItem('setup-guide-teacher',teacherId); else teacherId = localStorage.getItem('setup-guide-teacher'); } catch {}
  const path = '/api/portal/setup-guide' + (registration && teacherId ? `?teacher=${encodeURIComponent(teacherId)}` : '');
  let policy, active = false, index = 0, steps = [], anchor, previousFocus, revisionKey = '';
  const trigger = document.createElement('button');
  trigger.type = 'button'; trigger.className = 'setup-guide-button'; trigger.textContent = 'Setup help'; trigger.hidden = true;
  trigger.setAttribute('aria-label','Start guided portal and Oracle setup');
  const toolbar = $('.ide-header-controls') || $('.top-actions') || $('.topbar-right');
  if (!toolbar) return;
  toolbar.prepend(trigger);
  const panel = document.createElement('div');
  panel.className = 'setup-guide-panel'; panel.hidden = true; panel.tabIndex = -1;
  panel.setAttribute('role','dialog'); panel.setAttribute('aria-labelledby','setupGuideTitle');
  panel.innerHTML = '<p class="setup-guide-step" aria-live="polite"></p><h2 id="setupGuideTitle"></h2><p class="setup-guide-description"></p><p class="setup-guide-feedback" role="status" aria-live="polite"></p><div class="setup-guide-actions"><button type="button" data-guide-back>Back</button><button type="button" data-guide-next>Next</button><button type="button" data-guide-close>Close help</button></div>';
  const ring = document.createElement('div'); ring.className = 'setup-guide-highlight'; ring.hidden = true; ring.setAttribute('aria-hidden','true');
  const popovers = typeof panel.showPopover === 'function';
  if (popovers) { panel.setAttribute('popover','manual'); ring.setAttribute('popover','manual'); }
  const back = panel.querySelector('[data-guide-back]'), next = panel.querySelector('[data-guide-next]');
  function mount(element,host) {
    if (element.parentElement !== host) host.append(element);
    element.hidden = false;
    if (popovers && !element.matches(':popover-open')) element.showPopover();
  }
  function hide(element) {
    if (popovers && element.matches(':popover-open')) element.hidePopover();
    element.hidden = true;
  }
  function close(snooze = true) {
    active = false; hide(panel); hide(ring);
    if (snooze) try { sessionStorage.setItem(revisionKey,'1'); } catch {}
    if (previousFocus?.isConnected && !previousFocus.closest('dialog:not([open])')) previousFocus.focus();
  }
  function connectionOpen() {
    if (!$('#connectDialog')?.open && !$('#connectionDot')?.classList.contains('connected')) $('#openConnection')?.click();
  }
  function buildSteps() {
    if (choosingClass) return [
      {target:'.academic-form .theme-select-trigger',title:'Choose your class',text:'Choose the course, branch, semester, section and group that match your class. If your class is missing, ask your teacher or administrator before continuing.'},
      {target:'.academic-form button[type="submit"]',title:'Confirm your class',text:'Select Confirm class and continue. After enrollment, your teacher’s setup guide will help you open the Oracle compiler and connect your database.'}
    ];
    if (registration) return [
      {target:'#accessLink',title:'Register as a student',text:'Choose Student registration to create your portal account. Your teacher has enabled this guided setup.'},
      {target:'#registerForm',prepare:() => { if (!$('#registerDialog').open) $('#accessLink').click(); },title:'Complete your details',text:'Enter the required name, email and mobile details, and choose your portal password. This password is for this portal.'},
      {target:'#registerSubmit',title:'Create your portal account',text:'Select Create account and complete any verification requested. If approval is required, wait for your teacher or administrator before signing in.'},
      {target:'#loginForm',prepare:() => { if ($('#registerDialog').open) $('#registerDialog').close(); },title:'Sign in after registration',text:'Use the account ID or sign-in details provided by the portal. After approval and class selection, Setup help will guide you to Oracle.'}
    ];
    if (!compiler) {
      const selection = $('[data-engine="ORACLE"][data-ready="true"]') || $('[data-engine="PLSQL"][data-ready="true"]');
      return [{target:selection ? '[data-engine="'+selection.dataset.engine+'"][data-ready="true"]' : 'a[href="/online-compiler"]',title:'Open the Oracle compiler',text:selection ? 'Choose Oracle to open the live compiler workspace. Your teacher may need to assign a class or enable the compiler first.' : 'Choose Online Compiler, then select Oracle. Complete class selection first if the portal asks you to.',navigate:selection ? selection.dataset.href : '/online-compiler'}];
    }
    return [
      {target:'#openConnection',title:'Connect your Oracle database',text:'Select Connect to enter your personal Oracle schema details. If you need a free practice schema, the next step explains FreeSQL.'},
      {target:'#freeSqlConnectionHelp',prepare:() => { connectionOpen(); $('#freeSqlConnectionHelp').open = true; },title:'Get your FreeSQL schema details',text:'Open FreeSQL in the help box. Sign in or create an Oracle account, then open Schema Connection Details (look for Connect with…). Copy your schema username and generate a database password if needed. Return to this tab when ready.'},
      {target:'#connectForm [name="user"]',prepare:connectionOpen,title:'Enter the schema username',text:'Use the username shown in FreeSQL Schema Connection Details, or the database username your teacher provided. It is separate from your portal account ID.'},
      {target:'#connectForm [name="password"]',prepare:connectionOpen,title:'Enter the database password',text:'Enter the database password from the FreeSQL connection panel, or the one your teacher provided. The guide does not read or copy this field.'},
      {target:'#connectForm [name="connectString"]',prepare:connectionOpen,title:'Check the Oracle server',text:'Use the host, port and service from your schema’s connection panel. For TLS, use tcps://HOST:PORT/SERVICE. Replace the prefilled example if your schema uses different details.'},
      {target:'#saveCredentials',prepare:connectionOpen,title:'Choose whether to save credentials',text:'Saving is optional. Select this only if you want the portal to save your encrypted database password and reconnect after sign-in.'},
      {target:'#connectForm .dialog-submit',prepare:connectionOpen,title:'Connect to database',text:'Select Connect to database. Wait for success; if an error appears, check the username, database password and connection string. The guide will not submit credentials for you.'},
      {target:'#connectionLabel',title:'Check your connection',text:'When the status shows connected, you are ready to use SQLCL or SQL WorkSheet. You can try SELECT 1 FROM dual; in the editor.',connection:true}
    ];
  }
  function position() {
    if (!active || !anchor?.isConnected) return;
    const rect = anchor.getBoundingClientRect();
    ring.style.cssText = `left:${Math.max(2,rect.left-4)}px;top:${Math.max(2,rect.top-4)}px;width:${Math.min(rect.width+8,innerWidth-4)}px;height:${Math.min(rect.height+8,innerHeight-4)}px`;
    if (panel.classList.contains('setup-guide-inline')) return;
    const width = panel.offsetWidth, height = panel.offsetHeight;
    const gap = 12, edge = 8;
    const clamp = (value, maximum) => Math.max(edge,Math.min(value,maximum));
    let left, top;
    if (rect.right + gap + width <= innerWidth-edge) {
      left = rect.right+gap; top = clamp(rect.top,innerHeight-height-edge);
    } else if (rect.left-gap-width >= edge) {
      left = rect.left-gap-width; top = clamp(rect.top,innerHeight-height-edge);
    } else if (rect.bottom+gap+height <= innerHeight-edge) {
      left = clamp(rect.left,innerWidth-width-edge); top = rect.bottom+gap;
    } else if (rect.top-gap-height >= edge) {
      left = clamp(rect.left,innerWidth-width-edge); top = rect.top-gap-height;
    } else {
      // When there is no free space, put help in the document flow instead
      // of covering the instructions or form fields being explained.
      if (popovers && panel.matches(':popover-open')) panel.hidePopover();
      panel.removeAttribute('popover');
      panel.classList.add('setup-guide-inline');
      panel.style.left = ''; panel.style.top = '';
      anchor.insertAdjacentElement('beforebegin',panel);
      panel.scrollIntoView({block:'nearest',behavior:'instant'});
      return;
    }
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
  }

  function show() {
    const step = steps[index]; step.prepare?.();
    panel.classList.remove('setup-guide-inline');
    if (popovers) panel.setAttribute('popover','manual');
    anchor = step.connection && !$('#connectionDot')?.classList.contains('connected') && $('#connectDialog')?.open ? $('#connectForm .dialog-submit') : $(step.target);
    if (!anchor || !anchor.getClientRects().length) anchor = trigger;
    anchor.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});
    const host = anchor.closest('dialog[open]') || document.fullscreenElement || document.body;
    mount(ring,host); mount(panel,host);
    panel.querySelector('h2').textContent = step.title;
    panel.querySelector('.setup-guide-description').textContent = step.text;
    panel.querySelector('.setup-guide-step').textContent = `Setup help · ${index+1} of ${steps.length}`;
    panel.querySelector('.setup-guide-feedback').textContent = '';
    back.disabled = index === 0;
    next.textContent = step.navigate ? 'Open compiler' : index === steps.length-1 ? 'Finish' : 'Next';
    position(); panel.focus({preventScroll:true});
  }
  function start() {
    if (!policy?.enabled) return;
    previousFocus = document.activeElement;
    steps = buildSteps(); index = compiler && $('#connectionDot')?.classList.contains('connected') ? steps.length-1 : 0;
    if (compiler && $('#connectDialog')?.open && index === 0) index = 1;
    active = true; show();
  }
  trigger.addEventListener('click',start);
  back.addEventListener('click',() => { if (index > 0) { index--; show(); } });
  next.addEventListener('click',async () => {
    const step = steps[index];
    if (step.navigate) { location.assign(step.navigate); return; }
    if (index < steps.length-1) { index++; show(); return; }
    if (step.connection && !$('#connectionDot')?.classList.contains('connected')) {
      panel.querySelector('.setup-guide-feedback').textContent = 'Not connected yet. Use Back to return to the connection form, or close help and resume later.'; return;
    }
    try {
      if (compiler && !policy.teacher) await api(path,{revision:policy.revision});
      close();
    } catch (error) { panel.querySelector('.setup-guide-feedback').textContent = error.message; }
  });
  panel.querySelector('[data-guide-close]').addEventListener('click',() => close());
  document.addEventListener('keydown',event => { if (active && event.key === 'Escape') { event.preventDefault(); close(); } });
  document.addEventListener('scroll',position,{capture:true,passive:true});
  window.addEventListener('resize',position);
  if ($('#connectionDot')) new MutationObserver(() => {
    if (active && $('#connectionDot').classList.contains('connected')) { index = steps.length-1; show(); }
  }).observe($('#connectionDot'),{attributes:true,attributeFilter:['class']});
  if ($('#connectDialog')) new MutationObserver(() => {
    if (active && index === 0 && $('#connectDialog').open) { index = 1; show(); }
  }).observe($('#connectDialog'),{attributes:true,attributeFilter:['open']});
  // Show important setup outcomes as accessible, theme-aware popups.
  const notice = document.createElement('div');
  notice.className = 'setup-guide-panel setup-guide-notice';
  notice.hidden = true;
  notice.setAttribute('role','alertdialog');
  notice.setAttribute('aria-labelledby','setupGuideNoticeTitle');
  notice.setAttribute('aria-describedby','setupGuideNoticeText');
  notice.innerHTML = '<h2 id="setupGuideNoticeTitle">Setup update</h2><p id="setupGuideNoticeText"></p><div class="setup-guide-actions"><button type="button">Got it</button></div>';
  if (popovers) notice.setAttribute('popover','manual');
  let noticeOpen = false, noticeFocus, noticeKey = '', pendingNotice = 0;
  function dismissNotice(resume = true) {
    hide(notice); noticeOpen = false;
    if (resume && active) show();
    else if (resume && noticeFocus?.isConnected) noticeFocus.focus({preventScroll:true});
  }
  function notify(message, title = 'Setup needs your attention', source = null) {
    if (!policy?.enabled || policy.teacher || !message.trim()) return;
    const key = `${revisionKey}:${title}:${message}`;
    if (key === noticeKey && noticeOpen) return;
    noticeKey = key; noticeFocus = document.activeElement;
    if (active) { hide(panel); hide(ring); }
    notice.querySelector('h2').textContent = title;
    notice.querySelector('p').textContent = message;
    const host = source?.closest('dialog[open]') || $('dialog[open]') || document.fullscreenElement || document.body;
    mount(notice,host); noticeOpen = true;
    notice.querySelector('button').focus();
  }
  notice.querySelector('button').addEventListener('click',() => dismissNotice());
  notice.addEventListener('keydown',event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); dismissNotice(); }
  });
  const important = /error|fail|invalid|required|missing|unable|cannot|could not|pending|approval|verif|created|success|sent|registered|expired|incorrect|not match/i;
  ['#formStatus','#registerStatus','#registrationOtpStatus','#registrationOtpIntro','#connectError'].forEach(selector => {
    const source = $(selector);
    if (!source) return;
    let previous = '';
    new MutationObserver(() => {
      const message = source.textContent.trim();
      if (!message) { previous = ''; return; }
      if (message === previous || source.hidden || !source.getClientRects().length || !important.test(message) || /^(connecting|sending|checking|creating|please wait)/i.test(message)) return;
      previous = message;
      clearTimeout(pendingNotice);
      pendingNotice = setTimeout(() => notify(message,selector === '#connectError' ? 'Oracle connection needs attention' : 'Registration and sign-in update',source),0);
    }).observe(source,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['hidden']});
  });
  document.addEventListener('invalid',event => {
    if (!event.target.closest('#connectForm, #registerForm, #registrationOtpForm, #loginForm')) return;
    clearTimeout(pendingNotice);
    pendingNotice = setTimeout(() => notify('Complete the required fields and check the highlighted entries before continuing.','Check your setup details',event.target),0);
  },true);
  // Teachers can stop or restart help without requiring students to refresh.
  async function refresh() {
    try {
      const value = await api(path);
      const changed = !policy || value.teacherId !== policy.teacherId || value.revision !== policy.revision;
      policy = value; trigger.hidden = !policy.enabled;
      if (!policy.enabled) { if (active) close(false); if (noticeOpen) dismissNotice(false); return; }
      revisionKey = `setup-guide:${policy.userId || 'new'}:${policy.teacherId}:${policy.revision}:${stage}`;
      let dismissed = false;
      try { dismissed = sessionStorage.getItem(revisionKey) === '1'; } catch {}
      if (changed && !policy.teacher && !policy.completed && !dismissed) start();
    } catch { /* Leave normal registration and the compiler usable during outages. */ }
  }
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); },30000);
})();
