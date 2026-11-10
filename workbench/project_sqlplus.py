"""Native SQL*Plus sessions for uploaded projects, confined to the shared DB."""
import atexit
import html
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

from .sql_lexer import is_plsql_statement, tokens, bind_names, ordered_bind_names, split_statements
from .project_output import csv_table, describe_table


COMMAND_NAMES = (
    ('ACCEPT', 3), ('APPEND', 1), ('ARCHIVE', 7), ('ATTRIBUTE', 4),
    ('BREAK', 3), ('BTITLE', 3), ('CHANGE', 1), ('CLEAR', 2), ('COLUMN', 3),
    ('COMPUTE', 4), ('CONNECT', 4), ('COPY', 4), ('DEFINE', 3), ('DEL', 3),
    ('DESCRIBE', 4), ('DISCONNECT', 4), ('EDIT', 2), ('EXECUTE', 4),
    ('EXIT', 4), ('GET', 3), ('HELP', 4), ('HISTORY', 4), ('HOST', 2),
    ('INPUT', 1), ('LIST', 1), ('PASSWORD', 5), ('PAUSE', 3), ('PRINT', 3),
    ('PROMPT', 3), ('QUIT', 4), ('RECOVER', 7), ('REMARK', 3),
    ('REPFOOTER', 4), ('REPHEADER', 4), ('RUN', 1), ('SAVE', 3), ('SET', 3),
    ('SHOW', 3), ('SHUTDOWN', 4), ('SPOOL', 3), ('START', 3), ('STARTUP', 7),
    ('STORE', 3), ('TIMING', 4), ('TTITLE', 3), ('UNDEFINE', 5),
    ('VARIABLE', 3), ('WHENEVER', 8), ('XQUERY', 6),
)
CLIENT_COMMAND = re.compile(
    r'^(?:(?:' + '|'.join(sorted({name[:length] for name, minimum in COMMAND_NAMES
                                for length in range(minimum, len(name) + 1)}, key=len, reverse=True))
    + r')\b|[@!?]|/$)', re.I,
)
ERROR = re.compile(r'(?m)^\s*(?:ORA|SP2|PLS|TNS)-\d+:')


class SQLPlusError(ValueError):
    pass


class SQLPlusInputRequired(SQLPlusError):
    def __init__(self, requests):
        super().__init__('Enter the missing variable values to continue this file check.')
        self.requests = requests


def command_text(statement):
    first = next((part for part in tokens(statement) if part['kind'] != 'comment'), None)
    return statement[first['start']:].strip() if first else ''


def validate_sandbox(username, password, dsn):
    # Reuse the compiler's login and effective-privilege checks, including roles.
    from .views import connect_options, connect_info, open_oracle_connection
    options = connect_options({'user': username.strip('"'), 'password': password, 'connectString': dsn})
    connection = None
    try:
        connection = open_oracle_connection(options)
        return connect_info(connection, dsn, username)
    except Exception as exc:
        raise SQLPlusError(str(exc)) from exc
    finally:
        if connection:
            connection.close()


def validate_project_command(text):
    from .views import blocked_admin_command
    text = command_text(text)
    if not text:
        return
    blocked = blocked_admin_command(text)
    if blocked:
        raise SQLPlusError(f'{blocked} is disabled by the workbench security policy.')
    if re.match(r'^(?:ARCHIVE|STARTUP|SHUT\w*|RECOVER)\b', text, re.I):
        raise SQLPlusError('Database instance administration is disabled by the workbench security policy.')
    if re.search(r'(?im)^\s*#?\s*(?:CONN\w*|DISC\w*|PASSW\w*|COPY)\b', text):
        raise SQLPlusError('Project checks use the administrator-configured sandbox. Connection-changing commands are unavailable here.')
    if re.search(r'(?im)^\s*#?\s*SET\s+(?:SQLPRE\w*|CMDS\w*|SQLT\w*|BLO\w*|CON(?:CAT)?|ESC\w*|SCAN)\b', text):
        raise SQLPlusError('SQL*Plus command delimiters and substitution parsing are managed by the project checker.')
    if re.match(r'^[^A-Za-z/@!?]', text) or re.search(r'(?m)^\s*#', text):
        raise SQLPlusError('Use a literal command name; substitution values are supported inside SQL and command arguments.')
    if re.match(r'^(?:ACC\w*|PAU\w*)\b', text, re.I) or re.fullmatch(r'I(?:N(?:P(?:U(?:T)?)?)?)?', text, re.I):
        raise SQLPlusError('Interactive SQL*Plus input is unavailable in uploaded files. Use DEFINE for values and APPEND/CHANGE for buffer edits.')
    if re.match(r'^WHENEVER\b', text, re.I):
        raise SQLPlusError('The project checker pauses on errors and manages retries; WHENEVER cannot override that behavior.')


