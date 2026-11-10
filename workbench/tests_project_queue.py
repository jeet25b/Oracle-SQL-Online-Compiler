from unittest.mock import patch
from django.test import TestCase
from .models import PortalUser, OracleConnectionProfile, ProjectRunnerConfiguration, ProjectExecution
from .project_queue import reserve_next, enqueue


class ProjectQueueTests(TestCase):
    def setUp(self):
        self.teacher = PortalUser.objects.create_user(username='queue-teacher', role='TEACHER')
        self.student = PortalUser.objects.create_user(username='queue-student', role='STUDENT')
        self.profile = OracleConnectionProfile.objects.create(owner=self.teacher, name='Queue sandbox', username='RUNNER', connect_string='host/service')
        self.config = ProjectRunnerConfiguration.objects.create(pk=1, sandbox_connection=self.profile,
            teacher_active_checks=1, student_active_checks=1, max_active_checks=2)

    def run_for(self, role, **fields):
        return ProjectExecution.objects.create(owner=self.teacher if role == 'TEACHER' else self.student,
            sandbox_connection=self.profile, name='file.sql', statements=['SELECT 1 FROM dual;'],
            queue_role=role, status='queued', **fields)

    def test_teacher_priority_over_earlier_student(self):
        student = self.run_for('STUDENT')
        teacher = self.run_for('TEACHER')
        self.assertEqual(reserve_next(), teacher.pk)
        self.assertEqual(reserve_next(), student.pk)
        self.assertIsNone(reserve_next())

    def test_group_caps_queue_overflow_and_release_admits_next(self):
        first = self.run_for('STUDENT')
        second = self.run_for('STUDENT')
        self.assertEqual(reserve_next(), first.pk)
        self.assertIsNone(reserve_next())
        first.slot_reserved = False
        first.status = 'completed'
        first.save(update_fields=['slot_reserved', 'status'])
        self.assertEqual(reserve_next(), second.pk)

    def test_fifo_within_each_role_and_no_student_preemption(self):
        first = self.run_for('TEACHER')
        second = self.run_for('TEACHER')
        student = self.run_for('STUDENT')
        self.assertEqual(reserve_next(), first.pk)
        self.assertEqual(reserve_next(), student.pk)
        second.refresh_from_db()
        self.assertEqual(second.status, 'queued')

    def test_lowering_capacity_leaves_active_checks_and_blocks_new(self):
        teacher = self.run_for('TEACHER')
        student = self.run_for('STUDENT')
        reserve_next(); reserve_next()
        self.config.teacher_active_checks = 0
        self.config.student_active_checks = 1
        self.config.save()
        waiting = self.run_for('STUDENT')
        self.assertIsNone(reserve_next())
        self.assertEqual(ProjectExecution.objects.filter(slot_reserved=True).count(), 2)
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, 'queued')

    def test_enqueuing_needs_no_schema_or_http_worker_wait(self):
        run = self.run_for('STUDENT')
        run.status = 'pending'
        run.save(update_fields=['status'])
        with patch('workbench.project_queue.start_scheduler'):
            enqueue(run, {})
        run.refresh_from_db()
        self.assertEqual(run.status, 'queued')
        self.assertFalse(run.slot_reserved)
        self.assertEqual(run.schema_name, '')

    def test_combined_global_cap_is_never_greater_than_35(self):
        self.config.teacher_active_checks = 20
        self.config.student_active_checks = 20
        self.config.save()
        for _ in range(18): self.run_for('TEACHER')
        for _ in range(18): self.run_for('STUDENT')
        for _ in range(35): self.assertIsNotNone(reserve_next())
        self.assertIsNone(reserve_next())
        self.assertEqual(ProjectExecution.objects.filter(slot_reserved=True).count(), 35)
