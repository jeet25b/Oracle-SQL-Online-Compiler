const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

for (const contained of [false, true, 'native-history']) test(`portal scrollbar (${contained ? 'table' : 'generic'}) follows the visible block and synchronizes both directions`, () => {
  let resize, mutation, frame;
  const events = {};
  function element() {
    return {style: {}, hidden: false, isConnected: true, scrollLeft: 0,
      listeners: {}, classList: {contains: () => false, add() {}},
      appendChild(child) { this.child = child; return child; },
      append(child) { child.parentElement = this; },
      setAttribute() {}, matches: () => false,
      addEventListener(name, callback) { this.listeners[name] = callback; },
      get clientWidth() { return parseFloat(this.style.width || 700) - 2; },
      remove() { this.removed = true; }};
  }
  const source = Object.assign(element(), {clientLeft: 0, scrollWidth: 1400,
    getBoundingClientRect: () => ({top: 20, bottom: 1800, left: 250, width: 700})});
  source.querySelectorAll = () => [source];
  source.matches = selector => contained === 'native-history'
    ? selector.includes('input, textarea, select, .history-table-wrap,')
    : contained && selector === '.activity-table-wrap, .history-table-wrap';
  source.insertAdjacentElement = (position, bar) => { source.mirror = bar; };
  const body = element();
  const bars = [];
  const context = {
    document: {body, querySelector: () => source, querySelectorAll: () => [source],
      createElement: () => { const el = element(); bars.push(el); return el; },
      addEventListener: (name, fn) => { events[name] = fn; }},
    window: {addEventListener: (name, fn) => { events[name] = fn; }},
    innerHeight: 800, innerWidth: 1200,
    getComputedStyle: () => ({overflowX: 'auto', overflowY: 'visible'}),
    requestAnimationFrame: fn => { frame = fn; return 1; },
    ResizeObserver: class { constructor(fn) { resize = fn; } observe() {} unobserve() {} },
    MutationObserver: class { constructor(fn) { mutation = fn; } observe() {} },
  };
  const code = fs.readFileSync('online-oracle-compiler/theme.js', 'utf8').split('// Keep a horizontal scroll control')[1];
  vm.runInNewContext(code.slice(code.indexOf('\n')), context);
  frame();
  if (contained === 'native-history') {
    assert.equal(bars.length, 0, 'History must use only its native scrollbar');
    return;
  }
  const bar = bars[0];
  assert.equal(bar.hidden, false);
  if (contained) assert.equal(source.mirror, bar);
  else {
    assert.equal(bar.style.left, '250px');
    assert.equal(bar.style.top, '782px');
  }
  bar.scrollLeft = 300;
  bar.listeners.scroll();
  assert.equal(source.scrollLeft, 300);
  source.scrollLeft = 600;
  events.scroll(); frame();
  assert.equal(bar.scrollLeft, 600);
  source.getBoundingClientRect = () => ({top: -1000, bottom: 780, left: 250, width: 700});
  events.scroll(); frame();
  assert.equal(bar.hidden, false);
  if (!contained) assert.equal(bar.style.top, '762px');
  source.getBoundingClientRect = () => ({top: 20, bottom: 1800, left: 250, width: 700});
  source.scrollWidth = source.clientWidth;
  resize(); frame();
  assert.equal(bar.hidden, true);
  source.scrollWidth = 1400;
  mutation([{type: 'attributes', target: {closest: () => null}}]); frame();
  assert.equal(bar.hidden, false);
  mutation([{target: {closest: () => null}, addedNodes: [element()], removedNodes: []}]); frame();
  assert.equal(bar.hidden, false);
  source.isConnected = false;
  events.scroll(); frame();
  assert.equal(bar.removed, true);
});
