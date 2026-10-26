const test=require('node:test'),assert=require('node:assert/strict');
const core=require('../online-oracle-compiler/workspace-core.js');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');

test('copied feedback and prompted query output are excluded from executable paste',()=>{
 const source='SQL> DROP TABLE employee;\nORA-00942: table does not exist\nHelp: https://docs.oracle.com/error-help/db/ora-00942/\nSQL> Table dropped.\nSQL> SELECT ID FROM T;\nID\n----------\n123\n1 row selected.\nSQL> DELETE FROM T;\n2 rows deleted.\n';
 const sql=core.stripSqlPlusPrompts(core.cleanSqlPlusTranscript(source));
 const batch=core.pastedBatch(sql);
 assert.deepEqual(batch.commands,['DROP TABLE employee;','SELECT ID FROM T;','DELETE FROM T;']);
 assert.equal(batch.remaining.trim(),'');
});

test('unprompted feedback is removed while comments and multiline literal contents survive',()=>{
 const source="CREATE TABLE T (X VARCHAR2(50));\nTable created.\n-- Table dropped.\nINSERT INTO T VALUES ('text\nTable dropped.\nend');\n1 row created.\n";
 const cleaned=core.cleanSqlPlusTranscript(source);
 assert.ok(cleaned.includes('-- Table dropped.'));
 assert.ok(cleaned.includes("'text\nTable dropped.\nend'"));
 assert.ok(!cleaned.includes('\nTable created.'));
 assert.ok(!cleaned.includes('\n1 row created.'));
});

test('semicolon plus newline splits pasted commands, unfinished tail remains',()=>{
 const result=core.pastedBatch('-- heading\nCREATE TABLE A (ID NUMBER);\n\nCREATE TABLE B (ID NUMBER);\nSELECT');
 assert.equal(result.commands.length,2);assert.equal(result.remaining,'SELECT');
 assert.ok(result.commands[0].startsWith('-- heading'));
});
test('quoted semicolon/newline and comments do not split commands',()=>{
 const result=core.pastedBatch("SELECT q'[a;\nb]' FROM DUAL;\nSELECT ';' FROM DUAL;\n");
 assert.equal(result.commands.length,2);
 assert.ok(result.commands[0].includes("q'[a;\nb]'"));
});
test('PL/SQL internal semicolon/newline stays in block until slash',()=>{
 const result=core.pastedBatch('BEGIN\n NULL;\n NULL;\nEND;\n/\nSELECT 1 FROM DUAL;\n');
 assert.equal(result.commands.length,2);assert.ok(result.commands[0].endsWith('END;'));
});
test('same-line semicolon does not create the requested newline boundary',()=>{
 assert.equal(core.pastedBatch('SELECT 1 FROM DUAL; SELECT 2 FROM DUAL;').commands.length,0);
});
test('clean removes invisible controls and preserves normal SQL separation/newlines',()=>{
 const result=core.clean('CREATE\u00a0TABLE\u200b T (\r\n\tID NUMBER\ufeff\n);');
 assert.equal(result,'CREATE TABLE T (\n\tID NUMBER\n);');
});

function runner(execute){
 const source=fs.readFileSync(path.join(__dirname,'../online-oracle-compiler/oracle-sql-ide.js'),'utf8');
 const start=source.indexOf('  async function runPastedTerminalBatch('),end=source.indexOf("  terminalInput.addEventListener('paste'",start);
 const button={disabled:false,dataset:{connected:'true'}},input={value:'',readOnly:false,dispatchEvent(){},focus(){}};
 const context={window:{CompilerWorkspace:core},executionBusy:false,$:()=>button,terminalInput:input,
  terminalCommandHistory:[],terminalHistoryIndex:0,terminalHistoryDraft:'',execute,advanceTerminalComments:source=>source,
  terminalQueue:null,terminalQueueKey:'test-queue',sessionStorage:{removeItem(){}},appendTerminalResult(){},saveTerminalQueue(){},waitForTerminalConnection:async()=>{},setStatus(){},
  terminalInputIsComplete:sql=>sql.trim().endsWith(';'),Event:class{},setTimeout};
 const fn=vm.runInNewContext(`(()=>{${source.slice(start,end)};return runPastedTerminalBatch;})()`,context);
 return{fn,input,button,context};
}

test('SQLPlus paste removes prompts and continuation labels, preserving SQL numbers',()=>{
 assert.equal(core.stripSqlPlusPrompts('SQL> SELECT 200\n  2  FROM dual\n  3  WHERE 1 = 1;'), 'SELECT 200\nFROM dual\nWHERE 1 = 1;');
 assert.equal(core.stripSqlPlusPrompts('SELECT 200\n  2  FROM dual\n  3  WHERE 1 = 1;'), 'SELECT 200\nFROM dual\nWHERE 1 = 1;');
 assert.equal(core.stripSqlPlusPrompts("INSERT INTO T VALUES (2, 'SQL> 3');"), "INSERT INTO T VALUES (2, 'SQL> 3');");
});

