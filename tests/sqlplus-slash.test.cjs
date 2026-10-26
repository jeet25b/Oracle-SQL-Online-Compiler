const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const core=require('../online-oracle-compiler/workspace-core.js');
const source=fs.readFileSync('online-oracle-compiler/oracle-sql-ide.js','utf8');
const start=source.indexOf('  async function execute(sql,'),end=source.indexOf('    const editorPosition =',start);
function runner(buffer=''){
 const errors=[],bindRequests=[];
 const context={terminalSqlBuffer:buffer,executionBusy:false,window:{CompilerWorkspace:core},
  appendTerminalResult:(sql,data,error)=>errors.push(error),showToast(){},$:()=>({}),
  isSqlPlusClientInput:sql=>/^(SHOW|SET|CLEAR)\b/i.test(sql),
  collectBinds:async sql=>{bindRequests.push(sql);return {};}};
 vm.createContext(context);
 vm.runInContext(source.slice(start,end)+'executionBusy=false;return statement;}',context);
 return{context,errors,bindRequests,run:sql=>context.execute(sql,{sqlplus:true})};
}
test('slash reruns SQL buffer repeatedly, preserving it across client commands',async()=>{
 const r=runner();await r.run('SELECT 1 FROM DUAL;');
 await r.run('SHOW USER');
 assert.equal(await r.run('/'),'SELECT 1 FROM DUAL;');
 assert.equal(await r.run('/'),'SELECT 1 FROM DUAL;');
 assert.equal(r.bindRequests.at(-1),'SELECT 1 FROM DUAL;');
});
test('empty buffer slash sends no bind or execution request',async()=>{
 const r=runner();await r.run('/');assert.equal(r.bindRequests.length,0);assert.match(r.errors[0],/SP2-0103/);
});
test('last SQL in batch is retained and PL/SQL stays whole',async()=>{
 const r=runner();await r.run('SELECT 1 FROM DUAL;\nSELECT 2 FROM DUAL;');
 assert.equal(await r.run('/'),'SELECT 2 FROM DUAL;');
 await r.run('BEGIN\n NULL;\nEND;\n/');assert.equal(await r.run('/'),'BEGIN\n NULL;\nEND;');
 await r.run('CLEAR SQL');await r.run('/');assert.match(r.errors.at(-1),/SP2-0103/);
});

