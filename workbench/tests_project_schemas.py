from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from .models import PortalUser, OracleConnectionProfile, ProjectExecution
from .project_schemas import file_credentials, cleanup_expired_schemas, remove_schema, finish_previous_checks


class PerFileSchemaTests(TestCase):
    def setUp(self):
        self.owner = PortalUser.objects.create_user(username='file-owner', role='TEACHER')
        self.profile = OracleConnectionProfile.objects.create(owner=self.owner, name='Provisioning',
            username='PROJECT_RUNNER', connect_string='host/pdb', password_encrypted='')
        self.broker = MagicMock()
        self.patch = patch('workbench.project_schemas.broker_connection', return_value=self.broker)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def new_run(self):
        return ProjectExecution.objects.create(owner=self.owner, sandbox_connection=self.profile,
            name='same-file.sql', source_text='CREATE TABLE employee (id NUMBER);',
            statements=['CREATE TABLE employee (id NUMBER);'])

    def test_same_user_and_filename_get_distinct_schemas(self):
        first, second = self.new_run(), self.new_run()
        a, b = file_credentials(first), file_credentials(second)
        self.assertNotEqual(a[0], b[0])
        self.assertNotEqual(a[1], b[1])
        self.assertTrue(a[0].startswith('PCHK_'))
        first.refresh_from_db()
        self.assertNotIn(a[1], first.schema_password_encrypted)
        self.assertIsNone(first.schema_removed_at)

    def test_resuming_same_file_reuses_its_schema(self):
        run = self.new_run()
        self.assertEqual(file_credentials(run), file_credentials(run))

    def test_new_file_cancels_previous_check_only_for_same_user(self):
        previous = self.new_run()
        file_credentials(previous)
        other_user = PortalUser.objects.create_user(username='other-file-owner', role='TEACHER')
        other = self.new_run()
        other.owner = other_user
        other.save(update_fields=['owner'])
        file_credentials(other)
        with patch('workbench.project_sqlplus.close_project_session') as close:
            finish_previous_checks(self.owner)
        close.assert_called_once_with(previous.pk)
        previous.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(previous.status, 'cancelled')
        self.assertIsNotNone(previous.schema_removed_at)
        self.assertEqual(other.status, 'pending')
        self.assertIsNone(other.schema_removed_at)

    def test_finished_check_cleanup_leaves_concurrent_check_intact(self):
        first, second = self.new_run(), self.new_run()
        file_credentials(first)
        second_credentials = file_credentials(second)
        first.status = 'completed'
        first.save(update_fields=['status'])
        with patch('workbench.project_sqlplus.close_project_session') as close:
            cleanup_expired_schemas()
        close.assert_called_once_with(first.pk)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNotNone(first.schema_removed_at)
        self.assertEqual(first.schema_password_encrypted, '')
        self.assertIsNone(second.schema_removed_at)
        self.assertEqual(file_credentials(second), second_credentials)

    def test_expired_schema_cannot_be_resumed_and_is_removed(self):
        run = self.new_run()
        file_credentials(run)
        run.schema_expires_at = timezone.now() - timedelta(seconds=1)
        run.save(update_fields=['schema_expires_at'])
        with self.assertRaisesRegex(ValueError, 'expired'):
            file_credentials(run)
        with patch('workbench.project_sqlplus.close_project_session'):
            cleanup_expired_schemas()
        run.refresh_from_db()
        self.assertIsNotNone(run.schema_removed_at)

    def test_cleanup_rejects_existing_shared_schema(self):
        run = self.new_run()
        run.schema_name = 'SQL_PROJECT_RUNNER'
        with self.assertRaisesRegex(ValueError, 'unrecognized'):
            remove_schema(run)
        self.broker.cursor.assert_not_called()

    def test_cleanup_failure_is_saved_for_retry(self):
        run = self.new_run()
        file_credentials(run)
        self.broker.cursor.return_value.__enter__.return_value.callproc.side_effect = RuntimeError('busy')
        self.assertFalse(remove_schema(run))
        run.refresh_from_db()
        self.assertEqual(run.schema_cleanup_error, 'busy')
        self.assertIsNone(run.schema_removed_at)
        self.broker.cursor.return_value.__enter__.return_value.callproc.side_effect = None
        self.assertTrue(remove_schema(run))
