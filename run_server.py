import os
import re
import ssl
import socket
import subprocess
import sys
import traceback
from wsgiref.simple_server import WSGIRequestHandler, make_server

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "oraplay.settings")
import django
django.setup()
from django.conf import settings
from django.core.handlers.wsgi import WSGIHandler
from oraplay.static_delivery import with_static_files

from oraplay.http_workers import PooledWSGIServer

host = os.environ.get("HOST", "127.0.0.1")
port = int(os.environ.get("PORT", "3000"))
key_file = os.environ.get("TLS_KEY_FILE")
cert_file = os.environ.get("TLS_CERT_FILE")
tls_enabled = bool(key_file and cert_file)
if not tls_enabled and host not in ("127.0.0.1", "localhost", "::1"):
    raise SystemExit("Refusing network access without HTTPS. Configure TLS_KEY_FILE and TLS_CERT_FILE, or use HOST=127.0.0.1.")
application = with_static_files(WSGIHandler())
server = make_server(host, port, application, server_class=PooledWSGIServer, handler_class=WSGIRequestHandler)
if tls_enabled:
    key_path, cert_path = settings.BASE_DIR / key_file, settings.BASE_DIR / cert_file
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    # Handshake inside a timed worker, never in the single accept loop.
    server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    # wsgiref derives wsgi.url_scheme from HTTPS when handling each request.
    # Setting only wsgi.url_scheme here is overwritten with "http".
    server.base_environ["HTTPS"] = "on"
    server.base_environ["wsgi.url_scheme"] = "https"
scheme = 'https' if tls_enabled else 'http'
host_header = host if host not in ('0.0.0.0', '::', '') else os.environ.get('COMPUTERNAME') or socket.gethostname()
probe_host = '127.0.0.1' if host in ('0.0.0.0', '::', '') else host
stop_marker = settings.BASE_DIR / f'.server-monitor-stop-{os.getpid()}'
crash_summary = settings.BASE_DIR / f'.server-crash-{os.getpid()}.summary'
monitor_log = settings.BASE_DIR / 'server-monitor.log'
monitor_path = settings.BASE_DIR / 'server_monitor.py'
recipient = os.environ.get('SERVER_CRASH_REPORT_EMAIL', '').strip()
if recipient and monitor_path.is_file():
    monitor_command = [
        sys.executable, str(monitor_path), '--pid', str(os.getpid()), '--recipient', recipient,
        '--health-url', f'{scheme}://{probe_host}:{port}/api/status',
        '--host-header', f'{host_header}:{port}', '--stop-marker', str(stop_marker),
        '--monitor-log', str(monitor_log), '--crash-summary', str(crash_summary),
    ]
    try:
        creation_flags = (getattr(subprocess, 'DETACHED_PROCESS', 0x00000008)
                          | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x00000200)) if os.name == 'nt' else 0
        startup_info = None
        if os.name == 'nt':
            startup_info = subprocess.STARTUPINFO()
            startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup_info.wShowWindow = 0
        subprocess.Popen(
            monitor_command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True,
            creationflags=creation_flags, startupinfo=startup_info,
            start_new_session=os.name != 'nt',
        )
    except OSError as error:
        print(f'Could not start server outage monitor: {error}', file=sys.stderr, flush=True)
print(f"Django Oracle Learning Workbench listening on {scheme}://{host}:{port}", flush=True)
clean_shutdown = False
try:
    server.serve_forever()
except KeyboardInterrupt:
    clean_shutdown = True
except Exception as error:
    safe_message = re.sub(r'(?i)(password|passwd|token|secret)\s*([:=])\s*([^\s,;]+)', r'\1\2[redacted]', str(error))
    try:
        crash_summary.write_text(f'{type(error).__name__}: {safe_message[:400]}', encoding='utf-8')
    except OSError:
        pass
    traceback.print_exc()
    raise
finally:
    if clean_shutdown:
        try:
            stop_marker.touch(exist_ok=True)
        except OSError:
            pass
    server.server_close()
