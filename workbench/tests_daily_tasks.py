from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from .models import Assignment, ClassSection, Course, CompilerQueryHistory, Enrollment, PortalUser, Submission, TeacherAssignment


class DailyTasksTests(TestCase):
    def setUp(self):
        self.teacher = PortalUser.objects.create_user(username="daily-teacher", role="TEACHER")
        self.student = PortalUser.objects.create_user(username="daily-student", role="STUDENT")
        course = Course.objects.create(code="DAILY", name="SQL")
        self.section = ClassSection.objects.create(course=course, name="A")
        self.other = ClassSection.objects.create(course=course, name="B")
        TeacherAssignment.objects.create(teacher=self.teacher, class_section=self.section)
        Enrollment.objects.create(student=self.student, class_section=self.section)

    def login_student(self):
        # Simulate the session-token rotation performed by the real login API.
        PortalUser.objects.filter(pk=self.student.pk).update(active_student_session_token="", active_student_session_key="")
        self.client.force_login(self.student)

    def post_history_answer(self, task, answer):
        history = CompilerQueryHistory.objects.create(student=self.student, sql=answer, status="success")
        return self.client.post("/portal/daily-tasks/", {"task_id": task.pk, "history_id": history.pk})

    def test_teacher_report_includes_missing_submissions_and_excludes_other_teachers(self):
        import csv
        import io
        task = Assignment.objects.create(class_section=self.section, created_by=self.teacher, title="=Question")
        submitted = PortalUser.objects.create_user(username="report-submitted", role="STUDENT")
        Enrollment.objects.create(student=submitted, class_section=self.section)
        Submission.objects.create(assignment=task, student=submitted, status="reviewed", is_correct=True, score=100)
        other_teacher = PortalUser.objects.create_user(username="report-other", role="TEACHER")
        Assignment.objects.create(class_section=self.section, created_by=other_teacher, title="Private question")
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get("/portal/daily-tasks/?view=tasks"), "Download daily task report")
        response = self.client.get("/portal/daily-tasks/?report=csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 2)
        by_student = {row["Student username"]: row for row in rows}
        self.assertEqual(by_student[self.student.username]["Submission status"], "Not submitted")
        self.assertEqual(by_student[submitted.username]["Result"], "Correct")
        self.assertEqual(rows[0]["Question"], "'=Question")
        self.login_student()
        self.assertEqual(self.client.get("/portal/daily-tasks/?report=csv").status_code, 403)

    def test_bulk_enable_selected_and_all_respect_ownership_and_dates(self):
        from django.utils import timezone
        earlier = timezone.now() - timezone.timedelta(days=1)
        selected = Assignment.objects.create(class_section=self.section, created_by=self.teacher, title="Selected")
        remaining = Assignment.objects.create(class_section=self.section, created_by=self.teacher, title="Remaining")
        enabled = Assignment.objects.create(class_section=self.section, created_by=self.teacher, title="Enabled", published=True, enabled_at=earlier)
        outsider = PortalUser.objects.create_user(username="bulk-other", role="TEACHER")
        foreign = Assignment.objects.create(class_section=self.section, created_by=outsider, title="Foreign")
        self.client.force_login(self.teacher)
        for ids in ([], [selected.pk, foreign.pk]):
            response = self.client.post("/portal/daily-tasks/", {"action": "enable_selected", "task_ids": ids})
            self.assertEqual(response.status_code, 200)
            selected.refresh_from_db()
            self.assertFalse(selected.published)
        response = self.client.post("/portal/daily-tasks/", {"action": "enable_selected", "task_ids": [selected.pk]})
        self.assertEqual(response.status_code, 302)
        selected.refresh_from_db()
        remaining.refresh_from_db()
        self.assertTrue(selected.published)
        self.assertIsNotNone(selected.enabled_at)
        self.assertFalse(remaining.published)
        response = self.client.post("/portal/daily-tasks/", {"action": "enable_all"})
        self.assertEqual(response.status_code, 302)
        remaining.refresh_from_db()
        enabled.refresh_from_db()
        foreign.refresh_from_db()
        self.assertTrue(remaining.published)
        self.assertIsNotNone(remaining.enabled_at)
        self.assertEqual(enabled.enabled_at, earlier)
        self.assertFalse(foreign.published)

    def test_teacher_upload_and_class_scope(self):
        self.client.force_login(self.teacher)
        data = {"class_sections": [self.section.pk], "title": "Practice", "late_policy": "allow", "published": "on"}
        response = self.client.post("/portal/daily-tasks/", {**data, "question_file": SimpleUploadedFile("questions.txt", b"Select all employees\nCount departments")})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Save drafts to library")
        self.assertEqual(Assignment.objects.count(), 0)
        response = self.client.post("/portal/daily-tasks/", {**data, "class_sections": [self.other.pk], "instructions": "Question"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Save drafts to library")
        self.assertEqual(Assignment.objects.count(), 0)

    def test_student_sees_only_enrolled_enabled_tasks_and_can_retry(self):
        task = Assignment.objects.create(class_section=self.section, created_by=self.teacher, title="Visible task", instructions="Write SELECT", published=True)
        hidden = Assignment.objects.create(class_section=self.other, created_by=self.teacher, title="Other class secret", published=True)
        Assignment.objects.create(class_section=self.section, created_by=self.teacher, title="Draft secret")
        self.login_student()
        response = self.client.get("/portal/daily-tasks/")
        self.assertContains(response, "Visible task")
        self.assertNotContains(response, "Other class secret")
        self.assertNotContains(response, "Draft secret")
        self.post_history_answer(hidden, "No")
        self.assertFalse(Submission.objects.exists())
        self.post_history_answer(task, "SELECT 1 FROM dual;")
        self.post_history_answer(task, "Overwrite")
        self.assertEqual(Submission.objects.get().answer, "Overwrite")
        self.assertLessEqual(Submission.objects.get().first_attempt_at, Submission.objects.get().last_attempt_at)

    def test_empty_or_invalid_upload_is_not_saved(self):
        self.client.force_login(self.teacher)
        data = {"class_sections": [self.section.pk], "title": "Practice", "late_policy": "allow"}
        self.assertContains(self.client.post("/portal/daily-tasks/", data), "Enter a question")
        self.assertContains(self.client.post("/portal/daily-tasks/", {**data, "question_file": SimpleUploadedFile("questions.pdf", b"invalid")}), "Use a .txt")
        self.assertFalse(Assignment.objects.exists())

    def test_only_author_can_publish_a_draft(self):
        task = Assignment.objects.create(class_section=self.section, created_by=self.teacher, title="Draft")
        other_teacher = PortalUser.objects.create_user(username="daily-other-teacher", role="TEACHER")
        self.client.force_login(other_teacher)
        data = {"action": "set_published", "task_id": task.pk, "published": "1"}
        self.client.post("/portal/daily-tasks/", data)
        task.refresh_from_db()
        self.assertFalse(task.published)
        self.client.force_login(self.teacher)
        self.client.post("/portal/daily-tasks/", data)
        task.refresh_from_db()
        self.assertTrue(task.published)
        self.assertContains(self.client.get("/portal/daily-tasks/"), "Disable question")

    def test_login_and_csrf_are_required(self):
        self.assertEqual(self.client.get("/portal/daily-tasks/").status_code, 302)
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.teacher)
        self.assertEqual(client.post("/portal/daily-tasks/", {"title": "No token"}).status_code, 403)

    def test_question_date_tracks_enabling_and_disabled_question_rejects_answers(self):
        from datetime import timedelta
        from unittest.mock import patch
        from django.utils import timezone
        task = Assignment.objects.create(class_section=self.section, created_by=self.teacher, title="Dates")
        self.client.force_login(self.teacher)
        data = {"action": "set_published", "task_id": task.pk, "published": "1"}
        first = timezone.now()
        with patch("workbench.daily_tasks.timezone.now", return_value=first):
            self.client.post("/portal/daily-tasks/", data)
        task.refresh_from_db()
        self.assertEqual(task.enabled_at, first)
        self.client.post("/portal/daily-tasks/", {**data, "published": "0"})
        self.login_student()
        self.assertNotContains(self.client.get("/portal/daily-tasks/"), "Dates")
        self.post_history_answer(task, "blocked")
        self.assertFalse(Submission.objects.exists())
        self.client.force_login(self.teacher)
        later = first + timedelta(minutes=1)
        with patch("workbench.daily_tasks.timezone.now", return_value=later):
            self.client.post("/portal/daily-tasks/", data)
        task.refresh_from_db()
        self.assertEqual(task.enabled_at, later)
        self.login_student()
        self.assertContains(self.client.get("/portal/daily-tasks/"), "Question date:")

    def test_correct_review_locks_retries_and_preserves_attempt_dates(self):
        task = Assignment.objects.create(class_section=self.section, created_by=self.teacher, title="Review", published=True)
        self.login_student()
        self.post_history_answer(task, "First")
        submission = Submission.objects.get()
        first = submission.first_attempt_at
        old_version = submission.last_attempt_at.isoformat()
        self.post_history_answer(task, "Second")
        submission.refresh_from_db()
        self.assertEqual(submission.first_attempt_at, first)
        last = submission.last_attempt_at
        self.assertGreater(last, first)
        data = {"action": "review_answer", "task_id": task.pk, "submission_id": submission.pk, "verdict": "correct", "answer_version": old_version}
        self.client.force_login(self.teacher)
        self.assertContains(self.client.post("/portal/daily-tasks/", data), "newer answer")
        submission.refresh_from_db()
        self.assertFalse(submission.is_correct)
        self.client.post("/portal/daily-tasks/", {**data, "answer_version": last.isoformat()})
        submission.refresh_from_db()
        self.assertTrue(submission.is_correct)
        self.login_student()
        self.assertContains(self.client.get("/portal/daily-tasks/"), "Further attempts locked")
        self.post_history_answer(task, "Forbidden retry")
        submission.refresh_from_db()
        self.assertEqual(submission.answer, "Second")
        self.assertEqual(submission.first_attempt_at, first)
        self.assertEqual(submission.last_attempt_at, last)

    def test_other_teacher_cannot_review_and_retry_clears_previous_review(self):
        task = Assignment.objects.create(class_section=self.section, created_by=self.teacher, title="Review", published=True)
        self.login_student()
        self.post_history_answer(task, "First")
        submission = Submission.objects.get()
        data = {"action": "review_answer", "task_id": task.pk, "submission_id": submission.pk, "verdict": "correct", "answer_version": submission.last_attempt_at.isoformat()}
        other = PortalUser.objects.create_user(username="unauthorized-review", role="TEACHER")
        self.client.force_login(other)
        self.client.post("/portal/daily-tasks/", data)
        submission.refresh_from_db()
        self.assertFalse(submission.is_correct)
        self.client.force_login(self.teacher)
        self.client.post("/portal/daily-tasks/", {**data, "verdict": "retry", "feedback": "Try a WHERE clause"})
        submission.refresh_from_db()
        self.assertIsNotNone(submission.reviewed_at)
        self.login_student()
        self.assertContains(self.client.get("/portal/daily-tasks/"), "Try a WHERE clause")
        self.post_history_answer(task, "Revised")
        submission.refresh_from_db()
        self.assertIsNone(submission.reviewed_at)
        self.assertFalse(submission.is_correct)

    def test_history_picker_is_private_searchable_paginated_and_success_only(self):
        task = Assignment.objects.create(class_section=self.section, created_by=self.teacher, title="History", published=True)
        for i in range(23):
            CompilerQueryHistory.objects.create(student=self.student, sql=f"SELECT {i} FROM dual", status="success")
        CompilerQueryHistory.objects.create(student=self.teacher, sql="TEACHER SECRET", status="success")
        CompilerQueryHistory.objects.create(student=self.student, sql="FAILED SECRET", status="error")
        self.login_student()
        url = f"/api/portal/daily-tasks/history?task_id={task.pk}"
        response = self.client.get(url)
        data = response.json()
        self.assertEqual(len(data["statements"]), 20)
        self.assertTrue(data["hasMore"])
        self.assertNotContains(response, "TEACHER SECRET")
        self.assertNotContains(response, "FAILED SECRET")
        page2 = self.client.get(url + f"&before={data['statements'][-1]['id']}").json()
        self.assertEqual(len(page2["statements"]), 3)
        self.assertFalse(page2["hasMore"])
        self.assertEqual(len(self.client.get(url + "&q=SELECT+22+FROM").json()["statements"]), 1)
        self.assertEqual(self.client.get("/api/portal/daily-tasks/history?task_id=invalid").status_code, 404)
        task.published = False
        task.save()
        self.assertEqual(self.client.get(url).status_code, 404)
        task.published = True
        task.save()
        Submission.objects.create(assignment=task, student=self.student, is_correct=True)
        self.assertEqual(self.client.get(url).status_code, 409)

    def test_history_answer_uses_owned_successful_snapshot_and_obeys_correct_lock(self):
        task = Assignment.objects.create(class_section=self.section, created_by=self.teacher, title="History", published=True)
        own = CompilerQueryHistory.objects.create(student=self.student, sql="SELECT 1 FROM dual", status="success")
        foreign = CompilerQueryHistory.objects.create(student=self.teacher, sql="PRIVATE", status="success")
        failed = CompilerQueryHistory.objects.create(student=self.student, sql="BAD SQL", status="error")
        self.login_student()
        for history in (foreign, failed):
            response = self.client.post("/portal/daily-tasks/", {"task_id": task.pk, "history_id": history.pk})
            self.assertContains(response, "no longer available")
            self.assertFalse(Submission.objects.exists())
        self.client.post("/portal/daily-tasks/", {"task_id": task.pk, "history_id": own.pk, "answer": "tampered"})
        submission = Submission.objects.get()
        self.assertEqual(submission.answer, own.sql)
        submission.is_correct = True
        submission.save()
        last = submission.last_attempt_at
        another = CompilerQueryHistory.objects.create(student=self.student, sql="SELECT 2 FROM dual", status="success")
        self.client.post("/portal/daily-tasks/", {"task_id": task.pk, "history_id": another.pk})
        submission.refresh_from_db()
        self.assertEqual(submission.answer, own.sql)
        self.assertEqual(submission.last_attempt_at, last)

    def test_history_picker_renders_only_for_unlocked_students(self):
        task = Assignment.objects.create(class_section=self.section, created_by=self.teacher, title="History", published=True)
        self.login_student()
        self.assertContains(self.client.get("/portal/daily-tasks/"), "Use latest successful SQL")
        Submission.objects.create(assignment=task, student=self.student, is_correct=True)
        self.assertNotContains(self.client.get("/portal/daily-tasks/"), 'type="button" data-use-latest')
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(f"/api/portal/daily-tasks/history?task_id={task.pk}").status_code, 403)
