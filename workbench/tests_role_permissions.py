from django.test import TestCase
from .models import PortalUser


class TeacherPermissionTests(TestCase):
    def test_only_assigned_teachers_can_keep_permissions(self):
        for role, extra, allowed in [("STUDENT", [], False), ("ACCOUNTANT", [], False), ("TEACHER", [], True), ("STUDENT", ["TEACHER"], True)]:
            user = PortalUser.objects.create(username=f"permission-{role}-{allowed}", role=role, additional_roles=extra, can_approve_students=True, can_view_all_compiler_activity=True)
            user.refresh_from_db()
            self.assertEqual(user.can_approve_students, allowed)
            self.assertEqual(user.can_view_all_compiler_activity, allowed)

    def test_removing_teacher_role_clears_both_even_on_partial_save(self):
        user = PortalUser.objects.create(username="permission-change", role="TEACHER", can_approve_students=True, can_view_all_compiler_activity=True)
        user.role = "STUDENT"
        user.save(update_fields=["role"])
        user.refresh_from_db()
        self.assertFalse(user.can_approve_students)
        self.assertFalse(user.can_view_all_compiler_activity)
