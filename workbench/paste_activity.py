from datetime import timedelta
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .models import AttendanceSession, CompilerClientActivityEvent, CompilerPasteEvent
from .views import read_json, same_origin, _teacher_student_ids
from .portal_views import _active_portal_role


@login_required(login_url="/portal/")
@require_http_methods(["GET", "POST"])
def paste_activity(request):
    role = _active_portal_role(request)
    if request.method == "POST":
        if not same_origin(request) or role != "STUDENT":
            return JsonResponse({"error": "Only student editor paste events are accepted."}, status=403)
        try:
            data = read_json(request)
        except ValueError:
            return JsonResponse({"error": "Invalid paste event."}, status=400)
        text, source = data.get("text"), data.get("source")
        if not isinstance(text, str) or source not in ("PL/SQL", "SQL*Plus"):
            return JsonResponse({"error": "Invalid paste event."}, status=400)
        if CompilerPasteEvent.objects.filter(student=request.user, created_at__gte=timezone.now()-timedelta(hours=1)).count() >= 500:
            return JsonResponse({"error": "Paste recording limit reached."}, status=429)
        attendance = AttendanceSession.objects.filter(pk=request.session.get("attendance_session_id"), user=request.user).first()
        CompilerPasteEvent.objects.create(student=request.user, attendance_session=attendance, source=source, text=text[:65536], truncated=len(text)>65536 or data.get("truncated") is True)
        return JsonResponse({"ok": True})
    if role not in ("TEACHER", "ADMIN") and not request.user.is_superuser:
        return JsonResponse({"error": "Teacher access required."}, status=403)
    events = CompilerPasteEvent.objects.select_related("student")
    if role == "TEACHER" and not request.user.is_superuser:
        events = events.filter(student_id__in=_teacher_student_ids(request.user))
    student = request.GET.get("student")
    if student:
        if not student.isdigit():
            return JsonResponse({"error": "Invalid student."}, status=400)
        events = events.filter(student_id=student)
    total = events.count()
    return JsonResponse({"total":total, "limit":200, "events":[{"student":event.student.username,"source":event.source,"text":event.text,"truncated":event.truncated,"at":event.created_at.isoformat()} for event in events.order_by("-created_at")[:200]]})


@login_required(login_url="/portal/")
@require_http_methods(["POST"])
def compiler_client_activity_event(request):
    if not same_origin(request) or _active_portal_role(request) != "STUDENT":
        return JsonResponse({"error": "Only students may submit compiler activity events."}, status=403)
    try:
        data = read_json(request)
    except ValueError:
        return JsonResponse({"error": "Invalid activity event."}, status=400)
    kind, source = data.get("kind"), data.get("source")
    if kind not in CompilerClientActivityEvent.Kind.values or source not in ("PL/SQL", "SQL*Plus", "Workspace"):
        return JsonResponse({"error": "Invalid activity event."}, status=400)
    if CompilerClientActivityEvent.objects.filter(student=request.user, created_at__gte=timezone.now()-timedelta(hours=1)).count() >= 1200:
        return JsonResponse({"error": "Activity event limit reached."}, status=429)
    attendance = AttendanceSession.objects.filter(pk=request.session.get("attendance_session_id"), user=request.user).first()
    CompilerClientActivityEvent.objects.create(student=request.user, attendance_session=attendance, source=source, kind=kind)
    return JsonResponse({"ok": True})
