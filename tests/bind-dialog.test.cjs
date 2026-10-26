const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const core=require('../online-oracle-compiler/workspace-core.js');
const source=fs.readFileSync(path.join(__dirname,'../online-oracle-compiler/oracle-sql-ide.js'),'utf8');
function fixture(){
 const nodes=[];let modal;
 const document={createElement(tag){const node={tag,children:[],handlers:{},value:'',
  append(...children){this.children.push(...children);},addEventListener(name,callback){this.handlers[name]=callback;},setAttribute(){},
  close(){},remove(){},showModal(){modal=this;},focus(){}};nodes.push(node);return node;}};
 const start=source.indexOf('  async function collectBinds('),end=source.indexOf('  const terminalQueueKey',start);
 const collect=vm.runInNewContext(`(()=>{${source.slice(start,end)};return collectBinds;})()`,{
  document,window:{CompilerWorkspace:core},shell:{append(){}},Promise,Number
 });
 return{collect,nodes,getModal:()=>modal};
}
test('no bind dialog when colons occur only in quoted data',async()=>{
 const {collect,nodes}=fixture();assert.equal(Object.keys(await collect("SELECT ':fake' FROM DUAL")).length,0);assert.equal(nodes.length,0);
});

test('one popup contains both substitution and bind values',async()=>{
 const {collect,nodes}=fixture();const pending=collect("INSERT INTO T VALUES ('&NAME', :AGE)",{substitutionNames:['NAME']});
 const labels=nodes.filter(node=>node.tag==='label'),inputs=nodes.filter(node=>node.tag==='input'),types=nodes.filter(node=>node.tag==='select');
 assert.deepEqual(labels.map(label=>label.textContent),['&NAME',':AGE']);
 assert.ok(labels.every(label=>!label.hidden));
 types[0].value='text';inputs[0].value='Jeetendra';types[1].value='number';inputs[1].value='21';
 nodes.find(node=>node.tag==='form').handlers.submit({preventDefault(){}});
 const values=await pending;assert.equal(values['&NAME'],'Jeetendra');assert.equal(values.AGE,21);
});

test('PLSQL bind wizard shows one value at a time and executes after the last',async()=>{
 const {collect,nodes}=fixture();const result=collect('SELECT :name, :age FROM DUAL',{sequential:true});
 const labels=nodes.filter(node=>node.tag==='label'),inputs=nodes.filter(node=>node.tag==='input'),types=nodes.filter(node=>node.tag==='select');
 const form=nodes.find(node=>node.tag==='form');
 assert.equal(labels[0].hidden,false);assert.equal(labels[1].hidden,true);
 types[0].value='text';inputs[0].value='Jeetendra';form.handlers.submit({preventDefault(){}});
 assert.equal(labels[0].hidden,true);assert.equal(labels[1].hidden,false);
 types[1].value='number';inputs[1].value='21';form.handlers.submit({preventDefault(){}});
 const values=await result;assert.equal(values.NAME,'Jeetendra');assert.equal(values.AGE,21);
});
test('Cancel and Escape resolve null without requesting execution',async()=>{
 for(const escape of [false,true]){
  const {collect,nodes,getModal}=fixture();const result=collect('SELECT :id FROM DUAL');
  if(escape)getModal().handlers.cancel({preventDefault(){}});
  else nodes.find(node=>node.textContent==='Cancel').handlers.click();
  assert.equal(await result,null);
 }
});
test('bind submission validates numeric values and preserves explicit NULL',async()=>{
 const {collect,nodes}=fixture();const result=collect('SELECT :id, :optional FROM DUAL');
 const selects=nodes.filter(node=>node.tag==='select'),inputs=nodes.filter(node=>node.tag==='input');
 selects[0].value='number';inputs[0].value='not numeric';selects[1].value='null';
 const form=nodes.find(node=>node.tag==='form');form.handlers.submit({preventDefault(){}});
 assert.ok(nodes.some(node=>String(node.textContent).includes('Enter a valid number')));
 inputs[0].value='42';form.handlers.submit({preventDefault(){}});
 const values=await result;assert.equal(values.ID,42);assert.equal(values.OPTIONAL,null);
});