test('unknown note is a separate line command and does not consume following SQL',()=>{
 const batch=core.pastedBatch('Note:- Table must be empty.\n2\n-- heading\nALTER TABLE T ADD X NUMBER;\n');
 assert.deepEqual(batch.commands,['Note:- Table must be empty.','2','-- heading\nALTER TABLE T ADD X NUMBER;']);
 const later=core.pastedBatch('SELECT 1 FROM DUAL;\nNote:- explanation\nALTER TABLE T ADD X NUMBER;\n');
 assert.deepEqual(later.commands,['SELECT 1 FROM DUAL;','Note:- explanation','ALTER TABLE T ADD X NUMBER;']);
});
test('requests await completion in order and leave unfinished input',async()=>{
 const calls=[];let running=false;
 const {fn,input,button}=runner(async sql=>{
  assert.equal(input.value,'');assert.equal(input.readOnly,true);
  assert.equal(running,false);running=true;await Promise.resolve();calls.push(sql);running=false;return{};
 });
 input.value='SELECT 1 FROM DUAL;\nSELECT 2 FROM DUAL;\nSELECT';
 await fn(input.value);
 assert.deepEqual(calls,['SELECT 1 FROM DUAL;','SELECT 2 FROM DUAL;']);
 assert.equal(input.value,'SELECT');assert.equal(button.disabled,false);assert.equal(input.readOnly,false);
});
test('bind cancellation preserves unexecuted statements for the user',async()=>{
 let count=0;const {fn,input}=runner(async()=>++count===1?{}:null);
 await fn('SELECT 1 FROM DUAL;\nSELECT :id FROM DUAL;\nSELECT 3 FROM DUAL;');
 assert.equal(count,2);assert.ok(input.value.includes(':id'));assert.ok(input.value.includes('SELECT 3'));
});

test('Ctrl+C cancellation stops after the in-flight statement and discards remaining queue',async()=>{
 const calls=[];
 const {fn,input,button,context}=runner(async sql=>{calls.push(sql);context.terminalBatchCancelled=true;return{};});
 await fn('DELETE FROM T;\nINSERT INTO T VALUES (1);\nSELECT * FROM T;');
 assert.deepEqual(calls,['DELETE FROM T;']);
 assert.equal(context.terminalQueue,null);
 assert.equal(input.value,'');assert.equal(input.readOnly,false);assert.equal(button.disabled,false);
});

test('refresh resumes only uncompleted commands and waits for connection',async()=>{
 const calls=[];const {fn,context}=runner(async sql=>{calls.push(sql);return{};});
 let connected;
 context.waitForTerminalConnection=()=>new Promise(resolve=>{connected=resolve;});
 const recovered={commands:['DELETE FROM T;','INSERT INTO T VALUES (1);'],remaining:'',index:1,request:{id:'existing-request-id',body:'{}'}};
 const pending=fn('',recovered);
 await Promise.resolve();
 assert.deepEqual(calls,[]);
 assert.equal(context.terminalQueue.request.id,'existing-request-id');
 connected();await pending;
 assert.deepEqual(calls,['INSERT INTO T VALUES (1);']);
 assert.equal(context.terminalQueue,null);
});

test('paste stays hidden while another statement or navigator refresh is busy',async()=>{
 const calls=[];const {fn,input,button,context}=runner(async sql=>{assert.equal(input.value,'');calls.push(sql);return{};});
 context.executionBusy=true;button.disabled=true;
 const snapshot='SELECT 1 FROM DUAL;\nSELECT 2 FROM DUAL;';
 const pending=fn(snapshot);
 assert.equal(input.value,'');assert.equal(input.readOnly,true);
 assert.equal(input.compilerPendingDraft,snapshot);assert.deepEqual(calls,[]);
 context.executionBusy=false;button.disabled=false;
 await pending;
 assert.equal(calls.length,2);assert.equal(input.value,'');assert.equal(input.readOnly,false);
});

test('busy paste handler queues without inserting any batch text',()=>{
 const source=fs.readFileSync(path.join(__dirname,'../online-oracle-compiler/oracle-sql-ide.js'),'utf8');
 const start=source.indexOf("  terminalInput.addEventListener('paste', (event) => {"),end=source.indexOf("  terminalInput.addEventListener('input', (event) => trackManualEdit('sqlplus'",start);
 let handler,queued,prevented=false;
 const input={value:'',selectionStart:0,selectionEnd:0,readOnly:false,addEventListener:(type,fn)=>handler=fn,setRangeText(){throw Error('Batch painted into editor');}};
 const context={terminalInput:input,window:{CompilerWorkspace:core},executionBusy:true,$:()=>({disabled:true}),sqlplusCleanPaste:{checked:true},normalizePastedSql:core.clean,recordPaste(){},showToast(){},runPastedTerminalBatch:async sql=>{queued=sql;}};
 vm.runInNewContext(source.slice(start,end),context);
 const batch='SELECT 1 FROM DUAL;\nSELECT 2 FROM DUAL;';
 handler({clipboardData:{getData:()=>batch},preventDefault(){prevented=true;}});
 assert.equal(queued,batch);assert.equal(prevented,true);assert.equal(input.value,'');
});
