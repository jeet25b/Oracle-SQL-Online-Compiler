// Preserve unrelated navigator rows when refreshing a small metadata scope.
window.CompilerMergeObjects = (previous, incoming, names) => {
  const key=row=>JSON.stringify([row.OWNER,row.OBJECT_TYPE,row.OBJECT_NAME]);
  const keys=new Set(incoming.map(key));
  return previous.filter(row=>!names.includes(row.OBJECT_NAME)&&!names.includes(row.TABLE_NAME)&&!keys.has(key(row))).concat(incoming);
};
(() => {
  const menuButton = document.querySelector('[data-global-profile-menu]');
  const menu = document.querySelector('#globalProfileMenu');
  const avatarImage = document.querySelector('[data-global-avatar-image]');
  const avatarInitial = document.querySelector('[data-global-avatar-initial]');
  const uploadTrigger = document.querySelector('[data-global-avatar-upload-trigger]');
  const uploadInput = document.querySelector('[data-global-avatar-upload]');
  const toast = document.querySelector('#toast');
  const showMessage = (message) => {
    if (!toast) return;
    toast.textContent = message;
    toast.hidden = false;
  };
  const csrfToken = () => {
    const cookie = document.cookie.split('; ').find((part) => part.startsWith('ora_csrf='));
    return cookie ? decodeURIComponent(cookie.slice('ora_csrf='.length)) : '';
  };

  menuButton?.addEventListener('click', () => {
    const open = menu.hidden;
    menu.hidden = !open;
    menuButton.setAttribute('aria-expanded', String(open));
  });
  const passwordDialog = document.querySelector('#globalPasswordDialog');
  const passwordForm = document.querySelector('#globalPasswordForm');
  const passwordStatus = document.querySelector('#globalPasswordStatus');
  document.querySelector('[data-global-open-password]')?.addEventListener('click', () => {
    if (menu) menu.hidden = true;
    menuButton?.setAttribute('aria-expanded', 'false');
    passwordForm?.reset();
    if (passwordStatus) passwordStatus.hidden = true;
    passwordDialog?.showModal();
    passwordForm?.elements.namedItem('currentPassword')?.focus();
  });
  document.querySelectorAll('[data-close-global-password]').forEach((button) => {
    button.addEventListener('click', () => passwordDialog?.close());
  });
  passwordForm?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(passwordForm).entries());
    if (payload.newPassword !== payload.confirmPassword) {
      passwordStatus.textContent = 'The new passwords do not match.';
      passwordStatus.hidden = false;
      return;
    }
    const submit = passwordForm.querySelector('[type="submit"]');
    submit.disabled = true;
    passwordStatus.hidden = true;
    try {
      const response = await fetch('/api/portal/password/update', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify(payload)
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Password could not be updated.');
      passwordForm.reset();
      passwordDialog.close();
      showMessage(data.message || 'Password updated.');
    } catch (error) {
      passwordStatus.textContent = error.message || 'Password could not be updated.';
      passwordStatus.hidden = false;
    } finally {
      submit.disabled = false;
    }
  });
  document.addEventListener('click', (event) => {
    if (event.target instanceof Element && !event.target.closest('.profile-menu') && menu) {
      menu.hidden = true;
      menuButton?.setAttribute('aria-expanded', 'false');
    }
  });
  document.querySelector('[data-global-shortcut-help]')?.addEventListener('click', () => {
    document.querySelector('#shortcutDialog')?.showModal();
  });
  document.addEventListener('click', async (event) => {
    const button = event.target instanceof Element ? event.target.closest('#logoutBtn') : null;
    if (!button) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    button.disabled = true;
    try {
      const response = await fetch('/api/portal/logout', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'X-CSRFToken': csrfToken() }
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Sign out could not be completed.');
      window.location.assign(data.next || '/portal/');
    } catch (error) {
      showMessage(error.message || 'Sign out could not be completed.');
      button.disabled = false;
    }
  }, true);
  uploadTrigger?.addEventListener('click', () => {
    if (uploadInput) {
      uploadInput.value = '';
      uploadInput.click();
    }
  });
  uploadInput?.addEventListener('change', async () => {
    const file = uploadInput.files?.[0];
    if (!file || !uploadTrigger) return;
    uploadTrigger.disabled = true;
    try {
      const form = new FormData();
      form.append('avatar', file);
      const response = await fetch('/api/portal/avatar/update', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'X-CSRFToken': csrfToken() }, body: form
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Profile photo could not be updated.');
      if (avatarImage && data.avatarUrl) {
        avatarImage.src = `${data.avatarUrl}${data.avatarUrl.includes('?') ? '&' : '?'}v=${Date.now()}`;
        avatarImage.hidden = false;
        if (avatarInitial) avatarInitial.hidden = true;
      }
      showMessage(data.message || 'Profile photo updated.');
    } catch (error) {
      showMessage(error.message || 'Profile photo could not be updated.');
    } finally {
      uploadInput.value = '';
      uploadTrigger.disabled = false;
    }
  });

  fetch('/api/portal/profile', { credentials: 'same-origin' })
    .then((response) => response.ok ? response.json() : null)
    .then((profile) => {
      if (!profile) return;
      const initials = [profile.firstName, profile.middleName, profile.lastName]
        .filter(Boolean).map((part) => part.trim()[0]).join('').slice(0, 2).toUpperCase();
      if (avatarInitial) avatarInitial.textContent = initials || profile.username?.slice(0, 1).toUpperCase() || '?';
      if (avatarImage && profile.avatarUrl) {
        avatarImage.src = profile.avatarUrl;
        avatarImage.hidden = false;
        if (avatarInitial) avatarInitial.hidden = true;
      }
    })
    .catch(() => {});
})();

