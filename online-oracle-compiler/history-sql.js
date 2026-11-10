/* Shared history presentation; stored/executed SQL remains unchanged. */
(function(root){
 const core=root.CompilerWorkspace;
 const escape=text=>String(text).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 function format(text){return core.format(String(text||''));}
 function html(text,rich=false){
  let output='',position=0;
  for(const token of root.CompilerEditorAssist.syntaxTokens(text)){
   output+=escape(text.slice(position,token.start));
   // editor-assist exposes lexical classifications as `kind` (not `syntax`).
   // Keep the shared renderer aligned with that API so history rows receive
   // the same keyword/string/number/comment classes as the live editor.
   const kind=token.syntax || token.kind;
   const color={keyword:'#0757c9',string:'#16863d',number:'#b35c00',comment:'#6f7785'}[kind];
   output+=kind?`<span ${rich?`style="color:${color}"`:`class="history-syntax-${kind}"`}>${escape(token.text)}</span>`:escape(token.text);
   position=token.end;
  }
  return output+escape(text.slice(position));
 }
 function clipboardHtml(text){return `<pre style="font:11pt Consolas,'Courier New',monospace;white-space:pre-wrap;color:#172033;background:#ffffff">${html(text.replace(/\r\n?/g,'\n'),true).replace(/\n/g,'<br>').replace(/\t/g,'&nbsp;&nbsp;&nbsp;&nbsp;').replace(/  /g,'&nbsp; ')}</pre>`;}
 function render(element,text){const formatted=format(text);element.classList.add('history-syntax');element.innerHTML=html(formatted);return formatted;}
 async function copy(text){
  const formatted=format(text),rich=clipboardHtml(formatted);
  if(root.navigator.clipboard?.write&&root.ClipboardItem){
   await root.navigator.clipboard.write([new root.ClipboardItem({'text/plain':new Blob([formatted],{type:'text/plain'}),'text/html':new Blob([rich],{type:'text/html'})})]);
  }else if(root.navigator.clipboard?.writeText){await root.navigator.clipboard.writeText(formatted);}
  else {
   const holder=document.createElement('div');holder.innerHTML=rich;holder.style.position='fixed';holder.style.left='-10000px';document.body.append(holder);
   const selection=root.getSelection(),range=document.createRange();range.selectNodeContents(holder);selection.removeAllRanges();selection.addRange(range);
   try{if(!document.execCommand('copy'))throw Error('Clipboard access is unavailable.');}finally{selection.removeAllRanges();holder.remove();}
  }
  return formatted;
 }
 document.addEventListener('copy',event=>{
  const selection=root.getSelection();
  if(!selection||selection.isCollapsed||!event.clipboardData)return;
  const anchor=selection.anchorNode?.parentElement?.closest('.history-syntax');
  const focus=selection.focusNode?.parentElement?.closest('.history-syntax');
  if(!anchor||anchor!==focus)return;
  const text=selection.toString();
  event.clipboardData.setData('text/plain',text);
  event.clipboardData.setData('text/html',clipboardHtml(text));event.preventDefault();
 });
 root.HistorySql={format,html,render,copy,clipboardHtml};
})(window);
