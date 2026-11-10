from unittest.mock import patch

from django.test import TestCase, SimpleTestCase, RequestFactory

from .models import PortalUser, OracleConnectionProfile, ProjectExecution
from .project_output import csv_table, describe_table
from .project_runner import project_reports, project_run_detail


class OutputTableTests(SimpleTestCase):
    def test_csv_handles_commas_quotes_multiline_and_empty_cells(self):
        table = csv_table('"NAME","NOTE"\n"Alice, Smith","said ""hi""\nnext line"\n"Bob",\n')
        self.assertEqual(table['columns'], ['NAME', 'NOTE'])
        self.assertEqual(table['rows'], [['Alice, Smith', 'said "hi"\nnext line'], ['Bob', '']])

    def test_null_single_column_rows_are_retained(self):
        self.assertEqual(csv_table('"VALUE"\n\n"x"\n\n')['rows'], [[''], ['x'], ['']])
        self.assertEqual(csv_table('"VALUE"\n')['rowCount'], 0)

    def test_describe_tabs_are_decoded(self):
        output = 'Name\t\t\t\t\t   Null?    Type\n ----------------------------------------- -------- ----------------------------\n DUMMY\t\t\t\t\t\t    VARCHAR2(1)'
        table = describe_table(output)
        self.assertEqual(table['columns'], ['Name', 'Null?', 'Type'])
        self.assertEqual(table['rows'], [['DUMMY', '', 'VARCHAR2(1)']])

    def test_tables_limit_displayed_rows(self):
        table = csv_table('"ID"\n' + '\n'.join(str(i) for i in range(501)))
        self.assertTrue(table['truncated'])
        self.assertEqual(len(table['rows']), 500)


class TeacherProjectReportTests(TestCase):
    def setUp(self):
        self.teacher = PortalUser.objects.create_user(username='report-teacher', role='TEACHER')
        self.student = PortalUser.objects.create_user(username='report-student', role='STUDENT')
        self.other = PortalUser.objects.create_user(username='other-student', role='STUDENT')
        self.profile = OracleConnectionProfile.objects.create(owner=self.teacher, name='Private', username='PRIVATE_DB_USER', connect_string='private/service', password_encrypted='')
        self.run = ProjectExecution.objects.create(owner=self.student, sandbox_connection=self.profile,
            name='employee.sql', source_text='SELECT 1 FROM dual;', statements=['SELECT 1 FROM dual;'],
            status='completed', next_index=1, results=[{'type': 'connection', 'schema': 'SECRET_SCHEMA'},
                {'index': 0, 'status': 'success', 'table': {'columns': ['ID'], 'rows': [['1']]}}])
        self.rf = RequestFactory()

    def request(self, user):
        request = self.rf.get('/api/teacher/project-reports')
        request.user = user
        request.session = {'active_portal_role': user.role}
        return request

    def test_teacher_can_review_only_students_in_scope(self):
        with patch('workbench.views._teacher_student_ids', return_value=[self.student.pk]), patch('workbench.project_runner.same_origin', return_value=True):
            response = project_reports(self.request(self.teacher))
            self.assertContains(response, 'employee.sql')
            detail = project_run_detail(self.request(self.teacher), self.run.pk)
            self.assertContains(detail, 'SELECT 1 FROM dual;')
            self.assertNotContains(detail, 'SECRET_SCHEMA')
            self.assertNotContains(detail, 'PRIVATE_DB_USER')
        with patch('workbench.views._teacher_student_ids', return_value=[]), patch('workbench.project_runner.same_origin', return_value=True):
            self.assertNotContains(project_reports(self.request(self.teacher)), 'employee.sql')
            self.assertEqual(project_run_detail(self.request(self.teacher), self.run.pk).status_code, 404)

    def test_student_cannot_access_report_list_or_another_student_report(self):
        self.assertEqual(project_reports(self.request(self.student)).status_code, 403)
        with patch('workbench.project_runner.same_origin', return_value=True):
            self.assertEqual(project_run_detail(self.request(self.other), self.run.pk).status_code, 404)

    def test_saved_report_remains_after_schema_removal(self):
        from django.utils import timezone
        self.run.schema_removed_at = timezone.now()
        self.run.save(update_fields=['schema_removed_at'])
        with patch('workbench.views._teacher_student_ids', return_value=[self.student.pk]), patch('workbench.project_runner.same_origin', return_value=True):
            self.assertContains(project_run_detail(self.request(self.teacher), self.run.pk), '"rows": [["1"]]')

    def test_older_csv_results_gain_table_and_statement_without_rerunning(self):
        self.run.results = [{'index': 0, 'status': 'success', 'output': '"ID"\n1\n'}]
        self.run.save(update_fields=['results'])
        with patch('workbench.views._teacher_student_ids', return_value=[self.student.pk]), patch('workbench.project_runner.same_origin', return_value=True):
            response = project_run_detail(self.request(self.teacher), self.run.pk)
        self.assertContains(response, 'SELECT 1 FROM dual;')
        self.assertContains(response, '"columns": ["ID"]')
        self.assertIn('no-store', response['Cache-Control'])
