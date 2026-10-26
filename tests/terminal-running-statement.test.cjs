const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const core = require('../online-oracle-compiler/workspace-core.js');

test('terminal echoes SQL before request completes and appends failure without duplicating SQL', async () => {
  const source = fs.readFileSync('online-oracle-compiler/oracle-sql-ide.js', 'utf8');
  const node = () => ({ children: [], style: {}, append(child) { this.children.push(child); } });
  const output = { children: [], insertBefore(entry) { this.children.push(entry); } };
  const runtime = { textContent: 'Previous PL/SQL runtime' };
  const previousResult = { rows: [['Previous PL/SQL result']] };
  let failRequest, requestStarted = false;
  const context = vm.createContext({
    window: { CompilerWorkspace: core }, document: { createElement: node },
    $: selector => selector === '#terminalOutput' ? output : selector === '#resultRuntime' ? runtime : selector === '#sqlplusAutoCommit' ? { checked: false } : null,
    executionBusy: false, isStudent: false, outputStyle: 'website', performance, resultData: previousResult, terminalQueue: null,
    collectBinds: async () => ({}), isSqlPlusClientInput: () => true, takeEditMetrics: () => ({}),
    renderResult() { assert.fail('SQL*Plus must not render PL/SQL results'); }, addHistory() {}, trimTerminalBuffer() {},
    fetch: () => {
      requestStarted = true;
      assert.equal(output.children.length, 1);
      assert.equal(output.children[0].children[0].textContent, 'DROP TABLE employee;');
      assert.equal(output.children[0].children.length, 1);
      return new Promise((resolve, reject) => { failRequest = reject; });
    }
  });
  vm.runInContext(source.slice(source.indexOf('  async function execute(sql,'), source.indexOf('  async function connect(payload')), context);
  const running = vm.runInContext("execute('DROP TABLE employee;', {sqlplus:true})", context);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requestStarted, true);
  assert.equal(runtime.textContent, 'Previous PL/SQL runtime');
  failRequest(new Error('Connection lost'));
  await running;
  assert.equal(context.resultData, previousResult);
  assert.equal(runtime.textContent, 'Previous PL/SQL runtime');
  assert.equal(output.children.length, 1);
  assert.equal(output.children[0].children.length, 2);
  assert.equal(output.children[0].children[1].textContent, 'Connection lost');
});
