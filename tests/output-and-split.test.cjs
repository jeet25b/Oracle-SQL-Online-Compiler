const test=require('node:test'),assert=require('node:assert/strict');
const core=require('../online-oracle-compiler/workspace-core.js');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
test('DESC and DESCRIBE suppress selected-row feedback while SELECT retains it',()=>{
 const data={columns:[{name:'Name'},{name:'Null?'},{name:'Type'}],rows:[['EMP_ID','','CHAR(7)']]};
 for(const sql of ['DESC EMPLOYEE','DESCRIBE EMPLOYEE','-- heading\nDESC EMPLOYEE']) {
  const output=core.outputText(sql,data);
  assert.ok(output.includes('EMP_ID'));assert.ok(!output.includes('row selected'));
 }
 assert.ok(core.outputText('SELECT * FROM EMPLOYEE',data).includes('1 row selected.'));
});
test('SQLPlus-style feedback uses statement type and affected row count',()=>{
 assert.equal(core.outputText('CREATE TABLE T (ID NUMBER)',{message:'Statement 1 executed'}),'Table created.');
 assert.equal(core.outputText('INSERT INTO T VALUES (1)',{rowsAffected:1}),'1 row created.');
 assert.equal(core.outputText('UPDATE T SET ID=2',{rowsAffected:3}),'3 rows updated.');
 assert.equal(core.outputText('COMMIT',{}),'Commit complete.');
});
test('SQLPlus text preserves DBMS output, column alignment and Oracle errors',()=>{
 const text=core.outputText('SELECT ID, NAME FROM T',{columns:[{name:'ID'},{name:'NAME'}],rows:[[1,'A'],[22,null]],outputLines:['message']});
 assert.ok(text.startsWith('message\nID NAME\n-- ----\n'));
 assert.ok(text.endsWith('2 rows selected.'));
 assert.equal(core.outputText('bad',{error:'ORA-00911: invalid character'}),'ORA-00911: invalid character');
});
test('per-statement output stays ordered and does not repeat aggregate DBMS lines',()=>{
 const text=core.outputText('',{outputLines:['aggregate'],statementResults:[{statement:'CREATE TABLE A(ID NUMBER)'},{statement:'BEGIN NULL; END;',outputLines:['only once']}]});
 assert.equal(text,'Table created.\n\nonly once\nPL/SQL procedure successfully completed.');
});
test('editor split uses relative fractions and persists percentage',()=>{
 const source=fs.readFileSync(path.join(__dirname,'../online-oracle-compiler/oracle-sql-ide.js'),'utf8');
 const start=source.indexOf('  const ratioKey ='),end=source.indexOf('  const outputStyleKey=',start);
 const events={},slider={value:56,addEventListener(name,fn){events[name]=fn;}},label={};
 const layout={style:{},getBoundingClientRect:()=>({top:0,height:1010})};
 const resizer={addEventListener(){},setAttribute(){}};
 const storage=new Map();
 vm.runInNewContext(source.slice(start,end),{document:{body:{dataset:{userId:'42'}}},localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},compilerLayout:layout,editorResizer:resizer,$:selector=>selector==='#editorPanePercent'?slider:label});
 events.input({target:{value:'65'}});
 assert.equal(layout.style.gridTemplateRows,'minmax(0, 0.65fr) 10px minmax(0, 0.35fr)');
 assert.equal(storage.get('oracle-pane-ratio:42'),'0.65');
 assert.equal(label.textContent,'Editor 65% / Results 35%');
});
