const test=require('node:test'),assert=require('node:assert/strict');
const core=require('../online-oracle-compiler/workspace-core.js');
const assist=require('../online-oracle-compiler/editor-assist.js');

test('current statement, selected text and full script have distinct targets',()=>{
 const sql='SELECT 1 FROM DUAL;\nSELECT 2 FROM DUAL;\nSELECT 3 FROM DUAL;';
 assert.equal(core.target(sql,27).sql,'SELECT 2 FROM DUAL;');
 assert.equal(core.target(sql,0,6).sql,'SELECT');
 assert.equal(core.target(sql,27,27,true).sql,sql);
});
test('statement boundaries ignore strings, comments and Oracle q quotes',()=>{
 const sql="-- heading;\nSELECT q'[isn''t; :fake]' FROM DUAL; /* ; */ SELECT ';' FROM DUAL;";
 assert.equal(core.ranges(sql).length,2);
 assert.ok(core.target(sql,sql.indexOf("SELECT ';'")).sql.includes("SELECT ';'"));
});
test('anonymous and stored PL/SQL stay whole until slash separator',()=>{
 for(const block of ['BEGIN NULL; NULL; END;', '/* header */ CREATE OR REPLACE PROCEDURE p AS BEGIN NULL; END;']){
  const sql=block+'\n/\nSELECT 1 FROM DUAL;';
  assert.equal(core.ranges(sql).length,2);
  assert.equal(core.target(sql,block.indexOf('NULL')).sql,block);
 }
});
test('case conversion preserves literal data, quoted identifiers and comments',()=>{
 const sql="select 'AbC  x', q'[Don\'t Change]' from \"MixedName\" -- Keep Me\nwhere a=:id;";
 const upper=core.convert(sql,'upper');
 for(const token of core.scan(sql).tokens.filter(t=>['literal','identifier','comment'].includes(t.kind))) assert.ok(upper.includes(token.text));
 assert.ok(upper.startsWith('SELECT'));
 assert.equal(core.convert("select 'AbC'",'lower',9,10),"select 'AbC'");
});
test('formatter preserves protected token bytes and minus/comment separation',()=>{
 const sql="select 'A  B; c', q'[x\ny]' from \"Mixed\" -- comment  unchanged\nwhere a = 1 and b = - -2;";
 const output=core.format(sql);
 const protectedTokens=text=>core.scan(text).tokens.filter(t=>['literal','identifier','comment'].includes(t.kind)).map(t=>t.text);
 assert.deepEqual(protectedTokens(output),protectedTokens(sql));
 assert.ok(output.includes('- -2'));
 assert.equal(core.ranges(output).length,1);
});
test('binds are unique and ignore data/comments/assignment operators',()=>{
 const sql="BEGIN :result := :ID + :id + :1; x := q'[isn\'t :fake]'; -- :ignored\nEND;";
 assert.deepEqual(core.binds(sql),['RESULT','ID','1']);
});
test('draft keys isolate users, workspace and editor; anonymous drafts disabled',()=>{
 assert.notEqual(core.draftKey('1','/oracle-sql/','sqlEditor'),core.draftKey('2','/oracle-sql/','sqlEditor'));
 assert.notEqual(core.draftKey('1','/oracle-sql/','sqlEditor'),core.draftKey('1','/other/','sqlEditor'));
 assert.notEqual(core.draftKey('1','/oracle-sql/','sqlEditor'),core.draftKey('1','/oracle-sql/','terminalInput'));
 assert.equal(core.draftKey('','/oracle-sql/','sqlEditor'),null);
});
test('shared lexical fixes cover comments, values and literal whitespace',()=>{
 const sql='INSERT INTO EMP VALUES (1) RETURNING EMP';
 assert.notEqual(assist.completion(sql,sql.length),null);
 assert.equal(assist.indentation('/*\nBEGIN',8).insert,'\n');
 assert.deepEqual(assist.diagnostics("SELECT 'A\u00a0B' FROM DUAL;"),[]);
});

test('complete comments advance prompt; unfinished block or actual SQL do not count as comment-only',()=>{
 assert.equal(core.commentOnly('-- one line'),true);
 assert.equal(core.commentOnly('/* multiple\nlines */'),true);
 assert.equal(core.commentOnly('/* not closed'),false);
 assert.equal(core.commentOnly('-- heading\nSELECT 1 FROM DUAL;'),false);
});
