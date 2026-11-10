const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.textContent = ''; this.classList = {add() {}}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute() {}
  addEventListener() {}
}
function render(results, statements) {
  const template = fs.readFileSync('templates/teacher-project-runner.html', 'utf8');
  const source = template.slice(template.indexOf('function appendDataTable'), template.indexOf('function showRun'));
  const context = { document: { createElement: tag => new Element(tag) } };
  vm.createContext(context);
  vm.runInContext(source, context);
  const target = new Element('ol');
  context.renderResults(results, target, statements);
  return target;
}
function descendants(node, tag) {
  return node.children.flatMap(child => [ ...(child.tag === tag ? [child] : []), ...descendants(child, tag) ]);
}
test('statement 7 displays SQL immediately below its heading and table instead of CSV', () => {
  const sql = 'SELECT * FROM EMPLOYEE;';
  const root = render([{ index: 6, status: 'success', output: '"EMP_ID"\n"E1001"',
    table: { columns: ['EMP_ID'], rows: [['E1001']], rowCount: 1 } }], Array(6).fill('').concat(sql));
  const item = root.children[0];
  assert.match(item.children[0].textContent, /Statement 7:/);
  assert.equal(item.children[1].textContent, sql);
  assert.equal(descendants(root, 'table').length, 1);
  assert.equal(descendants(root, 'pre').length, 1);
  assert.ok(descendants(root, 'td').some(cell => cell.textContent === 'E1001'));
});
test('connection entries are excluded and statement/table values remain literal text', () => {
  const root = render([{ type: 'connection' }, { index: 0, status: 'success', statement: '<script>bad()</script>',
    table: { columns: ['NAME'], rows: [['<img src=x onerror=bad()>']] } }], []);
  assert.equal(root.children.length, 1);
  assert.equal(descendants(root, 'pre')[0].textContent, '<script>bad()</script>');
  assert.ok(descendants(root, 'td').some(cell => cell.textContent === '<img src=x onerror=bad()>'));
});
