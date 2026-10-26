/* Shared, side-effect-free SQL assistance core plus textarea adapter. */
(function (root) {
  const keywords = 'SELECT FROM WHERE INSERT INTO VALUES UPDATE SET DELETE CREATE TABLE ALTER DROP BEGIN END DECLARE NULL COMMIT ROLLBACK JOIN LEFT RIGHT INNER OUTER ON GROUP BY ORDER HAVING DISTINCT AS AND OR NOT IN IS LIKE BETWEEN EXISTS UNION ALL PRIMARY KEY FOREIGN REFERENCES CONSTRAINT DEFAULT UNIQUE CHECK NUMBER VARCHAR2 CHAR DATE TIMESTAMP PROCEDURE FUNCTION PACKAGE TRIGGER RETURN EXCEPTION LOOP THEN ELSE ELSIF IF CASE WHEN WITH MERGE USING'.split(' ');
  function scan(text) {
    const tokens = [];
    let i = 0, open = '';
    while (i < text.length) {
      const start = i, ch = text[i];
      if (/\s/.test(ch)) { i++; continue; }
      if (text.slice(i, i + 2) === '--') {
        const end = text.indexOf('\n', i); i = end < 0 ? text.length : end;
        tokens.push({kind:'comment', start, end:i, text:text.slice(start,i)});
        if (end < 0) open = 'comment';
        continue;
      }
      if (text.slice(i, i + 2) === '/*') {
        const end = text.indexOf('*/', i + 2); i = end < 0 ? text.length : end + 2;
        tokens.push({kind:'comment',start,end:i,text:text.slice(start,i)});
        if (end < 0) open = 'comment';
        continue;
      }
      if ((ch === 'q' || ch === 'Q') && text[i+1] === "'" && text[i+2]) {
        const delimiter = text[i+2], closing = ({'[':']','(':')','{':'}','<':'>'})[delimiter] || delimiter;
        const end = text.indexOf(closing + "'", i+3); i = end < 0 ? text.length : end+2;
        tokens.push({kind:'literal',start,end:i,text:text.slice(start,i)});
        if (end < 0) open = 'literal';
        continue;
      }
      if (ch === "'" || ch === '"') {
        i++; let closed = false;
        while (i < text.length) {
          if (text[i] === ch) { if (text[i+1] === ch) { i+=2; continue; } i++; closed=true; break; }
          i++;
        }
        const kind = ch === "'" ? 'literal' : 'identifier';
        tokens.push({kind,start,end:i,text:text.slice(start,i)});
        if (!closed) open=kind;
        continue;
      }
      const word = text.slice(i).match(/^[A-Za-z_][A-Za-z0-9_$#]*/);
      if (word) { i+=word[0].length; tokens.push({kind:'word',start,end:i,text:word[0]}); continue; }
      const number = text.slice(i).match(/^\d+(?:\.\d*)?/);
      if (number) { i+=number[0].length; tokens.push({kind:'number',start,end:i,text:number[0]}); continue; }
      i++; tokens.push({kind:'symbol',start,end:i,text:ch});
    }
    return {tokens,open};
  }
  function completion(text, caret) {
    const before = text.slice(0,caret), parsed = scan(before);
    if (parsed.open) return null;
    const prefix = before.match(/[A-Za-z_][A-Za-z0-9_$#]*$/);
    if (!prefix || prefix[0].length < 3) return null;
    const start = caret-prefix[0].length;
    const significant = parsed.tokens.filter(t=>t.kind!=='comment' && t.start<start);
    const lastSemi = significant.map(t=>t.text).lastIndexOf(';');
    const words = significant.slice(lastSemi+1).map(t=>t.text.toUpperCase());
    const context = words.join(' '), last=words.at(-1);
    let valueDepth = 0, depth = 0;
    for (let i=0;i<words.length;i++) {
      if(words[i]==='('){depth++;if(words[i-1]==='VALUES')valueDepth=depth;}
      if(words[i]===')'){if(valueDepth===depth)valueDepth=0;depth=Math.max(0,depth-1);}
    }
    if (valueDepth || /(?:=|<|>|!)\s*$/.test(context) || last==='DEFAULT') return null;
    if (/\b(?:NUMBER|VARCHAR2?|CHAR|TIMESTAMP)\s*\([^)]*$/.test(context)) return null;
    const kind = /\b(?:FROM|JOIN|UPDATE|INTO|TABLE|REFERENCES)\s*$/.test(context) ? 'table' :
      /\b(?:SELECT|WHERE|ON|SET|ORDER BY|GROUP BY|HAVING)\b/.test(context) ? 'column' : 'keyword';
    const suffix=text.slice(caret).match(/^[A-Za-z0-9_$#]*/)?.[0] || '';
    return {prefix:prefix[0], start, end:caret+suffix.length, kind};
  }
  function indentation(text, start, end=start) {
    const before=text.slice(0,start), current=before.slice(before.lastIndexOf('\n')+1);
    const base=current.match(/^[\t ]*/)[0];
    const state=scan(before);
    if(state.open)return {insert:'\n'+base,caret:start+1+base.length};
    const lineStart=before.lastIndexOf('\n')+1;
    const code=state.tokens.filter(t=>t.start>=lineStart).filter(t=>t.kind!=='comment' && t.kind!=='literal').map(t=>t.text).join(' ');
    const extra=/(?:\bBEGIN|\bTHEN|\bELSE|\bLOOP|\bDECLARE|\()\s*$/i.test(code)?'  ':'';
    const closing=/^\s*(?:END\b|\))/i.test(text.slice(end));
    const insert='\n'+base+extra+(extra && closing?'\n'+base:'');
    return {insert, caret:start+1+base.length+extra.length};
  }
  function diagnostics(text) {
    const {tokens,open}=scan(text), stack=[];
    const masked=text.split('');
    for(const token of tokens)if(['literal','identifier','comment'].includes(token.kind))for(let i=token.start;i<token.end;i++)masked[i]=' ';
    const badSpace=masked.join('').search(/[\u00a0\u200b\u202f\ufeff]/);
    if(badSpace>=0) return [{offset:badSpace,message:'Unusual invisible character; replace it with a normal space.'}];
    for(const t of tokens) {
      if(t.kind!=='symbol') continue;
      if(t.text==='(') stack.push(t);
      if(t.text===')') { if(!stack.length) return [{offset:t.start,message:'Unexpected closing parenthesis.'}]; stack.pop(); }
    }
    if(text.trimEnd().endsWith(';') && !open && stack.length) return [{offset:stack.at(-1).start,message:'Opening parenthesis has no matching closing parenthesis.'}];
    const first=tokens.find(t=>t.kind==='word');
    const typo={SELEC:'SELECT',SELET:'SELECT',UPDTE:'UPDATE',INSRT:'INSERT',CREAT:'CREATE'}[first?.text.toUpperCase()];
    if(typo) return [{offset:first.start,message:`Possible keyword typo: ${typo}.`}];
    return [];
  }
  const api={scan,completion,indentation,diagnostics,keywords};
  if(typeof module!=='undefined' && module.exports) { module.exports=api; return; }
  root.CompilerEditorAssist=api;
  api.attach=function(input,options={}) {
    const popup=document.createElement('div');
    popup.className='compiler-completions'; popup.hidden=true;
    popup.id=input.id+'-completions'; popup.setAttribute('role','listbox');
    popup.setAttribute('aria-label','SQL suggestions');
    popup.setAttribute('popover','manual');
    (input.closest('#ideShell') || document.body).append(popup);
    const hint=document.createElement('span'); hint.className='compiler-live-check';
    hint.setAttribute('role','status');
    (options.statusHost || input.parentElement).append(hint);
    input.setAttribute('aria-controls',popup.id); input.setAttribute('aria-autocomplete','list');
    let items=[],active=0,context=null,timer,suggestTimer,version=0,positionFrame;
    function close() {
      if(popup.matches(':popover-open')) popup.hidePopover();
      popup.hidden=true; input.setAttribute('aria-expanded','false');
      input.removeAttribute('aria-activedescendant'); cancelAnimationFrame(positionFrame);
    }
    function position() {
      if(popup.hidden)return;
      const r=root.WorkbenchCaretRect?.(input) || input.getBoundingClientRect();
      const width=Math.min(320,innerWidth-16);
      popup.style.width=width+'px'; popup.style.maxHeight=Math.min(220,innerHeight-16)+'px';
      const height=popup.getBoundingClientRect().height;
      popup.style.left=Math.max(8,Math.min(r.left,innerWidth-width-8))+'px';
      popup.style.top=Math.max(8,Math.min(r.bottom+5+height>innerHeight-8?r.top-height-5:r.bottom+5,innerHeight-height-8))+'px';
      positionFrame=requestAnimationFrame(position);
    }
    function select(index) {
      active=index;
      [...popup.children].forEach((node,i)=>node.setAttribute('aria-selected',String(i===active)));
      input.setAttribute('aria-activedescendant',`${popup.id}-${active}`);
    }
    function accept() {
      if(!context || !items[active])return;
      input.setRangeText(items[active].value,context.start,context.end,'end');
      close(); input.focus();
      input.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertReplacementText'}));
    }
    async function suggest(revision) {
      if(document.activeElement!==input || input.selectionStart!==input.selectionEnd) {close();return;}
      context=completion(input.value,input.selectionStart);
      if(!context) {close();return;}
      const found=[];
      if(context.kind!=='table') keywords.forEach(value=>found.push({value,kind:'Keyword'}));
      const objects=options.getObjects?.() || [];
      objects.forEach(row=>{
        const value=row.OBJECT_NAME || row.object_name || row.name;
        const kind=row.OBJECT_TYPE || row.object_type || row.type;
        if(value && (context.kind!=='table' || /TABLE|VIEW|SYNONYM/i.test(kind || ''))) found.push({value,kind:kind || 'Identifier'});
      });
      if(context.kind==='column' && options.getColumns) {
        const columns=await options.getColumns(input.value);
        if(revision!==version || document.activeElement!==input)return;
        columns.forEach(value=>found.push({value,kind:'Column'}));
      }
      const seen=new Set();
      const matches=found.filter(item=>item.value.toUpperCase().startsWith(context.prefix.toUpperCase()) && !seen.has(item.value.toUpperCase()) && seen.add(item.value.toUpperCase()));
      const completeWord=input.value.slice(context.start,context.end).toUpperCase();
      if(matches.some(item=>item.value.toUpperCase()===completeWord)) { close(); return; }
      items=matches.slice(0,1);
      if(!items.length){close();return;}
      popup.replaceChildren();
      items.forEach((item,index)=>{
        const row=document.createElement('div');row.id=`${popup.id}-${index}`;
        row.setAttribute('role','option');row.textContent=`${item.value}  ·  ${item.kind}`;
        row.addEventListener('pointerdown',event=>{event.preventDefault();select(index);accept();});
        popup.append(row);
      });
      popup.hidden=false;popup.showPopover();input.setAttribute('aria-expanded','true');
      select(0);cancelAnimationFrame(positionFrame);position();
    }
    function changed() {
      clearTimeout(suggestTimer);
      const revision=++version;
      suggestTimer=setTimeout(()=>suggest(revision).catch(close),180);
      clearTimeout(timer);
      timer=setTimeout(()=>{
        const errors=diagnostics(input.value);
        hint.textContent=errors.length?'Syntax hint (line '+input.value.slice(0,errors[0].offset).split('\n').length+'): '+errors[0].message:'';
        hint.title=errors.length?'Local check only; Oracle validates when you run.':'';
      },2000);
    }
    input.addEventListener('input',changed);
    input.addEventListener('click',()=>{close();});
    input.addEventListener('blur',()=>{version++;close();});
    input.addEventListener('keydown',event=>{
      if(event.isComposing)return;
      if((event.key==="'" || event.key==='"') && !event.ctrlKey && !event.metaKey && !event.altKey) {
        const before=input.value.slice(0,input.selectionStart);
        const lexicalState=scan(before).open;
        if(lexicalState!=='literal' && lexicalState!=='identifier' && lexicalState!=='comment') {
          const quote=event.key;
          let start=input.selectionStart, end=input.selectionEnd;
          const selected=start!==end;
          if(!selected) {
            while(start>0 && /[A-Za-z0-9_$#]/.test(input.value[start-1])) start--;
            while(end<input.value.length && /[A-Za-z0-9_$#]/.test(input.value[end])) end++;
          }
          event.preventDefault();event.stopImmediatePropagation();
          if(start===end) {
            input.setRangeText(quote+quote,start,end,'end');
            input.setSelectionRange(start+1,start+1);
          } else {
            input.setRangeText(quote+input.value.slice(start,end)+quote,start,end,'end');
          }
          input.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:quote}));
          return;
        }
      }
      const terminalHistoryKey=options.terminal && ['ArrowDown','ArrowUp'].includes(event.key);
      if(!popup.hidden && !terminalHistoryKey && ['ArrowDown','ArrowUp','Enter','Tab','Escape'].includes(event.key) && !event.ctrlKey && !event.metaKey) {
        if(event.key==='Enter') { close(); if(options.terminal)return; }
        else {
          event.preventDefault();event.stopImmediatePropagation();
          if(event.key==='Escape')close();
          else if(event.key==='Tab')accept();
          else select((active+(event.key==='ArrowDown'?1:-1)+items.length)%items.length);
          return;
        }
      }
      if(event.key==='Enter' && !event.ctrlKey && !event.metaKey && !event.altKey && (!options.terminal || event.shiftKey || !options.isComplete(input.value))) {
        event.preventDefault();event.stopImmediatePropagation();
        const next=indentation(input.value,input.selectionStart,input.selectionEnd);
        input.setRangeText(next.insert,input.selectionStart,input.selectionEnd,'end');
        input.setSelectionRange(next.caret,next.caret);
        input.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertLineBreak'}));
      }
    },true);
  };
})(typeof window==='undefined'?globalThis:window);
