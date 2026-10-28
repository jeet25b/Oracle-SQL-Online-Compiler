from datetime import date, datetime
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from .attendance_daily import daily_attendance


class DailyAttendanceTests(SimpleTestCase):
    def session(self, start, end, seconds=0):
        return SimpleNamespace(
            login_at=timezone.make_aware(datetime.fromisoformat(start)),
            logout_at=timezone.make_aware(datetime.fromisoformat(end)),
            last_heartbeat_at=timezone.make_aware(datetime.fromisoformat(end)),
            active_seconds=seconds, idle_seconds=0, logout_estimated=False,
        )

    def test_separate_days_multiple_logins_and_absence(self):
        sessions = [self.session('2026-10-26T09:00', '2026-10-26T10:00', 3600),
                    self.session('2026-10-26T11:00', '2026-10-26T12:00', 3600),
                    self.session('2026-10-28T09:00', '2026-10-28T09:30', 1800)]
        rows = daily_attendance(sessions, date(2026, 10, 26), date(2026, 10, 28))
        self.assertEqual([row['status'] for row in rows], ['Present', 'Absent', 'Present'])
        self.assertEqual([row['durationSeconds'] for row in rows], [7200, 0, 1800])
        self.assertTrue(rows[0]['firstLogin'].startswith('2026-10-26T09:00'))
        self.assertTrue(rows[0]['lastLogout'].startswith('2026-10-26T12:00'))

    def test_overnight_session_is_split_and_clipped_to_range(self):
        session = self.session('2026-10-26T23:00', '2026-10-27T01:00', 7200)
        rows = daily_attendance([session], date(2026, 10, 26), date(2026, 10, 27))
        self.assertEqual([row['durationSeconds'] for row in rows], [3600, 3600])
        clipped = daily_attendance([session], date(2026, 10, 27), date(2026, 10, 27))
        self.assertEqual(clipped[0]['durationSeconds'], 3600)

    def test_weekends_are_not_marked_absent(self):
        self.assertEqual(daily_attendance([], date(2026, 10, 24), date(2026, 10, 25)), [])
