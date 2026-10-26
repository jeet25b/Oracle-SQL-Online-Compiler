/* One lexical source (CompilerEditorAssist.scan) for editor operations. */
(function(root){
  const lexical=typeof module!=='undefined'&&module.exports?require('./editor-assist.js'):root.CompilerEditorAssist;
  const {scan,keywords}=lexical, keywordSet=new Set(keywords);
  function ranges(source){
    const tokens=scan(source).tokens, result=[];
    let start=0, words=[], plsql=false;
    function emit(end){
      const chunk=source.slice(start,end);
      if(scan(chunk).tokens.some(t=>t.kind!=='comment'))result.push({start,end,sql:chunk.trim()});
      start=end;words=[];plsql=false;
    }
    for(const token of tokens){
      if(token.kind==='comment')continue;
      if(token.kind==='word'&&words.length<8)words.push(token.text.toUpperCase());
      plsql=/^(?:BEGIN|DECLARE)\b|^CREATE (?:OR REPLACE )?(?:(?:NON)?EDITIONABLE )?(?:PROCEDURE|FUNCTION|PACKAGE|TRIGGER|TYPE)\b/.test(words.join(' '));
      if(token.text==='/'&&token.kind==='symbol'){
        const lineStart=source.lastIndexOf('\n',token.start-1)+1;
        let lineEnd=source.indexOf('\n',token.end);if(lineEnd<0)lineEnd=source.length;
        if(source.slice(lineStart,lineEnd).trim()==='/'){
          if(plsql)emit(token.start);
          start=token.end;words=[];plsql=false;continue;
        }
      }
      if(token.text===';'&&token.kind==='symbol'&&!plsql)emit(token.end);
    }
    if(start<source.length)emit(source.length);
    return result;
  }
  function target(source,start,end=start,script=false){
    if(script)return{start:0,end:source.length,sql:source};
    if(start!==end&&source.slice(start,end).trim())return{start,end,sql:source.slice(start,end)};
    const all=ranges(source);
    return all.find(r=>start>=r.start&&start<r.end)||all.find(r=>r.start>=start)||all.at(-1)||{start:0,end:0,sql:''};
  }
  function binds(source){
    const tokens=scan(source).tokens, found=new Set();
    for(let i=0;i<tokens.length-1;i++){
      const t=tokens[i],next=tokens[i+1];
      if(t.kind==='symbol'&&t.text===':'&&next.start===t.end&&(next.kind==='word'||next.kind==='number'&&/^\d+$/.test(next.text))&&tokens[i-1]?.text!==':')found.add(next.text.toUpperCase());
    }
    return [...found];
  }
  function convert(source,mode,start=0,end=source.length){
    let position=0,out='';
    for(const t of scan(source).tokens){
      out+=source.slice(position,t.start);
      const change=t.kind==='word'&&t.start>=start&&t.end<=end;
      out+=!change?t.text:mode==='lower'?t.text.toLowerCase():mode==='initial'?t.text[0].toUpperCase()+t.text.slice(1).toLowerCase():t.text.toUpperCase();
      position=t.end;
    }
    return out+source.slice(position);
  }
  function format(source){
    const tokens=scan(source).tokens;let out='',last=null;
    const breaks=new Set(['SELECT','FROM','WHERE','HAVING','VALUES','SET','JOIN','UNION','BEGIN','END','EXCEPTION']);
    for(const t of tokens){
      const gap=last?source.slice(last.end,t.start):source.slice(0,t.start);
      const word=t.kind==='word'?t.text.toUpperCase():t.text;
      if(last){
        if(last.kind==='comment'&&last.text.startsWith('--'))out+='\n';
        else if(last.text===';'||last.text===',')out+='\n';
        else if(t.kind==='word'&&breaks.has(word)&&last.text!=='.')out+='\n';
        else if(gap)out+=gap.includes('\n')?'\n':' ';
      }
      out+=t.kind==='word'&&keywordSet.has(word)?word:t.text;
      last=t;
    }
    return out;
  }
  function commentOnly(source){
    const tokens=scan(source).tokens;
    return tokens.length>0&&tokens.every(token=>token.kind==='comment'&&(!token.text.startsWith('/*')||token.text.endsWith('*/')));
  }
  function leadingComments(source){
    const comments=[];let end=0;
    for(const token of scan(source).tokens){
      if(token.kind!=='comment'||token.text.startsWith('/*')&&!token.text.endsWith('*/'))break;
      if(comments.length){
        const blankLines=(source.slice(end,token.start).match(/\n/g)||[]).length-1;
        for(let i=0;i<blankLines;i++)comments.push('');
      }
      comments.push(token.text);end=token.end;
    }
    if(!comments.length)return{comments,sql:source};
    const tail=source.slice(end),gap=tail.match(/^\s*/)[0];
    const blankLines=(gap.match(/\n/g)||[]).length-1;
    for(let i=0;i<blankLines;i++)comments.push('');
    return{comments,sql:tail.slice(gap.length)};
  }
  function clean(source){
    return source.replace(/[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]/g,' ')
      .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\u00ad\u034f\u061c\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]/g,'')
      .replace(/[\u2028\u2029]/g,'\n').replace(/\r\n?/g,'\n');
  }
  function cleanSqlPlusTranscript(source){
    source=source.replace(/\r\n?/g,'\n');
    const prompted=/^[\t ]*SQL>/im.test(source);
    const protectedTokens=scan(source).tokens.filter(token=>token.kind==='literal'||token.kind==='comment');
    const feedback=/^(?:(?:Table|View|Index|Sequence|Synonym|Trigger|Procedure|Function|Package(?: body)?|Type(?: body)?|Materialized view) (?:created|dropped|altered|truncated|renamed|compiled)\.|\d+ rows? (?:created|inserted|updated|deleted|merged|selected)\.|no rows selected\.?|Commit complete\.|Rollback complete\.|PL\/SQL procedure successfully completed\.|Statement executed successfully\.|(?:ORA|SP2|DPY|PLS)-\d+:.*|Help:\s*https:\/\/docs\.oracle\.com\/error-help\/.*|(?:old|new)\s+\d+:.*|Enter value for [\w$#]+:.*)$/i;
    let offset=0,inOutput=false;
    return source.split('\n').map(line=>{
      const start=offset;offset+=line.length+1;
      if(protectedTokens.some(token=>token.start<start&&token.end>start))return line;
      const prompt=/^[\t ]*SQL>/i.test(line);
      const text=line.replace(/^[\t ]*SQL>[\t ]?/i,'').trim();
      if(feedback.test(text))return '';
      if(prompt)inOutput=false;
      if(prompted&&inOutput&&!prompt&&!/^(?:\d+\s+)?\/$/.test(text))return '';
      if(prompted&&/;\s*$/.test(text))inOutput=true;
      return line;
    }).join('\n');
  }
  function stripSqlPlusPrompts(source){
    const prompted=/^[\t ]*SQL>/im.test(source);
    const unprompted=source.replace(/^[\t ]*SQL>[\t ]?/gim,'');
    const lines=unprompted.split(/\r?\n/);
    const hasNumberedSequence=lines.some((line,index)=>/^\s*2\s+\S/.test(line)&&/^\s*3\s+\S/.test(lines[index+1]||''));
    if(!prompted&&!hasNumberedSequence)return source;
    const protectedTokens=scan(unprompted).tokens.filter(token=>token.kind==='literal'||token.kind==='comment');
    let offset=0,expected=2;
    return lines.map(line=>{
      const start=offset;offset+=line.length+1;
      if(protectedTokens.some(token=>token.start<start&&token.end>start))return line;
      const match=line.match(/^[\t ]*(\d+)[\t ]+(.*)$/);
      if(match&&Number(match[1])===expected){expected++;return match[2];}
      if(line.trim())expected=2;
      return line;
    }).join('\n');
  }
  function pastedBatch(source){
    // SQL*Plus rejects an unknown command at the end of its physical line;
    // it must not absorb the next SQL statement into the same request.
    const leading=leadingComments(source), first=leading.sql.trimStart();
    const commandStart=/^(?:SELECT|WITH|INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|RENAME|GRANT|REVOKE|COMMENT|ANALYZE|EXPLAIN|CALL|BEGIN|DECLARE|COMMIT|ROLLBACK|SAVEPOINT|SET|DESC|DESCRIBE|SHOW|DEFINE|UNDEFINE|ACCEPT|PROMPT|REM|REMARK|HELP|HISTORY|TIMING|CLEAR|LIST|L|APPEND|CHANGE|C|INPUT|DEL|RUN|R|EXIT|QUIT|DISCONNECT|CONNECT|CONN|START|GET|SAVE|SPOOL|EXEC|EXECUTE|VARIABLE|PRINT|COLUMN|BREAK|COMPUTE|TTITLE|BTITLE|WHENEVER|PAUSE|STORE)\b|^[@/?]/i;
    if(first && !commandStart.test(first)){
      const newline=first.indexOf('\n');
      const line=newline<0?first:first.slice(0,newline);
      const prefix=source.slice(0,source.length-first.length);
      const tail=newline<0?{commands:[],remaining:''}:pastedBatch(first.slice(newline+1));
      return{commands:[(prefix+line).trim(),...tail.commands],remaining:tail.remaining};
    }
    const commands=[];let cursor=0;
    for(const part of ranges(source)){
      const suffix=source.slice(part.end);
      const sqlBoundary=source[part.end-1]===';'&&/^[\t ]*\r?\n/.test(suffix);
      const slash=suffix.match(/^[\t ]*\/[^\S\r\n]*(?:\r?\n|$)/);
      if(!sqlBoundary&&!slash)continue;
      const end=part.end+(slash?slash[0].length:suffix.match(/^[\t ]*\r?\n/)[0].length);
      const chunk=source.slice(cursor,part.end).trim();
      const chunkSql=leadingComments(chunk).sql.trimStart();
      if(chunkSql && !commandStart.test(chunkSql)){
        const split=pastedBatch(chunk);commands.push(...split.commands);
        if(split.remaining.trim())commands.push(split.remaining.trim());
      }else commands.push(chunk);
      cursor=end;
    }
    return{commands,remaining:source.slice(cursor)};
  }
  function sqlplusOutput(sql,data){
    if(data.error)return data.error;
    const lines=[...(data.outputLines||[])];
    if(data.columns?.length){
      const headings=data.columns.map(column=>String(column.name||column));
      const rows=(data.rows||[]).map(row=>row.map(value=>value==null?'':String(value)));
      const widths=headings.map((heading,index)=>Math.max(heading.length,...rows.map(row=>(row[index]||'').length)));
      const line=row=>row.map((value,index)=>value.padEnd(widths[index])).join(' ');
      if(data.sqlPlusHeading!==false)lines.push(line(headings),widths.map(width=>'-'.repeat(width)).join(' '));
      rows.forEach(row=>lines.push(line(row)));
      const command=scan(sql).tokens.find(token=>token.kind!=='comment')?.text.toUpperCase();
      if(data.sqlPlusFeedback!==false&&!['DESC','DESCRIBE'].includes(command))lines.push('',`${rows.length} row${rows.length===1?'':'s'} selected.`);
    }else{
      const words=scan(sql).tokens.filter(token=>token.kind==='word').map(token=>token.text.toUpperCase());
      const verb=words[0],object=words[1]==='OR'?words[3]:words[1];
      const count=Number(data.rowsAffected)||0;
      let feedback='';
      if(['CREATE','ALTER','DROP','TRUNCATE'].includes(verb)){
        const noun={TABLE:'Table',VIEW:'View',INDEX:'Index',SEQUENCE:'Sequence',PROCEDURE:'Procedure',FUNCTION:'Function',TRIGGER:'Trigger',PACKAGE:'Package',TYPE:'Type',SYNONYM:'Synonym'}[object];
        if(noun)feedback=noun+' '+({CREATE:'created.',ALTER:'altered.',DROP:'dropped.',TRUNCATE:'truncated.'})[verb];
      }else if(['INSERT','UPDATE','DELETE','MERGE'].includes(verb))feedback=`${count} row${count===1?'':'s'} `+({INSERT:'created.',UPDATE:'updated.',DELETE:'deleted.',MERGE:'merged.'})[verb];
      else if(verb==='COMMIT')feedback='Commit complete.';
      else if(verb==='ROLLBACK')feedback='Rollback complete.';
      else if(['BEGIN','DECLARE'].includes(verb))feedback='PL/SQL procedure successfully completed.';
      lines.push(feedback||data.message||'Statement executed successfully.');
    }
    return lines.join('\n');
  }
  function outputText(sql,data){
    if(data.statementResults?.length)return data.statementResults.map(item=>sqlplusOutput(item.statement,{...data,...item,statementResults:undefined,outputLines:item.outputLines||[],error:item.error})).join('\n\n');
    return sqlplusOutput(sql,data);
  }
  function draftKey(user,workspace,id){return user?`compiler-draft-v1:${encodeURIComponent(user)}:${encodeURIComponent(workspace)}:${id}`:null;}
  const api={scan,ranges,target,binds,convert,format,draftKey,clean,cleanSqlPlusTranscript,stripSqlPlusPrompts,pastedBatch,outputText,commentOnly,leadingComments};
  if(typeof module!=='undefined'&&module.exports){module.exports=api;return;}
  root.CompilerWorkspace=api;
  api.history=function(input){
    const snapshot=()=>({text:input.value,start:input.selectionStart,end:input.selectionEnd,top:input.scrollTop,left:input.scrollLeft});
    let current=snapshot(),undo=[],redo=[],restoring=false;
    function observe(){
      const next=snapshot();
      if(!restoring&&next.text!==current.text){undo.push(current);if(undo.length>100)undo.shift();redo=[];}
      current=next;
    }
    function restore(from,to,inputType){
      if(!from.length)return;
      to.push(snapshot());const next=from.pop();restoring=true;
      input.value=next.text;input.setSelectionRange(next.start,next.end);input.scrollTop=next.top;input.scrollLeft=next.left;
      current=next;input.dispatchEvent(new InputEvent('input',{bubbles:true,inputType}));restoring=false;
    }
    input.addEventListener('beforeinput',event=>{
      if(event.inputType==='historyUndo'||event.inputType==='historyRedo'){
        event.preventDefault();event.stopImmediatePropagation();
        if(event.inputType==='historyUndo')restore(undo,redo,'historyUndo');else restore(redo,undo,'historyRedo');
      }else if(current.text===input.value)current=snapshot();
    },true);
    input.addEventListener('input',observe,true);
    for(const event of ['select','click','keyup'])input.addEventListener(event,()=>{if(current.text===input.value)current=snapshot();});
    input.addEventListener('keydown',event=>{
      if(!(event.ctrlKey||event.metaKey)||event.altKey)return;
      const key=event.key.toLowerCase();
      if(key!=='z'&&key!=='y')return;
      event.preventDefault();event.stopImmediatePropagation();
      if(key==='y'||event.shiftKey)restore(redo,undo,'historyRedo');else restore(undo,redo,'historyUndo');
    },true);
    return{observe};
  };
  api.edit=function(input,text,start=0,end=input.value.length,type='insertReplacementText'){
    input.setRangeText(text,start,end,'end');
    input.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:type}));input.focus({preventScroll:true});
  };
  api.draft=function(input,{user,workspace,statusHost,runRecovered}){
    const key=draftKey(user,workspace,input.id);if(!key)return;
    const notice=document.createElement('span');notice.className='compiler-live-check';
    let timer,failed=false;
    function save(){
      try{
        const text=typeof input.compilerPendingDraft==='string'?input.compilerPendingDraft:input.value;
        if(text.length>1000000)throw new Error('Draft exceeds recovery limit');
        if(text)localStorage.setItem(key,JSON.stringify({text,at:Date.now()}));else localStorage.removeItem(key);
      }catch{if(!failed){notice.textContent='Draft recovery unavailable (storage full or blocked).';failed=true;}}
    }
    try{
      const saved=JSON.parse(localStorage.getItem(key)||'null');
      if(saved&&typeof saved.text==='string'&&!input.value){
        input.value=saved.text;notice.textContent='Unexecuted text recovered. ';
        if(runRecovered){
          const run=document.createElement('button');run.type='button';run.className='tool-button';run.textContent='Run statements';
          run.addEventListener('click',()=>{runRecovered();notice.textContent='';});notice.append(run);
        }
        const discard=document.createElement('button');discard.type='button';discard.className='tool-button';discard.textContent='Discard';
        discard.addEventListener('click',()=>{api.edit(input,'');save();notice.textContent='';});notice.append(discard);
      }
    }catch{/* Never let an unreadable draft block the editor. */}
    statusHost.append(notice);
    input.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(save,300);});
    root.addEventListener('pagehide',save);
    document.addEventListener('visibilitychange',()=>{if(document.hidden)save();});
    return{save};
  };
})(typeof window==='undefined'?globalThis:window);
