const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '..', 'global-controls.js'), 'utf8');
const start = source.indexOf('  const caretMirrors = new WeakMap();');
const end = source.indexOf('  const definitions = [', start);

function fixture() {
  const state = {
    caret: { left: 16, top: 15 },
    style: { fontSize: '18px', lineHeight: '30px' }
  };
  const host = { append(node) { node.parentElement = this; } };
  const document = {
    body: host, fullscreenElement: null,
    createElement(tag) {
      return {
        style: {}, setAttribute() {}, replaceChildren() {},
        getBoundingClientRect() {
          return tag === 'span' ? state.caret : { left: 0, top: 0 };
        }
      };
    },
    createTextNode(text) { return { textContent: text }; }
  };
  const getRect = vm.runInNewContext(`(() => { ${source.slice(start, end)}; return starterCaretRect; })()`, {
    document, window: {}, getComputedStyle: () => state.style
  });
  const input = {
    value: '', selectionStart: 0, clientLeft: 0, clientTop: 0,
    clientWidth: 800, clientHeight: 240, scrollLeft: 0, scrollTop: 0,
    getBoundingClientRect() {
      // Frozen geometry ensures the helper cannot mutate browser-style bounds.
      return Object.freeze({ left: 64, top: 85, right: 864, bottom: 325 });
    }
  };
  return { state, input, document, getRect };
}

test('empty PL/SQL popup anchors to first text line, not editor bottom', () => {
  const { input, getRect } = fixture();
  const rect = getRect(input);
  assert.equal(rect.left, 80);
  assert.equal(rect.top, 100);
  assert.equal(rect.bottom, 130);
  assert.notEqual(rect.bottom, input.getBoundingClientRect().bottom);
});

test('caret position follows both scroll axes and changed line/font geometry', () => {
  const { input, state, getRect } = fixture();
  input.value = '\n\n\t'; input.selectionStart = 3;
  state.caret = { left: 50, top: 105 };
  input.scrollTop = 60; input.scrollLeft = 20;
  const rect = getRect(input);
  assert.equal(rect.left, 94);
  assert.equal(rect.top, 130);
  state.style.lineHeight = '40px';
  assert.equal(getRect(input).bottom, 170);
});

test('offscreen caret is clamped to resized editor viewport', () => {
  const { input, state, getRect } = fixture();
  input.clientHeight = 60; input.clientWidth = 100;
  state.caret = { left: 1000, top: 1000 };
  const rect = getRect(input);
  assert.equal(rect.left, 164);
  assert.equal(rect.bottom, 145);
});

test('fullscreen mirror host and approved SQL*Plus anchor stay separate', () => {
  const { input, document, getRect } = fixture();
  let appended = false;
  document.fullscreenElement = { append(node) { appended = true; node.parentElement = this; } };
  getRect(input);
  assert.equal(appended, true);
  assert.ok(source.includes("['#sqlplusStarterMenu', '#terminalInput', true]"));
  assert.ok(source.includes("? starterCaretRect(anchor) : anchor.getBoundingClientRect()"));
});
