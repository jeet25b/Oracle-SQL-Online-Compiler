from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import ssl
from functools import partial
from pathlib import Path
root=Path(__file__).resolve().parent
server=ThreadingHTTPServer(('127.0.0.1',3032),partial(SimpleHTTPRequestHandler,directory=str(root)))
context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(str(root.parent/'server.crt'),str(root.parent/'server.key'))
server.socket=context.wrap_socket(server.socket,server_side=True)
server.serve_forever()
