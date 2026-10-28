"""Date-level attendance derived from saved portal sessions."""
from datetime import datetime, time, timedelta

from django.utils import timezone


def daily_attendance(sessions, start_day, end_day, scheduled_start=None):
    now = timezone.now()
    records = []
    day = start_day
    while day <= end_day:
        start = timezone.make_aware(datetime.combine(day, time.min))
        end = timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min))
        pieces = []
        for session in sessions:
            is_open = session.logout_at is None and (now - session.last_heartbeat_at).total_seconds() <= 180
            session_end = session.logout_at or (now if is_open else session.last_heartbeat_at)
            if session.login_at >= end or session_end < start:
                continue
            first, last = max(start, session.login_at), min(end, session_end)
            elapsed = max(0, (session_end - session.login_at).total_seconds())
            overlap = max(0, (last - first).total_seconds())
            tracked = session.active_seconds + session.idle_seconds
            seconds = int(overlap * min(1, tracked / elapsed)) if tracked and elapsed else int(overlap)
            pieces.append((first, last, seconds, is_open and session_end < end,
                           session.logout_estimated or (session.logout_at is None and not is_open)))
        if pieces or day.weekday() < 5:
            first = min((piece[0] for piece in pieces), default=None)
            last = max(pieces, key=lambda piece: piece[1]) if pieces else None
            arrival = ("late" if timezone.localtime(first).time().replace(tzinfo=None) > scheduled_start else "on_time") if first and scheduled_start else "not_configured"
            records.append({
                "date": day.isoformat(), "status": "Present" if pieces else "Absent",
                "firstLogin": first.isoformat() if first else "",
                "lastLogout": last[1].isoformat() if last else "",
                "openSession": any(piece[3] for piece in pieces),
                "logoutEstimated": bool(last and last[4]),
                "durationSeconds": sum(piece[2] for piece in pieces),
                "arrivalStatus": arrival,
            })
        day += timedelta(days=1)
    return records
