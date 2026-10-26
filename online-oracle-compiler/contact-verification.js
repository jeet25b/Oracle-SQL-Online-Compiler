(() => {
  const url='/api/portal/profile/contact-verification';
  const csrf=()=>decodeURIComponent((document.cookie.split('; ').find(c=>c.startsWith('ora_csrf='))||'').slice('ora_csrf='.length));
  async function request(payload){
    const response=await fetch(url,payload?{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':csrf()},body:JSON.stringify(payload)}:{credentials:'same-origin'});
    const data=await response.json();if(!response.ok)throw new Error(data.error||'Verification is unavailable.');return data;
  }
  function render(contacts){
    for(const channel of ['email','mobile']){
      const contact=contacts[channel];
      document.querySelectorAll(`[data-contact-row="${channel}"]`).forEach(row=>{
        row.querySelector('[data-contact-status]').textContent=contact.verified?'Verified':contact.value?'Not verified':'Not added';
        const button=row.querySelector('[data-verify-contact]');button.hidden=contact.verified||!contact.value;button.dataset.savedContact=contact.value;
      });
    }
  }
  let busy=false;
  document.addEventListener('click',async event=>{
    const trigger=event.target.closest('[data-verify-contact]');if(!trigger||busy)return;
    const channel=trigger.dataset.verifyContact;
    const dialog=document.createElement('dialog');dialog.className='help-dialog';dialog.setAttribute('aria-label',channel==='email'?'Verify email':'Verify mobile');
    const form=document.createElement('form');
    const title=document.createElement('h2');title.textContent=channel==='email'?'Verify email':'Verify mobile (WhatsApp)';
    const description=document.createElement('p');description.textContent='Send a verification code to your saved '+(channel==='email'?'email address.':'mobile number through WhatsApp.');
    const status=document.createElement('p');status.setAttribute('role','status');
    const label=document.createElement('label');label.textContent='Verification code';label.hidden=true;
    const code=document.createElement('input');code.inputMode='numeric';code.autocomplete='one-time-code';code.pattern='[0-9]{6}';code.maxLength=6;code.disabled=true;code.required=true;label.append(code);
    const cancel=document.createElement('button');cancel.type='button';cancel.className='register-cancel';cancel.textContent='Close';
    const submit=document.createElement('button');submit.type='submit';submit.className='submit-button';submit.textContent='Send code';
    const actions=document.createElement('div');actions.className='profile-form-actions';actions.append(cancel,submit);
    form.append(title,description,label,status,actions);dialog.append(form);document.body.append(dialog);dialog.showModal();
    let step='send';
    const close=()=>{if(!busy){dialog.close();dialog.remove();trigger.focus();}};
    cancel.addEventListener('click',close);
    dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(busy)return;
      const profileForm=trigger.closest('form');
      const edited=profileForm?.elements.namedItem(channel==='email'?'email':'mobileNumber');
      const normalize=value=>channel==='email'?value.trim().toLowerCase():value.replace(/\D/g,'');
      if(step==='send'&&edited&&normalize(edited.value)!==normalize(trigger.dataset.savedContact)){
        status.textContent='Save your changed contact in Update profile before requesting verification.';return;
      }
      busy=true;submit.disabled=true;cancel.disabled=true;
      try{
        const data=await request({action:step,channel,code:code.value.trim()});
        if(data.verified){render(data.contacts);status.textContent='Contact verified successfully.';submit.hidden=true;label.hidden=true;code.disabled=true;}
        else{step='complete';label.hidden=false;code.disabled=false;status.textContent=data.message;submit.textContent='Verify code';code.focus();}
      }catch(error){status.textContent=error.message;}
      finally{busy=false;submit.disabled=false;cancel.disabled=false;}
    });
  });
  const refresh=()=>request().then(render).catch(()=>{});
  document.addEventListener('portal-profile-updated',refresh);
  refresh();
})();
