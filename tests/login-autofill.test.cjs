const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

test('saved-login hints activate at three username characters and preserve paired password fill',()=>{
  function field(){return{value:'saved',autocomplete:'',handlers:{},addEventListener(n,f){this.handlers[n]=f;}};}
  const user=field(),password=field();
  const form={isConnected:true,handlers:{},querySelector(s){return s.includes('password')?password:user;},closest(){return null;},getClientRects(){return[{}];},addEventListener(n,f){this.handlers[n]=f;}};
  const document={body:{},activeElement:null,querySelectorAll(){return[form];}};
  let visibilityCheck;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../online-oracle-compiler/login-autofill.js'),'utf8'),{
    document, getComputedStyle(){return{visibility:'visible'};},MutationObserver:class{observe(){}},
    window:{setInterval(callback){visibilityCheck=callback;return 1;},addEventListener(){}},clearInterval(){},queueMicrotask
  });
  assert.equal(user.value,'');assert.equal(password.value,'');
  assert.equal(password.readOnly,true);
  assert.equal(password.autocomplete,'off');
  user.value='unattended-user';password.value='unattended-password';
  visibilityCheck();assert.equal(user.value,'');assert.equal(password.value,'');
  form.handlers.pointerdown({isTrusted:false});assert.equal(password.readOnly,true);
  form.handlers.pointerdown({isTrusted:true});assert.equal(password.readOnly,false);
  user.value='browser-selected-user';password.value='browser-filled-password';
  user.handlers.input({});visibilityCheck();
  assert.equal(user.value,'browser-selected-user');
  assert.equal(password.value,'browser-filled-password');
  user.value='';password.value='';
  document.activeElement=user;user.handlers.focus();
  assert.equal(password.readOnly,false);
  // A password-manager selection can emit input with no beforeinput event.
  user.value='ab';user.handlers.input({});assert.equal(user.autocomplete,'off');
  user.value='abc';user.handlers.input({});assert.equal(user.autocomplete,'username');assert.equal(password.autocomplete,'current-password');
  password.value='selected-browser-password';document.activeElement=password;password.handlers.focus();
  assert.equal(password.autocomplete,'current-password');assert.equal(password.value,'selected-browser-password');
  assert.equal(password.readOnly,false);
  document.activeElement=user;user.handlers.focus();assert.equal(password.autocomplete,'current-password');
});
