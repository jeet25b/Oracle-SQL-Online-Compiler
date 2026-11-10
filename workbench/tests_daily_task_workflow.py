import html
import re
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from .daily_task_workflow import ScheduleForm, split_prompt, stage_import
from .models import AssessmentDefinition, Assignment, ClassSection, CompilerQueryHistory, Course, Enrollment, PortalUser, Submission, TeacherAssignment


class DailyTaskWorkflowTests(TestCase):
    def setUp(self):
        self.teacher = PortalUser.objects.create_user(username="workflow-teacher", role="TEACHER")
        self.other = PortalUser.objects.create_user(username="workflow-other", role="TEACHER")
        self.student = PortalUser.objects.create_user(username="workflow-student", role="STUDENT")
        course = Course.objects.create(code="WORKFLOW", name="SQL")
        self.section = ClassSection.objects.create(course=course, name="Workflow class")
        TeacherAssignment.objects.create(teacher=self.teacher, class_section=self.section)
        Enrollment.objects.create(student=self.student, class_section=self.section)
        self.history = CompilerQueryHistory.objects.create(student=self.student, source="PL/SQL", status="success", sql="SELECT 1 FROM dual")

    def task(self, **kwargs):
        return Assignment.objects.create(class_section=self.section, created_by=self.teacher, title="Workflow task", instructions="Select one row", published=True, **kwargs)

    def login_student(self):
        PortalUser.objects.filter(pk=self.student.pk).update(active_student_session_token="", active_student_session_key="")
        self.client.force_login(self.student)

    def submit(self, task, path="/api/portal/daily-tasks/submit-answer"):
        return self.client.post(path, {"taskId": task.pk, "historyId": self.history.pk}, content_type="application/json")

    def preview(self):
        self.client.force_login(self.teacher)
        response = self.client.post("/portal/daily-tasks/", {
            "action": "preview_import", "class_sections": [self.section.pk], "title": "Practice", "published": "on", "late_policy": "allow",
            "tags": "joins", "hint": "Use EMP", "question_file": SimpleUploadedFile("tasks.txt", b"1. Select employees\nStarter SQL:\nSELECT * FROM emp;\n2. Count departments"),
            "schema_file": SimpleUploadedFile("schema.sql", b"CREATE TABLE emp (id NUMBER);"),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preview questions")
        self.assertEqual(Assignment.objects.count(), 0)
        self.assertContains(response, "CREATE TABLE emp (id NUMBER);")
        self.assertContains(response, "SELECT * FROM emp;")
        token = html.unescape(re.search(r'name="stage_token" value="([^"]+)"', response.content.decode()).group(1))
        return token

    def test_preview_edits_selection_schema_and_starter_are_saved_only_on_confirm(self):
        token = self.preview()
        response = self.client.post("/portal/daily-tasks/", {
            "action": "commit_import", "stage_token": token, "questions-TOTAL_FORMS": "2", "questions-INITIAL_FORMS": "2",
            "questions-0-selected": "on", "questions-0-title": "Edited title", "questions-0-instructions": "Edited instructions",
            "questions-0-general_instructions": "Use SQL syntax and explain your answer.",
            "questions-0-starter_sql": "SELECT * FROM emp;", "questions-0-tags": "joins", "questions-0-hint": "Check EMP",
            "questions-0-sample_schema": "CREATE TABLE emp (id NUMBER);",
            "questions-1-title": "Not selected", "questions-1-instructions": "Skip me",
        })
        self.assertEqual(response.status_code, 302)
        task = AssessmentDefinition.objects.get()
        self.assertEqual(task.title, "Practice")
        self.assertEqual(task.payload["questions"][0]["title"], "Edited title")
        self.assertEqual(task.payload["instructions"], "Edited instructions")
        self.assertEqual(task.payload["tags"], "joins")
        self.assertEqual(task.payload["hint"], "Check EMP")
        self.assertEqual(task.payload["general_instructions"], "Use SQL syntax and explain your answer.")
        self.assertIn("CREATE TABLE", task.payload["sample_schema"])
        self.assertEqual(task.payload["starter_sql"], "SELECT * FROM emp;")
        self.assertFalse(Assignment.objects.exists())

    def test_draft_can_be_saved_without_class_access(self):
        token = self.preview()
        TeacherAssignment.objects.filter(teacher=self.teacher).delete()
        response = self.client.post("/portal/daily-tasks/", {
            "action": "commit_import", "stage_token": token,
            "questions-TOTAL_FORMS": "1", "questions-INITIAL_FORMS": "1",
            "questions-0-selected": "on", "questions-0-title": "Unmapped draft",
            "questions-0-instructions": "Select employees",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AssessmentDefinition.objects.filter(title="Unmapped draft").exists())
        self.assertFalse(Assignment.objects.exists())

    def test_prompt_splitting_is_explicit(self):
        self.assertEqual(split_prompt("Find employees\nStarter SQL:\nSELECT * FROM emp"), ("Find employees", "SELECT * FROM emp"))
        self.assertEqual(split_prompt("Find employees\n```sql\nSELECT * FROM emp\n```"), ("Find employees", "SELECT * FROM emp"))
        self.assertEqual(split_prompt("Explain SELECT and joins"), ("Explain SELECT and joins", ""))

    def test_future_release_hidden_and_rejected_by_both_submission_apis(self):
        task = self.task(start_at=timezone.now() + timedelta(hours=1))
        self.login_student()
        self.assertNotContains(self.client.get("/portal/daily-tasks/"), "Select one row")
        self.assertEqual(self.client.get("/api/portal/daily-tasks/available").json()["tasks"], [])
        for path in ("/api/portal/daily-tasks/submit-answer", "/api/portal/daily-tasks/submit-latest-sqlcl"):
            self.assertEqual(self.submit(task, path).status_code, 404)
        self.assertFalse(Submission.objects.exists())

    def test_locked_deadline_rejects_all_submission_routes(self):
        self.login_student()
        for fields in ({"due_at": timezone.now() - timedelta(minutes=1), "late_policy": "lock"},):
            task = self.task(**fields)
            for path in ("/api/portal/daily-tasks/submit-answer", "/api/portal/daily-tasks/submit-latest-sqlcl"):
                self.assertEqual(self.submit(task, path).status_code, 403)
            response = self.client.post("/portal/daily-tasks/", {"task_id": task.pk, "history_id": self.history.pk})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(Submission.objects.filter(assignment=task).exists())

    def test_late_answer_is_saved_and_tagged(self):
        task = self.task(due_at=timezone.now() - timedelta(minutes=1), late_policy="allow")
        self.login_student()
        self.assertEqual(self.submit(task).status_code, 200)
        self.assertTrue(Submission.objects.get().is_late)
        self.assertContains(self.client.get("/portal/daily-tasks/"), "Late submission")

    def test_deep_link_and_context_feed(self):
        task = self.task(starter_sql="SELECT", hint="Try EMP", sample_schema="EMP(ID NUMBER)")
        self.login_student()
        self.assertContains(self.client.get("/portal/daily-tasks/"), f"dailyTask={task.pk}")
        item = self.client.get("/api/portal/daily-tasks/available").json()["tasks"][0]
        self.assertEqual(item["starterSql"], "SELECT")
        self.assertEqual(item["hint"], "Try EMP")

    def test_schedule_validates_order(self):
        now = timezone.now()
        form = ScheduleForm({"start_at": now.isoformat(), "due_at": (now - timedelta(hours=1)).isoformat(), "late_policy": "lock"})
        self.assertFalse(form.is_valid())

    def test_existing_schedule_edit_is_scoped_and_has_unique_form_ids(self):
        task = self.task()
        self.client.force_login(self.teacher)
        release = timezone.now() + timedelta(hours=1)
        data = {"action": "configure_schedule", "task_id": task.pk,
                f"schedule-{task.pk}-start_at": release.isoformat(), f"schedule-{task.pk}-late_policy": "lock"}
        self.assertEqual(self.client.post("/portal/daily-tasks/", data).status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.start_at, release)
        self.assertContains(self.client.get("/portal/daily-tasks/?view=tasks"), f'id_schedule-{task.pk}-start_at')
        self.client.force_login(self.other)
        self.client.post("/portal/daily-tasks/", {**data, f"schedule-{task.pk}-start_at": ""})
        task.refresh_from_db()
        self.assertEqual(task.start_at, release)

    def test_full_200_question_preview_can_commit_only_selected_questions(self):
        self.client.force_login(self.teacher)
        token, _ = stage_import(self.teacher, {"class_sections": [self.section], "title": "Batch", "published": False,
                                               "questions": [f"Question {index}" for index in range(200)], "late_policy": "allow"})
        data = {"action": "commit_import", "stage_token": token, "questions-TOTAL_FORMS": "200", "questions-INITIAL_FORMS": "200"}
        for index in range(200):
            for field in ("title", "instructions", "starter_sql", "tags", "hint", "sample_schema"):
                data[f"questions-{index}-{field}"] = ""
        data.update({"questions-0-selected": "on", "questions-0-title": "Keep this", "questions-0-instructions": "Find employees"})
        self.assertEqual(self.client.post("/portal/daily-tasks/", data).status_code, 302)
        self.assertEqual(Assignment.objects.get().title, "Keep this")

    def test_late_tag_applies_to_html_and_latest_sqlcl_routes(self):
        self.login_student()
        html_task = self.task(due_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.client.post("/portal/daily-tasks/", {"task_id": html_task.pk, "history_id": self.history.pk}).status_code, 302)
        sqlcl_task = self.task(due_at=timezone.now() - timedelta(minutes=1))
        CompilerQueryHistory.objects.create(student=self.student, source="SQL*Plus", status="success", sql="SELECT 2 FROM dual")
        self.assertEqual(self.submit(sqlcl_task, "/api/portal/daily-tasks/submit-latest-sqlcl").status_code, 200)
        self.assertEqual(Submission.objects.filter(is_late=True).count(), 2)

    def test_review_filters_macros_analytics_and_ownership(self):
        task = self.task()
        Submission.objects.create(assignment=task, student=self.student, answer="SELECT missing FROM emp", status="auto_graded", feedback="ORA-00904: invalid identifier")
        hidden = Assignment.objects.create(class_section=self.section, created_by=self.other, title="Private other task")
        Submission.objects.create(assignment=hidden, student=self.student, answer="PRIVATE_OTHER_ANSWER")
        self.client.force_login(self.teacher)
        response = self.client.get("/portal/daily-tasks/", {"view": "review", "review_status": "revision", "review_class": self.section.pk})
        self.assertContains(response, "Submission review")
        self.assertContains(response, "Check WHERE clause")
        self.assertContains(response, "ORA-00904")
        self.assertNotContains(response, "PRIVATE_OTHER_ANSWER")

