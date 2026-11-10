const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function element(tag, className = '', ...children) {
  const node = {tag, className, children,
    append(child) { child.parent = this; this.children.push(child); },
    remove() { this.parent.children.splice(this.parent.children.indexOf(this), 1); },
    get textContent() { return this.children.map(child => typeof child === 'string' ? child : child.textContent).join(''); },
    querySelectorAll(selector) {
      const matches = item => selector.split(',').some(part => part.startsWith('.')
        ? item.className.split(' ').includes(part.slice(1)) : item.tag === part);
      const found = [];
      for (const child of this.children) if (typeof child !== 'string') {
        if (matches(child)) found.push(child);
        found.push(...child.querySelectorAll(selector));
      }
      return found;
    }};
  children.forEach(child => { if (typeof child !== 'string') child.parent = node; });
  return node;
}

function copy(fragment) {
  let handler, copied, formatted, prevented = false;
  const source = fs.readFileSync('global-controls.js', 'utf8');
  const offset = source.indexOf("document.addEventListener('copy', event => {");
  assert.notEqual(offset, -1);
  const selection = {anchorNode: {}, focusNode: {}, rangeCount: 1, isCollapsed: false,
    getRangeAt: () => ({cloneContents: () => fragment})};
  vm.runInNewContext(source.slice(offset), {
    document: {addEventListener: (name, callback) => { handler = callback; },
      querySelectorAll: () => [{contains: () => true}], createTextNode: text => text,
      createElement: () => ({style:{cssText:''},append(fragment){this.fragment=fragment;},get outerHTML(){return `<div style="${this.style.cssText}">${this.fragment.textContent}</div>`;}})},
    window: {getSelection: () => selection},
  });
  handler({clipboardData: {setData: (type, value) => { if(type === 'text/plain') copied = value; else if(type === 'text/html') formatted = value; }},
    preventDefault() { prevented = true; }, stopImmediatePropagation() {}});
  assert.equal(prevented, true);
  assert.match(formatted, /text\/html|font-family:Consolas/);
  return copied;
}

test('selected multiline terminal SQL keeps line breaks between source lines', () => {
  const lines = ['INSERT INTO EMPLOYEE', 'SELECT *', 'FROM EMPLOYEE_BACKUP;'];
  const fragment = element('fragment', '', element('pre', 'terminal-command',
    ...lines.map(line => element('span', 'terminal-command-line', line))));
  assert.equal(copy(fragment).trimEnd(), lines.join('\n'));
});

test('partial multiline selection also separates the end and start of adjacent lines', () => {
  const fragment = element('fragment', '', element('span', 'terminal-command-line', 'EMPLOYEE'),
    element('span', 'terminal-command-line', 'SELECT *'));
  assert.equal(copy(fragment).trimEnd(), 'EMPLOYEE\nSELECT *');
});

test('syntax highlighting does not introduce newlines within a single source line', () => {
  const fragment = element('fragment', '', element('span', 'terminal-command-line',
    element('span', 'sql-keyword', 'INSERT'), ' INTO ', element('span', 'sql-identifier', 'EMPLOYEE')));
  assert.equal(copy(fragment).trimEnd(), 'INSERT INTO EMPLOYEE');
});

test('plain output retains existing newlines and decorative prompts are excluded', () => {
  const fragment = element('fragment', '', element('span', 'cli-command-prompt', 'SQL> '),
    element('pre', '', 'SELECT *\nFROM EMPLOYEE;'));
  assert.equal(copy(fragment).trimEnd(), 'SELECT *\nFROM EMPLOYEE;');
});
