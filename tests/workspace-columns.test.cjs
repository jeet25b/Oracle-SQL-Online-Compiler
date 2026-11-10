const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function load() {
  const context={window:{addEventListener(){}},location:{hash:'',pathname:'/report'},
    document:{querySelectorAll:()=>[],querySelector:()=>null,addEventListener(){},body:{dataset:{},classList:{remove(){},contains(){return false;}}}}};
  vm.runInNewContext(fs.readFileSync('online-oracle-compiler/workspace-ui.js','utf8'),context);
  return context.window.WorkspaceUI;
}
function cell(colSpan=1) {return {colSpan,hidden:false,matches:()=>true,classList:{toggle(name,value){this[name]=value;}}};}

test('column choice applies to refreshed rows without hiding whole-table empty states',()=>{
  const ui=load(), original={children:[cell(),cell(),cell()]}, empty={children:[cell(3)]};
  const rows=[original,empty];const table={querySelectorAll:()=>rows};
  ui.applyColumns(table,new Set([1]));
  assert.equal(original.children[0].classList['ui-column-hidden'],false);
  assert.equal(original.children[1].classList['ui-column-hidden'],true);
  assert.equal(empty.children[0].classList['ui-column-hidden'],undefined);
  const refreshed={children:[cell(),cell(),cell()]};rows.push(refreshed);
  ui.applyColumns(table,new Set([1]));
  assert.equal(refreshed.children[1].classList['ui-column-hidden'],true);
  ui.applyColumns(table,new Set());
  assert.equal(refreshed.children[1].classList['ui-column-hidden'],false);
});

test('column labels retain meaningful names without sort arrows',()=>{
  assert.equal(load().columnLabel({textContent:' Time ↓ '}),'Time');
});
