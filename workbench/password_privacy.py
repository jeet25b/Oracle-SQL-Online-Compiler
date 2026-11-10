"""Keep credentials and raw clipboard data out of portal reports."""
import json
import re

HIDDEN = "[hidden for privacy]"
_VALUE = r'(?:"(?:""|[^"\r\n])*"|\'(?:\'\'|[^\'\r\n])*\'|[^\s;]+)'
_PATTERNS = [
    re.compile(r'(\bidentified\s+by\s+)' + _VALUE, re.I),
    re.compile(r'(\b(?:conn|connect)\s+[^\s/]+/)' + _VALUE, re.I),
    re.compile(r'(\b(?:password|passwd|pwd)\s*(?:=|:)\s*)' + _VALUE, re.I),
]


def redact_text(value, bare_token=False):
    for pattern in _PATTERNS:
        value = pattern.sub(lambda match: match.group(1) + HIDDEN, value)
    # A bare, password-like token is not useful SQL and can be an accidental paste.
    if bare_token and re.fullmatch(r'(?=.{8,}$)(?=.*[A-Za-z])(?=.*[0-9])[^\s;]+', value.strip()):
        return HIDDEN
    return value


def redact_payload(value):
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            normalized = key.lower().replace('_', '')
            if normalized in {'password', 'oraclepassword', 'gmailapppassword', 'schemapassword', 'passwordencrypted', 'oraclepasswordencrypted'}:
                output[key] = HIDDEN if item else item
            elif normalized in {'pastedtexts', 'pastetexts'}:
                output[key] = []
            elif value.get('kind') == 'paste' and key == 'preview':
                output[key] = ''
            elif normalized in {'sql', 'statement', 'lastExecutionStatement'.lower(), 'editorText'.lower(), 'sqlplusText'.lower()} and isinstance(item, str):
                output[key] = redact_text(item, bare_token=True)
            else:
                output[key] = redact_payload(item)
        return output
    return redact_text(value) if isinstance(value, str) else value


class PasswordPrivacyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.streaming or response.get('Content-Encoding'):
            return response
        if 'application/json' in response.get('Content-Type', ''):
            try:
                payload = json.loads(response.content)
            except (ValueError, UnicodeDecodeError):
                return response
            response.content = json.dumps(redact_payload(payload), ensure_ascii=False)
            if response.has_header('Content-Length'):
                response['Content-Length'] = str(len(response.content))
            # Reports must not resurrect previously cached clipboard previews.
            response['Cache-Control'] = 'no-store'
        return response
