const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const code = fs.readFileSync('online-oracle-compiler/workspace-daily-task.js', 'utf8');

async function worksheet({available = true, recent = true} = {}) {
  const calls = [], nodes = [];
  const host = {append(node) { this.context = node; }};
  const editor = {value: 'Previous draft'};
  const task = {id: 23, title: 'Joins', instructions: 'Find employees */ safely', starterSql: 'SELECT * FROM emp;'};
  const context = {
    window: {}, URLSearchParams, location: {search: '?engine=ORACLE&dailyTask=23'}, isStudent: true, editor,
    document: {querySelector: () => host, createElement: tag => {
      const node = {tag, children: [], listeners: {}, append(...items) { this.children.push(...items); },
        setAttribute() {}, addEventListener(name, callback) { this.listeners[name] = callback; }};
      nodes.push(node); return node;
    }},
    async fetch(url, options) {
      calls.push({url, options});
      const data = url.endsWith('/available') ? {tasks: available ? [{id: 9}, task] : []}
        : url.includes('/history?') ? {statements: [{id: 99, executed_at: new Date(Date.now() + (recent ? 10000 : -10000)).toISOString()}]}
        : {ok: true, message: 'Saved'};
      return {ok: true, data};
    },
    compilerApiJson: async response => response.data,
    markInputMethod() {}, updateLineNumbers() {}, updateHighlight() {}, updateEditorCursorPosition() {},
    saveDailyTaskPosition(value) { this.selected = value; }, setView() {}, compilerCsrfToken: () => 'test',
    announceDailyTaskSubmitted() {},
  };
  vm.runInNewContext(code, context);
  await context.window.WorkspaceDailyTask.attach(context);
  return {calls, nodes, editor};
}

test('worksheet deep link loads the authorized task and submits its recent worksheet history', async () => {
  const {calls, nodes, editor} = await worksheet();
  assert.ok(editor.value.includes('Find employees * / safely'));
  assert.ok(editor.value.endsWith('SELECT * FROM emp;'));
  const submit = nodes.find(node => node.tag === 'button');
  await submit.listeners.click();
  assert.ok(calls[1].url.includes('source=PL%2FSQL&task_id=23'));
  assert.deepEqual(JSON.parse(calls[2].options.body), {taskId: 23, historyId: 99});
});

test('worksheet task cannot submit a successful statement from before the task was opened', async () => {
  const {calls, nodes} = await worksheet({recent: false});
  await nodes.find(node => node.tag === 'button').listeners.click();
  assert.equal(calls.length, 2);
  assert.ok(nodes.find(node => node.tag === 'span').textContent.includes('Run your answer successfully'));
});

test('unavailable task does not overwrite the editor or enable submission', async () => {
  const {editor, nodes} = await worksheet({available: false});
  assert.equal(editor.value, 'Previous draft');
  assert.equal(nodes.find(node => node.tag === 'button').disabled, true);
});
