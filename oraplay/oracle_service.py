"""Route stateful compiler requests to one dedicated Oracle-owning process."""

import hmac
import http.client
import json
import os
from urllib.parse import urlsplit


_HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"}
_MAX_REQUEST_BYTES = 64 * 1024 * 1024


def _service_request(path):
    return path.startswith("/api/") or path in {"/oracle-sql/", "/oraclesql/workspace/"}


def _error(start_response, status, message):
    body = message.encode("utf-8")
    start_response(status, [("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", str(len(body))), ("Cache-Control", "no-store")])
    return [body]


def configure_oracle_service(application):
    """Wrap Django WSGI without changing local single-process deployments."""
    mode = os.environ.get("ORACLE_SERVICE_MODE", "").strip().lower()
    endpoint = os.environ.get("ORACLE_SERVICE_URL", "").strip()
    if not mode and not endpoint:
        return application
    token = os.environ.get("ORACLE_SERVICE_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("ORACLE_SERVICE_TOKEN must be at least 32 characters when the Oracle service is enabled.")
    if mode == "owner":
        if endpoint:
            raise RuntimeError("The Oracle owner must not also proxy to an Oracle service.")

        def owner(environ, start_response):
            if not hmac.compare_digest(environ.get("HTTP_X_ORACLE_SERVICE_TOKEN", ""), token):
                return _error(start_response, "403 Forbidden", "Oracle service access denied.")
            if environ.get("PATH_INFO") == "/__oracle_service/health":
                if environ.get("REQUEST_METHOD") != "GET":
                    return _error(start_response, "405 Method Not Allowed", "GET required.")
                start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", "2"), ("Cache-Control", "no-store")])
                return [b"ok"]
            if environ.get("PATH_INFO") == "/__oracle_service/close":
                if environ.get("REQUEST_METHOD") != "POST":
                    return _error(start_response, "405 Method Not Allowed", "POST required.")
                try:
                    length = int(environ.get("CONTENT_LENGTH") or "0")
                    if length < 1 or length > 256:
                        raise ValueError
                    session_key = json.loads(environ["wsgi.input"].read(length)).get("session_key")
                    if not isinstance(session_key, str) or not session_key or len(session_key) > 128:
                        raise ValueError
                except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
                    return _error(start_response, "400 Bad Request", "Invalid session key.")
                from workbench.views import _close_oracle_session, _lock, _oracle_sessions
                with _lock:
                    state = _oracle_sessions.get(session_key)
                if state:
                    _close_oracle_session(state)
                start_response("204 No Content", [("Content-Length", "0"), ("Cache-Control", "no-store")])
                return [b""]
            environ.pop("HTTP_X_ORACLE_SERVICE_TOKEN", None)
            environ["REMOTE_ADDR"] = environ.pop("HTTP_X_ORACLE_CLIENT_IP", "127.0.0.1")
            scheme = environ.pop("HTTP_X_ORACLE_ORIGINAL_SCHEME", "")
            if scheme in ("http", "https"):
                environ["wsgi.url_scheme"] = scheme
                environ["HTTPS"] = "on" if scheme == "https" else "off"
            return application(environ, start_response)

        return owner
    if mode:
        raise RuntimeError("ORACLE_SERVICE_MODE must be 'owner' or unset.")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1") or not parsed.port or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise RuntimeError("ORACLE_SERVICE_URL must be a localhost HTTP address with an explicit port.")
    timeout = max(5, int(os.environ.get("ORACLE_SERVICE_TIMEOUT_SECONDS", "300")))

    def gateway(environ, start_response):
        if not _service_request(environ.get("PATH_INFO", "")):
            return application(environ, start_response)
        try:
            length = int(environ.get("CONTENT_LENGTH") or "0")
        except ValueError:
            return _error(start_response, "400 Bad Request", "Invalid request length.")
        if length < 0 or length > _MAX_REQUEST_BYTES:
            return _error(start_response, "413 Request Entity Too Large", "Oracle service request is too large.")
        body = environ["wsgi.input"].read(length) if length else b""
        if len(body) != length:
            return _error(start_response, "400 Bad Request", "Incomplete request body.")
        headers = {}
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                name = key[5:].replace("_", "-")
                if name.lower() not in _HOP_HEADERS and not name.lower().startswith("x-oracle-"):
                    headers[name] = value
        if environ.get("CONTENT_TYPE"):
            headers["Content-Type"] = environ["CONTENT_TYPE"]
        headers["Content-Length"] = str(length)
        headers["X-Oracle-Service-Token"] = token
        headers["X-Oracle-Client-IP"] = environ.get("REMOTE_ADDR", "127.0.0.1")
        headers["X-Oracle-Original-Scheme"] = environ.get("wsgi.url_scheme", "http")
        path = environ.get("PATH_INFO", "/")
        if environ.get("QUERY_STRING"):
            path += "?" + environ["QUERY_STRING"]
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
        try:
            connection.request(environ.get("REQUEST_METHOD", "GET"), path, body=body, headers=headers)
            response = connection.getresponse()
        except (OSError, http.client.HTTPException):
            connection.close()
            return _error(start_response, "503 Service Unavailable", "Oracle execution service is unavailable.")
        response_headers = [(key, value) for key, value in response.getheaders() if key.lower() not in _HOP_HEADERS]
        start_response(f"{response.status} {response.reason}", response_headers)

        def stream():
            try:
                while chunk := response.read(64 * 1024):
                    yield chunk
            finally:
                response.close()
                connection.close()

        return stream()

    return gateway


def close_remote_oracle_session(session_key):
    """Tell the owner to close a session revoked by a page request on a web worker."""
    endpoint = os.environ.get("ORACLE_SERVICE_URL", "").strip()
    if not endpoint or not session_key:
        return False
    parsed = urlsplit(endpoint)
    body = json.dumps({"session_key": session_key}).encode("utf-8")
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        connection.request("POST", "/__oracle_service/close", body=body,
                           headers={"Content-Type": "application/json", "X-Oracle-Service-Token": os.environ["ORACLE_SERVICE_TOKEN"]})
        response = connection.getresponse()
        response.read()
        return response.status == 204
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()
