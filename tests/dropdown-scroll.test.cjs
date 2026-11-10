const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('online-oracle-compiler/theme-controls.js','utf8');
const start = source.indexOf('    function placeMenu(');
const code = source.slice(start,source.indexOf('    function close(',start));
function fixture() {
  const writes = [];
  const menu = {scrollTop:240,scrollHeight:1600};
  menu.style = new Proxy({}, {set(obj,key,value) {
    writes.push([key,value]);
    if (key === 'maxHeight' && value === '') menu.scrollTop = 0;
    obj[key] = value; return true;
  }});
  const context = {menu,trigger:{getBoundingClientRect:()=>({left:200,top:150,bottom:190,width:500})},window:{innerHeight:800,innerWidth:1000}};
  vm.createContext(context);vm.runInContext(code,context);
  return {context,menu,writes};
}
test('scrolling dropdown options leaves menu geometry and scroll position unchanged',()=>{
  const {context,menu,writes} = fixture();
  context.placeMenu({type:'scroll',target:menu});
  assert.deepEqual(writes,[]);
  assert.equal(menu.scrollTop,240);
});
test('page scrolling repositions dropdown without clearing its height constraint',()=>{
  const {context,menu,writes} = fixture();
  context.placeMenu({type:'scroll',target:{}});
  assert.equal(menu.style.top,'194px');
  assert.equal(menu.style.maxHeight,'360px');
  assert.equal(menu.scrollTop,240);
  assert.ok(!writes.some(([key,value])=>key==='maxHeight' && value===''));
});
