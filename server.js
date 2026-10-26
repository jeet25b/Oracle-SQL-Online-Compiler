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
const STATIC_FILES=new Set(['index.html','app.js','styles.css','output.css']);

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
}

function parseSqlPlusDescribe(command) {
  const identifier = '(?:"(?:""|[^"])*"|[A-Za-z][A-Za-z0-9_$#]*)';
  const match = String(command || '').trim().match(new RegExp(`^(?:DESC|DESCRIBE)\\s+(${identifier})(?:\\.(${identifier}))?\\s*;?$`, 'i'));
  if (!match) return null;
  const normalize = (value) => value.startsWith('"') ? value.slice(1, -1).replaceAll('""', '"') : value.toUpperCase();
  return match[2]
    ? { owner: normalize(match[1]), name: normalize(match[2]) }
    : { owner: null, name: normalize(match[1]) };
}

function buildDescribeResult(rows) {
  const columns = [{ name: 'Name', type: '' }, { name: 'Null?', type: '' }, { name: 'Type', type: '' }];
  const describedRows = rows.map((row) => {
    let type = row.DATA_TYPE;
    if (['CHAR', 'VARCHAR2', 'NCHAR', 'NVARCHAR2'].includes(type)) {
      const length = row.CHAR_USED === 'C' ? row.CHAR_LENGTH : row.DATA_LENGTH;
      type = `${type}(${length}${row.CHAR_USED === 'C' ? ' CHAR' : ''})`;
    } else if (type === 'NUMBER' && row.DATA_PRECISION !== null) {
      type = `NUMBER(${row.DATA_PRECISION}${row.DATA_SCALE ? `,${row.DATA_SCALE}` : ''})`;
    }
    return [row.COLUMN_NAME, row.NULLABLE === 'Y' ? '' : 'NOT NULL', type];
  });
  return { columns, rows: describedRows };
}

function getSqlPlusState(session) {
  if (!session.sqlPlusState) {
    session.sqlPlusState = {
      buffer: '',
      currentLine: 0,
      lastSqlCode: 0,
      defines: {},
      history: [],
      timers: {},
      settings: { autocommit: true, serveroutput: true, feedback: true, heading: true, echo: false, timing: false, linesize: 80, pagesize: 14, sqlprompt: 'SQL>', define: '&' },
    };
  }
  return session.sqlPlusState;
}

