"""Regression checks for create-first assessment libraries and scoped mappings."""
from datetime import timedelta
from django.core import signing
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase
from django.utils import timezone
from .assessment_library import MappingForm, create_mappings, library_context, manage_mapping
from .daily_task_workflow import commit_import, stage_import
from .daily_tasks import DailyTaskForm, available_daily_tasks_for_student, _available_tests_for_student, daily_tasks
from .models import (AssessmentDefinition, Assignment, ClassSection, CompilerLab, Course,
                     Enrollment, LabEnrollment, PortalUser, SQLTest, SQLTestQuestion, Submission, TeacherAssignment)
from .portal_views import portal_tests


class AssessmentLibraryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        import uuid
        key = uuid.uuid4().hex[:10]
        cls.teacher = PortalUser.objects.create_user(username="lib-teacher-"+key, role="TEACHER")
        cls.other = PortalUser.objects.create_user(username="lib-other-"+key, role="TEACHER")
        cls.student = PortalUser.objects.create_user(username="lib-student-"+key, role="STUDENT")
        cls.peer = PortalUser.objects.create_user(username="lib-peer-"+key, role="STUDENT")
        cls.course = Course.objects.create(code="LIB-"+key, name="Database systems")
        cls.section = ClassSection.objects.create(course=cls.course, name="B.Tech", branch="CS", semester=3, section="A", group="G1")
        cls.second = ClassSection.objects.create(course=cls.course, name="B.Tech", branch="CS", semester=3, section="B", group="G2")
        cls.lab = CompilerLab.objects.create(class_section=cls.section, created_by=cls.teacher, name="DBMS lab G1")
        for section in (cls.section, cls.second): TeacherAssignment.objects.create(teacher=cls.teacher, class_section=section)
        for student in (cls.student, cls.peer): Enrollment.objects.create(student=student, class_section=cls.section)
        LabEnrollment.objects.create(student=cls.student, lab=cls.lab)

    def request(self, path, data=None, user=None):
        factory = RequestFactory()
        request = factory.post(path, data) if data is not None else factory.get(path)
        request.user = user or self.teacher
        request.session = {"portal_active_role": request.user.role}
        return request

    def entry(self, kind="daily"):
        payload = {"instructions": "Select all employees", "general_instructions": "Practice SQL", "sample_schema": "-- sample", "grading_mode": "manual"}
        if kind == "test": payload.update(questions=[{"prompt": "Select employees", "expected_sql": ""}], total_marks=1, duration_minutes=30)
        return AssessmentDefinition.objects.create(kind=kind, title="SQL practice", created_by=self.teacher, payload=payload)

    def mapping(self, entry, **overrides):
        data = {"class_sections": [self.section.pk], "late_policy": "allow", "frequency": "anytime", **overrides}
        form = MappingForm(data, user=self.teacher, kind=entry.kind)
        self.assertTrue(form.is_valid(), form.errors)
        return form.cleaned_data

    def test_draft_is_private_and_has_permanent_unique_code(self):
        first, second = self.entry(), self.entry("test")
        self.assertRegex(first.code, r"^DT-\d{4}-\d{6,}$")
        self.assertRegex(second.code, r"^TS-\d{4}-\d{6,}$")
        self.assertNotEqual(first.code, second.code)
        code = first.code
        first.save()
        self.assertEqual(first.code, code)
        self.assertFalse(Assignment.objects.filter(assessment=first).exists())
        with self.assertRaises(IntegrityError), transaction.atomic():
            AssessmentDefinition.objects.create(kind="daily", title="Collision", created_by=self.teacher, code=code)

    def test_daily_creation_needs_no_class_and_commit_creates_only_library_content(self):
        form = DailyTaskForm({"title": "Daily joins", "instructions": "1. Select employees"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn("class_sections", form.fields)
        token, preview = stage_import(self.teacher, form.cleaned_data)
        data = {"stage_token": token, "questions-TOTAL_FORMS": "1", "questions-INITIAL_FORMS": "1", "questions-MIN_NUM_FORMS": "0", "questions-MAX_NUM_FORMS": "200"}
        for key, value in preview.forms[0].initial.items(): data["questions-0-"+key] = "on" if value is True else value
        before = Assignment.objects.count()
        result, error = commit_import(self.request("/portal/daily-tasks/", data), ClassSection.objects.none())
        self.assertEqual(error, "")
        self.assertIsNone(result)
        entry = AssessmentDefinition.objects.get(created_by=self.teacher, title="Daily joins")
        self.assertTrue(entry.code)
        self.assertEqual(Assignment.objects.count(), before)

    def test_test_creation_without_class_is_a_draft(self):
        response = portal_tests(self.request("/portal/tests/", {"title": "Unmapped test", "question": ["Select employees"], "grading_mode": "manual"}))
        self.assertEqual(response.status_code, 302)
        entry = AssessmentDefinition.objects.get(created_by=self.teacher, title="Unmapped test")
        self.assertEqual(entry.kind, "test")
        self.assertFalse(SQLTest.objects.filter(assessment=entry).exists())

    def test_map_one_assessment_to_multiple_classes_without_changing_code(self):
        entry = self.entry()
        create_mappings(entry, self.mapping(entry, class_sections=[self.section.pk, self.second.pk]), self.teacher)
        deliveries = Assignment.objects.filter(assessment=entry)
        self.assertEqual(deliveries.count(), 2)
        self.assertFalse(deliveries.filter(published=True).exists())
        self.assertEqual({delivery.assessment.code for delivery in deliveries}, {entry.code})
        with self.assertRaises(ValueError): create_mappings(entry, self.mapping(entry), self.teacher)
        self.assertEqual(deliveries.count(), 2)

    def test_lab_visibility_for_both_assessment_types(self):
        for kind, Model, visible in (("daily", Assignment, available_daily_tasks_for_student), ("test", SQLTest, _available_tests_for_student)):
            entry = self.entry(kind)
            create_mappings(entry, self.mapping(entry, lab=self.lab.pk, published="on"), self.teacher)
            delivery = Model.objects.get(assessment=entry)
            self.assertIn(delivery.pk, [item.pk for item in visible(self.student)])
            self.assertNotIn(delivery.pk, [item.pk for item in visible(self.peer)])
            if kind == "test": self.assertEqual(delivery.questions.count(), 1)

    def test_inactive_and_unauthorized_classes_and_wrong_lab_rejected(self):
        entry = self.entry()
        form = MappingForm({"class_sections": [self.second.pk], "lab": self.lab.pk, "late_policy": "allow"}, user=self.teacher, kind="daily")
        self.assertFalse(form.is_valid())
        self.assertIn("lab", form.errors)
        foreign = MappingForm({"class_sections": [self.section.pk], "late_policy": "allow"}, user=self.other, kind="daily")
        self.assertFalse(foreign.is_valid())
        self.section.is_active = False
        self.section.save(update_fields=["is_active"])
        self.assertFalse(MappingForm({"class_sections": [self.section.pk], "late_policy": "allow"}, user=self.teacher, kind="daily").is_valid())

    def test_publish_pause_and_preserve_submissions(self):
        entry = self.entry()
        create_mappings(entry, self.mapping(entry), self.teacher)
        delivery = Assignment.objects.get(assessment=entry)
        submission = Submission.objects.create(assignment=delivery, student=self.student, answer="SELECT 1 FROM dual")
        path = f"/portal/assessment-library/{entry.pk}/map/"
        for action, published in (("publish", True), ("pause", False)):
            response = manage_mapping(self.request(path, {"action": action, "mapping_id": delivery.pk}), entry.pk)
            self.assertEqual(response.status_code, 302)
            delivery.refresh_from_db()
            self.assertEqual(delivery.published, published)
            self.assertTrue(Submission.objects.filter(pk=submission.pk, answer="SELECT 1 FROM dual").exists())

    def test_foreign_owner_and_student_cannot_map(self):
        from django.http import Http404
        entry = self.entry()
        path = f"/portal/assessment-library/{entry.pk}/map/"
        with self.assertRaises(Http404): manage_mapping(self.request(path, {}, self.other), entry.pk)
        self.assertEqual(manage_mapping(self.request(path, {}, self.student), entry.pk).status_code, 403)

    def test_schedules_and_search(self):
        entry = self.entry("test")
        bad = MappingForm({"class_sections": [self.section.pk], "frequency": "weekly"}, user=self.teacher, kind="test")
        self.assertFalse(bad.is_valid())
        self.assertIn("weekdays", bad.errors)
        later = timezone.now()+timedelta(days=1)
        create_mappings(entry, self.mapping(entry, start_at=later.isoformat(), published="on"), self.teacher)
        self.assertNotIn(SQLTest.objects.get(assessment=entry).pk, [item.pk for item in _available_tests_for_student(self.student)])
        context = library_context(self.request("/portal/tests/?q="+entry.code), "test")
        self.assertEqual([item.pk for item in context["library_entries"]], [entry.pk])

    def test_pages_render_for_library_create_and_results(self):
        entry = self.entry()
        create_mappings(entry, self.mapping(entry), self.teacher)
        test = self.entry("test")
        create_mappings(test, self.mapping(test), self.teacher)
        for view in ("library", "create", "tasks", "review"):
            response = daily_tasks(self.request("/portal/daily-tasks/?view="+view))
            self.assertEqual(response.status_code, 200)
            if view == "library": self.assertContains(response, entry.code)
        for view in ("library", "create", "mappings", "setup"):
            response = portal_tests(self.request("/portal/tests/?view="+view))
            self.assertEqual(response.status_code, 200)
            if view == "library": self.assertContains(response, test.code)

    def test_internal_grading_runs_are_inaccessible_from_project_endpoints(self):
        from unittest.mock import patch
        from .models import OracleConnectionProfile, ProjectExecution
        from .project_runner import project_resume, project_run_detail
        profile=OracleConnectionProfile.objects.create(owner=self.student, name="grading fixture", username="fixture", connect_string="fixture", password_encrypted="")
        run=ProjectExecution.objects.create(owner=self.student, sandbox_connection=profile, name="Internal grading", source_text="", activity_kind="grading", status="running")
        with patch("workbench.project_runner.same_origin", return_value=True):
            self.assertEqual(project_resume(self.request("/api/project/resume", {}, self.student),run.pk).status_code,404)
            self.assertEqual(project_run_detail(self.request("/api/project/detail", user=self.student),run.pk).status_code,404)
