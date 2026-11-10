const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('online-oracle-compiler/oracle-sql-ide.js','utf8');
const helpers=source.slice(source.indexOf('  function persistTerminalCommandHistory()'),source.indexOf("  window.addEventListener('pagehide'",source.indexOf('  function persistTerminalCommandHistory()')));
function setup(){
 const stored=new Map(),prompt={},output={children:['old-output'],replaceChildren(...children){this.children=children;}};
 const input={value:'draft',style:{height:'300px'},focus(){this.focused=true;}};
 const context={terminalCommandHistory:['select 1 from dual;','select 2 from dual;'],terminalHistoryIndex:0,terminalHistoryDraft:'draft',terminalInput:input,terminalPrompt:prompt,
 terminalCommandHistoryKey:'commands',terminalTranscriptKey:'transcript',personalTerminalBufferEntries:null,defaultTerminalBufferEntries:5000,
 sessionStorage:{setItem(k,v){stored.set(k,v);},getItem(k){return stored.get(k)||null;}},
 $:()=>output,updateTerminalInputHighlight(){},saveTerminalQueue(){},
 };
 vm.createContext(context);vm.runInContext(helpers,context);
 return {context,stored,input,output,prompt};
}
test('Clear empties display and input while preserving command history across reload',()=>{
 const {context:c,stored,input,output,prompt}=setup();
 vm.runInContext('clearTerminalDisplay()',c);
 assert.deepEqual(output.children,[prompt]);assert.equal(input.value,'');assert.equal(input.style.height,'');
 assert.equal(c.terminalCommandHistory.length,2);assert.equal(c.terminalHistoryIndex,2);
 assert.deepEqual(JSON.parse(stored.get('commands')),['select 1 from dual;','select 2 from dual;']);
 assert.equal(stored.get('transcript'),'[]');
 c.terminalCommandHistory=[];
 vm.runInContext('restoreTerminalCommandHistory([])',c);
 assert.equal(c.terminalCommandHistory.length,2);assert.equal(c.terminalHistoryIndex,2);
});
test('legacy transcript restores history when separate storage is missing',()=>{
 const {context:c}=setup();
 vm.runInContext('restoreTerminalCommandHistory([{sql:"select 3 from dual;"}])',c);
 assert.equal(c.terminalCommandHistory[0],'select 3 from dual;');
});
