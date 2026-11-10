"""Run the single Oracle session owner on a private localhost port."""

import os
from wsgiref.simple_server import WSGIRequestHandler, make_server

os.environ.setdefault("ORACLE_SERVICE_MODE", "owner")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "oraplay.settings")

from oraplay.http_workers import PooledWSGIServer
from oraplay.wsgi import application
from workbench.project_queue import start_scheduler


def main():
    if os.environ.get("ORACLE_SERVICE_MODE") != "owner":
        raise RuntimeError("run_oracle_service.py must run in owner mode.")
    port = int(os.environ.get("ORACLE_SERVICE_PORT", "3001"))
    start_scheduler()
    with make_server("127.0.0.1", port, application, server_class=PooledWSGIServer,
                     handler_class=WSGIRequestHandler) as server:
        print(f"Oracle execution service listening on 127.0.0.1:{port}", flush=True)
        try:
            server.serve_forever()
        finally:
            from workbench.views import _close_oracle_session, _lock, _oracle_sessions
            with _lock:
                sessions = list(_oracle_sessions.values())
            for session in sessions:
                _close_oracle_session(session)


if __name__ == "__main__":
    main()
