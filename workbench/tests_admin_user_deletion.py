from django.contrib import admin
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from .models import Assignment, ClassSection, Course, PortalUser


class AdminUserDeletionTests(TestCase):
    def setUp(self):
        self.admin = PortalUser.objects.create_superuser(
            username='deletion-admin', email='delete-admin@example.test', password='test-only-password')
        self.student = PortalUser.objects.create_user(username='deletion-student', role='STUDENT')
        self.teacher = PortalUser.objects.create_user(username='deletion-teacher', role='TEACHER')
        course = Course.objects.create(code='DELETE', name='Deletion test')
        section = ClassSection.objects.create(course=course, name='Test class')
        self.assignment = Assignment.objects.create(
            class_section=section, created_by=self.teacher, title='Keep this assignment')
        self.client.force_login(self.admin)
        self.url = reverse('admin:workbench_portaluser_changelist')

    def delete_users(self, *users):
        return self.client.post(self.url, {
            'action': 'delete_selected_users',
            '_selected_action': [str(user.pk) for user in users],
        })

    def test_student_can_be_deleted_from_changelist(self):
        response = self.delete_users(self.student)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(PortalUser.objects.filter(pk=self.student.pk).exists())

    def test_protected_teacher_shows_explanation_instead_of_500(self):
        response = self.delete_users(self.teacher)
        self.assertEqual(response.status_code, 302)
        messages = ' '.join(str(message) for message in get_messages(response.wsgi_request))
        self.assertIn('Cannot delete deletion-teacher', messages)
        self.assertIn('assignments', messages)
        self.assertIn('deactivate', messages)
        self.assertTrue(PortalUser.objects.filter(pk=self.teacher.pk).exists())
        self.assertTrue(Assignment.objects.filter(pk=self.assignment.pk).exists())

    def test_mixed_selection_deletes_student_and_preserves_protected_accounts(self):
        response = self.delete_users(self.student, self.teacher, self.admin)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(PortalUser.objects.filter(pk=self.student.pk).exists())
        self.assertTrue(PortalUser.objects.filter(pk=self.teacher.pk).exists())
        self.assertTrue(PortalUser.objects.filter(pk=self.admin.pk).exists())
        self.assertEqual(Assignment.objects.get(pk=self.assignment.pk).created_by_id, self.teacher.pk)

    def test_builtin_go_action_renders_filtered_deletion_confirmation(self):
        response = self.client.post(self.url + '?q=deletion-student', {
            'action': 'delete_selected',
            '_selected_action': [str(self.student.pk)],
        })
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'admin/delete_selected_confirmation.html')
        self.assertContains(response, 'deletion-student')
        self.assertTrue(PortalUser.objects.filter(pk=self.student.pk).exists())
