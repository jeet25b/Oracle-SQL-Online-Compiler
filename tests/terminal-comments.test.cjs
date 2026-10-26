const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const core=require('../online-oracle-compiler/workspace-core.js');
test('leading single comments and blank prompt stay separate from SQL',()=>{
 const input='--Delete all records from a table\n--remove all employees.\n\nDELETE FROM employee;';
 const split=core.leadingComments(input);
 assert.deepEqual(split.comments,['--Delete all records from a table','--remove all employees.','']);
 assert.equal(split.sql,'DELETE FROM employee;');
});
test('complete block advances prompt while incomplete block and inline SQL comments stay intact',()=>{
 assert.deepEqual(core.leadingComments('/* one\n two */\nSELECT 1 FROM dual;'),{comments:['/* one\n two */'],sql:'SELECT 1 FROM dual;'});
 for(const sql of ['/* unfinished','SELECT /* inline */ 1 FROM dual;','BEGIN\n-- inside block\nNULL;\nEND;'])assert.equal(core.leadingComments(sql).sql,sql);
 assert.equal(core.leadingComments("-- heading\nINSERT INTO T VALUES ('-- data');").sql,"INSERT INTO T VALUES ('-- data');");
});
test('script result renderer gives each leading comment a prompt and attaches result only to SQL',()=>{
 const source=fs.readFileSync('online-oracle-compiler/oracle-sql-ide.js','utf8');
 const start=source.indexOf('  function appendTerminalResult('),end=source.indexOf('  async function connect(',start);
 const node=()=>({children:[],style:{},dataset:{},append(child){this.children.push(child);},replaceChildren(...children){this.children=children;}});
 const output={children:[],insertBefore(entry){this.children.push(entry);}};
 const context={window:{CompilerWorkspace:core},document:{createElement:node},trimTerminalBuffer(){},outputStyle:'website',$:s=>s==='#terminalOutput'?output:null};
 vm.createContext(context);vm.runInContext(source.slice(start,end),context);
 context.appendTerminalResult('-- first\n-- second\n\nINSERT INTO T VALUES (1);',{message:'1 row created.'},null);
 assert.deepEqual(output.children.map(entry=>entry.children[0].textContent),['-- first','-- second','','INSERT INTO T VALUES (1);']);
 assert.equal(output.children.at(-1).children[1].textContent,'1 row created.');
 assert.ok(output.children.slice(0,3).every(entry=>entry.children.length===1));
 output.children=[];
 context.appendTerminalResult('/*Delete a specific record\nremove employee EMP0003.*/',null,null);
 assert.equal(output.children.length,1);
 assert.deepEqual(output.children[0].children.map(line=>line.textContent),['/*Delete a specific record','remove employee EMP0003.*/']);
 assert.ok(output.children[0].children.every(line=>line.className==='terminal-command'));
 output.children=[];
 context.appendTerminalResult('CREATE TABLE STUDENT (\n ENR_NO CHAR(5),\n AGE NUMBER(2)\n);',{message:'Table created.'},null);
 const command=output.children[0].children[0];
 assert.equal(command.textContent,'CREATE TABLE STUDENT (\n ENR_NO CHAR(5),\n AGE NUMBER(2)\n);');
 assert.equal(command.children[1].dataset.lineNumber,'2');
 assert.equal(command.children[1].textContent,' ENR_NO CHAR(5),');
});
