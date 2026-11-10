const test = require('node:test');
const assert = require('node:assert/strict');
const assist = require('../online-oracle-compiler/editor-assist.js');

test('keyword and table/column identifier completion contexts', () => {
  for (const [sql, kind] of [['SEL','keyword'],['SELECT * FROM EMP','table'],['SELECT EMP','column'],['SELECT * FROM EMP WHERE NAM','column']]) {
    assert.equal(assist.completion(sql,sql.length).kind,kind,sql);
  }
});

test('suggestions require three characters in the current keyword or identifier',()=>{
  for(const sql of ['S','SE','SELECT E','SELECT EM','SELECT * FROM E','SELECT * FROM EM','SELECT EMP.','SELECT EMP.NA'])assert.equal(assist.completion(sql,sql.length),null,sql);
  for(const sql of ['SEL','SELECT EMP','SELECT * FROM EMP','SELECT EMP.NAM'])assert.ok(assist.completion(sql,sql.length),sql);
  assert.equal(assist.completion('SELECT',2),null);
});
test('no suggestions inside data strings, numbers, VALUES or predicate values', () => {
  for(const sql of ["SELECT 'abc", "SELECT 'it''s abc", "SELECT q'[abc", 'INSERT INTO EMP VALUES (JO', 'SELECT * FROM EMP WHERE NAME = JO', 'SELECT 123', 'CREATE TABLE T (N NUMBER(12', 'CREATE TABLE T (N VARCHAR2(20) DEFAULT AB']) {
    assert.equal(assist.completion(sql,sql.length),null,sql);
  }
});
test('comments suppress completion; completion resumes after comment', () => {
  for(const sql of ['SELECT -- abc','/* abc']) assert.equal(assist.completion(sql,sql.length),null);
  assert.equal(assist.completion('-- comment\nSEL',14).kind,'keyword');
});
test('replacement range includes word suffix at caret', () => {
  const context=assist.completion('SELECT',3);
  assert.equal(context.start,0);assert.equal(context.end,6);assert.equal(context.prefix,'SEL');
});
test('Enter inherits indentation and increases inside PL/SQL blocks', () => {
  assert.equal(assist.indentation('  SELECT',8).insert,'\n  ');
  assert.equal(assist.indentation('  BEGIN',7).insert,'\n    ');
  assert.equal(assist.indentation("  'BEGIN'",9).insert,'\n  ');
});
test('Enter between block or bracket pair leaves closing line aligned', () => {
  const result=assist.indentation('BEGINEND;',5);
  assert.equal(result.insert,'\n  \n');assert.equal(result.caret,8);
});
test('live hints detect typo, parenthesis and nonstandard space', () => {
  for(const sql of ['SELEC 1 FROM DUAL;', 'SELECT (1 FROM DUAL;', 'SELECT ) FROM DUAL;', 'SELECT\u00a01 FROM DUAL;']) assert.equal(assist.diagnostics(sql).length,1);
});
test('quoted/comment parentheses are not syntax errors', () => {
  for(const sql of ["SELECT ')' FROM DUAL;", "SELECT q'[)]' FROM DUAL;", 'SELECT 1 /* ) */ FROM DUAL;', 'SELECT (1) FROM DUAL;']) assert.equal(assist.diagnostics(sql).length,0,sql);
});


function pairInput(value='',start=value.length,end=start){
 global.InputEvent=class {constructor(type,options){Object.assign(this,{type},options);}};
 return {value,selectionStart:start,selectionEnd:end,
  setRangeText(text,a,b){this.value=this.value.slice(0,a)+text+this.value.slice(b);},
  setSelectionRange(a,b){this.selectionStart=a;this.selectionEnd=b;},dispatchEvent(){}};
}
function press(input,key){return assist.pairKey(input,{key,preventDefault(){},stopImmediatePropagation(){}});}
test('pairing respects boundaries and wraps explicit selections',()=>{
 for(const [open,close] of [['(',')'],['[',']'],['{','}'],["'","'"],['"','"']]){
  const input=pairInput();assert.equal(press(input,open),true);assert.equal(input.value,open+close);assert.equal(input.selectionStart,1);
  const beforeWord=pairInput('foo',0);assert.equal(press(beforeWord,open),false);
  const selected=pairInput('foo + bar',0,9);assert.equal(press(selected,open),true);assert.equal(selected.value,open+'foo + bar'+close);assert.equal(selected.selectionStart,1);assert.equal(selected.selectionEnd,10);
 }
});
test('quotes do not pair after identifiers or inside strings and comments',()=>{
 for(const quote of ["'",'"'])for(const text of ['don','name_', '-- comment ', '/* comment ', "SELECT 'hello", 'SELECT "name']){
  const input=pairInput(text);assert.equal(press(input,quote),false,text);
 }
});
test('tracked closers skip and empty pairs backspace together',()=>{
 for(const [open,close] of [['(',')'],['[',']'],['{','}'],["'","'"],['"','"']]){
  const input=pairInput();press(input,open);assert.equal(press(input,close),true);assert.equal(input.value,open+close);assert.equal(input.selectionStart,2);
  const empty=pairInput();press(empty,open);assert.equal(press(empty,'Backspace'),true);assert.equal(empty.value,'');
  const untracked=pairInput(open+close,1);assert.equal(press(untracked,close),false);
 }
});
test('third quote expands to six quotes with cursor in the middle',()=>{
 for(const quote of ["'",'"']){
  const input=pairInput();press(input,quote);press(input,quote);press(input,quote);
  assert.equal(input.value,quote.repeat(6));assert.equal(input.selectionStart,3);
 }
});


test('matching brackets identify innermost and adjacent pairs',()=>{
 const sql='SELECT (a + (b * c)) FROM t;';
 const inner=assist.bracketPairs(sql,sql.indexOf('b')).active;
 assert.equal(inner.start,sql.indexOf('(b'));assert.equal(inner.end,sql.indexOf(')'));
 const outer=assist.bracketPairs(sql,sql.indexOf('(a')).active;
 assert.equal(outer.start,sql.indexOf('(a'));assert.equal(outer.end,sql.lastIndexOf(')'));
});
test('bracket guides ignore strings, quoted identifiers and comments',()=>{
 const sql=`SELECT '(' AS "[name]" /* } */ -- (
FROM t WHERE x IN (1, 2);`;
 const result=assist.bracketPairs(sql,sql.indexOf('1'));
 assert.equal(result.pairs.length,1);assert.equal(result.unmatched.length,0);
});
test('mismatched bracket kinds and unfinished pairs are reported',()=>{
 const result=assist.bracketPairs('([)]',2);
 assert.ok(result.unmatched.includes(2));assert.ok(result.unmatched.includes(0));
 assert.equal(assist.bracketPairs('SELECT (1',9).unmatched.length,1);
});
