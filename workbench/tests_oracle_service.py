"""Process boundary checks for the optional Oracle session owner."""

import io
import os
import sys
import threading
import types
import unittest
from unittest.mock import patch

from oraplay.oracle_service import configure_oracle_service


class _Response:
    status = 200
    reason = "OK"

    def __init__(self):
        self._body = io.BytesIO(b'{"connected":true}')

    def getheaders(self):
        return [("Content-Type", "application/json"), ("Set-Cookie", "ora=1"), ("Connection", "close")]

    def read(self, size=-1):
        return self._body.read(size)

    def close(self):
        pass


class _Connection:
    last = None

    def __init__(self, host, port, timeout):
        self.host, self.port, self.timeout = host, port, timeout
        self.request_data = None
        _Connection.last = self

    def request(self, method, path, body=None, headers=None):
        self.request_data = (method, path, body, headers)

    def getresponse(self):
        return _Response()

    def close(self):
        pass


class OracleServiceBoundaryTests(unittest.TestCase):
    def test_gateway_forwards_session_request_and_preserves_response(self):
        called = []
        def local(environ, start_response):
            called.append(environ["PATH_INFO"])
            start_response("200 OK", [])
            return [b"local"]

        with patch.dict(os.environ, {"ORACLE_SERVICE_URL": "http://127.0.0.1:3001",
                                  "ORACLE_SERVICE_TOKEN": "t" * 32}, clear=True), \
             patch("oraplay.oracle_service.http.client.HTTPConnection", _Connection):
            gateway = configure_oracle_service(local)
            result = {}
            def start(status, headers):
                result["status"], result["headers"] = status, headers
            environ = {"PATH_INFO": "/api/execute", "QUERY_STRING": "mode=sqlcl",
                       "REQUEST_METHOD": "POST", "CONTENT_LENGTH": "7", "CONTENT_TYPE": "text/plain",
                       "wsgi.input": io.BytesIO(b"select1"), "wsgi.url_scheme": "https",
                       "REMOTE_ADDR": "192.0.2.5", "HTTP_HOST": "example.test",
                       "HTTP_COOKIE": "sessionid=abc", "HTTP_X_ORACLE_SERVICE_TOKEN": "attacker"}
            self.assertEqual(b"".join(gateway(environ, start)), b'{"connected":true}')
            self.assertEqual(result["status"], "200 OK")
            self.assertIn(("Set-Cookie", "ora=1"), result["headers"])
            self.assertEqual(_Connection.last.request_data[1:3], ("/api/execute?mode=sqlcl", b"select1"))
            headers = _Connection.last.request_data[3]
            self.assertEqual(headers["COOKIE"], "sessionid=abc")
            self.assertEqual(headers["X-Oracle-Service-Token"], "t" * 32)
            self.assertEqual(headers["X-Oracle-Original-Scheme"], "https")
            self.assertEqual(b"".join(gateway({"PATH_INFO": "/static/app.js"}, start)), b"local")
            self.assertEqual(called, ["/static/app.js"])

    def test_owner_rejects_missing_token_before_application(self):
        called = []
        def local(environ, start_response):
            called.append(environ["PATH_INFO"])
            start_response("200 OK", [])
            return [b"owner"]

        with patch.dict(os.environ, {"ORACLE_SERVICE_MODE": "owner", "ORACLE_SERVICE_TOKEN": "t" * 32}, clear=True):
            owner = configure_oracle_service(local)
            result = {}
            def start(status, headers):
                result["status"] = status
            self.assertIn(b"denied", b"".join(owner({"PATH_INFO": "/api/execute"}, start)))
            self.assertEqual(result["status"], "403 Forbidden")
            self.assertEqual(called, [])
            environ = {"PATH_INFO": "/api/execute", "HTTP_X_ORACLE_SERVICE_TOKEN": "t" * 32,
                       "HTTP_X_ORACLE_CLIENT_IP": "192.0.2.5", "HTTP_X_ORACLE_ORIGINAL_SCHEME": "https"}
            self.assertEqual(b"".join(owner(environ, start)), b"owner")
            self.assertEqual(environ["REMOTE_ADDR"], "192.0.2.5")
            self.assertEqual(environ["wsgi.url_scheme"], "https")

    def test_gateway_returns_503_when_owner_is_unavailable(self):
        class UnavailableConnection(_Connection):
            def request(self, *args, **kwargs):
                raise ConnectionRefusedError

        with patch.dict(os.environ, {"ORACLE_SERVICE_URL": "http://127.0.0.1:3001",
                                  "ORACLE_SERVICE_TOKEN": "t" * 32}, clear=True), \
             patch("oraplay.oracle_service.http.client.HTTPConnection", UnavailableConnection):
            gateway = configure_oracle_service(lambda *_: self.fail("The web worker opened a local session"))
            result = {}
            body = b"".join(gateway({"PATH_INFO": "/api/status", "REQUEST_METHOD": "GET",
                                    "wsgi.input": io.BytesIO(b"")},
                                   lambda status, headers: result.update(status=status)))
            self.assertEqual(result["status"], "503 Service Unavailable")
            self.assertIn(b"unavailable", body)

    def test_owner_health_requires_token(self):
        with patch.dict(os.environ, {"ORACLE_SERVICE_MODE": "owner", "ORACLE_SERVICE_TOKEN": "t" * 32}, clear=True):
            owner = configure_oracle_service(lambda *_: self.fail("Health check reached Django"))
            result = {}
            body = b"".join(owner({"PATH_INFO": "/__oracle_service/health", "REQUEST_METHOD": "GET",
                                   "HTTP_X_ORACLE_SERVICE_TOKEN": "t" * 32},
                                  lambda status, headers: result.update(status=status)))
            self.assertEqual(result["status"], "200 OK")
            self.assertEqual(body, b"ok")

    def test_owner_closes_the_requested_session(self):
        state = {"session": "one"}
        sessions = {"session-key": state}
        closed = []
        fake_views = types.ModuleType("workbench.views")
        fake_views._oracle_sessions = sessions
        fake_views._lock = threading.RLock()
        fake_views._close_oracle_session = closed.append
        body = b'{"session_key":"session-key"}'
        with patch.dict(os.environ, {"ORACLE_SERVICE_MODE": "owner", "ORACLE_SERVICE_TOKEN": "t" * 32}, clear=True), \
             patch.dict(sys.modules, {"workbench.views": fake_views}):
            owner = configure_oracle_service(lambda *_: self.fail("Close request reached Django"))
            result = {}
            response = owner({"PATH_INFO": "/__oracle_service/close", "REQUEST_METHOD": "POST",
                              "HTTP_X_ORACLE_SERVICE_TOKEN": "t" * 32,
                              "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)},
                             lambda status, headers: result.update(status=status))
            self.assertEqual(b"".join(response), b"")
            self.assertEqual(result["status"], "204 No Content")
            self.assertEqual(closed, [state])
