import io
import json
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from django.test import SimpleTestCase, RequestFactory
from .native_compiler import compile_run, run_sandbox, runner_status, OUTPUT_LIMIT


class NativeCompilerTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = SimpleNamespace(pk=812, is_authenticated=True)

    def request(self, data):
        request = self.factory.post('/api/compiler/native/run', data, content_type='application/json', HTTP_SEC_FETCH_SITE='same-origin')
        request.user = self.user
        return request

    def test_no_docker_reports_unavailable_instead_of_using_host_gcc(self):
        with patch('workbench.native_compiler.docker_cli', return_value=None):
            self.assertFalse(runner_status(refresh=True)['ready'])
            with self.assertRaisesRegex(RuntimeError, 'Docker'):
                run_sandbox('C', 'int main(void){return 0;}', '')

    def test_api_validates_language_sizes_and_nul(self):
        for data in [{'language': 'PYTHON', 'source': 'test'}, {'language': 'C', 'source': ''},
                     {'language': 'C', 'source': 'a' * 65537}, {'language': 'C', 'source': 'abc\u0000'},
                     {'language': 'C', 'source': 'int main(){}', 'stdin': 'a' * 32769}]:
            with self.subTest(data=str(data)[:60]), patch('workbench.native_compiler.has_access') as access:
                # Use an actual NUL rather than an escaped source-code sequence.
                if data.get('source') == 'abc\u0000':
                    data['source'] = 'abc' + chr(0)
                response = compile_run(self.request(data))
                self.assertEqual(response.status_code, 400)
                access.assert_not_called()

    def test_api_denies_disabled_compiler_and_unavailable_runner(self):
        with patch('workbench.native_compiler.has_access', return_value=False):
            self.assertEqual(compile_run(self.request({'language': 'C', 'source': 'int main(){}'})).status_code, 403)
        with patch('workbench.native_compiler.has_access', return_value=True), patch('workbench.native_compiler.runner_status', return_value={'ready': False, 'message': 'Docker unavailable'}), patch('workbench.native_compiler.run_sandbox') as run:
            self.assertEqual(compile_run(self.request({'language': 'CPP', 'source': 'int main(){}'})).status_code, 503)
            run.assert_not_called()

    def test_api_returns_program_result_and_releases_slot_after_failure(self):
        with patch('workbench.native_compiler.has_access', return_value=True), patch('workbench.native_compiler.runner_status', return_value={'ready': True}), patch('workbench.native_compiler.run_sandbox', side_effect=[RuntimeError('failed'), {'status': 'success', 'stdout': 'hello'}]):
            data = {'language': 'C', 'source': 'int main(){}'}
            self.assertEqual(compile_run(self.request(data)).status_code, 503)
            response = compile_run(self.request(data))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(json.loads(response.content)['stdout'], 'hello')

    def test_docker_command_is_nonroot_limited_and_contains_no_user_source(self):
        process = MagicMock()
        process.stdout = io.BytesIO(b'Hello, world!\n')
        process.stderr = io.BytesIO(b'')
        process.stdin = io.BytesIO()
        process.poll.return_value = 0
        process.returncode = 0
        source = 'int main(void){return 0;} /* ; docker run --privileged */'
        with patch('workbench.native_compiler.docker_cli', return_value=['docker', '--context', 'desktop-linux']), patch('workbench.native_compiler.subprocess.Popen', return_value=process) as launch, patch('workbench.native_compiler.subprocess.run') as cleanup:
            result = run_sandbox('C', source, 'input')
        args = launch.call_args.args[0]
        self.assertIn('--network=none', args)
        self.assertIn('--read-only', args)
        self.assertIn('--cap-drop=ALL', args)
        self.assertIn('--user=65534:65534', args)
        self.assertIn('--memory=256m', args)
        self.assertIn('--pids-limit=64', args)
        self.assertIn('--pull=never', args)
        self.assertNotIn(source, args)
        self.assertNotIn('shell', launch.call_args.kwargs)
        self.assertEqual(result['status'], 'success')
        cleanup.assert_called()

    def test_output_is_bounded(self):
        process = MagicMock()
        process.stdout = io.BytesIO(b'x' * (OUTPUT_LIMIT * 2))
        process.stderr = io.BytesIO(b'')
        process.stdin = io.BytesIO()
        process.poll.return_value = 0
        process.returncode = 0
        with patch('workbench.native_compiler.docker_cli', return_value=['docker']), patch('workbench.native_compiler.subprocess.Popen', return_value=process), patch('workbench.native_compiler.subprocess.run'):
            result = run_sandbox('CPP', 'int main(){}', '')
        self.assertEqual(len(result['stdout'].encode()) + len(result['stderr'].encode()), OUTPUT_LIMIT)
        self.assertTrue(result['outputTruncated'])
