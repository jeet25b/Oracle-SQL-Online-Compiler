import threading
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from oraplay.http_workers import PooledWSGIServer


class WorkerPoolTests(SimpleTestCase):
    def server(self):
        with patch.dict('os.environ', {'SERVER_THREADS': '2'}), patch('wsgiref.simple_server.WSGIServer.__init__', return_value=None):
            server = PooledWSGIServer(None, None)
        server.shutdown_request = Mock()
        server.handle_error = Mock()
        self.addCleanup(server._pool.shutdown, wait=True)
        return server

    def test_concurrency_is_bounded_and_requests_complete(self):
        server = self.server()
        lock = threading.Lock()
        release = threading.Event()
        both_started = threading.Event()
        state = {'active': 0, 'peak': 0}
        def finish(request, address):
            with lock:
                state['active'] += 1
                state['peak'] = max(state['peak'], state['active'])
                if state['active'] == 2:
                    both_started.set()
            release.wait(5)
            with lock:
                state['active'] -= 1
        server.finish_request = finish
        try:
            for _ in range(4):
                server.process_request(Mock(), ('local', 1))
            self.assertTrue(both_started.wait(5))
            self.assertEqual(state['peak'], 2)
        finally:
            release.set()
            server._pool.shutdown(wait=True)
        self.assertEqual(server.shutdown_request.call_count, 4)
        server.handle_error.assert_not_called()

    def test_worker_is_reused_after_error(self):
        server = self.server()
        first_done = threading.Event()
        server.finish_request = Mock(side_effect=RuntimeError('test failure'))
        server.shutdown_request.side_effect = lambda request: first_done.set()
        server.process_request(Mock(), ('local', 1))
        self.assertTrue(first_done.wait(5))
        server.handle_error.assert_called_once()
        server.finish_request = Mock()
        server.process_request(Mock(), ('local', 2))
        server._pool.shutdown(wait=True)
        self.assertEqual(server.shutdown_request.call_count, 2)
