from unittest.mock import patch
from django.http import Http404
from django.test import TestCase, RequestFactory
from .assessment_library import create_mappings, MappingForm, delete_assessment
from .models import (AssessmentDefinition, Assignment, AuditEvent, ClassSection, Course,
                     OracleConnectionProfile, PortalUser, ProjectExecution, SQLTest,
                     Submission, TestAttempt, TestAnswer)


class AssessmentDeleteTests(TestCase):
    def setUp(self):
        import uuid
        key=uuid.uuid4().hex[:10]
        self.teacher=PortalUser.objects.create_user(username="delete-teacher-"+key,role="TEACHER")
        self.other=PortalUser.objects.create_user(username="delete-other-"+key,role="TEACHER")
        self.student=PortalUser.objects.create_user(username="delete-student-"+key,role="STUDENT")
        course=Course.objects.create(code="DEL-"+key,name="SQL")
        self.section=ClassSection.objects.create(course=course,name="A")
        from .models import TeacherAssignment
        TeacherAssignment.objects.create(teacher=self.teacher,class_section=self.section)

    def request(self,data=None,user=None):
        request=RequestFactory().get("/delete/") if data is None else RequestFactory().post("/delete/",data)
        request.user=user or self.teacher;request.session={"portal_active_role":request.user.role};return request

    def entry(self,kind="daily"):
        questions=[{"instructions":f"Question {i}","prompt":f"Question {i}","expected_sql":f"SELECT {i} FROM dual;","marks":"1"} for i in (1,2)]
        return AssessmentDefinition.objects.create(kind=kind,title="Delete fixture",created_by=self.teacher,payload={"questions":questions,"grading_mode":"manual","total_marks":"2"})

    def mapped(self,kind="daily"):
        entry=self.entry(kind)
        form=MappingForm({"class_sections":[self.section.pk],"late_policy":"allow","frequency":"anytime"},user=self.teacher,kind=kind)
        self.assertTrue(form.is_valid(),form.errors)
        create_mappings(entry,form.cleaned_data,self.teacher);return entry

    def test_confirmation_get_does_not_delete(self):
        for kind in ("daily","test"):
            entry=self.mapped(kind);response=delete_assessment(self.request(),entry.pk)
            self.assertEqual(response.status_code,200)
            self.assertContains(response,"This cannot be undone")
            self.assertContains(response,'name="confirm_delete"')
            self.assertTrue(AssessmentDefinition.objects.filter(pk=entry.pk).exists())

    def test_explicit_confirmation_required(self):
        entry=self.entry()
        response=delete_assessment(self.request({}),entry.pk)
        self.assertEqual(response.status_code,400)
        self.assertTrue(AssessmentDefinition.objects.filter(pk=entry.pk).exists())

    def test_delete_unmapped_draft_and_alias_codes(self):
        entry=self.entry();code=entry.code
        alias=AssessmentDefinition.objects.create(kind="daily",title="Alias",created_by=self.teacher,parent_task=entry,payload={})
        response=delete_assessment(self.request({"confirm_delete":"yes"}),entry.pk)
        self.assertEqual(response.status_code,302)
        self.assertFalse(AssessmentDefinition.objects.filter(pk__in=[entry.pk,alias.pk]).exists())
        self.assertTrue(AuditEvent.objects.filter(action="assessment_deleted",target=code).exists())

    def test_whole_daily_task_and_results_deleted_unrelated_task_remains(self):
        entry=self.mapped();other=self.mapped()
        deliveries=list(Assignment.objects.filter(assessment=entry))
        submissions=[Submission.objects.create(assignment=delivery,student=self.student,answer="SELECT 1 FROM dual") for delivery in deliveries]
        response=delete_assessment(self.request({"confirm_delete":"yes"}),entry.pk)
        self.assertEqual(response.status_code,302)
        self.assertFalse(Assignment.objects.filter(pk__in=[row.pk for row in deliveries]).exists())
        self.assertFalse(Submission.objects.filter(pk__in=[row.pk for row in submissions]).exists())
        self.assertTrue(AssessmentDefinition.objects.filter(pk=other.pk).exists())
        self.assertEqual(Assignment.objects.filter(assessment=other).count(),2)

    def test_whole_test_attempts_and_answers_deleted(self):
        entry=self.mapped("test");test=SQLTest.objects.get(assessment=entry)
        attempt=TestAttempt.objects.create(test=test,student=self.student)
        answer=TestAnswer.objects.create(attempt=attempt,question=test.questions.first(),answer="SELECT 1 FROM dual")
        question_ids=list(test.questions.values_list("pk",flat=True))
        response=delete_assessment(self.request({"confirm_delete":"yes"}),entry.pk)
        self.assertEqual(response.status_code,302)
        self.assertFalse(SQLTest.objects.filter(pk=test.pk).exists())
        self.assertFalse(TestAttempt.objects.filter(pk=attempt.pk).exists())
        self.assertFalse(TestAnswer.objects.filter(pk=answer.pk).exists())
        from .models import SQLTestQuestion
        self.assertFalse(SQLTestQuestion.objects.filter(pk__in=question_ids).exists())

    def test_other_teacher_student_and_alias_cannot_delete(self):
        entry=self.entry();alias=AssessmentDefinition.objects.create(kind="daily",title="Alias",created_by=self.teacher,parent_task=entry,payload={})
        with self.assertRaises(Http404):delete_assessment(self.request({"confirm_delete":"yes"},self.other),entry.pk)
        self.assertEqual(delete_assessment(self.request({"confirm_delete":"yes"},self.student),entry.pk).status_code,403)
        with self.assertRaises(Http404):delete_assessment(self.request({"confirm_delete":"yes"}),alias.pk)
        self.assertTrue(AssessmentDefinition.objects.filter(pk=entry.pk).exists())

    def test_active_sandboxes_cancelled_and_cleanup_scheduled(self):
        entry=self.mapped();delivery=Assignment.objects.filter(assessment=entry).first()
        profile=OracleConnectionProfile.objects.create(owner=self.teacher,name="cleanup fixture",username="fixture",connect_string="fixture",password_encrypted="")
        run=ProjectExecution.objects.create(owner=self.student,sandbox_connection=profile,name="Fixture",source_text="",activity_kind="daily_task",activity_id=delivery.pk,status="ready",slot_reserved=True,results=[{"statement":"SELECT 1 FROM dual"}],workspace_state={"answers":{"1":"SELECT 1 FROM dual"}})
        with patch("workbench.assessment_library._close_deleted_runs") as cleanup:
            with self.captureOnCommitCallbacks(execute=True):
                response=delete_assessment(self.request({"confirm_delete":"yes"}),entry.pk)
            self.assertEqual(response.status_code,302)
            cleanup.assert_called_once()
        run.refresh_from_db();self.assertEqual(run.status,"cancelled");self.assertFalse(run.slot_reserved);self.assertEqual(run.results,[]);self.assertEqual(run.workspace_state,{})
