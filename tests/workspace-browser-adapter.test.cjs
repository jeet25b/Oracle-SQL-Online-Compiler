const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const lexical=require('../online-oracle-compiler/editor-assist.js');
function fixture(){
 const store=new Map(),listeners={},timers=[];
 const node=()=>({textContent:'',children:[],append(child){this.children.push(child);}});
 const document={hidden:false,createElement:node,addEventListener(name,fn){listeners[name]=fn;}};
 const root={CompilerEditorAssist:lexical,addEventListener(name,fn){listeners[name]=fn;}};
 const storage={getItem:key=>store.get(key)||null,setItem:(key,value)=>store.set(key,value),removeItem:key=>store.delete(key)};
 class InputEvent{constructor(type,options){this.type=type;Object.assign(this,options);}}
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../online-oracle-compiler/workspace-core.js'),'utf8'),{
  window:root,document,localStorage:storage,InputEvent,setTimeout(fn){timers.push(fn);return timers.length;},clearTimeout(){}
 });
 const input={id:'sqlEditor',value:'select 1;',selectionStart:0,selectionEnd:0,scrollTop:0,scrollLeft:0,handlers:{},
  addEventListener(name,fn){(this.handlers[name]||=[]).push(fn);},
  dispatchEvent(event){for(const fn of this.handlers[event.type]||[])fn(event);},
  setSelectionRange(start,end){this.selectionStart=start;this.selectionEnd=end;},
  setRangeText(text,start,end){this.value=this.value.slice(0,start)+text+this.value.slice(end);this.setSelectionRange(start+text.length,start+text.length);},focus(){}
 };
 function key(name,shift=false){input.dispatchEvent({type:'keydown',ctrlKey:true,key:name,shiftKey:shift,preventDefault(){},stopImmediatePropagation(){}});}
 return{api:root.CompilerWorkspace,input,key,store,listeners,timers,node};
}
test('formatting, completion and indentation each undo and redo as one operation',()=>{
 const {api,input,key}=fixture();api.history(input);
 const states=[input.value];
 api.edit(input,api.format(input.value));states.push(input.value);
 api.edit(input,' FROM DUAL',input.value.indexOf(';'),input.value.indexOf(';'));states.push(input.value);
 api.edit(input,'\n  ',input.value.length,input.value.length);states.push(input.value);
 for(let index=2;index>=0;index--){key('z');assert.equal(input.value,states[index]);}
 for(let index=1;index<states.length;index++){key('z',true);assert.equal(input.value,states[index]);}
});
test('a new edit clears redo and native history input routes through same stack',()=>{
 const {api,input,key}=fixture();api.history(input);
 api.edit(input,'SELECT 2;');key('z');api.edit(input,'SELECT 3;');key('y');assert.equal(input.value,'SELECT 3;');
 input.dispatchEvent({type:'beforeinput',inputType:'historyUndo',preventDefault(){},stopImmediatePropagation(){}});
 assert.equal(input.value,'select 1;');
});
test('draft restoration and saving are isolated by user and workspace',()=>{
 const {api,input,store,listeners,node}=fixture();input.value='';
 const key=api.draftKey('42','/oracle-sql/','sqlEditor');store.set(key,JSON.stringify({text:'SELECT 42;'}));
 api.draft(input,{user:'42',workspace:'/oracle-sql/',statusHost:node()});assert.equal(input.value,'SELECT 42;');
 input.value='SELECT 43;';listeners.pagehide();assert.equal(JSON.parse(store.get(key)).text,'SELECT 43;');
 input.value='';api.draft(input,{user:'99',workspace:'/oracle-sql/',statusHost:node()});assert.equal(input.value,'');
});
test('existing source is not replaced by recovery and corrupt storage cannot block editor',()=>{
 const {api,input,store,node}=fixture();const key=api.draftKey('42','/oracle-sql/','sqlEditor');
 store.set(key,JSON.stringify({text:'OLD'}));api.draft(input,{user:'42',workspace:'/oracle-sql/',statusHost:node()});assert.equal(input.value,'select 1;');
 store.set(key,'bad-json');assert.doesNotThrow(()=>api.draft(input,{user:'42',workspace:'/oracle-sql/',statusHost:node()}));
});