function substituteSqlPlusVariables(sql, state) {
  const marker = state.settings.define;
  if (!marker) return { sql, missing: null };
  let output = '', quote = '', lineComment = false, blockComment = false, missing = null;
  for (let i = 0; i < sql.length; i++) {
    const char = sql[i], next = sql[i + 1];
    if (lineComment) { output += char; if (char === '\n') lineComment = false; continue; }
    if (blockComment) { output += char; if (char === '*' && next === '/') { output += next; i++; blockComment = false; } continue; }
    if (quote) { output += char; if (char === quote && next === quote) { output += next; i++; } else if (char === quote) quote = ''; continue; }
    if (char === '-' && next === '-') { output += char + next; i++; lineComment = true; continue; }
    if (char === '/' && next === '*') { output += char + next; i++; blockComment = true; continue; }
    if (char === "'" || char === '"') { quote = char; output += char; continue; }
    if (char === marker) {
      if (next === marker) i++;
      const nameMatch = sql.slice(i + 1).match(/^([A-Za-z][A-Za-z0-9_$#]*)/);
      if (nameMatch) {
        const name = nameMatch[1].toUpperCase();
        if (!Object.hasOwn(state.defines, name)) { missing = name; output += marker + nameMatch[1]; i += nameMatch[1].length; continue; }
        output += state.defines[name];
        i += nameMatch[1].length;
        continue;
      }
    }
    output += char;
  }
  return { sql: output, missing };
}

async function handleSqlPlusClientCommand(command, session, connection, oracledb) {
  const state = getSqlPlusState(session);
  const trimmed = String(command || '').trim();
  const simple = trimmed.replace(/;\s*$/, '').trim();
  const answer = (message, outputLines = [], extra = {}) => ({ message, outputLines, ...extra });

  const define = simple.match(/^DEFINE\s+([A-Za-z][A-Za-z0-9_$#]*)\s*=\s*(.*?)\s*$/i);
  if (define) {
    state.defines[define[1].toUpperCase()] = define[2].replace(/^(['"])(.*)\1$/, '$2');
    return answer(`${define[1].toUpperCase()} defined`);
  }
  const undefine = simple.match(/^UNDEFINE\s+(.+)$/i);
  if (undefine) {
    undefine[1].split(/[ ,]+/).filter(Boolean).forEach((name) => { delete state.defines[name.toUpperCase()]; });
    return answer('Substitution variable(s) undefined.');
  }
  if (/^DEFINE(?:\s+ALL)?$/i.test(simple)) {
    const entries = Object.entries(state.defines).map(([name, value]) => `${name} = "${value}"`);
    return answer('', entries.length ? entries : ['No substitution variables defined.']);
  }

  const set = simple.match(/^SET\s+([A-Za-z][A-Za-z0-9_]*)\s*(.*?)\s*$/i);
  if (set) {
    const key = set[1].toLowerCase();
    if (['role', 'transaction', 'constraints'].includes(key)) return null;
    const known = new Set(['autocommit', 'serveroutput', 'feedback', 'heading', 'echo', 'timing', 'linesize', 'pagesize', 'sqlprompt', 'define', 'verify', 'termout', 'null', 'tab', 'trimout', 'trimspool', 'sqlblanklines', 'long', 'numwidth']);
    if (!known.has(key)) return answer(`SET ${set[1].toUpperCase()} is not implemented in this browser SQL*Plus workspace.`);
    const value = set[2].trim();
    if (!value) return answer(`${set[1].toUpperCase()} = ${state.settings[key] ?? 'ON'}`);
    if (['autocommit', 'serveroutput', 'feedback', 'heading', 'echo', 'timing', 'verify', 'termout', 'tab', 'trimout', 'trimspool', 'sqlblanklines'].includes(key)) {
      if (!/^(ON|OFF)$/i.test(value)) return answer(`SET ${set[1].toUpperCase()} expects ON or OFF.`);
      state.settings[key] = value.toUpperCase() === 'ON';
    } else if (['linesize', 'pagesize', 'long', 'numwidth'].includes(key)) {
      if (!/^\d+$/.test(value)) return answer(`SET ${set[1].toUpperCase()} expects a whole number.`);
      state.settings[key] = Number(value);
    } else if (key === 'define') {
      state.settings.define = /^OFF$/i.test(value) ? '' : /^ON$/i.test(value) ? '&' : value.slice(0, 1);
    } else {
      state.settings[key] = value.replace(/^(['"])(.*)\1$/, '$2');
    }
    return answer(`${set[1].toUpperCase()} set to ${value}`, [], {
      ...(key === 'autocommit' ? { sqlPlusAutoCommit: state.settings.autocommit } : {}),
      ...(key === 'sqlprompt' ? { sqlPlusPrompt: state.settings.sqlprompt } : {}),
      ...(key === 'feedback' ? { sqlPlusFeedback: state.settings.feedback } : {}),
      ...(key === 'heading' ? { sqlPlusHeading: state.settings.heading } : {}),
    });
  }

  const show = simple.match(/^SHOW\s+(.+)$/i);
  if (show) {
    const option = show[1].trim();
    const showSetting = option.toLowerCase();
    if (/^errors?(?:\s+(?:procedure|function|package|package\s+body|trigger|view|type|type\s+body)\s+[\w.$#"]+)?$/i.test(option)) {
      const object = option.match(/^errors?\s+(procedure|function|package(?:\s+body)?|trigger|view|type(?:\s+body)?)\s+([\w.$#"]+)$/i);
      const name = object ? object[2].replace(/^"|"$/g, '').toUpperCase() : null;
      const type = object ? object[1].toUpperCase().replace(/\s+/g, ' ') : null;
      const result = await connection.execute(
        `SELECT line AS LINE, position AS POSITION, text AS TEXT FROM user_errors${name ? ' WHERE name = :name AND type = :type' : ''} ORDER BY name, type, sequence`,
        name ? { name, type } : [],
        { outFormat: oracledb.OUT_FORMAT_OBJECT },
      );
      return answer('', result.rows?.length
        ? result.rows.map((row) => `LINE ${row.LINE} (${row.POSITION}): ${row.TEXT}`)
        : ['No errors.']);
    }
    if (showSetting === 'user') {
      const result = await connection.execute('SELECT USER AS USER_NAME FROM dual', [], { outFormat: oracledb.OUT_FORMAT_OBJECT });
      return answer('', [`USER is "${result.rows?.[0]?.USER_NAME || session.settings.user}"`]);
    }
    if (showSetting === 'version' || showSetting === 'release') {
      const result = await connection.execute("SELECT banner FROM product_component_version WHERE product LIKE 'Oracle Database%' ORDER BY product", [], { outFormat: oracledb.OUT_FORMAT_OBJECT });
      return answer('', (result.rows || []).map((row) => row.BANNER).filter(Boolean));
    }
    if (showSetting === 'sqlcode') return answer(`SQL.SQLCODE = ${state.lastSqlCode}`);
    if (showSetting === 'all') return answer('', Object.entries(state.settings).map(([name, value]) => `${name.toUpperCase()} ${typeof value === 'boolean' ? (value ? 'ON' : 'OFF') : value}`));
    if (showSetting === 'define') return handleSqlPlusClientCommand('DEFINE', session, connection, oracledb);
    if (Object.hasOwn(state.settings, showSetting)) {
      const value = state.settings[showSetting];
      return answer(`${showSetting.toUpperCase()} = ${typeof value === 'boolean' ? (value ? 'ON' : 'OFF') : value}`);
    }
    const variable = state.defines[option.toUpperCase()];
    return variable === undefined ? answer(`SHOW ${option} is not implemented in this browser SQL*Plus workspace.`) : answer(`${option.toUpperCase()} = "${variable}"`);
  }

  const get = simple.match(/^GET\s+([^\s]+)(?:\s+(LIST|NOLIST))?$/i);
  if (get) {
    const fileName = safeSqlFileName(get[1]);
    const sql = await fs.promises.readFile(path.join(sqlFilesFor(session), fileName), 'utf8');
    state.buffer = sql.trim(); state.currentLine = state.buffer ? state.buffer.split(/\r?\n/).length : 0;
    return answer(`SQL buffer loaded from ${fileName}.`, get[2]?.toUpperCase() === 'LIST' ? state.buffer.split(/\r?\n/) : []);
  }
  const save = simple.match(/^SAVE\s+([^\s]+)(?:\s+(CREATE|REPLACE|APPEND))?$/i);
  if (save) {
    if (!state.buffer) return answer('SQL buffer is empty.');
    const fileName = safeSqlFileName(save[1]);
    const filePath = path.join(sqlFilesFor(session), fileName);
    const mode = (save[2] || 'CREATE').toUpperCase();
    await fs.promises.mkdir(path.dirname(filePath), { recursive: true });
    if (mode === 'CREATE' && fs.existsSync(filePath)) return answer(`${fileName} already exists. Use SAVE ${fileName} REPLACE to overwrite it.`);
    if (mode === 'APPEND') await fs.promises.appendFile(filePath, `${state.buffer}\n`, 'utf8');
    else await fs.promises.writeFile(filePath, `${state.buffer}\n`, 'utf8');
    return answer(`SQL buffer saved to ${fileName}.`);
  }
  const spool = simple.match(/^SPOOL(?:\s+(.*))?$/i);
  if (spool) {
    const argument = (spool[1] || '').trim();
    if (!argument) return answer(state.spool ? `Spooling to ${state.spool.filename}` : 'Spooling is off.');
    if (/^OFF$/i.test(argument)) {
      if (!state.spool) return answer('Spooling is already off.');
      const prior = state.spool;
      state.spool = null;
      return answer(`Spool transcript ready: ${prior.filename}`, [], { sqlPlusSpool: { action: 'stop', filename: prior.filename, mode: prior.mode } });
    }
    if (/^OUT$/i.test(argument)) return answer('SPOOL OUT requires a local printer and is unavailable in the browser workspace.');
    const parts = argument.match(/^(.*?)(?:\s+(CREATE|REPLACE|APPEND))?$/i);
    const filename = safeSqlFileName((parts?.[1] || '').trim().replace(/^(['"])(.*)\1$/, '$2'));
    const mode = (parts?.[2] || 'CREATE').toUpperCase();
    state.spool = { filename, mode };
    return answer(`Spooling started: ${filename}`, [], { sqlPlusSpool: { action: 'start', filename, mode } });
  }

  const prompt = simple.match(/^PROMPT(?:\s+(.*))?$/i);
  if (prompt) return answer('', prompt[1] ? [prompt[1]] : ['']);
  const remark = simple.match(/^(?:REM|REMARK)(?:\s+(.*))?$/i);
  if (remark) return answer('');
  if (/^(?:HELP|\?)$/i.test(simple)) return answer('', [
    'Browser SQL*Plus commands: DESC, SHOW USER/VERSION/SQLCODE, SET, DEFINE, UNDEFINE, PROMPT, REMARK, LIST, APPEND, CHANGE, INPUT, DEL, RUN, /, CLEAR SCREEN/BUFFER, GET, SAVE, START, @, SPOOL, HISTORY, TIMING, EXECUTE, DISCONNECT, EXIT.',
    'SQL and PL/SQL statements are sent to Oracle. Use the toolbar to connect, disconnect, upload, and download files.',
    'Operating-system commands and database instance administration are unavailable in this browser workspace.',
  ]);
  if (/^HISTORY(?:\s+\d+)?$/i.test(simple)) return answer('', state.history.slice(-20).map((item, index) => `${index + 1}  ${item}`));
  const timing = simple.match(/^TIMING\s+(START|STOP|SHOW)(?:\s+(.*))?$/i);
  if (timing) {
    const action = timing[1].toUpperCase(), name = (timing[2] || 'default').trim() || 'default';
    if (action === 'START') { state.timers[name] = Date.now(); return answer(`Timer ${name} started.`); }
    if (action === 'STOP') {
      if (!state.timers[name]) return answer(`Timer ${name} was not started.`);
      const elapsed = Date.now() - state.timers[name]; delete state.timers[name];
      return answer(`Elapsed: ${(elapsed / 1000).toFixed(3)} seconds.`);
    }
    const activeTimers = Object.entries(state.timers).map(([timerName, started]) => `${timerName}: ${((Date.now() - started) / 1000).toFixed(3)} seconds`);
    return answer('', activeTimers.length ? activeTimers : ['No active timers.']);
  }
  if (/^LIST(?:\s+.*)?$/i.test(simple) || /^L$/i.test(simple)) {
    const lines = state.buffer.split(/\r?\n/);
    return answer('', state.buffer ? lines.map((line, index) => `${index + 1 === state.currentLine ? '>' : ' '} ${String(index + 1).padStart(3, ' ')} ${line}`) : ['SQL buffer is empty.']);
  }
  const append = simple.match(/^APPEND(?:\s+(.*))?$/i);
  if (append) {
    const lines = state.buffer.split(/\r?\n/);
    if (!state.buffer) lines[0] = '';
    const current = Math.max(0, Math.min(lines.length - 1, state.currentLine - 1));
    lines[current] += append[1] ? ` ${append[1]}` : '';
    state.buffer = lines.join('\n'); state.currentLine = current + 1;
    return answer('Text appended to SQL buffer.');
  }
  if (/^(?:CHANGE|C)\s+/i.test(simple)) {
    const commandMatch = simple.match(/^(?:CHANGE|C)\s+(.)/i);
    const separator = commandMatch?.[1];
    const contentStart = commandMatch ? commandMatch[0].length : simple.length;
    const parts = separator ? simple.slice(contentStart).split(separator) : [];
    const lines = state.buffer ? state.buffer.split(/\r?\n/) : [];
    const current = Math.max(0, Math.min(lines.length - 1, state.currentLine - 1));
    if (parts.length < 2 || !lines.length) return answer('Use CHANGE /old/new/ on a non-empty SQL buffer line.');
    const oldText = parts[0], newText = parts[1];
    if (!oldText) return answer('CHANGE requires text to find.');
    const offset = lines[current].indexOf(oldText);
    if (offset < 0) return answer(`Text "${oldText}" was not found on the current SQL buffer line.`);
    lines[current] = `${lines[current].slice(0, offset)}${newText}${lines[current].slice(offset + oldText.length)}`;
    state.buffer = lines.join('\n');
    return answer('SQL buffer line changed.');
  }
  const input = simple.match(/^INPUT(?:\s+(.*))?$/i);
  if (input) {
    const lines = state.buffer ? state.buffer.split(/\r?\n/) : [];
    const insertAt = Math.max(0, Math.min(lines.length, state.currentLine));
    lines.splice(insertAt, 0, input[1] || '');
    state.buffer = lines.join('\n'); state.currentLine = insertAt + 1;
    return answer('Line added to SQL buffer.');
  }
  const del = simple.match(/^DEL(?:\s+(\d+)(?:\s+(\d+|LAST))?)?$/i);
  if (del) {
    const lines = state.buffer ? state.buffer.split(/\r?\n/) : [];
    if (!lines.length) return answer('SQL buffer is empty.');
    const first = del[1] ? Number(del[1]) : state.currentLine || lines.length;
    const last = del[2] ? (del[2].toUpperCase() === 'LAST' ? lines.length : Number(del[2])) : first;
    if (first < 1 || last < first || first > lines.length) return answer('DEL line range is outside the SQL buffer.');
    lines.splice(first - 1, Math.min(last, lines.length) - first + 1);
    state.buffer = lines.join('\n'); state.currentLine = Math.max(0, Math.min(first, lines.length));
    return answer('SQL buffer line(s) deleted.');
  }
  if (/^CLEAR\s+(?:SQL|BUFFER)$/i.test(simple)) { state.buffer = ''; state.currentLine = 0; return answer('SQL buffer cleared.'); }
  if (/^CLEAR\s+(?:SCREEN|SCR)$/i.test(simple) || /^CLS$/i.test(simple)) return answer('', [], { clearTerminal: true });
  if (/^(?:EXIT|QUIT|DISCONNECT)\b/i.test(simple)) return answer('Use the workspace Disconnect control to end the database session.');
  if (/^(?:CONNECT|CONN)\b/i.test(simple)) return answer('Use the workspace Connect control to enter database credentials securely.');
  if (/^(?:HOST|!|STARTUP|SHUTDOWN|RECOVER|ARCHIVE\s+LOG|ACCEPT|PAUSE|EDIT|STORE|PASSWORD)\b/i.test(simple)) {
    return answer('This SQL*Plus command requires operating-system or database-instance access and is unavailable in the browser workspace.');
  }
  if (/^(?:ATTRIBUTE|BREAK|BTITLE|COLUMN|COMPUTE|COPY|PRINT|REPFOOTER|REPHEADER|TTITLE|VARIABLE|WHENEVER|XQUERY)\b/i.test(simple)) {
    return answer(`The SQL*Plus ${simple.split(/\s+/, 1)[0].toUpperCase()} command is not implemented in the browser workspace.`);
  }
  return null;
}

async function handleExecute(req, res, session) {
  let body='';
  for await (const chunk of req) {
    body += chunk;
    if (Buffer.byteLength(body) > MAX_BODY) return json(res, 413, { error:'Request is too large.' });
  }
  let payload;
  try { payload=JSON.parse(body); } catch { return json(res, 400, { error:'Invalid JSON request.' }); }
  if (typeof payload.sql !== 'string' || !payload.sql.trim()) return json(res, 400, { error:'Provide a SQL statement.' });
  if (!session?.settings) return json(res, 401, { error:'Connect to Oracle before running SQL.' });
  if (payload.sqlplus && session.sqlPlusDisconnected) return json(res, 401, { error:'SQL*Plus is disconnected. Use Connect to start a new database session.' });
  const disconnectCommand = payload.sqlplus && payload.sql.trim().match(/^(?:EXIT|QUIT|DISCONNECT)(?:\s+(?:SUCCESS|FAILURE|WARNING|\d+))?(?:\s+(COMMIT|ROLLBACK))?\s*;?$/i);
  if (disconnectCommand) {
    try {
      if (session.sqlPlusConnection) {
        if ((disconnectCommand[2] || 'COMMIT').toUpperCase() === 'ROLLBACK') await session.sqlPlusConnection.rollback();
        else await session.sqlPlusConnection.commit();
        await session.sqlPlusConnection.close();
      }
    }
    catch (error) { return json(res, 400, { error:error.message || 'Could not disconnect from Oracle.' }); }
    session.sqlPlusConnection = null;
    session.sqlPlusDisconnected = true;
    return json(res, 200, { outputLines:['Disconnected from Oracle.'], message:'', sqlPlusDisconnected:true, durationMs:0 });
  }
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
    if(payload.sqlplus&&typeof payload.autoCommit==='boolean')getSqlPlusState(session).settings.autocommit=payload.autoCommit;
    const terminalSlash=payload.sqlplus&&payload.sql.trim()==='/';
    const statements=payload.mode==='script'||payload.script===true?splitScript(payload.sql):[terminalSlash?'/':payload.sql.trim().replace(/(?:;\s*|\/\s*)+$/,'').trim()].filter(Boolean);
    if(!statements.length)return json(res,400,{error:'Provide a SQL statement.'});
    let columns=[],rows=[],rowsAffected;const outputLines=[],messages=[];
    let clearTerminal=false,sqlPlusAutoCommit,sqlPlusPrompt,sqlPlusFeedback,sqlPlusHeading,sqlPlusSpool;
    let expandedScripts=0;
    for(let statementIndex=0;statementIndex<statements.length;statementIndex++){
      const raw=statements[statementIndex];
      const rawCommand=raw.trim();
      let sql=payload.sqlplus&&/^\/$/.test(rawCommand)?'/':rawCommand.replace(/(?:;\s*|\/\s*)+$/,'').trim();if(!sql)continue;failingStatement=sql;
      if(payload.sqlplus){
        const state=getSqlPlusState(session);
        state.history.push(sql);
        if(state.history.length>200)state.history.splice(0,state.history.length-200);
        const scriptFile=sql.match(/^(@@?|START)\s*(?:"([^"]+)"|'([^']+)'|([^\s;]+))\s*;?$/i);
        if(scriptFile){
          if(/^https?:\/\//i.test(scriptFile[2]||scriptFile[3]||scriptFile[4]||''))throw new Error('Remote SQL scripts are unavailable. Save the script in this workspace first.');
          if(++expandedScripts>20)throw new Error('SQL*Plus script nesting exceeded 20 files.');
          const fileName=safeSqlFileName(scriptFile[2]||scriptFile[3]||scriptFile[4]);
          const fileText=await fs.promises.readFile(path.join(sqlFilesFor(session),fileName),'utf8');
          statements.splice(statementIndex,1,...splitScript(fileText));
          statementIndex--;
          continue;
        }
        if(/^\/$|^(?:RUN|R)$/i.test(sql)){
          if(!state.buffer){outputLines.push('SQL buffer is empty.');continue;}
          sql=state.buffer;
          failingStatement=sql;
        }
        const clientResult=await handleSqlPlusClientCommand(sql,session,connection,oracledb);
        if(clientResult){
          outputLines.push(...(clientResult.outputLines||[]));
          if(clientResult.message)messages.push(clientResult.message);
          if(clientResult.columns){columns=clientResult.columns;rows=clientResult.rows||[];}
          if(clientResult.clearTerminal)clearTerminal=true;
          if(clientResult.sqlPlusAutoCommit!==undefined)sqlPlusAutoCommit=clientResult.sqlPlusAutoCommit;
          if(clientResult.sqlPlusPrompt!==undefined)sqlPlusPrompt=clientResult.sqlPlusPrompt;
          if(clientResult.sqlPlusFeedback!==undefined)sqlPlusFeedback=clientResult.sqlPlusFeedback;
          if(clientResult.sqlPlusHeading!==undefined)sqlPlusHeading=clientResult.sqlPlusHeading;
          if(clientResult.sqlPlusSpool!==undefined)sqlPlusSpool=clientResult.sqlPlusSpool;
          continue;
        }
      }
      const describe=payload.sqlplus?parseSqlPlusDescribe(sql):null;
      if(describe){
        const result=await connection.execute(
          `SELECT column_name AS COLUMN_NAME, nullable AS NULLABLE, data_type AS DATA_TYPE, data_length AS DATA_LENGTH, char_length AS CHAR_LENGTH, char_used AS CHAR_USED, data_precision AS DATA_PRECISION, data_scale AS DATA_SCALE FROM all_tab_columns WHERE owner = NVL(:owner, SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')) AND table_name = :name ORDER BY column_id`,
          {owner:describe.owner,name:describe.name},
          {outFormat:oracledb.OUT_FORMAT_OBJECT,maxRows:MAX_ROWS},
        );
        if(!result.rows?.length)throw new Error(`ORA-04043: object ${describe.owner?`${describe.owner}.`:''}${describe.name} does not exist or is not accessible.`);
        const described=buildDescribeResult(result.rows);
        columns=described.columns;
        rows=described.rows;
        messages.push(`Object ${describe.owner?`${describe.owner}.`:''}${describe.name} described`);
        continue;
      }
      const executeCommand=payload.sqlplus&&sql.match(/^(?:EXEC|EXECUTE)\s+([\s\S]+)$/i);
      if(executeCommand){
        sql=`BEGIN ${executeCommand[1].replace(/;\s*$/,'')}; END;`;
        failingStatement=sql;
      }
      if(payload.sqlplus){
        const state=getSqlPlusState(session);
        const substituted=substituteSqlPlusVariables(sql,state);
        if(substituted.missing)throw new Error(`SP2-0552: substitution variable ${substituted.missing} is not defined. Use DEFINE first.`);
        sql=substituted.sql;
        failingStatement=sql;
        state.buffer=sql;
        state.currentLine=sql.split(/\r?\n/).length;
      }
      const isPlsql=/^(DECLARE|BEGIN|CREATE(?:\s+OR\s+REPLACE)?\s+(?:EDITIONABLE\s+)?(?:PROCEDURE|FUNCTION|PACKAGE|TRIGGER|TYPE)\b)/i.test(sql);
      if(isPlsql&&(!payload.sqlplus||getSqlPlusState(session).settings.serveroutput))await connection.execute('BEGIN DBMS_OUTPUT.ENABLE(NULL); END;');
      const result=await connection.execute(sql,getStatementBindValues(sql,payload.binds),{outFormat:oracledb.OUT_FORMAT_OBJECT,maxRows:MAX_ROWS,autoCommit:payload.sqlplus?getSqlPlusState(session).settings.autocommit:true});
      const resultColumns=(result.metaData||[]).map(column=>({name:column.name,type:column.dbType?.name||column.dbTypeName||''}));
      if(resultColumns.length){columns=resultColumns;rows=(result.rows||[]).map(row=>columns.map(column=>{const value=row[column.name];if(value instanceof Date)return value.getFullYear()+'-'+String(value.getMonth()+1).padStart(2,'0')+'-'+String(value.getDate()).padStart(2,'0')+'T'+String(value.getHours()).padStart(2,'0')+':'+String(value.getMinutes()).padStart(2,'0')+':'+String(value.getSeconds()).padStart(2,'0')+'.'+String(value.getMilliseconds()).padStart(3,'0');if(typeof value==='bigint')return value.toString();if(Buffer.isBuffer(value))return value.toString('base64');if(value&&typeof value==='object')return String(value);return value}));}
      rowsAffected=result.rowsAffected??rowsAffected;
      messages.push(resultColumns.length?`Query completed (${rows.length} rows)`: `Statement ${messages.length+1} executed`);
      if(isPlsql&&(!payload.sqlplus||getSqlPlusState(session).settings.serveroutput)){let line;let status=0;do{const out=await connection.execute('BEGIN DBMS_OUTPUT.GET_LINE(:line, :status); END;',{line:{dir:oracledb.BIND_OUT,type:oracledb.STRING,maxSize:32767},status:{dir:oracledb.BIND_OUT,type:oracledb.NUMBER}});line=out.outBinds.line;status=out.outBinds.status;if(status===0)outputLines.push(line)}while(status===0)}
      const createdObject=sql.match(/^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:FORCE\s+)?(?:(?:NON)?EDITIONABLE\s+)?(MATERIALIZED\s+VIEW|PACKAGE\s+BODY|TYPE\s+BODY|PROCEDURE|FUNCTION|PACKAGE|TRIGGER|TYPE|VIEW)\s+("(?:""|[^"])+"|[A-Za-z][A-Za-z0-9_$#]*)/i);
      if(createdObject){const objectType=createdObject[1].toUpperCase(),rawName=createdObject[2],objectName=rawName.startsWith('"')?rawName.slice(1,-1).replace(/""/g,'"'):rawName.toUpperCase();const errors=await connection.execute("SELECT line, position, text FROM user_errors WHERE name=:name AND type=:type AND attribute='ERROR' ORDER BY sequence",{name:objectName,type:objectType},{outFormat:oracledb.OUT_FORMAT_OBJECT});if(errors.rows?.length){const compilationErrors=errors.rows.map(row=>({line:row.LINE,column:row.POSITION,message:row.TEXT}));const details=compilationErrors.map(item=>`${item.message} (line ${item.line}, column ${item.column})`).join('\n');return json(res,400,{error:'Oracle compilation failed:\n'+details,compilationErrors,statement:sql});}}
    }
    if(payload.sqlplus)getSqlPlusState(session).lastSqlCode=0;
    const terminalSettings=payload.sqlplus?getSqlPlusState(session).settings:{};
    const output={columns,rows,outputLines,rowsAffected,truncated:columns.length>0&&rows.length===MAX_ROWS,maxRows:MAX_ROWS,durationMs:Date.now()-started,message:messages.join(' · '),statementsExecuted:messages.length,clearTerminal,sqlPlusAutoCommit,sqlPlusPrompt,sqlPlusFeedback:terminalSettings.feedback,sqlPlusHeading:terminalSettings.heading,sqlPlusSpool};
    return json(res,200,output);
  } catch (error) {
    if(payload?.sqlplus)getSqlPlusState(session).lastSqlCode=Number(error?.code)||-1;
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
  if(session?.sqlPlusDisconnected)return json(res,200,{connected:false});
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
