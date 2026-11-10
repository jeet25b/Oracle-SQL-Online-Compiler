const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const core=require('../online-oracle-compiler/workspace-core.js'),assist=require('../online-oracle-compiler/editor-assist.js');
function load(){let copied;const window={CompilerWorkspace:core,CompilerEditorAssist:assist,navigator:{clipboard:{write:async value=>copied=value}},ClipboardItem:class{constructor(parts){this.parts=parts;}}};
 vm.runInNewContext(fs.readFileSync('online-oracle-compiler/history-sql.js','utf8'),{window,Blob,document:{addEventListener(){}}});return{api:window.HistorySql,getCopied:()=>copied};}
test('history formats SQL while preserving literals, comments and quoted identifiers',()=>{
 const {api}=load();const text=api.format(`select "MixedName", 'Keep  Case', 42 from emp where name='A&B'; -- stay`);
 assert.ok(text.startsWith('SELECT'));assert.ok(text.includes('\nFROM'));assert.ok(text.includes("'Keep  Case'"));assert.ok(text.includes('"MixedName"'));assert.ok(text.includes('-- stay'));
 assert.ok(api.html(text).includes('history-syntax-keyword'));assert.ok(api.html(text).includes('history-syntax-string'));
 assert.ok(api.html("SELECT '<script>' FROM dual;").includes('&lt;script&gt;'));
});
test('copy contains matching formatted plain text and inline syntax colors',async()=>{
 const {api,getCopied}=load();const text=await api.copy("select 'IT', 42 from department;");const parts=getCopied()[0].parts;
 assert.equal(await parts['text/plain'].text(),text);const html=await parts['text/html'].text();
 assert.ok(html.includes('color:#0757c9'));assert.ok(html.includes('color:#16863d'));assert.ok(html.includes('color:#b35c00'));assert.ok(html.includes('white-space:pre-wrap'));assert.ok(text.includes('\nFROM'));
});
