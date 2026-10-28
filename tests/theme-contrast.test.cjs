const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const read = file => fs.readFileSync(file, 'utf8');
const declarations = block => Object.fromEntries([...block.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)].map(match => [match[1], match[2].trim()]));
const base = declarations(read('online-oracle-compiler/styles.css').match(/:root\s*\{([^}]+)\}/)[1]);
const theme = read('online-oracle-compiler/theme.css');
const shared = declarations(theme.match(/:root\s*\{([^}]+)\}/)[1]);
const dark = declarations(theme.match(/:root\[data-theme="dark"\]\s*\{([^}]+)\}/)[1]);
function resolve(tokens, name) {
  const value = tokens[name];
  return value.startsWith('var(') ? resolve(tokens, value.slice(4, -1)) : value;
}
function luminance(hex) {
  hex = hex.slice(1);
  if (hex.length === 3) hex = [...hex].map(char => char + char).join('');
  return [0, 2, 4].map(index => parseInt(hex.slice(index, index + 2), 16) / 255)
    .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4)
    .reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
}
for (const mode of ['light', 'dark']) {
  test(`${mode} card, form, preview and status text meet normal-text contrast`, () => {
    const tokens = { ...base, ...shared, ...(mode === 'dark' ? dark : {}) };
    for (const background of ['--surface', '--surface-2']) {
      for (const foreground of ['--text', '--muted', '--status-success', '--status-warning', '--status-error']) {
        const values = [luminance(resolve(tokens, foreground)), luminance(resolve(tokens, background))].sort((a, b) => a - b);
        assert.ok((values[1] + 0.05) / (values[0] + 0.05) >= 4.5, `${foreground} on ${background}`);
      }
    }
  });
}
