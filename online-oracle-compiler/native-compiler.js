(()=>{
 const root=document.querySelector('.native-compiler');if(!root)return;
 const $=id=>document.getElementById(id),language=root.dataset.language;
 const source=$('nativeSource'),input=$('nativeInput'),run=$('nativeRun'),runner=$('nativeRunnerStatus'),status=$('nativeRunStatus');
 const key=`native-draft:${root.dataset.userId}:${language}`;let ready=false,busy=false,editor=null;
 const example=language==='C'?`#include <stdio.h>

int main(void) {
    printf("Hello, world!\\n");
    return 0;
}
`:`#include <iostream>

int main() {
    std::cout << "Hello, world!\\n";
    return 0;
}
`;
 source.value=example;
 try{const saved=JSON.parse(localStorage.getItem(key)||'null');if(saved&&typeof saved.source==='string'){source.value=saved.source;input.value=saved.stdin||'';}}catch{}
 function save(){try{localStorage.setItem(key,JSON.stringify({source:source.value,stdin:input.value}));}catch{}}
 source.addEventListener('input',save);input.addEventListener('input',save);
 async function json(response){const type=response.headers.get('content-type')||'';if(!type.includes('application/json'))throw Error('Your portal login may have expired. Sign in again.');const data=await response.json();if(!response.ok)throw Error(data.error||'Compiler request failed.');return data;}
 async function check(){ready=false;run.disabled=true;runner.textContent='Checking local compiler…';try{const data=await json(await fetch('/api/compiler/native/status',{credentials:'same-origin',cache:'no-store'}));ready=data.ready;runner.textContent=data.message;}catch(error){runner.textContent=error.message;}finally{run.disabled=!ready||busy;}}
 async function execute(){if(busy)return;if(!ready){await check();if(!ready)return;}if(!source.value.trim()){status.textContent='Enter source code first.';return;}busy=true;run.disabled=true;status.textContent='Compiling and running…';$('nativeStdout').textContent='';$('nativeStderr').textContent='';save();try{const csrf=root.querySelector('[name=csrfmiddlewaretoken]').value;const data=await json(await fetch('/api/compiler/native/run',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify({language,source:source.value,stdin:input.value})}));$('nativeStdout').textContent=data.stdout||'(no standard output)';$('nativeStderr').textContent=data.stderr||'(no diagnostics)';status.textContent=`${({success:'Completed',compile_error:'Compilation failed',runtime_error:'Runtime error',time_or_resource_limit:'Time or resource limit reached',output_limit:'Output limit reached'})[data.status]||data.status} · Exit ${data.exitCode} · ${data.durationMs} ms${data.outputTruncated?' · output truncated':''}`;}catch(error){status.textContent=error.message;}finally{busy=false;run.disabled=!ready;}}
 run.addEventListener('click',execute);$('nativeCheck').addEventListener('click',check);source.addEventListener('keydown',event=>{if(event.ctrlKey&&event.key==='Enter'){event.preventDefault();execute();}});
 $('nativeImport').addEventListener('click',()=>$('nativeFile').click());$('nativeFile').addEventListener('change',async event=>{const file=event.target.files[0];if(!file)return;if(file.size>65536){status.textContent='Source files must be 64 KB or smaller.';return;}source.value=await file.text();if(editor)editor.setValue(source.value);save();});
 $('nativeDownload').addEventListener('click',()=>{const url=URL.createObjectURL(new Blob([source.value],{type:'text/plain;charset=utf-8'})),a=document.createElement('a');a.href=url;a.download=language==='C'?'main.c':'main.cpp';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);});
 if(window.require?.config){require.config({paths:{vs:'/portal/vendor/monaco/vs'}});require(['vs/editor/editor.main'],()=>{const host=document.createElement('div');host.className='native-monaco';$('nativeEditor').append(host);editor=monaco.editor.create(host,{value:source.value,language:language==='C'?'c':'cpp',theme:document.documentElement.dataset.theme==='light'?'vs':'vs-dark',fontFamily:'Hack, Consolas, monospace',fontSize:16,automaticLayout:true,minimap:{enabled:true},stickyScroll:{enabled:true},folding:true,bracketPairColorization:{enabled:true},guides:{bracketPairs:true},matchBrackets:'always',scrollBeyondLastLine:false,tabSize:4});source.hidden=true;editor.onDidChangeModelContent(()=>{source.value=editor.getValue();save();});editor.addAction({id:'native-run',label:'Compile and run',keybindings:[monaco.KeyMod.CtrlCmd|monaco.KeyCode.Enter],run:execute});document.addEventListener('ooc-theme-change',()=>monaco.editor.setTheme(document.documentElement.dataset.theme==='light'?'vs':'vs-dark'));},()=>{});}
 check();
})();
