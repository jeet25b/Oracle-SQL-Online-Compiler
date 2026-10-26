const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
test('targeted merge retains unrelated objects, removes old constraints, avoids duplicate indexes',()=>{
 const source=fs.readFileSync('global-controls.js','utf8');const context={window:{}};vm.createContext(context);
 vm.runInContext(source.slice(0,source.indexOf('(() => {')),context);
 const row=(name,type='TABLE',extra={})=>({OWNER:'TEST',OBJECT_NAME:name,OBJECT_TYPE:type,...extra});
 const result=context.window.CompilerMergeObjects([row('T'),row('OTHER'),row('OLD','CONSTRAINT',{TABLE_NAME:'T'}),row('IX','INDEX')],[row('T'),row('NEW','CONSTRAINT',{TABLE_NAME:'T'}),row('IX','INDEX')],['T']);
 assert.deepEqual(Array.from(result,r=>r.OBJECT_NAME),['OTHER','T','NEW','IX']);
});
test('repeated connected status checks do not reload navigator',()=>{
 const source=fs.readFileSync('online-oracle-compiler/oracle-sql-ide.js','utf8');const start=source.indexOf('  function setStatus('),end=source.indexOf('  function saveHistory',start);
 const nodes=new Map();let loads=0;
 const $=key=>{if(!nodes.has(key)){const classes=new Set();nodes.set(key,{dataset:{},setAttribute(){},classList:{contains:k=>classes.has(k),toggle(k,on){on?classes.add(k):classes.delete(k);}}});}return nodes.get(key);};
 const context={$,objectRows:[],refreshDatabaseNavigator(){loads++;},renderDatabaseObjects(){}};vm.createContext(context);vm.runInContext(source.slice(start,end),context);
 context.setStatus(true,'Connected');context.setStatus(true,'Connected');context.setStatus(true,'Connected');assert.equal(loads,1);
 context.setStatus(false,'Disconnected');context.setStatus(true,'Connected');assert.equal(loads,2);
});
