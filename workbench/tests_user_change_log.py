from django.test import TestCase
from django.urls import reverse

from .models import PortalUser, PortalUserChangeLog, StudentManagement
from .user_change_log import user_change_actor


class PortalUserChangeLogTests(TestCase):
    def test_creation_and_deletion_remain_but_updates_follow_existing_user(self):
        admin = PortalUser.objects.create_superuser(
            username="lifecycle-admin", email="admin@example.test", password="test-password"
        )
        with user_change_actor(admin):
            student = PortalUser.objects.create_user(
                username="lifecycle-student", role=PortalUser.Role.STUDENT
            )
        student_id = student.pk
        student.first_name = "Updated"
        with user_change_actor(admin):
            student.save(update_fields=["first_name"])
        updates = PortalUserChangeLog.objects.filter(
            subject_id_snapshot=student_id, action=PortalUserChangeLog.Action.UPDATED
        )
        self.assertEqual(updates.count(), 1)
        self.assertEqual(updates.get().changed_fields, ["first_name"])
        self.assertEqual(updates.get().actor_name_snapshot, admin.username)
        with user_change_actor(admin):
            student.delete()
        self.assertFalse(updates.exists())
        self.assertEqual(
            set(PortalUserChangeLog.objects.filter(subject_id_snapshot=student_id)
                .values_list("action", flat=True)),
            {PortalUserChangeLog.Action.CREATED, PortalUserChangeLog.Action.DELETED},
        )

    def test_last_login_update_does_not_create_account_update_log(self):
        user = PortalUser.objects.create_user(username="login-only")
        user.last_login = None
        user.save(update_fields=["last_login"])
        self.assertFalse(PortalUserChangeLog.objects.filter(
            subject=user, action=PortalUserChangeLog.Action.UPDATED
        ).exists())

    def test_student_management_proxy_update_is_logged(self):
        user = PortalUser.objects.create_user(username="managed-student", role=PortalUser.Role.STUDENT)
        proxy = StudentManagement.objects.get(pk=user.pk)
        proxy.first_name = "Edited"
        proxy.save(update_fields=["first_name"])
        self.assertEqual(
            PortalUserChangeLog.objects.get(subject_id_snapshot=user.pk, action="updated").changed_fields,
            ["first_name"],
        )

    def test_logs_are_visible_in_admin_and_read_only(self):
        admin = PortalUser.objects.create_superuser(
            username="audit-admin", email="audit@example.test", password="test-password"
        )
        self.client.force_login(admin)
        url = reverse("admin:workbench_portaluserchangelog_changelist")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "audit-admin")
