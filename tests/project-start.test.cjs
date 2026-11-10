const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const template = fs.readFileSync('templates/teacher-project-runner.html', 'utf8');
const script = template.split('<script>')[1].split('</script>')[0];

test('complete project checker script parses so button handlers can register', () => {
  assert.doesNotThrow(() => new vm.Script(script));
});

test('start handler posts the selected file with a CSRF token and shows the run', async () => {
  const start = script.indexOf("form.addEventListener('submit',");
  const end = script.indexOf("\nconst configForm", start);
  let handler, posted, shown;
  const button = {disabled: false, textContent: 'Start project check'};
  const file = {name: 'sample.sql', text: async () => 'SELECT 1 FROM dual;'};
  const run = {id: 42, status: 'queued', statements: ['SELECT 1 FROM dual;']};
  const context = {
    projectFileInput: {files: [file]}, projectNameInput: {value: 'sample.sql'}, projectStartStatus: {textContent: ''},
    form: {addEventListener: (name, callback) => {handler = callback;}, querySelector: () => button},
    document: {querySelector: selector => selector === '#file' ? {files: [file]} : {value: 'Project file'}},
    csrf: () => 'test-csrf', fetch: async (url, options) => {posted = {url, options}; return {};},
    jsonResponse: async () => run, correction: {value: ''}, variableFields: {replaceChildren() {}},
    saveCheckDraft() {}, saveSelectedFile() {}, showRun: result => {shown = result;},
    out: {}, errorNode: {}, activeRun: null,
  };
  vm.runInNewContext(script.slice(start, end), context);
  let prevented = false;
  await handler({preventDefault() {prevented = true;}});
  assert.equal(prevented, true);
  assert.equal(posted.url, '/api/teacher/project-runs');
  assert.equal(posted.options.headers['X-CSRFToken'], 'test-csrf');
  assert.deepEqual(JSON.parse(posted.options.body), {name: 'sample.sql', fileName: 'sample.sql', source: 'SELECT 1 FROM dual;'});
  assert.equal(shown, run);
  assert.equal(button.disabled, false);
});
