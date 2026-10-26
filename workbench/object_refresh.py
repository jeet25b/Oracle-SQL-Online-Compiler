"""Conservative metadata invalidation for successfully executed statements."""
from .sql_lexer import tokens


def metadata_refresh(results):
    names = set()
    for result in results:
        if result.get("error"):
            continue
        parts = [t for t in tokens(result["statement"]) if t["kind"] != "comment"]
        words = [t["text"].upper() for t in parts]
        if not words:
            continue
        verb = words[0]
        if verb in {"RENAME", "GRANT", "REVOKE", "BEGIN", "DECLARE", "CALL", "EXEC", "EXECUTE"}:
            return {"full": True, "names": []}
        if verb not in {"CREATE", "ALTER", "DROP", "TRUNCATE"}:
            continue
        i = 1
        for prefix in (("OR", "REPLACE"), ("GLOBAL", "TEMPORARY"), ("PRIVATE", "TEMPORARY")):
            if tuple(words[i:i+len(prefix)]) == prefix:
                i += len(prefix)
        while i < len(words) and words[i] in {"UNIQUE", "BITMAP", "EDITIONABLE", "NONEDITIONABLE", "FORCE"}:
            i += 1
        if words[i:i+2] == ["MATERIALIZED", "VIEW"]:
            i += 1
        if i >= len(words) or words[i] not in {"TABLE", "VIEW", "INDEX", "SEQUENCE", "PROCEDURE", "FUNCTION", "PACKAGE", "TRIGGER", "TYPE", "SYNONYM"}:
            return {"full": True, "names": []}
        kind = words[i]
        if verb == "DROP" and kind in {"TABLE", "TYPE", "PACKAGE"} or "RENAME" in words[i+1:] or verb == "ALTER" and "DROP" in words[i+1:]:
            return {"full": True, "names": []}
        i += 1
        if i < len(words) and words[i] == "BODY":
            i += 1
        if i+1 < len(words) and words[i+1] == ".":
            i += 2
        if i >= len(parts) or parts[i]["kind"] not in {"word", "identifier"}:
            return {"full": True, "names": []}
        token = parts[i]
        names.add(token["text"][1:-1].replace('""', '"') if token["kind"] == "identifier" else token["text"].upper())
    return {"full": len(names) > 64, "names": sorted(names) if len(names) <= 64 else []}
