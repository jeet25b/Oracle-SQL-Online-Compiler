const test = require('node:test');
const assert = require('node:assert/strict');
const assist = require('../online-oracle-compiler/editor-assist.js');
const core = require('../online-oracle-compiler/workspace-core.js');

test('expanded vocabulary uses case insensitive whole identifiers', () => {
 const words = 'TRUNCATE RENAME VIEW INDEX SEQUENCE SYNONYM DATABASE CASCADE CONSTRAINTS ENABLE DISABLE NOVALIDATE MODIFY ADD COLUMN ANY SOME DUAL ASC DESC NULLS FIRST LAST OVER PARTITION INTERSECT MINUS SAVEPOINT GRANT REVOKE LOCK SHARE EXCLUSIVE VARCHAR INTEGER INT SMALLINT FLOAT CLOB BLOB RAW LONG ROWID FOR WHILE EXIT CONTINUE CURSOR OPEN FETCH CLOSE PRAGMA RAISE TYPE RECORD ROWTYPE BODY CROSS FULL NATURAL'.split(' ');
 for (const word of words) assert.equal(assist.syntaxTokens(word.toLowerCase())[0].syntax,'keyword',word);
 for (const word of ['SELECTED_COL','FULL_NAME','NATURAL_ID','COUNT_TOTAL']) assert.equal(assist.syntaxTokens(word)[0].syntax,'');
 for (const word of assist.builtins) assert.equal(assist.syntaxTokens(word.toLowerCase())[0].syntax,'keyword',word);
});

test('protected text suppresses keyword and variable recognition', () => {
 const source = "\"SELECT\" 'NATURAL :dept &&name' q'[a]b SELECT]' Q'#FULL :x#' -- CROSS :x\rSELECT /* FULL :x */";
 const tokens = assist.syntaxTokens(source);
 assert.equal(tokens[0].kind,'identifier'); assert.equal(tokens[0].syntax,'');
 assert.equal(tokens.filter(t=>t.kind==='literal').length,3);
 assert.equal(tokens.filter(t=>t.kind==='variable').length,0);
 assert.equal(tokens.find(t=>t.text==='SELECT').syntax,'keyword');
 for (const input of ["'SELECT","q'[NATURAL",'/* FULL','"SELECT']) {
  assert.equal(assist.syntaxTokens(input).length,1); assert.ok(assist.scan(input).open);
 }
});

test('variables preserve source, bind discovery and assignment behavior', () => {
 const source='SELECT :dept_id, :1, &emp_id, &&dept_no, :SELECT FROM dual';
 const tokens=assist.syntaxTokens(source);
 assert.deepEqual(tokens.filter(t=>t.kind==='variable').map(t=>t.text),[':dept_id',':1','&emp_id','&&dept_no',':SELECT']);
 for (const t of tokens) assert.equal(source.slice(t.start,t.end),t.text);
 assert.deepEqual(core.binds(source),['DEPT_ID','1','SELECT']);
 assert.deepEqual(core.binds('BEGIN :out := :input; END; -- :ignored'),['OUT','INPUT']);
 for(const source of ['SELECT :sel','SELECT &&dep','SELECT 1e123']) assert.equal(assist.completion(source,source.length),null);
});

test('Oracle numeric literals stay intact through scanning and formatting', () => {
 const numbers=['100','3.14','.5','1.','1e-4','25E+03','25f','0.5D'];
 const source='SELECT '+numbers.join(', ')+' FROM dual';
 assert.deepEqual(assist.scan(source).tokens.filter(t=>t.kind==='number').map(t=>t.text),numbers);
 assert.ok(core.format(source).includes('1e-4'));
});

test('aliases highlight declarations and references without crossing statements', () => {
 const tokens=assist.syntaxTokens('SELECT e.DEPT_ID FROM department d FULL JOIN employee e ON e.DEPT_ID=d.DEPT_ID; SELECT d.DEPT_ID FROM employee;');
 for(const t of tokens.filter(t=>['d','e'].includes(t.text)).slice(0,-1)) assert.equal(t.syntax,'keyword');
 assert.equal(tokens.filter(t=>t.text==='d').at(-1).syntax,'');
});

test('variable classification never creates overlapping source spans', () => {
 for (const source of ['SELECT :1e4, :1, &&123name FROM dual', 'SELECT :x := 1, x::y FROM dual']) {
  const tokens=assist.syntaxTokens(source);
  for(let i=0;i<tokens.length;i++) {
   assert.equal(source.slice(tokens[i].start,tokens[i].end),tokens[i].text);
   if(i) assert.ok(tokens[i-1].end<=tokens[i].start);
  }
 }
});

test('TO and unquoted NAME columns use blue highlighting', () => {
 assert.equal(assist.syntaxTokens('to')[0].syntax,'keyword');
 for (const source of ['SELECT name FROM employee', 'SELECT e.NAME FROM employee e', 'UPDATE employee SET name = :name', 'CREATE TABLE employee (name VARCHAR2(30))']) {
  assert.equal(assist.syntaxTokens(source).find(t=>t.text.toUpperCase()==='NAME').syntax,'keyword',source);
 }
 const tokens=assist.syntaxTokens('SELECT name, "NAME", \'NAME\', :name FROM name; -- NAME');
 assert.deepEqual(tokens.filter(t=>t.kind==='word'&&t.text==='name').map(t=>t.syntax),['keyword','']);
 const aliased=assist.syntaxTokens('SELECT name.id FROM employee name');
 assert.ok(aliased.filter(t=>t.text==='name').every(t=>t.syntax==='keyword'));
 assert.equal(assist.syntaxTokens('SELECT surname FROM employee')[1].syntax,'');
});

test('comma-separated self join highlights all aliases and references', () => {
 const sql='select e.name as Employee, m.name as manager from employee e, employee m where e.manager_id = m.emp_id;';
 const tokens=assist.syntaxTokens(sql);
 for(const alias of ['e','m']) {
  const matches=tokens.filter(t=>t.text===alias);
  assert.equal(matches.length,3);
  assert.ok(matches.every(t=>t.syntax==='keyword'),alias);
 }
 for(const sql of [
  'SELECT a.id, b.id, c.id FROM employee a, hr.employee b, employee c',
  'SELECT m.id FROM employee e, /* manager */ hr.employee m',
  'SELECT m.id FROM employee e, employee AS m'
 ]) {
  const matches=assist.syntaxTokens(sql).filter(t=>['a','b','c','m'].includes(t.text));
  assert.ok(matches.length>0);
  assert.ok(matches.every(t=>t.syntax==='keyword'),sql);
 }
});

test('commas in expressions and nested predicates do not declare table aliases', () => {
 for(const sql of [
  'SELECT salary, bonus m FROM employee e',
  'SELECT * FROM employee e WHERE salary IN (bonus, amount m)',
  'SELECT * FROM employee e ORDER BY salary, bonus m',
  'SELECT * FROM employee e JOIN department d ON fn(e.id, salary m)=1'
 ]) {
  assert.equal(assist.syntaxTokens(sql).find(t=>t.text==='m').syntax,'',sql);
 }
});
