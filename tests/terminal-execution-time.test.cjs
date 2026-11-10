const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const core = require('../online-oracle-compiler/workspace-core.js');
const source = fs.readFileSync('online-oracle-compiler/oracle-sql-ide.js', 'utf8');
function renderer(style = 'website') {
  const node = () => ({ children: [], style: {}, append(...items) { this.children.push(...items); } });
  const output = { insertBefore() {}, scrollHeight: 0 };
  const context = { window: { CompilerWorkspace: core }, document: { createElement: node },
    $: selector => selector === '#terminalOutput' ? output : null,
    outputStyle: style, renderSyntaxHighlight: text => text, renderTable: node, trimTerminalBuffer() {} };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('  function appendTerminalResult('), source.indexOf('  function connectionDiagnosis(')), context);
  return context.appendTerminalResult;
}
test('execution time follows row feedback in website and SQLCL text output', () => {
  for (const style of ['website', 'sqlplus']) {
    const entry = renderer(style)('SELECT 1 FROM dual', {columns:[{name:'ID'}],rows:[[1]],durationMs:120}, null);
    assert.equal(entry.children.at(-1).textContent, 'Execution time: 120 ms');
    assert.equal(entry.children.at(-1).className, 'terminal-execution-time');
  }
});
test('zero duration and errors are timed, but pending output and unknown duration are not', () => {
  const render = renderer();
  assert.equal(render('BAD SQL', {durationMs:0}, 'Oracle error').children.at(-1).textContent, 'Execution time: 0 ms');
  for (const data of [{pending:true,durationMs:120}, {}, {durationMs:null}, {durationMs:NaN}]) {
    assert.ok(!render('SELECT 1 FROM dual', data, null).children.some(node => node.className === 'terminal-execution-time'));
  }
});
