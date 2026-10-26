import os
import ssl
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "oraplay.settings")
import django
django.setup()
from django.conf import settings
from django.core.handlers.wsgi import WSGIHandler
from django.contrib.staticfiles.handlers import StaticFilesHandler

class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True

host = os.environ.get("HOST", "127.0.0.1")
port = int(os.environ.get("PORT", "3000"))
key_file = os.environ.get("TLS_KEY_FILE")
cert_file = os.environ.get("TLS_CERT_FILE")
tls_enabled = bool(key_file and cert_file)
if not tls_enabled and host not in ("127.0.0.1", "localhost", "::1"):
    raise SystemExit("Refusing network access without HTTPS. Configure TLS_KEY_FILE and TLS_CERT_FILE, or use HOST=127.0.0.1.")
application = StaticFilesHandler(WSGIHandler())
server = make_server(host, port, application, server_class=ThreadedWSGIServer, handler_class=WSGIRequestHandler)
if tls_enabled:
    key_path, cert_path = settings.BASE_DIR / key_file, settings.BASE_DIR / cert_file
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.base_environ["wsgi.url_scheme"] = "https"
print(f"Django Oracle Learning Workbench listening on {'https' if tls_enabled else 'http'}://{host}:{port}", flush=True)
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
finally:
    server.server_close()
