const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('online-oracle-compiler/workspace-session-ui.js','utf8');

function fixture({user='teacher-1',storage=new Map()}={}) {
  const nodes=[],events={};
  function element(tag) {const node={tag,children:[],listeners:{},dataset:{},hidden:false,value:'',append(...children){this.children.push(...children);},setAttribute(){},addEventListener(name,fn){this.listeners[name]=fn;}};nodes.push(node);return node;}
  const editor=element('textarea');
  const context={window:{addEventListener(name,fn){events[name]=fn;},confirm(){context.confirmed=true;return false;}},
    document:{body:{dataset:{userId:user}},createElement:element,querySelector:()=>({prepend(){}})},
    localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},
    sessionStorage:{setItem(){}},setTimeout(fn){fn();return 1;},clearTimeout(){},Date};
  vm.runInNewContext(source,context);
  const form={elements:{user:{value:'edited-but-not-connected'},connectString:{value:'not-active'}}};
  const api=context.window.WorkspaceSessionUI.create({editor,form,onRestore(){context.restored=true;}});
  return {api,editor,nodes,events,context,storage};
}

test('active connection follows the server rather than unsubmitted connection form edits',()=>{
  const f=fixture();
  f.api.setDetails({connected:true,user:'ACTUAL_USER',service:'Actual database',connectString:'host/service'});
  f.api.setConnection(true,'Connected');
  assert.ok(f.nodes.some(node=>node.textContent?.includes('ACTUAL_USER · Actual database')));
  assert.ok(!f.nodes.some(node=>node.textContent?.includes('edited-but-not-connected')));
});

test('worksheet recovery is account and database scoped and excludes credentials',()=>{
  const f=fixture();
  f.api.setDetails({connected:true,user:'DBUSER',connectString:'host/service'});f.api.setConnection(true,'Connected');
  f.editor.value='SELECT 1 FROM dual';f.editor.listeners.input();
  const key=[...f.storage.keys()].find(key=>key.includes('DBUSER@host/service'));
  assert.ok(key.includes('teacher-1'));
  assert.deepEqual(JSON.parse(f.storage.get(key)).sql,'SELECT 1 FROM dual');
  const other=fixture({user:'teacher-2',storage:f.storage});
  other.api.setDetails({connected:true,user:'DBUSER',connectString:'host/service'});other.api.setConnection(true,'Connected');
  assert.equal(other.nodes.find(node=>node.tag==='button').hidden,true);
});

test('only server-reported pending transactions trigger navigation and disconnect warnings',()=>{
  const f=fixture();f.api.setConnection(true,'Connected');
  let prevented=false;
  f.events.beforeunload({preventDefault(){prevented=true;}});
  assert.equal(prevented,false);
  f.api.updateTransactions({worksheet:true,sqlcl:false});
  f.events.beforeunload({preventDefault(){prevented=true;}});
  assert.equal(prevented,true);
  assert.equal(f.api.confirmDisconnect(),false);
  f.api.updateTransactions({worksheet:false,sqlcl:false});
  assert.equal(f.api.confirmDisconnect(),true);
});
