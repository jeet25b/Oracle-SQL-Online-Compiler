from datetime import datetime, time, timedelta
from django.utils import timezone
from django.db.models import Q
from .models import AttendanceSession, ClassSection, StudentDailyAccessReset, PortalSetting


def session_time_restricted(user):
    if user.is_superuser or 'ADMIN' in user.portal_roles or user.unrestricted_session_time:
        return False
    if user.role != 'STUDENT':
        return False
    if user.groups.filter(Q(name__iexact='admin') | Q(name__iexact='admins') | Q(name__iexact='administrators')).exists():
        return False
    setting = PortalSetting.objects.only('restrict_student_session_time').first()
    return setting.restrict_student_session_time if setting else True


def merged_seconds(intervals):
    total = 0
    end = None
    for start, stop in sorted(intervals):
        start = max(start, end) if end else start
        total += max(0, (stop - start).total_seconds())
        end = max(end, stop) if end else stop
    return int(total)


def daily_allowance(user, attendance=None, now=None):
    now = now or timezone.now()
    day = timezone.localdate(now)
    if not session_time_restricted(user):
        return {'restricted': False, 'limitMinutes': None, 'usedSeconds': 0, 'remainingSeconds': None, 'day': day.isoformat()}
    start = timezone.make_aware(datetime.combine(day, time.min))
    reset = StudentDailyAccessReset.objects.filter(student=user, day=day).only('reset_at').first()
    if reset:
        start = max(start, reset.reset_at)
    if attendance and attendance.lab_id:
        lab = attendance.lab
        minutes = lab.daily_session_minutes if lab.daily_session_minutes is not None else lab.class_section.daily_session_minutes
    elif attendance and attendance.class_section_id:
        minutes = attendance.class_section.daily_session_minutes
    else:
        # With no selected class, the strictest enrolled class allowance applies.
        limits = ClassSection.objects.filter(enrollments__student=user, enrollments__active=True, is_active=True).values_list('daily_session_minutes', flat=True)
        minutes = min(limits, default=120)
    sessions = AttendanceSession.objects.filter(user=user, login_at__lt=now).filter(Q(logout_at__gte=start) | Q(logout_at__isnull=True) | Q(logout_estimated=True, last_activity_at__gte=start - timedelta(minutes=30))).only('login_at', 'logout_at', 'logout_estimated', 'last_activity_at')
    intervals = []
    for session in sessions:
        idle_end = session.last_activity_at + timedelta(minutes=30)
        stop = min(now, idle_end, session.logout_at) if session.logout_at and not session.logout_estimated else min(now, idle_end)
        first = max(start, session.login_at)
        if stop > first:
            intervals.append((first, stop))
    used = merged_seconds(intervals)
    return {'restricted': True, 'limitMinutes': minutes, 'usedSeconds': used, 'remainingSeconds': max(0, minutes * 60 - used), 'day': day.isoformat()}