// Paint invisible Unicode characters in place without changing source or caret widths.
(() => {
  const unusual = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u00a0\u00ad\u034f\u061c\u1680\u2000-\u200f\u2028-\u202f\u205f\u2060-\u206f\u3000\ufeff]/u;
  const installed = new WeakMap();
  function install(input) {
    const parent = input.parentElement;
    if (getComputedStyle(parent).position === 'static') parent.style.position = 'relative';
    const layer = document.createElement('div');
    layer.className = 'invisible-character-overlay';
    layer.setAttribute('aria-hidden', 'true');
    const mirror = document.createElement('pre');
    Object.assign(mirror.style, { margin: '0', border: '0', boxSizing: 'border-box', background: 'transparent' });
    layer.append(mirror);
    parent.append(layer);
    let previous = null;
    const refresh = () => {
      if (!input.isConnected) { layer.remove(); return; }
      if (!input.getClientRects().length) { layer.hidden = true; return; }
      layer.hidden = false;
      const style = getComputedStyle(input);
      Object.assign(layer.style, {
        left: `${input.offsetLeft + input.clientLeft}px`,
        top: `${input.offsetTop + input.clientTop}px`,
        width: `${input.clientWidth}px`, height: `${input.clientHeight}px`
      });
      for (const key of ['fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'lineHeight',
        'letterSpacing', 'wordSpacing', 'tabSize', 'textAlign', 'textIndent',
        'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'whiteSpace',
        'overflowWrap', 'wordBreak']) mirror.style[key] = style[key];
      mirror.style.width = `${input.clientWidth}px`;
      mirror.style.transform = `translate(${-input.scrollLeft}px, ${-input.scrollTop}px)`;
      if (previous === input.value) return;
      previous = input.value;
      const fragment = document.createDocumentFragment();
      let plain = '';
      for (const character of previous) {
        if (!unusual.test(character)) { plain += character; continue; }
        if (plain) { fragment.append(document.createTextNode(plain)); plain = ''; }
        const mark = document.createElement('span');
        mark.className = 'invisible-character-box';
        mark.textContent = character;
        // These format/control characters occupy no visible text cell.
        if (/[\u0000-\u001f\u007f-\u009f\u00ad\u034f\u061c\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]/u.test(character)) {
          mark.classList.add('invisible-character-zero');
        }
        fragment.append(mark);
      }
      if (plain) fragment.append(document.createTextNode(plain));
      mirror.replaceChildren(fragment);
    };
    input.addEventListener('input', () => requestAnimationFrame(refresh));
    input.addEventListener('scroll', refresh, { passive: true });
    installed.set(input, refresh);
    refresh();
  }
  function refreshEditors() {
    document.querySelectorAll('#sqlEditor, #terminalInput, #editor, #cliInput').forEach((input) => {
      if (!installed.has(input)) install(input);
      else installed.get(input)();
    });
  }
  refreshEditors();
  // Also reflect file loads, font changes and replacement SQL*Plus prompts.
  window.setInterval(refreshEditors, 200);
})();

