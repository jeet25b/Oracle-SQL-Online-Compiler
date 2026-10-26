"""Oracle quote/comment-aware lexical boundaries for server execution and binds."""
import re


def tokens(source):
    i = 0
    while i < len(source):
        start, ch = i, source[i]
        if ch.isspace():
            i += 1
            continue
        kind = "symbol"
        if source.startswith("--", i):
            end = source.find("\n", i)
            i = len(source) if end < 0 else end
            kind = "comment"
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            i = len(source) if end < 0 else end + 2
            kind = "comment"
        elif ch.lower() == "q" and source[i:i+2].lower() == "q'" and i + 2 < len(source):
            opening = source[i+2]
            closing = {"[": "]", "(": ")", "{": "}", "<": ">"}.get(opening, opening)
            end = source.find(closing + "'", i + 3)
            i = len(source) if end < 0 else end + 2
            kind = "literal"
        elif ch in "\"'":
            kind = "literal" if ch == "'" else "identifier"
            i += 1
            while i < len(source):
                if source[i] == ch:
                    if i + 1 < len(source) and source[i+1] == ch:
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
        else:
            match = re.match(r"[A-Za-z_][A-Za-z0-9_$#]*|[0-9]+", source[i:])
            if match:
                i += len(match[0])
                kind = "word"
            else:
                i += 1
        yield {"kind": kind, "text": source[start:i], "start": start, "end": i}


def split_statements(source, sqlplus=False, client_command=None):
    result, words = [], []
    start, skip_until = 0, 0
    plsql = False

    def emit(end):
        nonlocal start, words, plsql
        part = source[start:end].strip()
        if any(t["kind"] != "comment" for t in tokens(part)):
            result.append(part)
        start, words, plsql = end, [], False

    for token in tokens(source):
        if token["start"] < skip_until or token["kind"] == "comment":
            continue
        line_start = source.rfind("\n", 0, token["start"]) + 1
        line_end = source.find("\n", token["end"])
        if line_end < 0:
            line_end = len(source)
        line = source[line_start:line_end].strip()
        is_client_command = (
            not plsql and sqlplus and client_command and client_command.match(line)
            and not source[line_start:token["start"]].strip()
        )
        if is_client_command:
            # SQL*Plus commands can only start a new buffer. In particular, a
            # multiline UPDATE's SET clause must remain part of its SQL statement.
            prefix_has_sql = any(item["kind"] != "comment" for item in tokens(source[start:line_start]))
        else:
            prefix_has_sql = True
        if is_client_command and not prefix_has_sql:
            if source[start:line_start].strip():
                emit(line_start)
            result.append(line)
            skip_until = line_end
            start, words = line_end, []
            continue
        if token["kind"] == "word" and len(words) < 8:
            words.append(token["text"].upper())
        plsql = bool(re.match(r"^(?:BEGIN|DECLARE)\b|^CREATE (?:OR REPLACE )?(?:(?:NON)?EDITIONABLE )?(?:PROCEDURE|FUNCTION|PACKAGE|TRIGGER|TYPE)\b", " ".join(words)))
        if token["kind"] == "symbol" and token["text"] == "/" and line == "/":
            if source[start:token["start"]].strip():
                emit(token["start"])
            start, words, plsql = token["end"], [], False
        elif token["kind"] == "symbol" and token["text"] == ";" and not plsql:
            emit(token["end"])
    if start < len(source):
        emit(len(source))
    return result


def bind_names(source):
    parts = list(tokens(source))
    return {following["text"].upper() for index, (token, following) in enumerate(zip(parts, parts[1:]))
            if token["kind"] == "symbol" and token["text"] == ":"
            and following["kind"] == "word" and following["start"] == token["end"]
            and (index == 0 or parts[index-1]["text"] != ":")}


def out_bind_names(source):
    """Return bind names used as assignment targets or RETURNING INTO outputs."""
    parts = list(tokens(source))
    names = set()
    for index, (colon, name) in enumerate(zip(parts, parts[1:])):
        if colon["kind"] != "symbol" or colon["text"] != ":" or name["kind"] != "word":
            continue
        after = parts[index + 2:index + 4]
        if len(after) == 2 and after[0]["text"] == ":" and after[1]["text"] == "=":
            names.add(name["text"].upper())
    for index, token in enumerate(parts):
        if token["kind"] != "word" or token["text"].upper() != "RETURNING":
            continue
        for candidate in parts[index + 1:]:
            if candidate["kind"] == "word" and candidate["text"].upper() == "INTO":
                into_index = parts.index(candidate, index + 1)
                if into_index + 2 < len(parts) and parts[into_index + 1]["text"] == ":" and parts[into_index + 2]["kind"] == "word":
                    names.add(parts[into_index + 2]["text"].upper())
                break
            if candidate["kind"] == "symbol" and candidate["text"] == ";":
                break
    return names


def normalize_execution_whitespace(source):
    """Clean only the execution copy, preserving quoted tokens and comments."""
    result, previous, end = [], None, 0
    for token in tokens(source):
        gap = source[end:token['start']]
        if gap:
            # A stray gap before an underscore suffix cannot be an unquoted
            # Oracle alias; repair EMPLOYEE _BACKUP to EMPLOYEE_BACKUP.
            suffix = (previous and previous['kind'] == 'word'
                      and re.fullmatch(r'[A-Za-z][A-Za-z0-9_$#]*', previous['text'])
                      and previous['text'].upper() not in {
                          'SELECT', 'FROM', 'JOIN', 'INTO', 'UPDATE', 'TABLE', 'WHERE',
                          'SET', 'AS', 'AND', 'OR', 'BY', 'ORDER', 'GROUP', 'HAVING',
                          'ON', 'VALUES', 'RETURNING', 'BEGIN', 'END', 'DECLARE',
                          'CREATE', 'ALTER', 'DROP', 'DELETE', 'INSERT', 'MERGE',
                      }
                      and token['kind'] == 'word' and token['text'].startswith('_'))
            if not suffix:
                result.append('\n' if '\n' in gap or '\r' in gap else ' ')
        result.append(token['text'])
        previous, end = token, token['end']
    return ''.join(result).strip()


def is_plsql_statement(source):
    words = [token["text"].upper() for token in tokens(source) if token["kind"] == "word"][:8]
    return bool(re.match(
        r"^(?:BEGIN|DECLARE)\b|^CREATE (?:OR REPLACE )?(?:(?:NON)?EDITIONABLE )?(?:PROCEDURE|FUNCTION|PACKAGE|TRIGGER|TYPE)\b",
        " ".join(words),
    ))


def prepare_statement(source):
    source = normalize_execution_whitespace(source)
    return source.strip() if is_plsql_statement(source) else re.sub(r";\s*$", "", source.strip())
