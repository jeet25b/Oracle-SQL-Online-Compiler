const http = require('node:http');
const https = require('node:https');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { pathToFileURL } = require('node:url');

const ROOT = __dirname;
const SQL_FILES = path.join(ROOT,'sql-files');
loadLocalConfig();
const sessions=new Map();
const connectAttempts=new Map();



const SESSION_COOKIE='ora_session';
const WORKSPACE_COOKIE='ora_workspace';
const SESSION_IDLE_MS=30*60*1000;
const TLS_KEY_FILE=process.env.TLS_KEY_FILE;
const TLS_CERT_FILE=process.env.TLS_CERT_FILE;
const TLS_ENABLED=Boolean(TLS_KEY_FILE&&TLS_CERT_FILE);
const HOST = process.env.HOST || (TLS_ENABLED?'0.0.0.0':'127.0.0.1');
const PORT = Number(process.env.PORT || 3000);
const MAX_BODY = 256 * 1024;
const MAX_ROWS = 500;
const MIME = { '.html':'text/html; charset=utf-8', '.css':'text/css; charset=utf-8', '.js':'text/javascript; charset=utf-8', '.json':'application/json; charset=utf-8' };
const STATIC_FILES=new Set(['index.html','app.js','styles.css','output.css','lms.html','lms.css']);

