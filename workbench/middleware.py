"""Apply the shared login UI policy to current and future HTML pages."""
from django.http import HttpResponse


class StudentSessionLimitMiddleware:
    """Enforce daily logged-in allowance and a separate inactivity timeout."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.utils import timezone
        from django.contrib.auth import logout
        from django.http import JsonResponse
        from django.shortcuts import redirect
        deadline = None
        if request.user.is_authenticated and request.user.role == "STUDENT":
            from .models import PortalUser
            import secrets
            token = request.session.get('student_session_token')
            if not request.user.active_student_session_token:
                # Only the first pre-existing session may adopt an unclaimed account.
                token = token or secrets.token_hex(32)
                claimed = PortalUser.objects.filter(pk=request.user.pk, active_student_session_token='').update(active_student_session_token=token, active_student_session_key=request.session.session_key or '')
                if claimed:
                    request.session['student_session_token'] = token
                    request.user.active_student_session_token = token
            if not token or token != request.user.active_student_session_token:
                from .views import _lock, _oracle_sessions
                with _lock:
                    state = _oracle_sessions.pop(request.session.session_key, None)
                if state:
                    for kind in ('sqlplus', 'editor'):
                        if state.get(kind):
                            try:
                                state[kind].close()
                            except Exception:
                                pass
                logout(request)
                if request.path.startswith('/api/'):
                    return JsonResponse({'error': 'Your account was signed in elsewhere. Please sign in again.', 'sessionExpired': True, 'next': '/portal/'}, status=401)
                return redirect('/portal/')
            from .models import AttendanceSession
            from .session_policy import daily_allowance
            attendance = AttendanceSession.objects.filter(pk=request.session.get('attendance_session_id'), user=request.user).select_related('lab__class_section', 'class_section').first()
            now = timezone.now()
            last_active = attendance.last_activity_at.timestamp() if attendance else request.session.get('portal_login_at', now.timestamp())
            allowance = daily_allowance(request.user, attendance, now)
            request.student_daily_allowance = allowance
            idle_deadline = last_active + 1800
            restricted = allowance.get('restricted', True)
            deadline = min(idle_deadline, now.timestamp() + allowance['remainingSeconds']) if restricted else now.timestamp() + 86400
            if restricted and (now.timestamp() >= idle_deadline or allowance['remainingSeconds'] <= 0):
                from .views import _lock, _oracle_sessions
                from .models import AttendanceSession, CompilerActivitySlice
                with _lock:
                    state = _oracle_sessions.pop(request.session.session_key, None)
                if state:
                    for key in ("sqlplus", "editor"):
                        if state.get(key):
                            try:
                                state[key].close()
                            except Exception:
                                pass
                ended = timezone.datetime.fromtimestamp(deadline, tz=timezone.get_current_timezone())
                attendance_id = request.session.get("attendance_session_id")
                AttendanceSession.objects.filter(pk=attendance_id, user=request.user, logout_at__isnull=True).update(logout_at=ended, logout_estimated=False, end_reason="session_timeout", compiler_active=False, is_active_now=False)
                if attendance_id:
                    CompilerActivitySlice.objects.filter(attendance_session_id=attendance_id, ended_at__isnull=True).update(ended_at=ended, compiler_active=False, is_active_now=False)
                logout(request)
                if request.path.startswith('/api/'):
                    message = 'Your daily portal allowance is used. Ask your teacher to reset it, or return tomorrow.' if allowance['remainingSeconds'] <= 0 else 'You were inactive for 30 minutes. Please sign in again.'
                    return JsonResponse({"error": message, "sessionExpired": True, "next": "/portal/"}, status=401)
                return redirect('/portal/')
        response = self.get_response(request)
        if deadline and response.status_code == 200 and not response.streaming and 'text/html' in response.get('Content-Type', ''):
            script = b'<script src="/portal/assets/student-session.js?v=20261028-heartbeat-reuse" defer></script>'
            response.content = response.content.replace(b'</body>', script + b'</body>')
            if response.has_header('Content-Length'):
                response['Content-Length'] = str(len(response.content))
        return response


class LoginAutofillPolicyMiddleware:
    script = b'<script src="/portal/assets/login-autofill.js?v=20261019-6" defer></script>'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (request.method == "HEAD" or response.status_code != 200
                or "text/html" not in response.get("Content-Type", "")
                or response.get("Content-Encoding")):
            return response
        # HTML FileResponses are small local templates; other streaming responses
        # (downloads, event streams) never enter this branch.
        if response.streaming:
            if not getattr(response, "file_to_stream", None):
                return response
            original = response
            content = b"".join(original.streaming_content)
            response = HttpResponse(content, status=original.status_code,
                                    headers=dict(original.headers))
            response.cookies = original.cookies
            original.file_to_stream.close()
        content = response.content
        if b"/portal/assets/login-autofill.js" not in content:
            position = content.lower().find(b"</head>")
            if position >= 0:
                response.content = content[:position] + self.script + content[position:]
                if response.has_header("Content-Length"):
                    response["Content-Length"] = str(len(response.content))
                for header in ("ETag", "Content-MD5"):
                    if response.has_header(header):
                        del response[header]
        return response
