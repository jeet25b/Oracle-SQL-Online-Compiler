from unittest.mock import Mock, patch
from django.test import TestCase, SimpleTestCase, RequestFactory
from .models import PortalUser, OracleConnectionProfile, ProjectRunnerConfiguration
from .project_runner import project_runner_capacity
from .project_sqlplus import project_session, SQLPlusError


class CapacitySettingsTests(TestCase):
    def setUp(self):
        self.admin = PortalUser.objects.create_user(username='capacity-admin', role='ADMIN')
        self.teacher = PortalUser.objects.create_user(username='capacity-teacher', role='TEACHER')
        profile = OracleConnectionProfile.objects.create(owner=self.admin, name='Sandbox', username='RUNNER', connect_string='host/service')
        self.config = ProjectRunnerConfiguration.objects.create(pk=1, sandbox_connection=profile)

    def save(self, user, teacher, student):
        request = RequestFactory().post('/api/teacher/project-runs/capacity', {'teacherActiveChecks': teacher, 'studentActiveChecks': student}, content_type='application/json')
        request.user = user
        request.session = {'active_portal_role': user.role}
        with patch('workbench.project_runner.same_origin', return_value=True), patch('workbench.project_queue.start_scheduler'):
            return project_runner_capacity(request)

    def test_admin_can_set_both_boundaries(self):
        self.assertEqual(self.config.max_active_checks, 30)
        for teacher, student in [(1, 0), (5, 30)]:
            self.assertEqual(self.save(self.admin, teacher, student).status_code, 200)
            self.config.refresh_from_db()
            self.assertEqual(self.config.max_active_checks, teacher + student)

    def test_invalid_combined_limits_rejected_and_teachers_can_configure(self):
        for teacher, student in [(0,0), (20,16), (-1,5), (1.5,5), (True,5), ('5',5), (None,5)]:
            self.assertEqual(self.save(self.admin, teacher, student).status_code, 400)
        self.assertEqual(self.save(self.teacher, 5,25).status_code, 200)
        self.config.refresh_from_db()
        self.assertEqual(self.config.max_active_checks, 30)

    def test_regular_teacher_cannot_increase_or_decrease_teacher_limit(self):
        for value in [4, 6]:
            self.assertEqual(self.save(self.teacher, value, 25).status_code, 403)
        self.assertEqual(self.save(self.teacher, 5, 24).status_code, 200)
        self.config.refresh_from_db()
        self.assertEqual(self.config.teacher_active_checks, 5)
        self.assertEqual(self.config.student_active_checks, 24)

    def test_teacher_with_admin_rights_can_change_teacher_limit(self):
        self.admin.additional_roles = ['TEACHER']
        self.admin.save()
        request = RequestFactory().post('/api/teacher/project-runs/capacity', {'teacherActiveChecks': 8, 'studentActiveChecks': 25}, content_type='application/json')
        request.user = self.admin
        request.session = {'active_portal_role': 'TEACHER'}
        with patch('workbench.project_runner.same_origin', return_value=True), patch('workbench.project_queue.start_scheduler'):
            self.assertEqual(project_runner_capacity(request).status_code, 200)
        self.config.refresh_from_db()
        self.assertEqual(self.config.teacher_active_checks, 8)


class CapacityEnforcementTests(SimpleTestCase):
    def test_new_check_is_blocked_at_limit(self):
        run = Mock(pk=3, next_index=0)
        with patch('workbench.project_sqlplus._sessions', {1: Mock(), 2: Mock()}), patch('workbench.project_sqlplus.active_file_limit', return_value=2), patch('workbench.project_sqlplus.SQLPlusSession') as create:
            with self.assertRaisesRegex(SQLPlusError, 'limit \\(2\\)'):
                project_session(run)
            create.assert_not_called()

    def test_lowering_limit_does_not_block_existing_session(self):
        session = Mock()
        session.process.poll.return_value = None
        with patch('workbench.project_sqlplus._sessions', {1: session}), patch('workbench.project_sqlplus.active_file_limit', return_value=1):
            self.assertIs(project_session(Mock(pk=1)), session)
