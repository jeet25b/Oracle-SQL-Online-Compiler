/* Advanced SQL authoring and result tools, using locally bundled libraries. */
(function(root){
 'use strict';
 const core=root.CompilerWorkspace;
 const $=(selector)=>document.querySelector(selector);
 const field=(row,name)=>row[name]??row[name.toLowerCase()];
 const escape=text=>String(text??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 function button(label,fn){const node=document.createElement('button');node.type='button';node.className='tool-button';node.textContent=label;node.addEventListener('click',fn);return node;}
 function download(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}
 function tableRows(rows,search,filters,sort){
  const query=search.toLocaleLowerCase();
  const indexed=rows.map((values,index)=>({values,index})).filter(({values})=>values.some(v=>String(v??'NULL').toLocaleLowerCase().includes(query))&&filters.every((filter,i)=>!filter||String(values[i]??'NULL').toLocaleLowerCase().includes(filter.toLocaleLowerCase())));
  if(sort.index>=0)indexed.sort((a,b)=>{const x=a.values[sort.index],y=b.values[sort.index];const result=x==null?(y==null?0:-1):y==null?1:typeof x==='number'&&typeof y==='number'?x-y:String(x).localeCompare(String(y),undefined,{numeric:true});return result*sort.direction||a.index-b.index;});
  return indexed.map(row=>row.values);
 }
 function grid(table,columns,rows){
  if(table.dataset.proGrid)return table;table.dataset.proGrid='true';
  const wrapper=document.createElement('div');wrapper.className='pro-grid';
  const controls=document.createElement('div');controls.className='pro-grid-controls';
  const search=document.createElement('input');search.type='search';search.placeholder='Search result rows';search.setAttribute('aria-label','Search result rows');
  const count=document.createElement('span');count.setAttribute('role','status');
  let sort={index:-1,direction:1},filtered=rows,filters=columns.map(()=>''),page=1;
  const size=100,tbody=table.tBodies[0],filterRow=document.createElement('tr');
  columns.forEach((column,index)=>{
   const th=table.tHead.rows[0].cells[index],name=column.name||String(column);th.textContent='';
   const sortButton=button(name+' ↕',()=>{sort={index,direction:sort.index===index?-sort.direction:1};page=1;render();});th.append(sortButton);
   const cell=document.createElement('th'),input=document.createElement('input');input.type='search';input.placeholder='Filter '+name;input.setAttribute('aria-label','Filter '+name);input.addEventListener('input',()=>{filters[index]=input.value;page=1;render();});cell.append(input);filterRow.append(cell);
  });
  table.tHead.append(filterRow);
  const previous=button('Previous',()=>{page--;render();}),next=button('Next',()=>{page++;render();});
  function render(){
   filtered=tableRows(rows,search.value,filters,sort);const pages=Math.max(1,Math.ceil(filtered.length/size));page=Math.min(page,pages);
   const fragment=document.createDocumentFragment();
   filtered.slice((page-1)*size,page*size).forEach(values=>{const row=document.createElement('tr');values.forEach(value=>{const td=document.createElement('td');td.textContent=value==null?'NULL':typeof value==='object'?JSON.stringify(value):String(value);row.append(td);});fragment.append(row);});
   if(!filtered.length){const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=columns.length;cell.textContent='No matching rows';row.append(cell);fragment.append(row);}
   tbody.replaceChildren(fragment);count.textContent=`${filtered.length} of ${rows.length} fetched rows · Page ${page}/${pages}`;previous.disabled=page===1;next.disabled=page===pages;
   [...table.tHead.rows[0].cells].forEach((th,i)=>th.setAttribute('aria-sort',sort.index===i?(sort.direction===1?'ascending':'descending'):'none'));
  }
  search.addEventListener('input',()=>{page=1;render();});
  const headers=columns.map(c=>c.name||String(c));
  controls.append(search,count,button('CSV',()=>download(new Blob([[headers,...filtered].map(row=>row.map(v=>'"'+String(v??'').replace(/"/g,'""')+'"').join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}),'query-results.csv')),button('Excel (.xlsx)',async event=>{
   const target=event.currentTarget;target.disabled=true;try{if(!root.ExcelJS)throw Error('Excel exporter is still loading. Try again.');const book=new root.ExcelJS.Workbook(),sheet=book.addWorksheet('Results');sheet.addRow(headers);filtered.forEach(row=>sheet.addRow(row));sheet.getRow(1).font={bold:true};sheet.views=[{state:'frozen',ySplit:1}];sheet.autoFilter={from:{row:1,column:1},to:{row:Math.max(1,filtered.length+1),column:headers.length}};sheet.columns.forEach(column=>column.width=22);download(new Blob([await book.xlsx.writeBuffer()],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}),'query-results.xlsx');}catch(error){count.textContent=error.message;}finally{target.disabled=false;}
  }),previous,next);wrapper.append(controls,table);render();return wrapper;
 }
 function formatOracle(source){
  const text=core.format(source),tokens=core.scan(text).tokens;let depth=0,blocks=0,offset=0,previousComma=false;
  return text.split('\n').map(line=>{
   const start=offset;offset+=line.length+1;
   const protectedLine=tokens.some(token=>['literal','identifier'].includes(token.kind)&&token.start<start&&token.end>start);
   const row=tokens.filter(token=>token.start>=start&&token.start<start+line.length);
   const code=row.filter(token=>token.kind!=='comment'),first=code[0]?.text.toUpperCase();
   const closes=first===')'||first===']'||first==='}'?1:0;
   const endBlock=['END','ELSE','ELSIF','EXCEPTION'].includes(first)?1:0;
   const indent=Math.max(0,depth-closes)+Math.max(0,blocks-endBlock)+(previousComma&&depth===0?1:0);
   const output=protectedLine?line:'  '.repeat(indent)+line.trimStart();
   for(let i=0;i<code.length;i++){
    const token=code[i],word=token.text.toUpperCase(),prev=code[i-1]?.text.toUpperCase();
    if(token.kind==='symbol'&&'([{'.includes(word))depth++;
    else if(token.kind==='symbol'&&')]}'.includes(word))depth=Math.max(0,depth-1);
    else if(token.kind==='word'&&word==='END')blocks=Math.max(0,blocks-1);
    else if(token.kind==='word'&&['BEGIN','LOOP','CASE'].includes(word)&&prev!=='END')blocks++;
   }
   previousComma=code[code.length-1]?.text===',';return output;
  }).join('\n');
 }
 function references(sql,defaultOwner){
  const text=core.scan(sql).tokens.map(token=>['literal','comment'].includes(token.kind)?' '.repeat(token.text.length):token.text).join(' ');
  const reserved=new Set(['WHERE','JOIN','LEFT','RIGHT','INNER','OUTER','FULL','ON','SET','VALUES','GROUP','ORDER','HAVING','UNION','CROSS']);
  return [...text.matchAll(/\b(?:FROM|JOIN|UPDATE|INTO)\s+([A-Za-z][\w$#]*)(?:\s*\.\s*([A-Za-z][\w$#]*))?(?:\s+(?:AS\s+)?([A-Za-z][\w$#]*))?/gi)].slice(0,12).map(m=>({owner:(m[2]?m[1]:defaultOwner||'').toUpperCase(),name:(m[2]||m[1]).toUpperCase(),alias:m[3]&&!reserved.has(m[3].toUpperCase())?m[3].toUpperCase():null}));
 }
 const snippets=[
  ['SELECT','SELECT ${1:columns}\nFROM ${2:table_name}\nWHERE ${3:condition};'],
  ['PL/SQL block','DECLARE\n  ${1:variable} ${2:NUMBER};\nBEGIN\n  ${3:NULL;}\nEXCEPTION\n  WHEN OTHERS THEN\n    RAISE;\nEND;\n/'],
  ['Trigger','CREATE OR REPLACE TRIGGER ${1:trigger_name}\nBEFORE ${2:INSERT} ON ${3:table_name}\nFOR EACH ROW\nBEGIN\n  ${4:NULL;}\nEND;\n/'],
  ['Cursor loop','BEGIN\n  FOR rec IN (SELECT ${1:*} FROM ${2:table_name}) LOOP\n    ${3:NULL;}\n  END LOOP;\nEND;\n/']
 ];
 function boot(bridge){
  if(root.WorkbenchPro?.ready)return;root.WorkbenchPro.ready=true;
  const message=$('#proEditorMessage'); if(!root.require?.config){message.textContent='Advanced editor assets could not load. Standard editors remain available.';return;}
  root.require.config({paths:{vs:'/portal/vendor/monaco/vs'}});
  root.require(['vs/editor/editor.main'],()=>{
   const monaco=root.monaco;
   monaco.languages.setLanguageConfiguration('sql',{comments:{lineComment:'--',blockComment:['/*','*/']},brackets:[['(',')'],['[',']'],['{','}']],autoClosingPairs:[{open:'(',close:')',notIn:['string','comment']},{open:'[',close:']',notIn:['string','comment']},{open:'{',close:'}',notIn:['string','comment']},{open:"'",close:"'",notIn:['string','comment']},{open:'"',close:'"',notIn:['string','comment']}],surroundingPairs:[{open:'(',close:')'},{open:'[',close:']'},{open:'{',close:'}'},{open:"'",close:"'"},{open:'"',close:'"'}],folding:{markers:{start:/^\s*--\s*#?region\b/i,end:/^\s*--\s*#?endregion\b/i}}});
   function theme(){const style=getComputedStyle(document.documentElement),dark=document.documentElement.dataset.theme!=='light';const color=(name,fallback)=>{const value=style.getPropertyValue(name).trim();return /^#[0-9a-f]{6}$/i.test(value)?value:fallback;};monaco.editor.defineTheme('portal-sql',{base:dark?'vs-dark':'vs',inherit:true,rules:[],colors:{'editor.background':color('--ide-panel',dark?'#111c2d':'#ffffff'),'editor.foreground':color('--ide-text',dark?'#edf3ff':'#172033'),'editor.lineHighlightBackground':dark?'#17263a':'#eef3fa'}});monaco.editor.setTheme('portal-sql');}
   theme();document.addEventListener('ooc-theme-change',theme);
   const instances=new Map();
   function attach(input,terminal=false){
    const host=document.createElement('div');host.className=terminal?'pro-monaco pro-terminal':'pro-monaco pro-workbook';input.parentElement.append(host);input.parentElement.classList.add('pro-editor-mounted');
    const model=monaco.editor.createModel(input.value,'sql');
    const editor=monaco.editor.create(host,{model,theme:'portal-sql',automaticLayout:true,fontFamily:getComputedStyle(input).fontFamily||'Hack, monospace',fontSize:parseFloat(getComputedStyle(input).fontSize)||14,minimap:{enabled:true},stickyScroll:{enabled:true},folding:true,showFoldingControls:'always',bracketPairColorization:{enabled:true},guides:{bracketPairs:true,highlightActiveBracketPair:true,indentation:true},matchBrackets:'always',autoClosingBrackets:'languageDefined',autoClosingQuotes:'languageDefined',autoSurround:'languageDefined',multiCursorModifier:'alt',columnSelection:false,copyWithSyntaxHighlighting:true,scrollBeyondLastLine:false,wordWrap:'off',tabSize:2,readOnly:input.readOnly||input.disabled,contextmenu:true});
    let syncing=false;
    function toInput(){if(syncing)return;syncing=true;input.value=model.getValue();const selection=editor.getSelection();input.setSelectionRange(model.getOffsetAt(selection.getStartPosition()),model.getOffsetAt(selection.getEndPosition()));input.dispatchEvent(new Event('input',{bubbles:true}));syncing=false;}
    function fromInput(){if(syncing)return;syncing=true;if(model.getValue()!==input.value){const offset=input.selectionStart;model.setValue(input.value);editor.setPosition(model.getPositionAt(offset));}syncing=false;}
    host.addEventListener('copy',event=>{
      if(!event.clipboardData||!bridge.clipboardHtml)return;
      const selections=editor.getSelections();
      const text=selections.map(selection=>model.getValueInRange(selection)).filter(Boolean).join('\n');
      if(!text)return;
      event.clipboardData.setData('text/plain',text);event.clipboardData.setData('text/html',bridge.clipboardHtml(text));event.preventDefault();event.stopImmediatePropagation();
    },true);
    editor.onDidChangeModelContent(toInput);editor.onDidChangeCursorSelection(()=>{if(syncing)return;const selection=editor.getSelection();input.setSelectionRange(model.getOffsetAt(selection.getStartPosition()),model.getOffsetAt(selection.getEndPosition()));});input.addEventListener('input',fromInput);
    // Existing run/history/draft APIs write to the textarea; keep that bridge synchronized.
    const timer=setInterval(()=>{if(!host.isConnected){clearInterval(timer);editor.dispose();model.dispose();return;}fromInput();const style=getComputedStyle(input);editor.updateOptions({readOnly:input.readOnly||input.disabled,fontFamily:style.fontFamily,fontSize:parseFloat(style.fontSize)||14});},500);
    const run=async script=>{toInput();try{await (terminal?bridge.runTerminal():bridge.runWorkbook(script));}finally{fromInput();editor.focus();}};
    editor.addAction({id:'portal-run-'+input.id,label:'Run current statement',keybindings:[monaco.KeyMod.CtrlCmd|monaco.KeyCode.Enter],run:()=>run(false)});editor.addAction({id:'portal-script-'+input.id,label:'Run script',keybindings:[monaco.KeyMod.CtrlCmd|monaco.KeyMod.Shift|monaco.KeyCode.Enter],run:()=>run(true)});
    if(terminal){
      const atFirst=editor.createContextKey('portalTerminalFirstLine',false),atLast=editor.createContextKey('portalTerminalLastLine',false);
      function updateHistoryBoundary(){
        const selection=editor.getSelection(),single=editor.getSelections().length===1&&selection.isEmpty();
        atFirst.set(single&&selection.positionLineNumber===1);
        atLast.set(single&&selection.positionLineNumber===model.getLineCount());
      }
      editor.onDidChangeCursorSelection(updateHistoryBoundary);editor.onDidChangeModelContent(updateHistoryBoundary);updateHistoryBoundary();
      function recall(direction){
        toInput();bridge.terminalHistory(direction);fromInput();
        const position=model.getPositionAt(input.selectionStart);editor.setPosition(position);editor.revealPosition(position);editor.focus();
      }
      editor.addAction({id:'portal-terminal-enter',label:'Run terminal command',keybindings:[monaco.KeyCode.Enter],run:()=>{if(bridge.isTerminalComplete(model.getValue()))run(false);else editor.trigger('keyboard','type',{text:'\n'});}});
      editor.addAction({id:'portal-terminal-previous',label:'Previous terminal command',keybindings:[monaco.KeyMod.Alt|monaco.KeyCode.UpArrow],run:()=>recall(-1)});
      editor.addAction({id:'portal-terminal-next',label:'Next terminal command',keybindings:[monaco.KeyMod.Alt|monaco.KeyCode.DownArrow],run:()=>recall(1)});
      editor.addAction({id:'portal-terminal-boundary-previous',label:'Previous statement from first line',keybindings:[monaco.KeyCode.UpArrow],precondition:'portalTerminalFirstLine && !suggestWidgetVisible',run:()=>recall(-1)});
      editor.addAction({id:'portal-terminal-boundary-next',label:'Next statement from last line',keybindings:[monaco.KeyCode.DownArrow],precondition:'portalTerminalLastLine && !suggestWidgetVisible',run:()=>recall(1)});
    }
    host.addEventListener('paste',event=>{if(!event.clipboardData||input.readOnly)return;event.preventDefault();event.stopImmediatePropagation();const selection=editor.getSelection();input.setSelectionRange(model.getOffsetAt(selection.getStartPosition()),model.getOffsetAt(selection.getEndPosition()));const forwarded=new ClipboardEvent('paste',{clipboardData:event.clipboardData,cancelable:true,bubbles:false});input.dispatchEvent(forwarded);if(!forwarded.defaultPrevented){editor.executeEdits('paste',[{range:selection,text:event.clipboardData.getData('text/plain'),forceMoveMarkers:true}]);}else fromInput();},true);
    editor.addAction({id:'portal-format',label:'Format Oracle SQL',keybindings:[monaco.KeyMod.Alt|monaco.KeyMod.Shift|monaco.KeyCode.KeyF],run:()=>editor.getAction('editor.action.formatDocument').run()});
    instances.set(model.uri.toString(),{input,editor,model,terminal,toInput,fromInput});return editor;
   }
   const workbook=attach(bridge.editor),terminal=attach(bridge.terminal,true);
   $('#formatSql')?.addEventListener('click',event=>{event.preventDefault();event.stopImmediatePropagation();const selection=workbook.getSelection();workbook.getAction(selection.isEmpty()?'editor.action.formatDocument':'editor.action.formatSelection').run();},true);

   const cache=new Map();async function schema(owner,name){if(!owner)return{columns:[],constraints:[]};const key=`${bridge.schema()}:${owner}.${name}`;const hit=cache.get(key);if(hit&&Date.now()-hit.at<60000)return hit.value;const value=Promise.all(['structure','constraints'].map(async view=>{const response=await fetch(`/api/objects/${encodeURIComponent(owner)}/${encodeURIComponent(name)}?view=${view}`,{credentials:'same-origin'});return response.ok?response.json():{};})).then(([structure,constraints])=>({columns:structure.columns||[],constraints:constraints.constraints||[]}));cache.set(key,{value,at:Date.now()});return value;}
   const safe=value=>String(value??'').replace(/[\\`*_{}\[\]<>]/g,' ');
   monaco.languages.registerDocumentFormattingEditProvider('sql',{provideDocumentFormattingEdits(model){return[{range:model.getFullModelRange(),text:formatOracle(model.getValue())}];}});
   monaco.languages.registerDocumentRangeFormattingEditProvider('sql',{provideDocumentRangeFormattingEdits(model,range){return[{range,text:formatOracle(model.getValueInRange(range))}];}});
   function blocks(model){const tokens=core.scan(model.getValue()).tokens,stack=[],ranges=[];for(let i=0;i<tokens.length;i++){const t=tokens[i],word=t.text.toUpperCase();if(t.kind==='word'&&['BEGIN','CASE','LOOP','IF'].includes(word)&&tokens[i-1]?.text.toUpperCase()!=='END')stack.push(t);else if(t.kind==='word'&&word==='END'&&stack.length){const start=stack.pop();const a=model.getPositionAt(start.start).lineNumber,b=model.getPositionAt(t.end).lineNumber;if(b>a)ranges.push({start:a,end:b});}else if(t.kind==='comment'&&t.text.startsWith('/*')){const a=model.getPositionAt(t.start).lineNumber,b=model.getPositionAt(t.end).lineNumber;if(b>a)ranges.push({start:a,end:b});}}
    for(const pair of root.CompilerEditorAssist.bracketPairs(model.getValue(),0).pairs){const a=model.getPositionAt(pair.start).lineNumber,b=model.getPositionAt(pair.end).lineNumber;if(b>a)ranges.push({start:a,end:b});}
    for(const statement of core.ranges(model.getValue())){const a=model.getPositionAt(statement.start).lineNumber,b=model.getPositionAt(statement.end).lineNumber;if(b>a)ranges.push({start:a,end:b});}return ranges;}
   monaco.languages.registerFoldingRangeProvider('sql',{provideFoldingRanges:blocks});
   monaco.languages.registerDocumentSymbolProvider('sql',{provideDocumentSymbols(model){return core.ranges(model.getValue()).map((row,index)=>{const first=core.scan(row.sql).tokens.find(t=>t.kind!=='comment');const start=model.getPositionAt(row.start),end=model.getPositionAt(row.end),range=new monaco.Range(start.lineNumber,start.column,end.lineNumber,end.column);return{name:(first?.text||'Statement')+' '+(index+1),kind:monaco.languages.SymbolKind.Function,range,selectionRange:range};});}});
   monaco.languages.registerCompletionItemProvider('sql',{triggerCharacters:['.',' '],async provideCompletionItems(model,position,context,cancellation){const word=model.getWordUntilPosition(position),range=new monaco.Range(position.lineNumber,word.startColumn,position.lineNumber,word.endColumn),before=model.getValue().slice(0,model.getOffsetAt(position));if(['literal','identifier','comment'].includes(core.scan(before).open))return{suggestions:[]};const refs=references(model.getValue(),bridge.schema()),objects=bridge.objects(),suggestions=root.CompilerEditorAssist.keywords.map(label=>({label,kind:monaco.languages.CompletionItemKind.Keyword,insertText:label,range}));for(const row of objects){const name=field(row,'OBJECT_NAME');if(name)suggestions.push({label:name,detail:field(row,'OBJECT_TYPE')||'Database object',kind:monaco.languages.CompletionItemKind.Class,insertText:name,range});}
    const qualifier=before.match(/([\w$#]+)\.\w*$/)?.[1]?.toUpperCase();for(const ref of refs){if(qualifier&&![ref.alias,ref.name,ref.owner].includes(qualifier))continue;const data=await schema(ref.owner,ref.name);if(cancellation.isCancellationRequested)return{suggestions:[]};for(const col of data.columns){const name=field(col,'COLUMN_NAME');suggestions.push({label:name,detail:`${ref.alias||ref.name} · ${field(col,'DATA_TYPE')} · ${field(col,'NULLABLE')==='N'?'NOT NULL':'nullable'}`,kind:monaco.languages.CompletionItemKind.Field,insertText:qualifier?name:refs.length>1?`${ref.alias||ref.name}.${name}`:name,range});}for(const fk of data.constraints.filter(row=>field(row,'CONSTRAINT_TYPE')==='R')){const target=field(fk,'REFERENCED_TABLE_NAME'),column=field(fk,'COLUMN_NAME'),referenced=field(fk,'REFERENCED_COLUMN_NAME'),owner=field(fk,'REFERENCED_OWNER'),qualified=owner&&owner!==bridge.schema()?`${owner}.${target}`:target;if(target&&column)suggestions.push({label:`JOIN ${target} (${column})`,kind:monaco.languages.CompletionItemKind.Snippet,detail:'Foreign key relation',insertText:`JOIN ${qualified} ON ${ref.alias||ref.name}.${column} = ${target}.${referenced||'\${1:referenced_column}'}`,insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,range});}}
    for(const [label,text] of snippets)suggestions.push({label:'snippet '+label,kind:monaco.languages.CompletionItemKind.Snippet,insertText:text,insertTextRules:monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,range});return{suggestions};}});
   monaco.languages.registerHoverProvider('sql',{async provideHover(model,position){const word=model.getWordAtPosition(position);if(!word)return null;const name=word.word.toUpperCase(),refs=references(model.getValue(),bridge.schema()),contents=[];for(const ref of refs){const data=await schema(ref.owner,ref.name);if([ref.name,ref.alias].includes(name))contents.push({value:`**${safe(ref.owner)}.${safe(ref.name)}**\n\n${data.columns.length} columns`});for(const column of data.columns.filter(col=>field(col,'COLUMN_NAME')===name)){const relations=data.constraints.filter(row=>field(row,'COLUMN_NAME')===name).map(row=>`${field(row,'CONSTRAINT_TYPE')}: ${field(row,'REFERENCED_TABLE_NAME')||field(row,'CONSTRAINT_NAME')}`).join(', ');contents.push({value:`**${safe(ref.name)}.${safe(name)}**\n\nType: ${safe(field(column,'DATA_TYPE'))} · Length: ${safe(field(column,'DATA_LENGTH'))}\n\n${field(column,'NULLABLE')==='N'?'NOT NULL':'Nullable'}${relations?'\n\n'+safe(relations):''}`});}}return contents.length?{contents}:null;}});
   function makeDialog(title){const dialog=document.createElement('dialog');dialog.className='pro-dialog';const header=document.createElement('div');header.className='pro-tools';const heading=document.createElement('h2');heading.textContent=title;header.append(heading,button('Close',()=>dialog.close()));dialog.append(header);document.body.append(dialog);dialog.addEventListener('close',()=>{dialog._dispose?.();dialog.remove();});dialog.showModal();return dialog;}
   let referenceEditor=null;const tools=document.createElement('div');tools.className='pro-tools';
   tools.append(button('Split editor',()=>{const container=workbook.getDomNode().parentElement;if(referenceEditor){referenceEditor.getModel().dispose();referenceEditor.dispose();$('#proReferenceEditor').remove();container.classList.remove('pro-split');referenceEditor=null;}else{const host=document.createElement('div');host.id='proReferenceEditor';host.className='pro-reference';container.append(host);container.classList.add('pro-split');referenceEditor=monaco.editor.create(host,{value:workbook.getValue(),language:'sql',theme:'portal-sql',automaticLayout:true,fontFamily:'Hack, monospace',minimap:{enabled:false},readOnly:false});}}),button('Compare SQL / output',()=>{const dialog=makeDialog('Compare SQL or output');const choices=document.createElement('div');choices.className='pro-tools';const host=document.createElement('div');host.className='pro-diff';dialog.append(choices,host);const original=monaco.editor.createModel(workbook.getValue(),'sql'),modified=monaco.editor.createModel(referenceEditor?.getValue()||'','sql');const diff=monaco.editor.createDiffEditor(host,{automaticLayout:true,originalEditable:true,readOnly:false,renderSideBySide:true,fontFamily:'Hack, monospace'});diff.setModel({original,modified});choices.append(button('Load fetched output on left',()=>{original.setValue(bridge.output());original.setLanguage?.('plaintext');}),button('Load workbook on left',()=>original.setValue(workbook.getValue())),button('Load reference on right',()=>modified.setValue(referenceEditor?.getValue()||'')));dialog._dispose=()=>{diff.dispose();original.dispose();modified.dispose();};}),button('Snippets',()=>{const dialog=makeDialog('SQL snippets');const search=document.createElement('input');search.type='search';search.placeholder='Search snippets';search.setAttribute('aria-label','Search snippets');const list=document.createElement('div'),name=document.createElement('input'),body=document.createElement('textarea'),status=document.createElement('p');name.placeholder='Snippet name';name.setAttribute('aria-label','Snippet name');body.placeholder='Reusable SQL';body.setAttribute('aria-label','Snippet SQL');body.rows=6;
    const key='sql-snippets:'+document.body.dataset.userId;let custom=[];try{custom=JSON.parse(localStorage.getItem(key)||'[]');if(!Array.isArray(custom))custom=[];}catch{}
    function render(){list.replaceChildren();[...snippets,...custom].filter(([label,sql])=>(label+' '+sql).toLowerCase().includes(search.value.toLowerCase())).forEach(([label,sql],index)=>{const row=document.createElement('div');row.className='pro-tools';row.append(button(label,()=>{workbook.focus();workbook.getContribution('snippetController2').insert(sql);dialog.close();}));if(custom.some(item=>item[0]===label))row.append(button('Delete',()=>{custom=custom.filter(item=>item[0]!==label);localStorage.setItem(key,JSON.stringify(custom));render();}));list.append(row);});}
    search.addEventListener('input',render);dialog.append(search,list,name,body,button('Save personal snippet',()=>{if(!name.value.trim()||!body.value.trim()){status.textContent='Enter a name and SQL.';return;}custom.push([name.value.trim(),body.value]);try{localStorage.setItem(key,JSON.stringify(custom));status.textContent='Saved';render();}catch{status.textContent='Browser storage unavailable';}}),status);render();}),button('Editor help',()=>{const dialog=makeDialog('Advanced editor shortcuts');const text=document.createElement('p');text.textContent='Alt + Click: multiple cursors · Shift + Alt + drag: column selection · Ctrl + D: next occurrence · Shift + Alt + F: format · Ctrl + Shift + [: fold · Ctrl + Shift + ]: unfold · F1: command palette · Ctrl + Enter: run statement · Ctrl + Shift + Enter: run script. In SQLcl, Enter runs a complete command; Shift + Enter adds a line.';dialog.append(text);}));
   bridge.editor.closest('.editor-card')?.querySelector('.editor-card-head')?.append(tools);
   if(!tools.isConnected)workbook.getDomNode().parentElement.before(tools);
   message.textContent='Advanced SQL editors ready';
   root.WorkbenchPro.editors={workbook,terminal};
  },error=>{message.textContent='Advanced editor could not load. Standard editors remain available.';console.error(error);});
 }
 root.WorkbenchPro={boot,grid,tableRows,references,formatOracle};
 if(root.PortalSqlBridge)boot(root.PortalSqlBridge);else document.addEventListener('portal-sql-ready',()=>boot(root.PortalSqlBridge),{once:true});
})(window);
