"""C/C++ execution exclusively inside a resource-limited local Docker container."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from threading import BoundedSemaphore, Lock, Thread, Event
import time
import uuid
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, Http404
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

SOURCE_LIMIT = 65536
INPUT_LIMIT = 32768
OUTPUT_LIMIT = 65536
IMAGE = os.environ.get('C_COMPILER_IMAGE', 'gcc:14.2.0')
_slots = BoundedSemaphore(3)
_guard = Lock()
_active_users = set()
_status_cache = {'at': 0, 'value': None}


def docker_cli():
    found = shutil.which('docker')
    if not found:
        for base in (os.environ.get('ProgramFiles', 'C:/Program Files') + '/Docker/Docker',
                     os.environ.get('LOCALAPPDATA', '') + '/Programs/DockerDesktop'):
            path = Path(base) / 'resources/bin/docker.exe'
            if path.is_file():
                found = str(path)
                break
    if not found:
        return None
    context = os.environ.get('C_COMPILER_DOCKER_CONTEXT', 'desktop-linux' if os.name == 'nt' else 'default')
    return [found, '--context', context]


def process_options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


def runner_status(refresh=False):
    now = time.monotonic()
    with _guard:
        if not refresh and _status_cache['value'] and now - _status_cache['at'] < 15:
            return dict(_status_cache['value'])
    command = docker_cli()
    state = {'ready': False, 'message': 'Local Docker sandbox is not installed on the server.'}
    if command:
        try:
            info = subprocess.run(command + ['info', '--format', '{{.OSType}}'], capture_output=True, timeout=4, **process_options())
            if info.returncode or info.stdout.strip() != b'linux':
                state['message'] = 'Start Docker Desktop with Linux containers to enable C/C++ execution.'
            else:
                image = subprocess.run(command + ['image', 'inspect', IMAGE], capture_output=True, timeout=4, **process_options())
                if image.returncode:
                    state['message'] = 'The local compiler image is not prepared. Ask the administrator to complete C/C++ setup.'
                else:
                    state = {'ready': True, 'message': 'Local C/C++ sandbox ready.'}
        except (OSError, subprocess.TimeoutExpired):
            state['message'] = 'The local Docker sandbox is not responding.'
    with _guard:
        _status_cache.update(at=now, value=state)
    return dict(state)


def run_sandbox(language, source, stdin):
    """Never execute the compiled binary or GCC on the Windows host."""
    options = {'C': ('main.c', 'gcc -std=c17'), 'CPP': ('main.cpp', 'g++ -std=c++20')}
    if language not in options:
        raise ValueError('Choose C or C++.')
    command = docker_cli()
    if not command:
        raise RuntimeError('The local Docker sandbox is not installed.')
    filename, compiler = options[language]
    name = 'portal-cc-' + uuid.uuid4().hex
    started = time.monotonic()
    overflow = Event()
    output = {'stdout': bytearray(), 'stderr': bytearray()}
    total = [0]
    output_lock = Lock()
    process = None
    with tempfile.TemporaryDirectory(prefix='portal-cc-') as directory:
        path = Path(directory) / filename
        path.write_text(source, encoding='utf-8')
        path.chmod(0o644)
        # The only mounted host directory contains this request's source file.
        script = f'{compiler} -O0 -Wall -Wextra -pedantic /source/{filename} -o /work/program || exit 65; timeout --signal=KILL 5s /work/program'
        args = command + ['run', '--rm', '--pull=never', '--name', name, '--interactive',
            '--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--pids-limit=64', '--memory=256m', '--memory-swap=256m', '--cpus=1',
            '--ulimit', 'cpu=10:10', '--ulimit', 'fsize=8388608:8388608',
            '--user=65534:65534', '--workdir=/work',
            '--tmpfs', '/work:rw,exec,nosuid,size=64m,mode=1777',
            '--tmpfs', '/tmp:rw,noexec,nosuid,size=32m,mode=1777',
            '--mount', f'type=bind,source={directory},target=/source,readonly',
            IMAGE, 'sh', '-c', script]
        timed_out = False
        def read_pipe(pipe, key):
            try:
                while True:
                    chunk = pipe.read(4096)
                    if not chunk:
                        break
                    with output_lock:
                        remaining = max(0, OUTPUT_LIMIT - total[0])
                        output[key].extend(chunk[:remaining])
                        total[0] += min(remaining, len(chunk))
                        if len(chunk) > remaining:
                            overflow.set()
            finally:
                pipe.close()
        def feed_input():
            try:
                process.stdin.write(stdin.encode('utf-8'))
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        try:
            process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **process_options())
            readers = [Thread(target=read_pipe, args=(process.stdout, 'stdout'), daemon=True),
                       Thread(target=read_pipe, args=(process.stderr, 'stderr'), daemon=True)]
            for thread in readers:
                thread.start()
            Thread(target=feed_input, daemon=True).start()
            while process.poll() is None:
                if overflow.wait(.05) or time.monotonic() - started > 25:
                    timed_out = not overflow.is_set()
                    try:
                        subprocess.run(command + ['rm', '--force', name], capture_output=True, timeout=5, **process_options())
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                    process.kill()
                    break
            process.wait(timeout=5)
            for thread in readers:
                thread.join(timeout=2)
            exit_code = process.returncode
        finally:
            if process and process.poll() is None:
                process.kill()
            try:
                subprocess.run(command + ['rm', '--force', name], capture_output=True, timeout=5, **process_options())
            except (OSError, subprocess.TimeoutExpired):
                pass
    limited = timed_out or exit_code in (124, 137)
    status = 'output_limit' if overflow.is_set() else 'time_or_resource_limit' if limited else 'compile_error' if exit_code == 65 else 'success' if exit_code == 0 else 'runtime_error'
    return {'status': status, 'stdout': bytes(output['stdout']).decode('utf-8', errors='replace'),
            'stderr': bytes(output['stderr']).decode('utf-8', errors='replace'),
            'exitCode': exit_code, 'durationMs': round((time.monotonic() - started) * 1000),
            'outputTruncated': overflow.is_set(), 'language': language}


def has_access(request, language):
    from .models import CompilerOption
    from .portal_views import _active_portal_role
    if not request.user.is_authenticated or (_active_portal_role(request) not in {'STUDENT', 'TEACHER', 'ADMIN'} and not request.user.is_superuser):
        return False
    return CompilerOption.objects.filter(engine=language, is_active=True).exists()


@login_required(login_url='/portal/')
@ensure_csrf_cookie
@require_GET
def compiler_page(request, language):
    if language not in {'C', 'CPP'}:
        raise Http404
    if not has_access(request, language):
        return JsonResponse({'error': 'This compiler is not available to your account.'}, status=403)
    from .portal_views import _active_portal_role, _available_portal_roles, _dashboard_navigation_items, _global_default_accent
    role = _active_portal_role(request)
    return render(request, 'native-compiler.html', {
        'language': language, 'language_label': 'C++' if language == 'CPP' else 'C',
        'portal_role': role, 'user': request.user, 'is_compiler_page': True,
        'has_multiple_roles': len(_available_portal_roles(request.user)) > 1,
        'dashboard_navigation_items': _dashboard_navigation_items(role), 'global_default_accent': _global_default_accent(),
        'dashboard_home_path': '/teacher' if role == 'TEACHER' else '/portal/student/' if role == 'STUDENT' else '/admin/',
    })


@login_required(login_url='/portal/')
@require_GET
def compiler_status(request):
    return JsonResponse(runner_status())


@login_required(login_url='/portal/')
@require_POST
def compile_run(request):
    from .views import same_origin
    if not same_origin(request):
        return JsonResponse({'error': 'Cross-origin request blocked.'}, status=403)
    if len(request.body) > 128 * 1024:
        return JsonResponse({'error': 'Request is too large.'}, status=413)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            raise ValueError()
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'error': 'Send a valid JSON object.'}, status=400)
    language, source, stdin = data.get('language'), data.get('source'), data.get('stdin', '')
    if language not in {'C', 'CPP'} or not isinstance(source, str) or not source.strip() or not isinstance(stdin, str):
        return JsonResponse({'error': 'Choose C/C++ and enter source code and text input.'}, status=400)
    if len(source.encode('utf-8')) > SOURCE_LIMIT or len(stdin.encode('utf-8')) > INPUT_LIMIT or '\x00' in source:
        return JsonResponse({'error': 'Maximum source size is 64 KB and input size is 32 KB; NUL characters are unsupported.'}, status=400)
    if not has_access(request, language):
        return JsonResponse({'error': 'This compiler is not available to your account.'}, status=403)
    state = runner_status()
    if not state['ready']:
        return JsonResponse({'error': state['message']}, status=503)
    with _guard:
        if request.user.pk in _active_users:
            return JsonResponse({'error': 'Your previous program is still running.'}, status=429)
        if not _slots.acquire(blocking=False):
            return JsonResponse({'error': 'All three compiler slots are busy. Try again shortly.'}, status=429)
        _active_users.add(request.user.pk)
    try:
        return JsonResponse(run_sandbox(language, source, stdin))
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return JsonResponse({'error': 'The local compiler sandbox could not finish. Check Docker and try again.'}, status=503)
    finally:
        with _guard:
            _active_users.discard(request.user.pk)
            _slots.release()
