from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from django.test import TestCase, RequestFactory
from django.utils import timezone
from .models import AssessmentDefinition, Assignment, ClassSection, Course, PortalUser, Submission, TeacherAssignment
from .assessment_library import create_mappings, library_context, manage_mapping, MappingForm
from .daily_tasks import DailyTaskForm, daily_tasks
from .daily_task_workflow import stage_import, commit_import


class TaskMappingTests(TestCase):
    def setUp(self):
        import uuid
        key=uuid.uuid4().hex[:10]
        self.teacher=PortalUser.objects.create_user(username="bundle-teacher-"+key,role="TEACHER")
        self.student=PortalUser.objects.create_user(username="bundle-student-"+key,role="STUDENT")
        course=Course.objects.create(code="BUNDLE-"+key,name="SQL")
        self.classes=[ClassSection.objects.create(course=course,name="A"),ClassSection.objects.create(course=course,name="B")]
        for section in self.classes:TeacherAssignment.objects.create(teacher=self.teacher,class_section=section)

    def request(self,path,data=None):
        request=RequestFactory().get(path) if data is None else RequestFactory().post(path,data)
        request.user=self.teacher;request.session={"portal_active_role":"TEACHER"};return request

    def bundle(self):
        return AssessmentDefinition.objects.create(kind="daily",title="DQL exercise",created_by=self.teacher,payload={"questions":[{"title":f"Question {index}","instructions":f"Question {index}","expected_sql":f"SELECT {index} FROM dual;","grading_mode":"manual","max_score":"5"} for index in range(1,4)]})

    def data(self,entry):
        form=MappingForm({"class_sections":[section.pk for section in self.classes],"published":"on","late_policy":"allow"},user=self.teacher,kind="daily")
        self.assertTrue(form.is_valid(),form.errors);return form.cleaned_data

    def test_import_is_one_task_one_code_with_multiple_questions(self):
        form=DailyTaskForm({"title":"DQL bundle","instructions":"Q1. Select one\nAnswer: SELECT 1 FROM dual;\nQ2. Select two\nAnswer: SELECT 2 FROM dual;"})
        self.assertTrue(form.is_valid(),form.errors)
        token,preview=stage_import(self.teacher,form.cleaned_data)
        data={"stage_token":token,"questions-TOTAL_FORMS":"2","questions-INITIAL_FORMS":"2"}
        for i,row in enumerate(preview.forms):
            for key,value in row.initial.items():data[f"questions-{i}-{key}"]="on" if value is True else value
        _,error=commit_import(self.request("/portal/daily-tasks/",data),ClassSection.objects.none())
        self.assertEqual(error,"")
        entries=AssessmentDefinition.objects.filter(created_by=self.teacher)
        self.assertEqual(entries.count(),1)
        entry=entries.get();self.assertEqual(entry.title,"DQL bundle");self.assertEqual(len(entry.payload["questions"]),2)
        self.assertTrue(entry.code);self.assertFalse(Assignment.objects.filter(assessment=entry).exists())

    def test_mapping_includes_all_questions_once_per_class(self):
        entry=self.bundle();create_mappings(entry,self.data(entry),self.teacher)
        self.assertEqual(Assignment.objects.filter(assessment=entry).count(),6)
        context=library_context(self.request("/portal/daily-tasks/"),"daily")
        self.assertEqual(len(context["library_entries"]),1)
        item=context["library_entries"][0]
        self.assertEqual(item.question_count,3);self.assertEqual(len(item.deliveries),2)
        self.assertEqual([mapping.question_count for mapping in item.deliveries],[3,3])
        for section in self.classes:self.assertEqual(Assignment.objects.filter(assessment=entry,class_section=section).count(),3)
        with self.assertRaises(ValueError):create_mappings(entry,self.data(entry),self.teacher)

    def test_pause_and_publish_apply_to_whole_task_and_keep_results(self):
        entry=self.bundle();create_mappings(entry,self.data(entry),self.teacher)
        members=Assignment.objects.filter(assessment=entry,class_section=self.classes[0])
        first=members.first();submission=Submission.objects.create(assignment=first,student=self.student,answer="SELECT 1 FROM dual",score=Decimal("5"),is_correct=True)
        for action,published in (("pause",False),("publish",True)):
            response=manage_mapping(self.request("/map/",{"action":action,"mapping_id":first.pk}),entry.pk)
            self.assertEqual(response.status_code,302)
            self.assertEqual(members.filter(published=published).count(),3)
            self.assertEqual(Assignment.objects.filter(assessment=entry,class_section=self.classes[1],published=True).count(),3)
            submission.refresh_from_db();self.assertEqual(submission.score,Decimal("5"))

    def test_schedule_edit_applies_to_task_not_one_question(self):
        entry=self.bundle();create_mappings(entry,self.data(entry),self.teacher)
        first=Assignment.objects.filter(assessment=entry,class_section=self.classes[0]).first()
        due=(timezone.now()+timedelta(days=2)).replace(second=0,microsecond=0)
        response=daily_tasks(self.request("/portal/daily-tasks/",{"action":"configure_schedule","task_id":first.pk,f"schedule-{first.pk}-due_at":due.isoformat(),f"schedule-{first.pk}-late_policy":"allow"}))
        self.assertEqual(response.status_code,302)
        self.assertEqual(Assignment.objects.filter(assessment=entry,class_section=self.classes[0],due_at=due).count(),3)
        self.assertEqual(Assignment.objects.filter(assessment=entry,class_section=self.classes[1],due_at__isnull=True).count(),3)

    def test_old_question_alias_is_not_independently_mappable(self):
        from django.http import Http404
        entry=self.bundle()
        alias=AssessmentDefinition.objects.create(kind="daily",title="Old question code",created_by=self.teacher,parent_task=entry,payload={"instructions":"old question"})
        context=library_context(self.request("/portal/daily-tasks/"),"daily")
        self.assertEqual([item.pk for item in context["library_entries"]],[entry.pk])
        with self.assertRaises(Http404):manage_mapping(self.request("/map/",{}),alias.pk)

    def test_test_mapping_creates_one_test_per_class_with_all_questions(self):
        from .models import SQLTest
        questions=[{"prompt":f"Select {i}","expected_sql":f"SELECT {i} FROM dual;","marks":"5"} for i in range(1,4)]
        entry=AssessmentDefinition.objects.create(kind="test",title="SQL test bundle",created_by=self.teacher,payload={"questions":questions,"grading_mode":"manual","total_marks":"15","email_results_on_submit":True})
        form=MappingForm({"class_sections":[section.pk for section in self.classes],"frequency":"anytime"},user=self.teacher,kind="test")
        self.assertTrue(form.is_valid(),form.errors)
        create_mappings(entry,form.cleaned_data,self.teacher)
        deliveries=SQLTest.objects.filter(assessment=entry)
        self.assertEqual(deliveries.count(),2)
        for delivery in deliveries:
            self.assertEqual(delivery.questions.count(),3)
            self.assertTrue(delivery.email_results_on_submit)
            self.assertEqual(delivery.assessment.code,entry.code)
