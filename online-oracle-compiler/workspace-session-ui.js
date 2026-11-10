/* Worksheet drafts and server-reported connection state; never stores credentials. */
(() => {
  function draftKey(user, identity) { return `worksheet-draft:${user}:${identity || 'unconnected'}`; }
  function create({editor, form, onRestore}) {
    const user=document.body.dataset.userId || 'anonymous';
    const bar=document.createElement('div'); bar.className='workspace-session-status'; bar.setAttribute('aria-label','Worksheet session');
    const connection=document.createElement('span'); connection.textContent='No active database connection';
    const transaction=document.createElement('span'); transaction.textContent='Transaction state unavailable';
    const draft=document.createElement('span'); draft.textContent='Draft recovery ready'; draft.setAttribute('role','status');
    const restore=document.createElement('button'); restore.type='button'; restore.className='tool-button'; restore.textContent='Restore worksheet draft'; restore.hidden=true;
    bar.append(connection,transaction,draft,restore);
    // Session bookkeeping stays active without a workbook status panel.
    let identity='unconnected', connected=false, details={}, pending={worksheet:null,sqlcl:null}, timer;
    const read=()=>{try{return JSON.parse(localStorage.getItem(draftKey(user,identity)) || 'null');}catch{return null;}};
    const offer=()=>{const saved=read(); restore.hidden=!saved?.sql || saved.sql===editor.value;};
    function save() {
      if (!editor.value.trim()) return;
      try { localStorage.setItem(draftKey(user,identity),JSON.stringify({sql:editor.value,savedAt:new Date().toISOString()})); draft.textContent='Worksheet draft saved on this device'; }
      catch { draft.textContent='Draft storage unavailable'; }
    }
    editor.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(save,400);});
    restore.addEventListener('click',()=>{
      const saved=read(); if(!saved?.sql) return;
      // Keep the current text recoverable when replacing it with an older draft.
      if(editor.value.trim()) {try{sessionStorage.setItem(`worksheet-before-restore:${user}`,editor.value);}catch{}}
      editor.value=saved.sql;onRestore();restore.hidden=true;draft.textContent='Worksheet draft restored';
    });
    offer();
    function renderTransactions() {
      const labels=[];
      if(pending.worksheet===true) labels.push('Worksheet has uncommitted changes');
      if(pending.sqlcl===true) labels.push('SQLCL has uncommitted changes');
      transaction.textContent=!connected?'No active transaction':labels.join(' · ') || (Object.values(pending).some(value=>value===false)?'No uncommitted changes reported':'Transaction state unavailable');
      transaction.dataset.pending=String(labels.length>0);
    }
    window.addEventListener('beforeunload',event=>{
      save();
      if(connected && Object.values(pending).some(value=>value===true)) {event.preventDefault();event.returnValue='';}
    });
    return {
      setDetails(value) { if(value?.connected) details=value; },
      setConnection(active,label) {
        connected=active;
        if(active) {
          const next=`${details.loginUser || details.user || ''}@${details.connectString || details.service || ''}`;
          if(next!==identity && details.user) {save();identity=next;offer();}
          connection.textContent=`Active connection: ${details.user || 'Oracle'}${details.service ? ' · '+details.service : ''}`;
        } else {pending={worksheet:null,sqlcl:null};connection.textContent='No active database connection';}
        renderTransactions();
      },
      updateTransactions(snapshot) {if(snapshot) {pending={...pending,...snapshot};renderTransactions();}},
      confirmDisconnect() {return !Object.values(pending).some(value=>value===true) || window.confirm('This connection has uncommitted changes. Disconnecting rolls them back. Disconnect anyway?');},
    };
  }
  window.WorkspaceSessionUI={create,draftKey};
})();