// Keep anchored menus above clipped editor cards while preserving their DOM/events.
(() => {
  if (!document.body.classList.contains('oracle-ide-page')) return;
  const caretMirrors = new WeakMap();
  function starterCaretRect(input) {
    let mirror = caretMirrors.get(input);
    if (!mirror) {
      mirror = document.createElement('div');
      mirror.setAttribute('aria-hidden', 'true');
      Object.assign(mirror.style, {
        position: 'fixed', left: '0', top: '0', visibility: 'hidden',
        pointerEvents: 'none', margin: '0', border: '0', boxSizing: 'border-box'
      });
      caretMirrors.set(input, mirror);
    }
    const host = document.fullscreenElement || document.body;
    if (mirror.parentElement !== host) host.append(mirror);
    const style = getComputedStyle(input);
    for (const property of ['fontFamily', 'fontSize', 'fontWeight', 'fontStyle',
      'lineHeight', 'letterSpacing', 'wordSpacing', 'tabSize', 'textAlign',
      'textIndent', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
      'whiteSpace', 'overflowWrap', 'wordBreak', 'direction']) {
      mirror.style[property] = style[property];
    }
    mirror.style.width = `${input.clientWidth}px`;
    const cursor = input.selectionStart ?? 0;
    const marker = document.createElement('span');
    marker.textContent = '\u200b';
    mirror.replaceChildren(document.createTextNode(input.value.slice(0, cursor)),
      marker, document.createTextNode(input.value.slice(cursor)));
    const box = input.getBoundingClientRect();
    const origin = mirror.getBoundingClientRect();
    const caret = marker.getBoundingClientRect();
    const lineHeight = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.2 || 24;
    const minLeft = box.left + input.clientLeft;
    const minTop = box.top + input.clientTop;
    const left = Math.max(minLeft, Math.min(minLeft + caret.left - origin.left - input.scrollLeft,
      minLeft + input.clientWidth));
    const top = Math.max(minTop, Math.min(minTop + caret.top - origin.top - input.scrollTop,
      minTop + Math.max(0, input.clientHeight - lineHeight)));
    // Use a plain rectangle: assigning DOMRect.bottom does not move its edge.
    return { left, right: left, top, bottom: top + lineHeight };
  }
  window.WorkbenchCaretRect = starterCaretRect;
  const definitions = [
    ['#sqlplusStarterMenu', '#terminalInput', true],
    ['#plsqlStarterMenu', '#sqlEditor', false],
    ['#convertCaseMenu', '#convertCaseToggle', false],
    ['#globalProfileMenu', '[data-global-profile-menu]', false],
    ['.palette-options', '.palette-picker > summary', false]
  ];
  for (const [selector, anchorSelector, preferAbove] of definitions) {
    const menu = document.querySelector(selector);
    const anchor = document.querySelector(anchorSelector);
    if (!menu || !anchor || !menu.showPopover) continue;
    menu.setAttribute('popover', 'manual');
    menu.classList.add('anchored-workspace-popup');
    let frame = 0;
    function place() {
      if (!menu.matches(':popover-open')) return;
      const rect = selector === '#plsqlStarterMenu'
        ? starterCaretRect(anchor) : anchor.getBoundingClientRect();
      const viewport = window.visualViewport;
      const leftEdge = (viewport?.offsetLeft || 0) + 8;
      const topEdge = (viewport?.offsetTop || 0) + 8;
      const rightEdge = leftEdge + (viewport?.width || innerWidth) - 16;
      const bottomEdge = topEdge + (viewport?.height || innerHeight) - 16;
      const below = Math.max(0, bottomEdge - rect.bottom - 6);
      const above = Math.max(0, rect.top - topEdge - 6);
      menu.style.setProperty('max-width', `${Math.max(0, rightEdge - leftEdge)}px`, 'important');
      menu.style.setProperty('max-height', `${Math.max(above, below)}px`, 'important');
      const height = menu.getBoundingClientRect().height;
      const useAbove = preferAbove ? above >= height || above > below : below < height && above > below;
      menu.style.setProperty('max-height', `${useAbove ? above : below}px`, 'important');
      const bounds = menu.getBoundingClientRect();
      const left = Math.max(leftEdge, Math.min(rect.left, rightEdge - bounds.width));
      const top = useAbove ? rect.top - bounds.height - 6 : rect.bottom + 6;
      menu.style.setProperty('left', `${left}px`, 'important');
      menu.style.setProperty('top', `${Math.max(topEdge, Math.min(top, bottomEdge - bounds.height))}px`, 'important');
      frame = requestAnimationFrame(sync);
    }
    function sync() {
      cancelAnimationFrame(frame);
      const details = menu.closest('details');
      const visible = !menu.hidden && (!details || details.open) &&
        anchor.getClientRects().length > 0 && getComputedStyle(anchor).visibility !== 'hidden';
      if (visible) {
        if (!menu.matches(':popover-open')) menu.showPopover();
        place();
      } else if (menu.matches(':popover-open')) menu.hidePopover();
    }
    new MutationObserver(sync).observe(menu, { attributes:true, attributeFilter:['hidden'] });
    menu.closest('details')?.addEventListener('toggle', sync);
    document.addEventListener('fullscreenchange', sync);
    // Panel switching can hide an anchor without changing the menu itself.
    new MutationObserver(sync).observe(document.querySelector('.ide-main') || document.body,
      { subtree:true, attributes:true, attributeFilter:['hidden'] });
    sync();
  }
})();

