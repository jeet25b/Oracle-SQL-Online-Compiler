import json
from unittest.mock import patch
from django.test import TestCase,RequestFactory
from django.utils import timezone
from datetime import timedelta
from .models import (PortalUser,Course,ClassSection,Enrollment,SQLTest,SQLTestQuestion,
    Assignment,OracleConnectionProfile,ProjectRunnerConfiguration,ProjectExecution,TestAnswer)
from .assessment_sandbox import assessment_action,assessment_page,executed_test_run


class AssessmentSandboxTests(TestCase):
    def setUp(self):
        self.teacher=PortalUser.objects.create_user(username='sandbox-teacher',role='TEACHER')
        self.student=PortalUser.objects.create_user(username='sandbox-student',role='STUDENT')
        self.other=PortalUser.objects.create_user(username='sandbox-other',role='STUDENT')
        course=Course.objects.create(code='SBX',name='Sandbox course')
        section=ClassSection.objects.create(course=course,name='A')
        Enrollment.objects.create(student=self.student,class_section=section)
        self.test=SQLTest.objects.create(course=course,class_section=section,created_by=self.teacher,
            title='Sandbox test',published=True,sample_schema='CREATE TABLE employee (id NUMBER);')
        self.question=SQLTestQuestion.objects.create(test=self.test,prompt='Select employees',expected_sql='SELECT * FROM employee;')
        self.task=Assignment.objects.create(class_section=section,created_by=self.teacher,title='Daily SQL',published=True)
        self.profile=OracleConnectionProfile.objects.create(owner=self.teacher,name='Sandbox',username='RUNNER',connect_string='host/pdb')
        ProjectRunnerConfiguration.objects.create(pk=1,sandbox_connection=self.profile)
        self.rf=RequestFactory()

    def request(self,data=None,user=None):
        request=self.rf.get('/portal/sandbox/test/') if data is None else self.rf.post('/api/portal/sandbox/test/',data,content_type='application/json')
        request.user=user or self.student
        request.session={'portal_active_role':'STUDENT'}
        return request

    def start(self,kind,item):
        with patch('workbench.assessment_sandbox.same_origin',return_value=True),patch('workbench.project_queue.enqueue'):
            with self.captureOnCommitCallbacks(execute=True):
                response=assessment_action(self.request({'action':'start'}),kind,item.pk)
        self.assertEqual(response.status_code,201,response.content)
        return ProjectExecution.objects.get(pk=json.loads(response.content)['id'])

    def test_sample_setup_executes_only_once_and_refresh_reuses_run(self):
        run=self.start('test',self.test)
        self.assertEqual(run.statements,['CREATE TABLE employee (id NUMBER);'])
        self.assertEqual(run.setup_statement_count,1)
        run.workspace_state={'editorSQL':'SELECT * FROM employee;','questionId':str(self.question.pk)}
        run.save(update_fields=['workspace_state'])
        for _ in range(2):
            response=assessment_page(self.request(),'test',self.test.pk)
            self.assertContains(response,'SELECT * FROM employee;')
        self.assertEqual(ProjectExecution.objects.filter(owner=self.student).count(),1)

    def test_new_daily_task_stops_previous_test_but_keeps_results(self):
        first=self.start('test',self.test)
        first.results=[{'index':0,'status':'success','statement':first.statements[0]}]
        first.status='ready';first.save(update_fields=['results','status'])
        second=self.start('daily_task',self.task)
        first.refresh_from_db()
        self.assertEqual(first.status,'cancelled')
        self.assertTrue(first.results)
        self.assertEqual(second.activity_kind,'daily_task')

    def test_only_enrolled_students_can_start(self):
        with patch('workbench.assessment_sandbox.same_origin',return_value=True):
            response=assessment_action(self.request({'action':'start'},self.other),'test',self.test.pk)
        self.assertEqual(response.status_code,403)

    def test_new_file_stops_existing_daily_task_workspace(self):
        from .project_runner import project_start
        run=self.start('daily_task',self.task)
        run.status='ready';run.save(update_fields=['status'])
        with patch('workbench.project_runner.same_origin',return_value=True),patch('workbench.project_queue.enqueue'):
            with self.captureOnCommitCallbacks(execute=True):
                response=project_start(self.request({'name':'new.sql','source':'SELECT 1 FROM dual;'}))
        self.assertEqual(response.status_code,201)
        run.refresh_from_db()
        self.assertEqual(run.status,'cancelled')

    def test_setup_cannot_be_submitted_as_an_answer(self):
        run=self.start('test',self.test)
        run.results=[{'index':0,'status':'success','statement':run.statements[0]}]
        run.save(update_fields=['results'])
        with patch('workbench.assessment_sandbox.same_origin',return_value=True):
            response=assessment_action(self.request({'action':'save_answer','runId':run.pk,'questionId':self.question.pk,'index':0}),'test',self.test.pk)
        self.assertEqual(response.status_code,400)
        self.assertFalse(TestAnswer.objects.exists())

    def test_submission_requires_actual_execution_in_active_test_schema(self):
        with self.assertRaises(ValueError):executed_test_run(self.student,self.test,['SELECT 1 FROM dual;'])
        run=self.start('test',self.test)
        run.status='ready';run.schema_expires_at=timezone.now()+timedelta(minutes=30)
        run.results=[{'index':1,'status':'success','statement':'SELECT * FROM employee;'}]
        run.save(update_fields=['status','schema_expires_at','results'])
        self.assertEqual(executed_test_run(self.student,self.test,['SELECT * FROM employee;']).pk,run.pk)
        with self.assertRaises(ValueError):executed_test_run(self.student,self.test,['DROP TABLE employee;'])
