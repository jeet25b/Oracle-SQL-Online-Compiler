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
