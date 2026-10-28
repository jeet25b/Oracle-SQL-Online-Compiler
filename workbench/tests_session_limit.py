import json
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from .models import PortalUser, Course, ClassSection, CompilerLab, Enrollment, TeacherAssignment, AttendanceSession, StudentDailyAccessReset
from .session_policy import daily_allowance


class StudentSessionLimitTests(TestCase):
    def setUp(self):
        self.student = PortalUser.objects.create_user(username='budget-student', role='STUDENT')
        self.teacher = PortalUser.objects.create_user(username='budget-teacher', role='TEACHER')
        course = Course.objects.create(code='BUDGET', name='Budget')
        self.section = ClassSection.objects.create(course=course, name='A')
        Enrollment.objects.create(student=self.student, class_section=self.section)
        TeacherAssignment.objects.create(teacher=self.teacher, class_section=self.section)
        self.now = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)

    def session(self, minutes, closed=False, idle_minutes=0):
        return AttendanceSession.objects.create(user=self.student, login_at=self.now-timedelta(minutes=minutes), last_activity_at=self.now-timedelta(minutes=idle_minutes), last_heartbeat_at=self.now, logout_at=self.now if closed else None)

    def test_logged_in_time_counts_and_overlaps_are_not_doubled(self):
        self.session(60)
        self.session(30)
        self.assertEqual(daily_allowance(self.student, now=self.now)['remainingSeconds'], 3600)

    def test_idle_time_is_capped_at_thirty_minutes(self):
        self.session(120, idle_minutes=120)
        self.assertEqual(daily_allowance(self.student, now=self.now)['usedSeconds'], 1800)

    def test_reset_restores_daily_allowance(self):
        self.session(120)
        self.assertEqual(daily_allowance(self.student, now=self.now)['remainingSeconds'], 0)
        StudentDailyAccessReset.objects.create(student=self.student, day=timezone.localdate(self.now), reset_at=self.now)
        self.assertEqual(daily_allowance(self.student, now=self.now)['remainingSeconds'], 7200)

    def test_lab_override_and_class_inheritance(self):
        lab = CompilerLab.objects.create(class_section=self.section, created_by=self.teacher, name='Lab', daily_session_minutes=180)
        attendance = self.session(60)
        attendance.lab = lab
        self.assertEqual(daily_allowance(self.student, attendance, self.now)['limitMinutes'], 180)
        lab.daily_session_minutes = None
        self.assertEqual(daily_allowance(self.student, attendance, self.now)['limitMinutes'], 120)

    def test_active_student_is_not_logged_out_after_thirty_minutes(self):
        attendance = self.session(45)
        attendance.last_activity_at = timezone.now()
        attendance.login_at = timezone.now()-timedelta(minutes=45)
        attendance.save()
        self.client.force_login(self.student)
        session = self.client.session
        session['attendance_session_id'] = attendance.pk
        session.save()
        self.assertEqual(self.client.get('/api/portal/session-status').status_code, 200)
        attendance.last_activity_at = timezone.now()-timedelta(minutes=31)
        attendance.save()
        self.assertEqual(self.client.get('/api/portal/session-status').status_code, 401)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_teacher_can_save_and_reset_only_assigned_class(self):
        self.client.force_login(self.teacher)
        headers = {'HTTP_ORIGIN': 'https://testserver', 'secure': True}
        response = self.client.post('/api/teacher/session-limits', data=json.dumps({'action':'save', 'classId': self.section.pk, 'limitMinutes': 180}), content_type='application/json', **headers)
        self.assertEqual(response.status_code, 200)
        response = self.client.post('/api/teacher/session-limits', data=json.dumps({'action':'reset', 'classId': self.section.pk}), content_type='application/json', **headers)
        self.assertEqual(response.json()['resetCount'], 1)
        TeacherAssignment.objects.all().delete()
        response = self.client.post('/api/teacher/session-limits', data=json.dumps({'action':'reset', 'classId': self.section.pk}), content_type='application/json', **headers)
        self.assertEqual(response.status_code, 403)

    def test_exhausted_daily_allowance_blocks_relogin(self):
        self.student.set_password('DailyBudgetExample!45')
        self.student.save()
        now = timezone.now()
        AttendanceSession.objects.create(user=self.student, login_at=now-timedelta(minutes=121), last_activity_at=now, last_heartbeat_at=now, logout_at=now)
        response = self.client.post('/api/portal/login', data=json.dumps({'username': self.student.username, 'password': 'DailyBudgetExample!45'}), content_type='application/json', HTTP_ORIGIN='https://testserver', secure=True)
        self.assertEqual(response.status_code, 403)
        self.assertIn('daily portal allowance', response.json()['error'])

    def test_previous_day_does_not_consume_today(self):
        yesterday = self.now-timedelta(days=1)
        AttendanceSession.objects.create(user=self.student, login_at=yesterday-timedelta(hours=2), logout_at=yesterday, last_activity_at=yesterday)
        self.assertEqual(daily_allowance(self.student, now=self.now)['remainingSeconds'], 7200)

    def test_new_student_login_replaces_previous_browser(self):
        from django.test import Client
        self.student.set_password('SingleSessionExample!45')
        self.student.save()
        first, second = Client(), Client()
        payload = json.dumps({'username': self.student.username, 'password': 'SingleSessionExample!45'})
        headers = {'content_type': 'application/json', 'HTTP_ORIGIN': 'https://testserver', 'secure': True}
        self.assertEqual(first.post('/api/portal/login', data=payload, **headers).status_code, 200)
        old_attendance = first.session['attendance_session_id']
        self.assertEqual(first.get('/api/portal/session-status').status_code, 200)
        self.assertEqual(second.post('/api/portal/login', data=payload, **headers).status_code, 200)
        self.assertEqual(first.get('/api/portal/session-status').status_code, 401)
        self.assertNotIn('_auth_user_id', first.session)
        self.assertEqual(second.get('/api/portal/session-status').status_code, 200)
        self.assertEqual(second.get('/api/portal/session-status').status_code, 200)
        self.assertEqual(AttendanceSession.objects.get(pk=old_attendance).end_reason, 'replaced_login')

    def test_teacher_sessions_are_not_restricted(self):
        from django.test import Client
        first, second = Client(), Client()
        first.force_login(self.teacher)
        second.force_login(self.teacher)
        self.assertEqual(first.get('/api/portal/session-status').status_code, 200)
        self.assertEqual(second.get('/api/portal/session-status').status_code, 200)

    def test_admin_group_has_no_time_limit(self):
        from django.contrib.auth.models import Group
        self.student.groups.add(Group.objects.create(name='Admin'))
        self.assertFalse(daily_allowance(self.student)['restricted'])

    def test_individual_unrestricted_exemption(self):
        self.student.unrestricted_session_time = True
        self.student.save()
        self.assertFalse(daily_allowance(self.student)['restricted'])

    def test_admin_can_disable_sitewide_time_restrictions(self):
        from .models import PortalSetting
        PortalSetting.objects.create(restrict_student_session_time=False)
        attendance = AttendanceSession.objects.create(user=self.student, login_at=timezone.now()-timedelta(hours=4), last_activity_at=timezone.now()-timedelta(hours=2))
        self.client.force_login(self.student)
        session = self.client.session
        session['attendance_session_id'] = attendance.pk
        session.save()
        response = self.client.get('/api/portal/session-status')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['restricted'])