// Record only user-triggered pastes into compiler inputs, never other tabs/fields.
(() => {
  if (!document.querySelector('#sqlEditor, #editor')) return;
  let role='', pending=[];
  const token=()=>decodeURIComponent((document.cookie.split('; ').find(c=>c.startsWith('ora_csrf='))||'').slice(9));
  async function send(payload){
    try{
      const response=await fetch('/api/compiler/paste-events',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':token()},body:JSON.stringify(payload)});
      if(!response.ok)throw new Error();
    }catch{document.querySelector('[data-paste-notice]')?.replaceChildren(document.createTextNode('Paste recording unavailable.'));}
  }
  document.addEventListener('paste',event=>{
    if(!event.isTrusted||!event.target.matches('#sqlEditor, #terminalInput, #editor, #cliInput'))return;
    const text=event.clipboardData?.getData('text/plain');if(text===undefined)return;
    const payload={source:['cliInput','terminalInput'].includes(event.target.id)?'SQL*Plus':'PL/SQL',text:text.slice(0,65536),truncated:text.length>65536};
    if(role==='STUDENT')send(payload);else if(!role&&pending.length<20)pending.push(payload);
  },true);
  fetch('/api/portal/profile',{credentials:'same-origin'}).then(r=>r.ok?r.json():null).then(profile=>{
    if(!profile)return;role=String(profile.role).toUpperCase();
    const host=document.querySelector('.editor-status, .editor-bottom');
    if(role==='STUDENT'){
      pending.forEach(send);pending=[];
      // Old workbench gets the same two-editor access control as the new one.
      if(document.querySelector('#editor')&&!document.querySelector('#teacherEditorAccess')){
        const label=document.createElement('label');label.textContent='Teacher editor access (both editors) ';
        const select=document.createElement('select');
        for(const [value,text] of [['private','Private'],['session','This session'],['always','Always allow']]){
          const option=document.createElement('option');option.value=value;option.textContent=text;select.append(option);
        }
        label.append(select);document.querySelector('#settingsView')?.append(label);
        const sessionToken=crypto.randomUUID().replaceAll('-','');
        let publishedEditor=document.querySelector('#editor').value, publishedCli=document.querySelector('#cliInput')?.value||'', revision=0;
        async function share(){
          const mode=select.value;
          const response=await fetch('/api/compiler/editor-share',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':token()},body:JSON.stringify({mode,sessionToken,editorText:mode==='private'?'':document.querySelector('#editor').value,sqlplusText:mode==='private'?'':document.querySelector('#cliInput')?.value||''})});
          if(response.ok){const data=await response.json();revision=data.revision;publishedEditor=document.querySelector('#editor').value;publishedCli=document.querySelector('#cliInput')?.value||'';}
        }
        select.addEventListener('change',()=>share().catch(()=>{}));
        fetch('/api/compiler/editor-share',{headers:{'X-CSRFToken':token()},credentials:'same-origin'}).then(r=>r.json()).then(data=>{select.value=['session','always'].includes(data.mode)?data.mode:'private';}).catch(()=>{});
        setInterval(async()=>{
          if(document.hidden)return;
          try{
            const response=await fetch('/api/compiler/editor-share',{credentials:'same-origin'});
            if(!response.ok)return;
            const data=await response.json();
            select.value=['session','always'].includes(data.mode)?data.mode:'private';
            if(select.value==='private'){revision=data.revision;return;}
            if(data.revision>revision){
              const editor=document.querySelector('#editor'),cli=document.querySelector('#cliInput');
              if(editor.value===publishedEditor&&data.editorText!==editor.value){editor.value=data.editorText;editor.dispatchEvent(new Event('input',{bubbles:true}));}
              if(cli&&cli.value===publishedCli&&data.sqlplusText!==cli.value){cli.value=data.sqlplusText;cli.dispatchEvent(new Event('input',{bubbles:true}));}
            }
            revision=data.revision;await share();
          }catch{}
        },4000);
      }
    }else if(role==='TEACHER'||role==='ADMIN'){
      const button=document.createElement('button');button.type='button';button.className='tool-button plain-button';button.textContent='Paste activity';
      (document.querySelector('.history-heading-actions, .editor-tools')||host)?.append(button);
      button.addEventListener('click',async()=>{
        const dialog=document.createElement('dialog');dialog.className='paste-activity-dialog';dialog.setAttribute('aria-label','Paste activity');
        const close=document.createElement('button');close.type='button';close.className='tool-button plain-button paste-activity-close';close.textContent='Close';close.setAttribute('aria-label','Close paste activity');close.addEventListener('click',()=>{dialog.close();dialog.remove();});
        const body=document.createElement('div');body.textContent='Loading paste activity…';dialog.append(close,body);document.body.append(dialog);dialog.showModal();
        try{
          const student=document.querySelector('#historyStudentFilter')?.value||'';
          const response=await fetch('/api/compiler/paste-events'+(student?'?student='+encodeURIComponent(student):''),{credentials:'same-origin'});
          const data=await response.json();if(!response.ok)throw new Error(data.error);
          body.replaceChildren();const summary=document.createElement('p');summary.textContent=`${data.total} paste events; showing latest ${data.events.length}. Original tab/source cannot be identified. Text is limited to 65,536 characters per paste.`;body.append(summary);
          for(const event of data.events){
            const item=document.createElement('details'),heading=document.createElement('summary');heading.textContent=`${event.student} · ${({'SQL*Plus':'SQLCL','PL/SQL':'SQL WorkSheet'}[event.source]||event.source)} · ${new Date(event.at).toLocaleString()}${event.truncated?' · truncated':''}`;
            const text=document.createElement('pre');text.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere';text.textContent=event.text;item.append(heading,text);body.append(item);
          }
        }catch(error){body.textContent=error.message||'Paste activity unavailable.';}
      });
    }
  }).catch(()=>{});
})();

// Legacy workbench: checking Clean code also cleans the current buffer.
document.addEventListener('change', event => {
  const control=event.target;
  if(!control.checked)return;
  const id=control.id;
  const input=['cliCleanPaste','settingsCleanPaste'].includes(id)?document.querySelector('#cliInput'):
    ['editorCleanCode','settingsCleanCode'].includes(id)?document.querySelector('#editor'):null;
  if(!input)return;
  const clean=text=>text.replace(/[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]/g,' ').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\u00ad\u034f\u061c\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]/g,'').replace(/[\u2028\u2029]/g,'\n');
  const text=clean(input.value),start=clean(input.value.slice(0,input.selectionStart)).length,end=clean(input.value.slice(0,input.selectionEnd)).length;
  if(text===input.value)return;
  input.setRangeText(text,0,input.value.length,'end');input.setSelectionRange(start,end);
  input.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertReplacementText'}));
});

