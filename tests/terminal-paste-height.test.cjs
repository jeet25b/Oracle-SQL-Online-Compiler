const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../online-oracle-compiler/oracle-sql-ide.js'),'utf8');
const start=source.indexOf('  function resizeTerminalInput() {');
const end=source.indexOf('  let terminalViewportWidth',start);
function resize(input){
 const fn=vm.runInNewContext(`(()=>{${source.slice(start,end)};return resizeTerminalInput;})()`,{terminalInput:input});
 fn();
}
test('pasted multi-table script gets full height beyond the old 150px limit',()=>{
 const input={style:{},scrollHeight:650,scrollTop:200,getClientRects:()=>[{}]};
 resize(input);assert.equal(input.style.height,'650px');assert.equal(input.scrollTop,0);
});
test('input shrinks after deletion and recalculates after wrapping/font changes',()=>{
 const input={style:{height:'650px'},scrollHeight:30,getClientRects:()=>[{}]};
 resize(input);assert.equal(input.style.height,'30px');
 input.scrollHeight=800;resize(input);assert.equal(input.style.height,'800px');
});
test('hidden terminal is not collapsed while another editor is active',()=>{
 const input={style:{height:'650px'},getClientRects:()=>[]};
 resize(input);assert.equal(input.style.height,'650px');
});