def metadata_text(output):
    # SQL*Plus HTML mode encodes DEFINE/LIST output. Never interpret it as HTML.
    return html.unescape(output) if output.startswith('<br>') else output


def wire_statement(statement):
    statement = command_text(statement)
    if CLIENT_COMMAND.match(statement) or statement == '/':
        if re.match(r'^(?:VAR\w*|EXEC\w*|DEF\w*|UNDEF\w*|PRINT|DESC\w*)\b', statement, re.I):
            statement = statement.removesuffix(';').rstrip()
        return statement + '\n'
    if is_plsql_statement(statement):
        return statement.rstrip().removesuffix('/').rstrip() + '\n/\n'
    # Put the terminator on its own line so a trailing -- note cannot hide it.
    parts = [part for part in tokens(statement) if part['kind'] != 'comment']
    return statement + ('\n' if parts and parts[-1]['text'] == ';' else '\n;\n')


class SQLPlusSession:
    def __init__(self):
        executable = os.environ.get('PROJECT_SQLPLUS_EXECUTABLE') or shutil.which('sqlplus')
        if not executable:
            raise SQLPlusError('SQL*Plus is required on the portal server. Install it or set PROJECT_SQLPLUS_EXECUTABLE, then restart the portal.')
        self.lock = threading.Lock()
        self.last_used = time.monotonic()
        self.next_index = 0
        self.secret = ''
        self.substitution_inputs = {}
        self.pending_inputs = []
        self.last_table = None
        self.lines = queue.Queue(maxsize=10000)
        self.directory = tempfile.TemporaryDirectory(prefix='portal-project-', ignore_cleanup_errors=True)
        environment = os.environ.copy()
        oracle_home = os.path.dirname(os.path.dirname(os.path.abspath(executable)))
        if not environment.get('ORACLE_HOME') and os.path.isdir(os.path.join(oracle_home, 'sqlplus', 'mesg')):
            environment['ORACLE_HOME'] = oracle_home
        try:
            self.process = subprocess.Popen(
                [executable, '-R', '3', '-L', '-S', '/nolog'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace', bufsize=1,
                cwd=self.directory.name, shell=False,
                env=environment,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except Exception:
            self.directory.cleanup()
            raise
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self._exchange('set echo off\nset verify off\nset sqlprompt ""\nset sqlblanklines on\nset escape off\nset concat .\nset cmdsep off\nset sqlprefix #\nset autocommit on\n')
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    self.lines.put_nowait(line)
                except queue.Full:
                    self.process.kill()
                    break
        finally:
            try:
                self.lines.put_nowait(None)
            except queue.Full:
                pass

    def _exchange(self, source, timeout=5, allow_exit=False, preserve_text=False):
        marker = 'PORTAL_' + uuid.uuid4().hex
        try:
            self.process.stdin.write(source + '\nprompt ' + marker + '\n')
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise SQLPlusError('SQL*Plus session ended. Start a new project check.') from exc
        deadline, output, size = time.monotonic() + timeout, [], 0
        while True:
            try:
                line = self.lines.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                self.close()
                raise SQLPlusError('SQL*Plus timed out or requested interactive input. Define substitution values before use; ACCEPT and PAUSE require an interactive client. Start a new project check.')
            if line is None:
                if allow_exit:
                    break
                text = ''.join(output)[-4000:]
                if self.secret:
                    text = text.replace(self.secret, '[redacted]')
                raise SQLPlusError('SQL*Plus session ended. ' + text)
            if line.strip() == marker:
                break
            size += len(line)
            if size > 200000:
                self.close()
                raise SQLPlusError('SQL*Plus output exceeded 200,000 characters. Limit query results and start a new project check.')
            output.append(line)
        text = ''.join(output)
        if not preserve_text:
            text = text.strip()
        if self.secret:
            text = text.replace(self.secret, '[redacted]')
        if ERROR.search(text):
            raise SQLPlusError(text)
        return text

    def connect(self, username, password, dsn):
        if any(ch in value for value in (username, password, dsn) for ch in '\r\n\x00'):
            raise SQLPlusError('Sandbox credentials must not contain newlines.')
        validate_sandbox(username, password, dsn)
        # No credentials appear in the process command line or a script file.
        if '"' in password:
            raise SQLPlusError('SQL*Plus project passwords cannot contain double quotes.')
        if re.fullmatch(r'[A-Za-z][A-Za-z0-9_$#]*', username):
            username = '"' + username.upper() + '"'
        elif not re.fullmatch(r'"(?:[^"]|"")+"', username):
            raise SQLPlusError('Enter an Oracle username or an explicitly quoted Oracle identifier.')
        self.secret = password
        return self._exchange(f'set define off\nconnect {username}/"{password}"@{dsn}\nset define on\n')

    def _expand(self, text):
        setting = metadata_text(self._exchange('SHOW DEFINE\n'))
        match = re.search(r'\(hex ([0-9a-f]+)\)', setting, re.I)
        if not match:
            if re.search(r'\bdefine\s+OFF\b', setting, re.I):
                return text, None
            raise SQLPlusError('Unable to verify SQL*Plus substitution settings.')
        character = chr(int(match[1], 16))
        references = re.compile(re.escape(character) + r'{1,2}([A-Za-z0-9_$#]+)\.?')
        missing = []
        def replace(reference):
            name = reference[1]
            if name.upper() in self.substitution_inputs:
                return self.substitution_inputs[name.upper()]
            try:
                value = metadata_text(self._exchange(f'DEFINE {name}\n'))
            except SQLPlusError as exc:
                if 'SP2-0135:' not in str(exc):
                    raise
                if name.upper() not in missing:
                    missing.append(name.upper())
                return reference[0]
            definition = re.search(r'(?m)^DEFINE\s+' + re.escape(name) + r'\s*=\s*"(.*)"\s+\(\w+\)\s*$', value, re.I)
            if not definition:
                raise SQLPlusError('Cannot verify substitution value. Define a single-line value before use.')
            return definition[1]
        expanded = references.sub(replace, text)
        if missing:
            raise SQLPlusInputRequired([{'kind': 'substitution', 'name': name} for name in missing])
        # Supplied data containing '&' is literal, not another input request.
        return expanded, character

    def _missing_binds(self, text):
        if CLIENT_COMMAND.match(text) and not re.match(r'^EXEC\w*\b', text, re.I):
            return []
        names = bind_names(text)
        if not names:
            return []
        try:
            output = metadata_text(self._exchange('VARIABLE\n'))
        except SQLPlusError as exc:
            if 'SP2-0568:' not in str(exc):
                raise
            output = ''
        declared = {name.upper() for name in re.findall(r'(?im)^variable\s+(\w+)', output)}
        return [{'kind': 'bind', 'name': name} for name in ordered_bind_names(text) if name not in declared]

    def provide_inputs(self, values):
        if not isinstance(values, list) or len(values) > 128:
            raise SQLPlusError('Invalid variable input list.')
        allowed = {(item['kind'], item['name']) for item in self.pending_inputs}
        for item in values:
            if not isinstance(item, dict):
                raise SQLPlusError('Invalid variable value.')
            kind, name = item.get('kind'), str(item.get('name', '')).upper()
            if (kind, name) not in allowed or not re.fullmatch(r'[A-Za-z0-9_$#]+', name):
                raise SQLPlusError('Only requested variables can be supplied.')
            value = str(item.get('value', ''))
            if len(value) > 1000 or '\x00' in value:
                raise SQLPlusError('Variable values must be at most 1,000 characters without NUL.')
            if kind == 'substitution':
                if '\n' in value or '\r' in value:
                    raise SQLPlusError('Substitution values must be a single line.')
                self.substitution_inputs[name] = value
                continue
            datatype = item.get('datatype', 'VARCHAR2')
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_$#]*', name):
                raise SQLPlusError('Use a named SQL*Plus bind variable.')
            if datatype == 'NUMBER':
                if value and not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', value):
                    raise SQLPlusError(f'{name} requires a number.')
                literal = value or 'NULL'
            elif datatype == 'VARCHAR2':
                literal = "'" + value.replace("'", "''").replace('\r', "'||CHR(13)||'").replace('\n', "'||CHR(10)||'") + "'"
            else:
                raise SQLPlusError('Unsupported bind variable type.')
            declaration = 'VARCHAR2(4000)' if datatype == 'VARCHAR2' else datatype
            _, character = self._expand('')
            source = f'SET DEFINE OFF\nVARIABLE {name} {declaration}\nEXEC :{name} := {literal}\n'
            if character:
                source += f'SET DEFINE {character}\n'
            self._exchange(source)

    def _buffer(self):
        output = metadata_text(self._exchange('LIST\n'))
        lines = re.findall(r'(?m)^\s*(\d+)[ *] (.*)$', output)
        if not lines or [int(number) for number, _ in lines] != list(range(1, len(lines) + 1)):
            raise SQLPlusError('Cannot verify the SQL buffer. Submit the SQL statement directly.')
        return '\n'.join(line for _, line in lines)

    def execute(self, statement):
        self.last_table = None
        self.last_used = time.monotonic()
        text = command_text(statement)
        validate_project_command(text)
        if re.fullmatch(r'(?:R(?:U(?:N)?)?|/)\s*;?', text, re.I):
            text = self._buffer()
        missing_binds = self._missing_binds(text)
        try:
            expanded, define_character = self._expand(text)
        except SQLPlusInputRequired as exc:
            self.pending_inputs = exc.requests + missing_binds
            raise SQLPlusInputRequired(self.pending_inputs)
        missing_binds = self._missing_binds(expanded) if expanded != text else missing_binds
        if missing_binds:
            self.pending_inputs = missing_binds
            raise SQLPlusInputRequired(missing_binds)
        validate_project_command(command_text(expanded))
        if expanded != text and len(split_statements(expanded, sqlplus=True, client_command=CLIENT_COMMAND)) != 1:
            raise SQLPlusError('Substitution values cannot add extra statements.')
        # Freeze the checked SQL so native substitution cannot change it later.
        source = wire_statement(expanded)
        if define_character:
            source = 'set define off\n' + source + '\nset define ' + define_character + '\n'
        # SET DEFINE itself must retain the setting chosen by the script.
        if re.match(r'^SET\s+DEF\w*\b', expanded, re.I):
            source = wire_statement(expanded)
        exited = bool(re.match(r'^(?:EXIT|QUIT)\b', expanded, re.I))
        tabular = bool(re.match(r'^(?:SELECT|WITH|PRINT)\b', expanded, re.I))
        restore = None
        if tabular:
            settings = metadata_text(self._exchange('SHOW MARKUP\nSHOW HEADING\nSHOW FEEDBACK\n'))
            heading = re.search(r'\bheading (ON|OFF)', settings, re.I)
            feedback = re.search(r'\bFEEDBACK ON for (\d+)', settings, re.I)
            markup = re.search(r'markup CSV (ON|OFF) DELIMITER (\S) QUOTE (ON|OFF)', settings, re.I)
            html_on = bool(re.search(r'markup HTML ON\b', settings, re.I))
            if not heading or not markup:
                raise SQLPlusError('Unable to read report formatting settings.')
            restore = (f'SET HEADING {heading[1]}\nSET FEEDBACK {feedback[1] if feedback else "OFF"}\n'
                       f'SET MARKUP CSV {markup[1]} DELIMITER {markup[2]} QUOTE {markup[3]}\n'
                       f'SET MARKUP HTML {"ON" if html_on else "OFF"}\n')
            self._exchange('SET MARKUP HTML OFF\nSET MARKUP CSV ON DELIMITER , QUOTE ON\nSET HEADING ON\nSET FEEDBACK OFF\n')
        try:
            output = self._exchange(source, allow_exit=exited, preserve_text=tabular)
        finally:
            if restore and self.process.poll() is None:
                self._exchange(restore)
        if tabular:
            self.last_table = csv_table(output)
        elif re.match(r'^DESC\w*\b', expanded, re.I):
            self.last_table = describe_table(metadata_text(output))
        defined = re.match(r'^DEF\w*\s+([A-Za-z0-9_$#]+)\s*=', expanded, re.I)
        if defined:
            self.substitution_inputs.pop(defined[1].upper(), None)
        undefined = re.match(r'^UNDEF\w*\s+(.+)', expanded, re.I)
        if undefined:
            for name in re.findall(r'[A-Za-z0-9_$#]+', undefined[1]):
                self.substitution_inputs.pop(name.upper(), None)
        self.pending_inputs = []
        return output or ('Project script requested exit.' if exited else ''), exited

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
        try:
            self.directory.cleanup()
        except OSError:
            pass


_sessions = {}
_registry_lock = threading.Lock()


def active_file_limit():
    from .models import ProjectRunnerConfiguration
    value = ProjectRunnerConfiguration.objects.filter(pk=1).values_list('max_active_checks', flat=True).first()
    return max(1, min(35, value if value is not None else 30))


def project_session(run):
    """Keep client variables and buffer through an error without replaying DDL."""
    with _registry_lock:
        session = _sessions.get(run.pk)
        if session and session.process.poll() is not None:
            _sessions.pop(run.pk).close()
            session = None
        if session is None:
            if run.next_index:
                raise SQLPlusError('The SQL*Plus session is no longer on this server worker (restart, expiry, or worker change). Start a new project check; successful SQL will not be replayed automatically.')
            limit = active_file_limit()
            if len(_sessions) >= limit:
                raise SQLPlusError(f'The active file-check limit ({limit}) has been reached. Try again after another check finishes.')
            from .project_schemas import file_credentials
            credentials = file_credentials(run)
            session = SQLPlusSession()
            try:
                session.connect(*credentials)
                session.schema_username = credentials[0]
            except Exception:
                session.close()
                raise
            _sessions[run.pk] = session
        return session


def close_project_session(run_id):
    with _registry_lock:
        session = _sessions.pop(run_id, None)
    if session:
        session.close()


def touch_project_session(run_id):
    with _registry_lock:
        session = _sessions.get(run_id)
        if session and session.process.poll() is None:
            session.last_used = time.monotonic()
            return True
    return False


def _reap():
    while True:
        time.sleep(30)
        expired = []
        with _registry_lock:
            for key, session in list(_sessions.items()):
                if time.monotonic() - session.last_used > 1800 and session.lock.acquire(blocking=False):
                    expired.append(_sessions.pop(key))
        for session in expired:
            try:
                session.close()
            finally:
                session.lock.release()
        try:
            from django.db import close_old_connections
            from .project_schemas import cleanup_expired_schemas
            close_old_connections()
            cleanup_expired_schemas()
            close_old_connections()
        except Exception:
            # Persistent leases retain failed cleanup for the next sweep/command.
            import logging
            logging.getLogger(__name__).exception('Project schema cleanup failed')


atexit.register(lambda: [session.close() for session in list(_sessions.values())])
threading.Thread(target=_reap, daemon=True).start()
