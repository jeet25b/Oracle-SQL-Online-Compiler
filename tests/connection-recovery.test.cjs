const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

test('failed saved connection opens login with profile details retained', async () => {
  const nodes = new Map();
  const $ = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {});
    return nodes.get(selector);
  };
  const connectForm = { dataset: {}, elements: { user: {}, password: {}, connectString: {} } };
  const connectDialog = { open: false, showModal() { this.open = true; }, close() { this.open = false; } };
  const source = fs.readFileSync(path.join(__dirname, '../online-oracle-compiler/oracle-sql-ide.js'), 'utf8');
  const functions = source.slice(source.indexOf('  async function connect(payload'), source.indexOf('  let statusRequest = null;'));
  const context = vm.createContext({ $, connectForm, connectDialog, Date, legacyCredentials: null,
    setStatus() {}, showToast() {}, fetch: async () => ({ ok: false, json: async () => ({ saved: true,
      user: 'PROFILE_USER', connectString: 'db/service', error: 'Database unavailable' }) }) });
  vm.runInContext(functions, context);
  for (const operation of ['reconnectSavedCredentials()', 'connectWithSavedCredentials()']) {
    connectDialog.open = false;
    assert.equal(await vm.runInContext(operation, context), false);
    assert.equal(connectDialog.open, true);
    assert.equal(connectForm.elements.user.value, 'PROFILE_USER');
    assert.equal(connectForm.dataset.savedOracleUser, 'PROFILE_USER');
    assert.equal(connectForm.elements.connectString.value, 'db/service');
    assert.equal($('#saveCredentials').checked, true);
    assert.match(connectForm.elements.password.placeholder, /Saved password/);
    assert.equal($('#connectError').textContent, 'Database unavailable');
  }
});

test('autofill guard preserves profile username when the database popup opens', () => {
  const field = () => ({ value: '', handlers: {}, addEventListener(name, fn) { this.handlers[name] = fn; } });
  const user = field(), password = field();
  const dialog = { open: false, handlers: {}, addEventListener(name, fn) { this.handlers[name] = fn; } };
  const form = { dataset: {}, isConnected: true, querySelector: s => s.includes('password') ? password : user,
    closest: () => dialog, getClientRects: () => [{}], addEventListener() {} };
  let guard;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../online-oracle-compiler/login-autofill.js'), 'utf8'), {
    document: { body: {}, querySelectorAll: () => [form] }, getComputedStyle: () => ({ visibility: 'visible' }),
    MutationObserver: class { observe() {} }, window: { setInterval(fn) { guard = fn; }, addEventListener() {} },
    clearInterval() {}, queueMicrotask
  });
  form.dataset.savedOracleUser = 'PROFILE_USER';
  dialog.handlers.beforetoggle({ newState: 'open' });
  dialog.open = true;
  guard();
  assert.equal(user.value, 'PROFILE_USER');
  assert.equal(password.value, '');
});