// Linkify output text using DOM nodes so database content never becomes HTML.
(() => {
  const roots=[...document.querySelectorAll('#terminalOutput,#cliOutput,#resultBody,#resultContent')];
  const excluded='a,button,input,textarea,script,style,.terminal-command,.cli-command,.cli-prompt,.terminal-prompt';
  function linkify(root){
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
    const nodes=[];
    while(walker.nextNode())if(!walker.currentNode.parentElement?.closest(excluded))nodes.push(walker.currentNode);
    for(const node of nodes){
      const text=node.nodeValue,pattern=/https?:\/\/[^\s<>"'\u0060]+/gi;
      let match,last=0;const fragment=document.createDocumentFragment();
      while((match=pattern.exec(text))){
        const url=match[0].replace(/[.,;:!?\)\]\}]+$/,'');
        if(!url)continue;
        fragment.append(document.createTextNode(text.slice(last,match.index)));
        const link=document.createElement('a');link.href=url;link.textContent=url;
        link.target='_blank';link.rel='noopener noreferrer';link.className='compiler-output-link';
        fragment.append(link);last=match.index+url.length;
      }
      if(last){fragment.append(document.createTextNode(text.slice(last)));node.replaceWith(fragment);}
    }
  }
  for(const root of roots){
    const observer=new MutationObserver(()=>{observer.disconnect();linkify(root);observer.observe(root,{childList:true,subtree:true,characterData:true});});
    linkify(root);observer.observe(root,{childList:true,subtree:true,characterData:true});
  }
})();

// Manual terminal selection copies source/output, never the decorative SQL prompt.
document.addEventListener('copy', event => {
  const selection=window.getSelection();
  if(!event.clipboardData||!selection||selection.isCollapsed||!selection.rangeCount)return;
  const host=[...document.querySelectorAll('#terminalOutput,#cliOutput')].find(node=>node.contains(selection.anchorNode)&&node.contains(selection.focusNode));
  if(!host)return;
  const fragment=selection.getRangeAt(0).cloneContents();
  fragment.querySelectorAll('.cli-command-prompt,.terminal-prompt>label,.cli-prompt>span,.cli-prompt-label').forEach(node=>node.remove());
  fragment.querySelectorAll('td,th').forEach(node=>node.append(document.createTextNode('\t')));
  fragment.querySelectorAll('pre,p,article,tr').forEach(node=>node.append(document.createTextNode('\n')));
  event.clipboardData.setData('text/plain',fragment.textContent.replace(/\n{3,}/g,'\n\n'));
  event.preventDefault();event.stopImmediatePropagation();
},true);
