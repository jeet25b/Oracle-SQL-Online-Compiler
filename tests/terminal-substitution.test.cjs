const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const core=require('../online-oracle-compiler/workspace-core.js');
test('PLSQL requests ampersand values one at a time before executing',async()=>{
 const source=fs.readFileSync('online-oracle-compiler/oracle-sql-ide.js','utf8');
 const asked=[];
 const context=vm.createContext({window:{CompilerWorkspace:core},executionBusy:false,terminalQueue:null,isStudent:false,performance,
  editor:{selectionStart:0,selectionEnd:0,scrollTop:0,scrollLeft:0,value:'',focus(){},setSelectionRange(){}},
  updateHighlight(){},updateEditorCursorPosition(){},renderResult(){},addHistory(){},collectBinds:async()=>({}),takeEditMetrics:()=>({}),
  promptPlsqlSubstitution:async name=>{asked.push(name);return name==='NAME'?'Jeetendra':'21';},$:()=>({checked:false}),
  fetch:async(url,options)=>{assert.equal(asked.length,2);assert.equal(JSON.parse(options.body).sql,"INSERT INTO T VALUES ('Jeetendra', 21);");return{ok:true,json:async()=>({message:'Statement executed',rowsAffected:1})};}
 });
 vm.runInContext(source.slice(source.indexOf('  async function execute(sql,'),source.indexOf('  async function promptPlsqlSubstitution(')),context);
 await vm.runInContext(`execute("INSERT INTO T VALUES ('&NAME', &AGE);",{sqlplus:false})`,context);
 assert.deepEqual(asked,['NAME','AGE']);
});
test('SQLPlus prompts in occurrence order and sends substituted values with verification output',async()=>{
 const source=fs.readFileSync('online-oracle-compiler/oracle-sql-ide.js','utf8');
 const asked=[],rendered=[];
 const context=vm.createContext({window:{CompilerWorkspace:core},executionBusy:false,terminalQueue:null,isStudent:false,performance,
  collectBinds:async()=>({}),isSqlPlusClientInput:()=>true,takeEditMetrics:()=>({}),
  promptTerminalSubstitution:async name=>{asked.push(name);return {NAME:'Jeetendra',AGE:'21'}[name];},
  appendTerminalResult:(sql,data)=>{rendered.push({sql,data});return {remove(){}};},addHistory(){},
  $:()=>({checked:false}),fetch:async(url,options)=>{
   assert.equal(JSON.parse(options.body).sql,"INSERT INTO T VALUES ('Jeetendra', 21);");
   return{ok:true,json:async()=>({rowsAffected:1,message:'1 row created.'})};
  }});
 vm.runInContext(source.slice(source.indexOf('  async function execute(sql,'),source.indexOf('  async function promptTerminalSubstitution(')),context);
 await vm.runInContext(`execute("INSERT INTO T VALUES ('&NAME', &AGE);",{sqlplus:true})`,context);
 assert.deepEqual(asked,['NAME','AGE']);
 assert.ok(rendered.at(-1).data.outputLines.some(line=>line.startsWith('old ')));
 assert.ok(rendered.at(-1).data.outputLines.some(line=>line.includes("new   1: INSERT INTO T VALUES ('Jeetendra', 21)")));
});
