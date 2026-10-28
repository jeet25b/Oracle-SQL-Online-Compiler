const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('Enter submission waits for autosave and sends the entire SQLCL batch once', async () => {
  const source = fs.readFileSync('online-oracle-compiler/teacher-remote-code.js', 'utf8');
  const handler = source.slice(source.indexOf('  async function executeRemoteCommand('), source.indexOf('  function worksheetStatementAtCursor('));
  const sent = [];
  const context = vm.createContext({
    studentSelect: { value: '42' }, shared: {}, loading: false, saving: true,
    sendingRemoteCommand: false, saveTimer: 0, clearTimeout() {},
    hasAccess: () => true, crypto: { randomUUID: () => 'request-id' },
    $: () => ({}), status: {}, setEditorContent() {}, showRemoteCommand() {}, pollRemoteCommand() {},
    window: { setTimeout(callback) { context.saving = false; callback(); } },
    request: async (url, options) => { sent.push(JSON.parse(options.body)); return { command: {} }; },
  });
  vm.runInContext(handler, context);
  const batch = 'select 1 from dual;\nselect 2 from dual;';
  assert.equal(await context.executeRemoteCommand(batch, 'sqlplus', false), true);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].command, batch);
  assert.equal(sent[0].editor, 'sqlplus');
  assert.equal(context.sendingRemoteCommand, false);
});

test('result delivery retries original output and clears only the executed statement', async () => {
  const source = fs.readFileSync('online-oracle-compiler/oracle-sql-ide.js', 'utf8');
  const delivery = source.slice(source.indexOf('  async function deliverRemoteResult()'), source.indexOf('  async function pollRemoteTerminalCommand()'));
  let fail = true;
  const sent = [];
  const terminalInput = { value: 'select 1;', style: {}, dispatchEvent() {} };
  const context = vm.createContext({ terminalInput, Event: class {}, editorShareRequest: async (url, options) => {
    sent.push(JSON.parse(options.body));
    if (fail) throw new Error('Network interrupted');
  } });
  vm.runInContext(`let pendingRemoteResult = { job: { id: '1', editor: 'sqlplus', text: 'select 1;' }, payload: { output: '1 row selected', executed: true } }; ${delivery}`, context);
  await assert.rejects(vm.runInContext('deliverRemoteResult()', context));
  assert.equal(terminalInput.value, 'select 1;');
  fail = false;
  await vm.runInContext('deliverRemoteResult()', context);
  assert.deepEqual(sent[0], sent[1]);
  assert.equal(terminalInput.value, '');
  vm.runInContext("pendingRemoteResult = { job: { id: '2', editor: 'sqlplus', text: 'select 1;' }, payload: { output: 'done', executed: true } }", context);
  terminalInput.value = 'select 2;';
  await vm.runInContext('deliverRemoteResult()', context);
  assert.equal(terminalInput.value, 'select 2;');
});