function safeSqlFileName(value){
  const name=path.basename(String(value||'').trim()).replace(/[<>:"/\\|?*\x00-\x1f]/g,'_');
  if(!name||name==='.'||name==='..')throw new Error('Enter a valid SQL file name.');
  return /\.sql$/i.test(name)?name:`${name}.sql`;
}

function splitScript(source){
  const statements=[];let current='';let single=false;let quoted=false;let lineComment=false;let blockComment=false;let plsql=false;
  const lines=source.split(/\r?\n/);
  for(const line of lines){
    if(plsql&&/^\s*\/\s*$/.test(line)){if(current.trim())statements.push(current.trim());current='';plsql=false;continue}
    if(!current.trim()){plsql=/^\s*(DECLARE|BEGIN)\b/i.test(line)||/^\s*CREATE(?:\s+OR\s+REPLACE)?\s+(?:EDITIONABLE\s+)?(?:PROCEDURE|FUNCTION|PACKAGE|TRIGGER|TYPE)\b/i.test(line)}
    lineComment=false;
    for(let i=0;i<line.length;i++){
      const ch=line[i],next=line[i+1];
      if(lineComment){current+=ch;continue}
      if(blockComment){current+=ch;if(ch==='*'&&next==='/'){current+=next;i++;blockComment=false}continue}
      if(single){current+=ch;if(ch==="'"&&next==="'"){current+=next;i++}else if(ch==="'")single=false;continue}
      if(quoted){current+=ch;if(ch==='"'&&next==='"'){current+=next;i++}else if(ch==='"')quoted=false;continue}
      if(ch==='-'&&next==='-'){current+=ch+next;i++;lineComment=true;continue}
      if(ch==='/'&&next==='*'){current+=ch+next;i++;blockComment=true;continue}
      if(ch==="'"){single=true;current+=ch;continue}
      if(ch==='"'){quoted=true;current+=ch;continue}
      if(ch===';'&&!plsql){if(current.trim())statements.push(current.trim());current='';continue}
      current+=ch;
    }
    current+='\n';
  }
  if(current.trim())statements.push(current.trim().replace(/;\s*$/,''));
  return statements.filter(Boolean);
}

function loadLocalConfig() {
  const file=path.join(ROOT,'.env');
  if(!fs.existsSync(file)) return;
  for(const line of fs.readFileSync(file,'utf8').split(/\r?\n/)){
    const match=line.match(/^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$/);
    if(!match || process.env[match[1]]) continue;
    const value=match[2].replace(/^(["'])(.*)\1$/,'$2');
    process.env[match[1]]=value;
  }
}
function json(res, status, body) {
  res.writeHead(status, { 'Content-Type':'application/json; charset=utf-8', 'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff' });
  res.end(JSON.stringify(body));
}

function isFreeSqlDefaultConnection(connectString,user){const value=String(connectString||'');return String(user||'').trim().toUpperCase()==='JIT25B_SCHEMA_YV521'||(/db\.freesql\.com/i.test(value)&&/2484/.test(value)&&/26ai_un3c1/i.test(value))}

function readCookie(req,name){const match=(req.headers.cookie||'').match(new RegExp(`(?:^|;\\s*)${name}=([A-Za-z0-9_-]+)`));return match?.[1]||''}
function readSession(req){const session=sessions.get(readCookie(req,SESSION_COOKIE));if(session)session.lastActivity=Date.now();return session||null}
function setSessionCookie(res,id,req){const secure=req.socket.encrypted||process.env.COOKIE_SECURE==='true';res.setHeader('Set-Cookie',`${SESSION_COOKIE}=${id}; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800${secure?'; Secure':''}`)}
function clearSessionCookie(res,req){const secure=req.socket.encrypted||process.env.COOKIE_SECURE==='true';res.setHeader('Set-Cookie',`${SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0${secure?'; Secure':''}`)}
async function closeSession(session){if(!session)return;if(session.sqlPlusConnection){try{await session.sqlPlusConnection.close()}catch{}session.sqlPlusConnection=null}sessions.delete(session.id)}
function sqlFilesFor(session){const key=`${session.settings.connectString.toUpperCase()}\0${session.settings.user.toUpperCase()}\0${session.workspaceId}`,folder=crypto.createHash('sha256').update(key).digest('hex').slice(0,32);return path.join(SQL_FILES,folder)}
function allowConnectAttempt(req){const key=req.socket.remoteAddress||'unknown',now=Date.now(),attempts=(connectAttempts.get(key)||[]).filter(time=>now-time<60_000);if(attempts.length>=10){connectAttempts.set(key,attempts);return false}attempts.push(now);connectAttempts.set(key,attempts);return true}

async function getDriver() {
  try { return require('oracledb'); }
  catch { throw new Error('Oracle driver is missing. Run npm install, then restart the server.'); }
}

function isBrokenOracleConnection(error) {
  const code=String(error?.code||'');
  const message=String(error?.message||'');
  return /^(?:NJS-(?:003|500|511|521)|ORA-(?:03113|03114|03135|12537|12547|12570|12571))$/.test(code)
    || /\b(?:NJS-(?:003|500|511|521)|ORA-(?:03113|03114|03135|12537|12547|12570|12571))\b/.test(message);
}

function getStatementBindValues(sql, values) {
  const supplied = values && typeof values === 'object' && !Array.isArray(values) ? values : {};
  const names = new Set();
  const pattern = /--[^\r\n]*|\/\*[\s\S]*?\*\/|'(?:''|[^'])*'|"(?:""|[^"])*"|:([A-Za-z][A-Za-z0-9_$#]*)/g;
  let match;
  while ((match = pattern.exec(sql))) if (match[1]) names.add(match[1].toUpperCase());
  const selected = {};
  for (const name of names) {
    const key = Object.keys(supplied).find(item => item.toUpperCase() === name);
    if (key === undefined) throw new Error(`No value was supplied for bind variable :${name}.`);
    selected[name] = supplied[key];
  }
  return Object.keys(selected).length ? selected : [];
}async function handleExecute(req, res, session) {
  let body='';
  for await (const chunk of req) {
    body += chunk;
    if (Buffer.byteLength(body) > MAX_BODY) return json(res, 413, { error:'Request is too large.' });
  }
  let payload;
  try { payload=JSON.parse(body); } catch { return json(res, 400, { error:'Invalid JSON request.' }); }
  if (typeof payload.sql !== 'string' || !payload.sql.trim()) return json(res, 400, { error:'Provide a SQL statement.' });
  if (!session?.settings) return json(res, 401, { error:'Connect to Oracle before running SQL.' });
  const started=Date.now();
  let connection;
  let failingStatement="";
  try {
    const oracledb=await getDriver();
    if(payload.sqlplus){
      if(session.sqlPlusConnection){
        try { await session.sqlPlusConnection.ping(); connection=session.sqlPlusConnection; }
        catch { try { await session.sqlPlusConnection.close(); } catch {} session.sqlPlusConnection=null; }
      }
      if(!connection){connection=await oracledb.getConnection(session.settings);session.sqlPlusConnection=connection}
    }else connection=await oracledb.getConnection(session.settings);
    const statements=payload.mode==='script'?splitScript(payload.sql):[payload.sql.trim().replace(/(?:;\s*|\/\s*)+$/,'').trim()].filter(Boolean);
    if(!statements.length)return json(res,400,{error:'Provide a SQL statement.'});
    let columns=[],rows=[],rowsAffected;const outputLines=[],messages=[];
    for(const raw of statements){
      const sql=raw.trim().replace(/(?:;\s*|\/\s*)+$/,'').trim();if(!sql)continue;failingStatement=sql;
      const isPlsql=/^(DECLARE|BEGIN|CREATE(?:\s+OR\s+REPLACE)?\s+(?:EDITIONABLE\s+)?(?:PROCEDURE|FUNCTION|PACKAGE|TRIGGER|TYPE)\b)/i.test(sql);
      if(isPlsql)await connection.execute('BEGIN DBMS_OUTPUT.ENABLE(NULL); END;');
      const result=await connection.execute(sql,getStatementBindValues(sql,payload.binds),{outFormat:oracledb.OUT_FORMAT_OBJECT,maxRows:MAX_ROWS,autoCommit:payload.sqlplus?payload.autoCommit===true:true});
      const resultColumns=(result.metaData||[]).map(column=>({name:column.name,type:column.dbType?.name||column.dbTypeName||''}));
      if(resultColumns.length){columns=resultColumns;rows=(result.rows||[]).map(row=>columns.map(column=>{const value=row[column.name];if(value instanceof Date)return value.getFullYear()+'-'+String(value.getMonth()+1).padStart(2,'0')+'-'+String(value.getDate()).padStart(2,'0')+'T'+String(value.getHours()).padStart(2,'0')+':'+String(value.getMinutes()).padStart(2,'0')+':'+String(value.getSeconds()).padStart(2,'0')+'.'+String(value.getMilliseconds()).padStart(3,'0');if(typeof value==='bigint')return value.toString();if(Buffer.isBuffer(value))return value.toString('base64');if(value&&typeof value==='object')return String(value);return value}));}
      rowsAffected=result.rowsAffected??rowsAffected;
      messages.push(resultColumns.length?`Query completed (${rows.length} rows)`: `Statement ${messages.length+1} executed`);
      if(isPlsql){let line;let status=0;do{const out=await connection.execute('BEGIN DBMS_OUTPUT.GET_LINE(:line, :status); END;',{line:{dir:oracledb.BIND_OUT,type:oracledb.STRING,maxSize:32767},status:{dir:oracledb.BIND_OUT,type:oracledb.NUMBER}});line=out.outBinds.line;status=out.outBinds.status;if(status===0)outputLines.push(line)}while(status===0)}
    }
    const output={columns,rows,outputLines,rowsAffected,truncated:columns.length>0&&rows.length===MAX_ROWS,maxRows:MAX_ROWS,durationMs:Date.now()-started,message:messages.join(' · '),statementsExecuted:messages.length};
    return json(res,200,output);
  } catch (error) {
    if(payload?.sqlplus && isBrokenOracleConnection(error) && session.sqlPlusConnection){
      try { await session.sqlPlusConnection.close(); } catch {}
      session.sqlPlusConnection=null;
    }
    return json(res,400,{ error:error.message || 'Oracle execution failed.',code:error.code||null,offset:Number.isFinite(error.offset)?error.offset:null,statement:failingStatement||null });
  } finally {
    if (connection && !(payload?.sqlplus && connection===session.sqlPlusConnection)) try { await connection.close(); } catch {}
  }
}

async function handleStatus(res,session) {
  const settings=session?.settings;
  if (!settings) return json(res,200,{connected:false});
  let connection;
  try {
    const oracledb=await getDriver();
    connection=await oracledb.getConnection(settings);
    const result=await connection.execute("SELECT SYS_CONTEXT('USERENV','SERVICE_NAME') AS SERVICE_NAME, USER AS DB_USER FROM dual");
    const row=result.rows[0];
    const defaultSchemaOnly=isFreeSqlDefaultConnection(settings.connectString,row.DB_USER);return json(res,200,{connected:true,user:row.DB_USER,service:row.SERVICE_NAME,defaultSchemaOnly});
  } catch (error) { return json(res,200,{connected:false,error:error.message}); }
  finally { if(connection) try{await connection.close()}catch{} }
}

async function handleConnect(req,res,priorSession){
  let body='';
  for await(const chunk of req){body+=chunk;if(Buffer.byteLength(body)>MAX_BODY)return json(res,413,{error:'Request is too large.'})}
  let payload;try{payload=JSON.parse(body)}catch{return json(res,400,{error:'Invalid JSON request.'})}
  const {user,password,connectString,admin}=payload||{};
  if(typeof user!=='string'||typeof password!=='string'||typeof connectString!=='string'||!user.trim()||!password||!connectString.trim())return json(res,400,{error:'Enter the Oracle username, password, and connect string.'});


  let connection;
  try{
    const oracledb=await getDriver();
    const connectionOptions={user:user.trim(),password,connectString:connectString.trim()};
    if(admin===true)connectionOptions.privilege=oracledb.SYSDBA;
    connection=await oracledb.getConnection(connectionOptions);
    const result=await connection.execute("SELECT SYS_CONTEXT('USERENV','SERVICE_NAME') AS SERVICE_NAME, USER AS DB_USER FROM dual");
    const row=result.rows[0];
    const id=crypto.randomBytes(32).toString('base64url');
    const workspaceId=readCookie(req,WORKSPACE_COOKIE)||crypto.randomBytes(32).toString('base64url');
    const session={id,workspaceId,settings:{user:user.trim(),password,connectString:connectString.trim(),...(admin===true?{privilege:oracledb.SYSDBA}:{})},sqlPlusConnection:connection,lastActivity:Date.now()};
    sessions.set(id,session);setSessionCookie(res,id,req);const secureCookie=req.socket.encrypted||process.env.COOKIE_SECURE==='true';res.setHeader('Set-Cookie',[res.getHeader('Set-Cookie'),`${WORKSPACE_COOKIE}=${workspaceId}; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000${secureCookie?'; Secure':''}`]);

    connection=null;
    if(priorSession)await closeSession(priorSession);
    const defaultSchemaOnly=isFreeSqlDefaultConnection(connectString,row.DB_USER);return json(res,200,{connected:true,user:row.DB_USER,service:row.SERVICE_NAME,defaultSchemaOnly});
  }catch(error){return json(res,400,{error:error.message||'Oracle connection failed.'})}
  finally{if(connection)try{await connection.close()}catch{}}
}

async function handleDisconnect(res,session,req){if(session)await closeSession(session);clearSessionCookie(res,req);return json(res,200,{connected:false})}

async function handleObjects(res,session){
  const settings=session?.settings;if(!settings)return json(res,401,{error:'Connect to Oracle first.'});let connection;
  const defaultSchemaOnly=isFreeSqlDefaultConnection(settings.connectString,settings.user);
  try{const oracledb=await getDriver();connection=await oracledb.getConnection(settings);const excluded="('SYS','OUTLN','DBSNMP','APPQOSSYS','AUDSYS','CTXSYS','DBSFWUSER','GSMADMIN_INTERNAL','LBACSYS','MDSYS','OJVMSYS','ORDDATA','ORDSYS','WMSYS','XDB')";const ownerFilter=defaultSchemaOnly?'owner = USER':'owner NOT IN '+excluded;const result=await connection.execute(`SELECT owner, object_name, object_type, status, created FROM all_objects WHERE object_type IN ('TABLE','VIEW','MATERIALIZED VIEW','INDEX','SEQUENCE','PROCEDURE','FUNCTION','PACKAGE','TRIGGER','TYPE','SYNONYM') AND ${ownerFilter} ORDER BY CASE WHEN owner = USER THEN 0 ELSE 1 END, owner, object_type, object_name`,[],{outFormat:oracledb.OUT_FORMAT_OBJECT,maxRows:4000});const constraintResult=await connection.execute(`SELECT owner, constraint_name AS object_name, 'CONSTRAINT' AS object_type, status, table_name FROM all_constraints WHERE ${ownerFilter} ORDER BY CASE WHEN owner = USER THEN 0 ELSE 1 END, owner, table_name, constraint_name`,[],{outFormat:oracledb.OUT_FORMAT_OBJECT,maxRows:10000});return json(res,200,{objects:[...(result.rows||[]),...(constraintResult.rows||[])],user:String(settings.user||'').toUpperCase(),defaultSchemaOnly})}
  catch(error){return json(res,400,{error:error.message||'Could not load Oracle objects.'})}finally{if(connection)try{await connection.close()}catch{}}
}async function handleObjectDetails(res,owner,name,view='structure',session){
  const settings=session?.settings;if(!settings)return json(res,401,{error:'Connect to Oracle first.'});let connection;
  try{const oracledb=await getDriver();connection=await oracledb.getConnection(settings);const normalizedOwner=owner.toUpperCase(),normalizedName=name.toUpperCase();
    if(!/^[A-Z][A-Z0-9_$#]{0,127}$/.test(normalizedOwner)||!/^[A-Z][A-Z0-9_$#]{0,127}$/.test(normalizedName))return json(res,400,{error:'Object name is not a supported Oracle identifier.'});
    const defaultSchemaOnly=isFreeSqlDefaultConnection(settings.connectString,settings.user);if(defaultSchemaOnly&&normalizedOwner!==String(settings.user||'').toUpperCase())return json(res,403,{error:'Navigator can access objects in your connected schema only.'});
    if(view==='data'){const access=await connection.execute("SELECT object_type FROM all_objects WHERE owner=:owner AND object_name=:name AND object_type IN ('TABLE','VIEW','MATERIALIZED VIEW') AND ROWNUM=1",{owner:normalizedOwner,name:normalizedName},{outFormat:oracledb.OUT_FORMAT_OBJECT});if(!access.rows.length)return json(res,400,{error:'Data preview is available for tables and views only.'});const result=await connection.execute(`SELECT * FROM "${normalizedOwner}"."${normalizedName}" FETCH FIRST 100 ROWS ONLY`,[],{outFormat:oracledb.OUT_FORMAT_OBJECT,maxRows:100});const columns=(result.metaData||[]).map(column=>({name:column.name}));const rows=(result.rows||[]).map(row=>columns.map(column=>{const value=row[column.name];if(value instanceof Date)return value.getFullYear()+'-'+String(value.getMonth()+1).padStart(2,'0')+'-'+String(value.getDate()).padStart(2,'0')+'T'+String(value.getHours()).padStart(2,'0')+':'+String(value.getMinutes()).padStart(2,'0')+':'+String(value.getSeconds()).padStart(2,'0')+'.'+String(value.getMilliseconds()).padStart(3,'0');if(typeof value==='bigint')return value.toString();if(Buffer.isBuffer(value))return value.toString('base64');if(value&&typeof value==='object')return String(value);return value}));return json(res,200,{owner:normalizedOwner,name:normalizedName,view,columns,rows,truncated:rows.length===100})}
    if(view==='constraints'){const result=await connection.execute('SELECT c.constraint_name, c.constraint_type, c.status, c.validated, cc.column_name, cc.position, c.r_constraint_name FROM all_constraints c LEFT JOIN all_cons_columns cc ON cc.owner=c.owner AND cc.constraint_name=c.constraint_name AND cc.table_name=c.table_name WHERE c.owner=:owner AND c.table_name=:name ORDER BY c.constraint_name, cc.position',{owner:normalizedOwner,name:normalizedName},{outFormat:oracledb.OUT_FORMAT_OBJECT});return json(res,200,{owner:normalizedOwner,name:normalizedName,view,constraints:result.rows||[]})}
    if(view==='structure'){
      const indexCheck=await connection.execute("SELECT object_type FROM all_objects WHERE owner=:owner AND object_name=:name AND object_type='INDEX' AND ROWNUM=1",{owner:normalizedOwner,name:normalizedName},{outFormat:oracledb.OUT_FORMAT_OBJECT});
      if(indexCheck.rows.length){const result=await connection.execute('SELECT c.column_name AS COLUMN_NAME, c.column_position AS COLUMN_ID, i.uniqueness AS DATA_TYPE, NULL AS NULLABLE FROM all_ind_columns c JOIN all_indexes i ON i.owner=c.index_owner AND i.index_name=c.index_name WHERE c.index_owner=:owner AND c.index_name=:name ORDER BY c.column_position',{owner:normalizedOwner,name:normalizedName},{outFormat:oracledb.OUT_FORMAT_OBJECT});return json(res,200,{owner:normalizedOwner,name:normalizedName,view,columns:result.rows||[],objectType:'INDEX'})}
      const result=await connection.execute('SELECT column_name, data_type, nullable FROM all_tab_columns WHERE owner = :owner AND table_name = :name ORDER BY column_id',{owner:normalizedOwner,name:normalizedName},{outFormat:oracledb.OUT_FORMAT_OBJECT});return json(res,200,{owner:normalizedOwner,name:normalizedName,view,columns:result.rows||[]})
    }
    return json(res,400,{error:'Unknown object property view.'})
  }catch(error){return json(res,400,{error:error.message||'Could not load object properties.'})}finally{if(connection)try{await connection.close()}catch{}}
}
async function handleFiles(res,session){
  try{const folder=sqlFilesFor(session);await fs.promises.mkdir(folder,{recursive:true});const files=(await fs.promises.readdir(folder,{withFileTypes:true})).filter(item=>item.isFile()&&/\.sql$/i.test(item.name)).map(item=>item.name).sort((a,b)=>a.localeCompare(b));return json(res,200,{files})}
  catch(error){return json(res,500,{error:error.message||'Could not list saved files.'})}
}

async function handleReadFile(res,name,session){
  try{const file=safeSqlFileName(name);const sql=await fs.promises.readFile(path.join(sqlFilesFor(session),file),'utf8');return json(res,200,{fileName:file,sql})}
  catch(error){return json(res,404,{error:'Saved SQL file not found.'})}
}

async function handleSaveFile(req,res,session){
  let body='';for await(const chunk of req){body+=chunk;if(Buffer.byteLength(body)>MAX_BODY)return json(res,413,{error:'File is too large.'})}
  let payload;try{payload=JSON.parse(body)}catch{return json(res,400,{error:'Invalid JSON request.'})}
  if(typeof payload.sql!=='string')return json(res,400,{error:'SQL text is required.'});
  try{const fileName=safeSqlFileName(payload.fileName),folder=sqlFilesFor(session);await fs.promises.mkdir(folder,{recursive:true});await fs.promises.writeFile(path.join(folder,fileName),payload.sql,'utf8');return json(res,200,{fileName})}
  catch(error){return json(res,400,{error:error.message||'Could not save SQL file.'})}
}

const requestHandler=async(req,res)=>{
  const url=new URL(req.url,'http://localhost');
  if(url.pathname.startsWith('/api/')&&['POST','PUT','PATCH','DELETE'].includes(req.method)&&!req.headers.origin)return json(res,403,{error:'Origin header is required for API changes.'});
  if(url.pathname.startsWith('/api/') && req.headers.origin){let origin;try{origin=new URL(req.headers.origin)}catch{return json(res,403,{error:'Invalid request origin.'})}const expectedProtocol=req.socket.encrypted?'https:':'http:';if(origin.protocol!==expectedProtocol||origin.host.toLowerCase()!==String(req.headers.host||'').toLowerCase())return json(res,403,{error:'Cross-origin API requests are blocked.'})}
  const session=readSession(req);
  if(req.method==='POST' && url.pathname==='/api/connect'){if(!allowConnectAttempt(req))return json(res,429,{error:'Too many connection attempts. Wait a minute and try again.'});if(!session&&sessions.size>=100)return json(res,503,{error:'The server has reached its active login limit.'});return handleConnect(req,res,session)}
  if(req.method==='POST' && url.pathname==='/api/disconnect') return handleDisconnect(res,session,req);
  if(req.method==='GET' && url.pathname==='/api/status') return handleStatus(res,session);
  if(req.method==='POST' && url.pathname==='/api/execute') return session?handleExecute(req,res,session):json(res,401,{error:'Connect to Oracle first.'});
  if(req.method==='GET' && url.pathname==='/api/objects') return session?handleObjects(res,session):json(res,401,{error:'Connect to Oracle first.'});
  if(req.method==='GET' && url.pathname.startsWith('/api/objects/')){if(!session)return json(res,401,{error:'Connect to Oracle first.'});const parts=url.pathname.slice('/api/objects/'.length).split('/').map(decodeURIComponent);if(parts.length!==2)return json(res,400,{error:'Provide an object owner and name.'});return handleObjectDetails(res,parts[0],parts[1],url.searchParams.get('view')||'structure',session)}
  if(req.method==='GET' && url.pathname==='/api/files') return session?handleFiles(res,session):json(res,401,{error:'Connect to Oracle first.'});
  if(req.method==='GET' && url.pathname.startsWith('/api/files/')) return session?handleReadFile(res,decodeURIComponent(url.pathname.slice('/api/files/'.length)),session):json(res,401,{error:'Connect to Oracle first.'});
  if(req.method==='POST' && url.pathname==='/api/files') return session?handleSaveFile(req,res,session):json(res,401,{error:'Connect to Oracle first.'});
  if(req.method==='GET' && url.pathname.startsWith('/api/')) return json(res,404,{error:'API route not found.'});
  if(req.method!=='GET' && req.method!=='HEAD') return json(res,405,{error:'Method not allowed.'});
  const relative=url.pathname==='/'?'index.html':decodeURIComponent(url.pathname).replace(/^\/+/, '');
  if(!STATIC_FILES.has(relative))return json(res,404,{error:'Not found.'});
  const target=path.resolve(ROOT,relative);
  if(!target.startsWith(ROOT+path.sep) && target!==path.join(ROOT,'index.html')) return json(res,403,{error:'Forbidden.'});
  fs.readFile(target,(error,contents)=>{
    if(error){res.writeHead(404,{'Content-Type':'text/plain; charset=utf-8'});return res.end('Not found');}
    res.writeHead(200,{'Content-Type':MIME[path.extname(target)]||'application/octet-stream','X-Content-Type-Options':'nosniff','Cache-Control':'no-cache'});
    res.end(req.method==='HEAD'?undefined:contents);
  });
};

if(!TLS_ENABLED&&!['127.0.0.1','localhost','::1'].includes(HOST))throw new Error('Refusing network access without HTTPS. Configure TLS_KEY_FILE and TLS_CERT_FILE, or keep HOST on localhost.');
const server=TLS_ENABLED?https.createServer({key:fs.readFileSync(path.resolve(ROOT,TLS_KEY_FILE)),cert:fs.readFileSync(path.resolve(ROOT,TLS_CERT_FILE))},requestHandler):http.createServer(requestHandler);

server.listen(PORT,HOST,()=>console.log(`Oracle SQL workbench listening on ${TLS_ENABLED?'https':'http'}://${HOST}:${PORT}`));

const sessionCleanup=setInterval(()=>{const now=Date.now();for(const session of sessions.values())if(now-session.lastActivity>SESSION_IDLE_MS)closeSession(session)},5*60*1000);sessionCleanup.unref();

process.on('SIGINT',()=>server.close(()=>process.exit(0)));
process.on('SIGTERM',()=>server.close(()=>process.exit(0)));
