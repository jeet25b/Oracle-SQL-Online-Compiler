import json
import re
from unittest.mock import patch
from django.test import TestCase, RequestFactory
from .models import PortalUser, OracleConnectionProfile, ProjectRunnerConfiguration, ProjectExecution
from .project_runner import project_runner_page, project_start


class ProjectRefreshTests(TestCase):
    def setUp(self):
        self.user = PortalUser.objects.create_user(username='refresh-owner', role='TEACHER')
        self.other = PortalUser.objects.create_user(username='refresh-other', role='TEACHER')
        self.profile = OracleConnectionProfile.objects.create(owner=self.user, name='Sandbox', username='RUNNER', connect_string='host/service')
        ProjectRunnerConfiguration.objects.create(pk=1, sandbox_connection=self.profile)
        self.run = ProjectExecution.objects.create(owner=self.user, sandbox_connection=self.profile,
            name='My project', original_file_name='employee.sql', source_text='SELECT 1 FROM dual;\nSELECT :id FROM dual;',
            statements=['SELECT 1 FROM dual;', 'SELECT :id FROM dual;'], next_index=1, status='paused', slot_reserved=True,
            results=[{'index':0,'status':'success','output':'1'}, {'index':1,'status':'input','inputs':[{'kind':'bind','name':'ID'}]}])
        self.rf = RequestFactory()

    def request(self, path, data=None):
        request = self.rf.get(path) if data is None else self.rf.post(path, data, content_type='application/json')
        request.user = self.user
        request.session = {'active_portal_role':'TEACHER'}
        return request

    def test_refresh_restores_state_without_new_execution(self):
        for _ in range(2):
            response = project_runner_page(self.request('/portal/teacher/project-runs/'))
            script = re.search(r'<script id="initialProjectCheck" type="application/json">(.*?)</script>', response.content.decode(), re.S)
            data = json.loads(script[1])
            self.assertEqual(data['id'], self.run.pk)
            self.assertEqual(data['nextIndex'], 1)
            self.assertEqual(data['fileName'], 'employee.sql')
            self.assertEqual(data['source'], self.run.source_text)
            self.assertEqual(data['results'][-1]['inputs'][0]['name'], 'ID')
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, 'paused')
        self.assertTrue(self.run.slot_reserved)
        self.assertEqual(ProjectExecution.objects.count(), 1)

    def test_refresh_never_restores_another_users_file(self):
        ProjectExecution.objects.create(owner=self.other, sandbox_connection=self.profile,
            name='PRIVATE_FILE.sql', source_text='PRIVATE SOURCE', statements=[])
        response = project_runner_page(self.request('/portal/teacher/project-runs/'))
        self.assertNotIn('PRIVATE SOURCE', response.content.decode())

    def test_submission_queues_without_a_second_browser_request(self):
        with patch('workbench.project_runner.same_origin', return_value=True), patch('workbench.project_queue.enqueue') as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                response = project_start(self.request('/api/teacher/project-runs', {'name':'New file', 'fileName':'new.sql', 'source':'SELECT 2 FROM dual;'}))
        self.assertEqual(response.status_code, 201)
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.args[0].original_file_name, 'new.sql')
