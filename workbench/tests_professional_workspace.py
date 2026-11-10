import io
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import timedelta
from types import SimpleNamespace

from django.core.management import call_command, CommandError
from django.conf import settings
from django.test import TestCase, SimpleTestCase
from django.utils import timezone

from .models import Assignment, ClassSection, CompilerQueryHistory, Course, Enrollment, PortalUser, Submission, TeacherAssignment
from .views import _oracle_transaction_pending


class ProfessionalWorkspaceTests(TestCase):
    def setUp(self):
        self.teacher = PortalUser.objects.create_user(username="professional-teacher", role="TEACHER")
        self.student = PortalUser.objects.create_user(username="professional-student", role="STUDENT")
        course = Course.objects.create(code="PRO", name="SQL practice")
        self.section = ClassSection.objects.create(course=course, name="Professional class")
        TeacherAssignment.objects.create(teacher=self.teacher, class_section=self.section)
        Enrollment.objects.create(student=self.student, class_section=self.section)

    def task(self, title="Visible task", **kwargs):
        return Assignment.objects.create(class_section=self.section, created_by=self.teacher, title=title, instructions="Practice SQL", published=True, **kwargs)

    def login_student(self):
        PortalUser.objects.filter(pk=self.student.pk).update(active_student_session_token="", active_student_session_key="")
        self.client.force_login(self.student)

    def test_dashboard_applies_release_rule_and_cards_are_links(self):
        task = self.task()
        self.task(title="Future hidden task", start_at=timezone.now() + timedelta(days=1))
        self.login_student()
        response = self.client.get("/portal/student/")
        self.assertContains(response, f"?task={task.pk}#daily-task-{task.pk}")
        self.assertContains(response, f"?class={self.section.pk}")
        self.assertNotContains(response, "Future hidden task")
        self.assertNotContains(response, "Practice streak")

    def test_task_states_and_feed_agree_about_automatic_incorrect_answers(self):
        fresh, waiting, revision, correct = [self.task(title=name) for name in ("Fresh", "Waiting", "Revise", "Correct")]
        Submission.objects.create(assignment=waiting, student=self.student)
        Submission.objects.create(assignment=revision, student=self.student, status="auto_graded", is_correct=False)
        Submission.objects.create(assignment=correct, student=self.student, status="auto_graded", is_correct=True)
        self.login_student()
        response = self.client.get("/portal/daily-tasks/")
        self.assertContains(response, 'data-task-state="submitted"')
        self.assertContains(response, 'data-task-state="revision"')
        self.assertContains(response, 'data-task-state="completed"')
        feed = self.client.get("/api/portal/daily-tasks/available").json()["tasks"]
        self.assertEqual({item["id"] for item in feed}, {fresh.pk, revision.pk})

    def test_teacher_default_list_create_and_review_are_separate_views(self):
        self.task()
        self.client.force_login(self.teacher)
        response = self.client.get("/portal/daily-tasks/")
        self.assertContains(response, "Your daily tasks")
        self.assertNotContains(response, 'id="task-create"')
        self.assertNotContains(response, 'id="submission-review"')
        self.assertContains(self.client.get("/portal/daily-tasks/?view=create"), 'id="task-create"')
        self.assertContains(self.client.get("/portal/daily-tasks/?view=review"), 'id="submission-review"')

    def test_navigation_grouping_does_not_expose_administration_to_students(self):
        self.login_student()
        response = self.client.get("/portal/student/")
        self.assertContains(response, "LEARNING")
        self.assertContains(response, 'data-workspace-menu')
        self.assertNotContains(response, 'href="/admin/"')
        self.assertNotContains(response, 'href="/portal/classes/"')

    def test_task_deep_link_selects_only_an_authorized_task(self):
        task = self.task()
        other = PortalUser.objects.create_user(username="professional-other-student", role="STUDENT")
        self.login_student()
        self.assertContains(self.client.get(f"/portal/daily-tasks/?task={task.pk}"), f'data-focus-task="{task.pk}"')
        private_course = Course.objects.create(code="OTHER", name="Other course")
        private_class = ClassSection.objects.create(course=private_course, name="Other class")
        private_task = Assignment.objects.create(class_section=private_class, created_by=self.teacher, title="PRIVATE TASK", published=True)
        self.assertNotContains(self.client.get(f"/portal/daily-tasks/?task={private_task.pk}"), "PRIVATE TASK")

    def test_new_shared_assets_are_served(self):
        for asset in ("workspace-ui.css", "workspace-ui.js", "workspace-session-ui.js", "workspace-daily-task.js"):
            response = self.client.get("/portal/assets/" + asset)
            self.assertEqual(response.status_code, 200)
            response.close()

    def test_backup_is_encrypted_verified_and_never_overwrites(self):
        with TemporaryDirectory(dir=settings.BASE_DIR) as directory:
            self.assertTrue(Path(directory).resolve().is_relative_to(settings.BASE_DIR.resolve()))
            output = Path(directory) / "portal.backup"
            call_command("backup_portal", output=str(output), without_media=True, stdout=io.StringIO())
            self.assertNotIn(b"professional-teacher", output.read_bytes())
            status = io.StringIO()
            call_command("backup_portal", verify=str(output), stdout=status)
            self.assertIn("Backup verified", status.getvalue())
            with self.assertRaises(CommandError):
                call_command("backup_portal", output=str(output), stdout=io.StringIO())
            output.write_bytes(b"damaged backup")
            with self.assertRaises(CommandError):
                call_command("backup_portal", verify=str(output), stdout=io.StringIO())


class OracleTransactionStateTests(SimpleTestCase):
    def test_transaction_state_uses_driver_boolean_without_executing_sql(self):
        self.assertIs(_oracle_transaction_pending(SimpleNamespace(transaction_in_progress=True)), True)
        self.assertIs(_oracle_transaction_pending(SimpleNamespace(transaction_in_progress=False)), False)
        self.assertIsNone(_oracle_transaction_pending(None))
        self.assertIsNone(_oracle_transaction_pending(SimpleNamespace(transaction_in_progress="unknown")))
