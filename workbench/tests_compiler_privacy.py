from unittest.mock import patch
from django.test import TestCase
from .models import PortalUser, CompilerPasteEvent, CompilerEditorShare, CompilerQueryHistory


class CompilerPrivacyTests(TestCase):
    def test_buffer_default_permissions_and_validation(self):
        response = self.client.get('/api/compiler/settings', HTTP_SEC_FETCH_SITE='same-origin')
        self.assertEqual(response.json()['bufferEntries'], 5000)
        self.assertFalse(response.json()['canManageDefault'])
        self.assertEqual(self.post('/api/compiler/settings', {'bufferEntries': 10000}).status_code, 403)
        self.client.force_login(self.teacher)
        self.assertEqual(self.post('/api/compiler/settings', {'bufferEntries': 99}).status_code, 400)
        self.assertEqual(self.post('/api/compiler/settings', {'bufferEntries': True}).status_code, 400)
        self.assertEqual(self.post('/api/compiler/settings', {'bufferEntries': 10000}).status_code, 200)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get('/api/compiler/settings', HTTP_SEC_FETCH_SITE='same-origin').json()['bufferEntries'], 10000)

    def setUp(self):
        self.student = PortalUser.objects.create_user(username="paste-student", role="STUDENT")
        self.other = PortalUser.objects.create_user(username="other-student", role="STUDENT")
        self.teacher = PortalUser.objects.create_user(username="paste-teacher", role="TEACHER")
        self.client.force_login(self.student)

    def post(self, url, data):
        return self.client.post(url, data, content_type='application/json', HTTP_SEC_FETCH_SITE='same-origin')

    def test_student_pastes_saved_but_student_cannot_read_teacher_log(self):
        response = self.post('/api/compiler/paste-events', {'source':'SQL*Plus','text':'SELECT 1 FROM DUAL;'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CompilerPasteEvent.objects.get().text, 'SELECT 1 FROM DUAL;')
        self.assertEqual(self.client.get('/api/compiler/paste-events').status_code, 403)

    def test_teacher_scoped_paste_log_and_student_content_limit(self):
        self.post('/api/compiler/paste-events', {'source':'PL/SQL','text':'x'*70000})
        self.assertEqual(len(CompilerPasteEvent.objects.get().text), 65536)
        self.assertTrue(CompilerPasteEvent.objects.get().truncated)
        CompilerPasteEvent.objects.create(student=self.other, source='PL/SQL', text='hidden')
        self.client.force_login(self.teacher)
        with patch('workbench.paste_activity._teacher_student_ids', return_value=[self.student.pk]):
            response = self.client.get('/api/compiler/paste-events')
        self.assertEqual(response.json()['total'], 1)
        self.assertNotIn('hidden', str(response.json()))

    def test_private_revokes_both_editors_and_statement_mode_rejected(self):
        share = CompilerEditorShare.objects.create(student=self.student, access_mode='always', editor_text='plsql code', sqlplus_text='sqlplus code')
        self.assertEqual(self.post('/api/compiler/editor-share', {'mode':'statement'}).status_code, 400)
        self.assertEqual(self.post('/api/compiler/editor-share', {'mode':'private'}).status_code, 200)
        share.refresh_from_db()
        self.assertEqual(share.editor_text, '')
        self.assertEqual(share.sqlplus_text, '')
        self.client.force_login(self.teacher)
        with patch('workbench.views._teacher_student_ids', return_value=[self.student.pk]):
            self.assertEqual(self.client.get(f'/api/compiler/editor-share/{self.student.pk}', HTTP_SEC_FETCH_SITE='same-origin').status_code, 403)

    def test_student_history_omits_edit_metrics(self):
        CompilerQueryHistory.objects.create(student=self.student, source='PL/SQL', sql='SELECT 1 FROM DUAL;')
        response = self.client.get('/api/compiler/history', HTTP_SEC_FETCH_SITE='same-origin')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('typedChars', response.json()['records'][0])

    def test_student_cannot_clear_history(self):
        entry = CompilerQueryHistory.objects.create(student=self.student, source='PL/SQL', sql='SELECT 1 FROM DUAL;')
        response = self.client.delete('/api/compiler/history', HTTP_SEC_FETCH_SITE='same-origin')
        self.assertEqual(response.status_code, 403)
        self.assertTrue(CompilerQueryHistory.objects.filter(pk=entry.pk).exists())

    def test_history_includes_records_beyond_old_thousand_limit(self):
        CompilerQueryHistory.objects.bulk_create([
            CompilerQueryHistory(student=self.student, source='SQL*Plus' if i % 2 else 'PL/SQL', sql=f'SELECT {i} FROM DUAL;')
            for i in range(1001)
        ])
        response = self.client.get('/api/compiler/history?own=1', HTTP_SEC_FETCH_SITE='same-origin')
        self.assertEqual(len(response.json()['records']), 1001)

    def test_teacher_compact_history_uses_own_executions(self):
        CompilerQueryHistory.objects.create(student=self.student, source='SQL*Plus', sql='SELECT 1 FROM DUAL;')
        CompilerQueryHistory.objects.create(student=self.teacher, source='PL/SQL', sql='SELECT 2 FROM DUAL;')
        self.client.force_login(self.teacher)
        response = self.client.get('/api/compiler/history?own=1', HTTP_SEC_FETCH_SITE='same-origin')
        self.assertEqual([row['sql'] for row in response.json()['records']], ['SELECT 2 FROM DUAL;'])

    def test_bulk_history_clear_is_teacher_only_and_scoped(self):
        def entry(user):
            return CompilerQueryHistory.objects.create(student=user, source='SQL*Plus', sql='DELETE FROM T;')
        own, other = entry(self.student), entry(self.other)
        response = self.client.delete('/api/compiler/history/clear', data='{"scope":"all"}', content_type='application/json', HTTP_SEC_FETCH_SITE='same-origin')
        self.assertEqual(response.status_code, 403)
        self.client.force_login(self.teacher)
        with patch('workbench.views._teacher_student_ids', return_value=[self.student.pk]):
            response = self.client.delete('/api/compiler/history/clear', data='{"scope":"all"}', content_type='application/json', HTTP_SEC_FETCH_SITE='same-origin')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CompilerQueryHistory.objects.filter(pk=own.pk).exists())
        self.assertTrue(CompilerQueryHistory.objects.filter(pk=other.pk).exists())

    def test_class_history_clear_checks_class_scope_and_enrollment(self):
        from .models import Course, ClassSection, Enrollment
        course = Course.objects.create(code='HIST', name='History test')
        section = ClassSection.objects.create(course=course, name='A')
        Enrollment.objects.create(class_section=section, student=self.student)
        own = CompilerQueryHistory.objects.create(student=self.student, source='PL/SQL', sql='SELECT 1 FROM DUAL;')
        other = CompilerQueryHistory.objects.create(student=self.other, source='PL/SQL', sql='SELECT 2 FROM DUAL;')
        self.client.force_login(self.teacher)
        import json
        payload = json.dumps({'scope': 'class', 'classId': section.pk})
        with patch('workbench.views._teacher_student_ids', return_value=[self.student.pk, self.other.pk]), patch('workbench.views._history_classes', return_value=[]):
            response = self.client.delete('/api/compiler/history/clear', data=payload, content_type='application/json', HTTP_SEC_FETCH_SITE='same-origin')
        self.assertEqual(response.status_code, 403)
        with patch('workbench.views._teacher_student_ids', return_value=[self.student.pk, self.other.pk]), patch('workbench.views._history_classes', return_value=[{'id': section.pk, 'name': 'A'}]):
            response = self.client.delete('/api/compiler/history/clear', data=payload, content_type='application/json', HTTP_SEC_FETCH_SITE='same-origin')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CompilerQueryHistory.objects.filter(pk=own.pk).exists())
        self.assertTrue(CompilerQueryHistory.objects.filter(pk=other.pk).exists())
