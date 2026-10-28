"""Bounded reusable workers for the existing HTTPS listener."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore
from wsgiref.simple_server import WSGIServer


class PooledWSGIServer(WSGIServer):
    request_queue_size = 64

    def __init__(self, *args, **kwargs):
        self.worker_count = max(1, min(32, int(os.environ.get('SERVER_THREADS', '8'))))
        self._slots = BoundedSemaphore(self.worker_count * 2)
        self._pool = ThreadPoolExecutor(max_workers=self.worker_count, thread_name_prefix='portal-http')
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        self._slots.acquire()
        try:
            self._pool.submit(self._process_request, request, client_address)
        except Exception:
            self._slots.release()
            self.shutdown_request(request)
            raise

    def _process_request(self, request, client_address):
        try:
            # Browser preconnections must not occupy the pool for 30 seconds.
            request.settimeout(5)
            self.finish_request(request, client_address)
        except (TimeoutError, ConnectionError):
            # Clients may abandon preconnections without sending an HTTP request.
            pass
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)
            self._slots.release()

    def server_close(self):
        super().server_close()
        self._pool.shutdown(wait=True)
