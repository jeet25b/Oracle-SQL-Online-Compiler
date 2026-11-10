import json
from django.test import TestCase, Client
from .models import (PortalUser, TeacherActivityPreference, TeacherAssignment, Enrollment,
                     Course, ClassSection, SetupGuideProgress)


class SetupGuideTests(TestCase):
    def setUp(self):
        self.teacher = PortalUser.objects.create_user(username='setup-teacher', role='TEACHER')
        self.other_teacher = PortalUser.objects.create_user(username='other-setup-teacher', role='TEACHER')
        self.student = PortalUser.objects.create_user(username='setup-student', role='STUDENT')
        self.outsider = PortalUser.objects.create_user(username='outside-student', role='STUDENT')
        course = Course.objects.create(code='SETUP', name='Setup')
        section = ClassSection.objects.create(course=course, name='Setup class')
        TeacherAssignment.objects.create(teacher=self.teacher, class_section=section)
        Enrollment.objects.create(student=self.student, class_section=section)
        self.preference = TeacherActivityPreference.objects.create(teacher=self.teacher)
        self.settings_url = '/api/teacher/setup-guide'
        self.state_url = '/api/portal/setup-guide'

    def post(self, url, data):
        return self.client.post(url, json.dumps(data), content_type='application/json')

    def enable(self):
        self.preference.guided_setup_enabled = True
        self.preference.save()

    def test_only_teacher_can_control_own_students_help(self):
        for user in (self.student, self.outsider):
            self.client.force_login(user)
            self.assertEqual(self.post(self.settings_url, {'action':'enable'}).status_code, 403)
        self.client.force_login(self.teacher)
        self.assertTrue(self.post(self.settings_url, {'action':'enable'}).json()['enabled'])
        self.assertEqual(self.post(self.settings_url, {'action':'unknown'}).status_code, 400)
        self.client.force_login(self.other_teacher)
        self.assertFalse(self.client.get(self.settings_url).json()['enabled'])

    def test_only_assigned_students_receive_enabled_help(self):
        self.enable()
        self.client.force_login(self.student)
        self.assertTrue(self.client.get(self.state_url).json()['enabled'])
        self.client.force_login(self.outsider)
        self.assertFalse(self.client.get(self.state_url + f'?teacher={self.teacher.pk}').json()['enabled'])
        self.assertEqual(self.post(self.state_url, {'revision':1}).status_code, 403)

    def test_registration_link_follows_teacher_switch(self):
        url = self.state_url + f'?teacher={self.teacher.pk}'
        self.assertFalse(self.client.get(url).json()['enabled'])
        self.enable()
        self.assertTrue(self.client.get(url).json()['enabled'])
        self.assertEqual(self.post(url, {'revision':1}).status_code, 403)
        self.assertFalse(self.client.get(self.state_url + '?teacher=invalid').json()['enabled'])

    def test_completion_persists_and_teacher_restart_resets_guide(self):
        self.enable()
        teacher_client = Client()
        teacher_client.force_login(self.teacher)
        self.client.force_login(self.student)
        self.assertFalse(self.client.get(self.state_url).json()['completed'])
        self.assertTrue(self.post(self.state_url, {'revision':1}).json()['completed'])
        self.assertTrue(self.client.get(self.state_url).json()['completed'])
        response = teacher_client.post(self.settings_url, json.dumps({'action':'restart'}), content_type='application/json')
        self.assertEqual(response.json()['revision'], 2)
        self.assertFalse(self.client.get(self.state_url).json()['completed'])
        self.assertEqual(self.post(self.state_url, {'revision':1}).status_code, 409)
        teacher_client.post(self.settings_url, json.dumps({'action':'disable'}), content_type='application/json')
        self.assertFalse(self.client.get(self.state_url).json()['enabled'])
        self.assertEqual(SetupGuideProgress.objects.count(), 1)

    def test_settings_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.teacher)
        self.assertEqual(client.post(self.settings_url, '{"action":"enable"}', content_type='application/json').status_code, 403)

    def test_inactive_enrollment_does_not_receive_teacher_help(self):
        self.enable()
        Enrollment.objects.filter(student=self.student).update(active=False)
        self.client.force_login(self.student)
        self.assertFalse(self.client.get(self.state_url).json()['enabled'])

    def test_new_student_gets_class_selection_guide_before_enrollment(self):
        self.enable()
        PortalUser.objects.filter(pk=self.outsider.pk).update(class_selection_required=True)
        self.outsider.refresh_from_db()
        self.client.force_login(self.outsider)
        response = self.client.get(self.state_url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['enabled'])
        self.assertTrue(response.json()['classSelectionRequired'])
        self.assertEqual(self.post(self.state_url, {'revision':1}).status_code, 409)
        self.assertEqual(SetupGuideProgress.objects.count(), 0)

    def test_new_student_guide_still_respects_teacher_disable(self):
        PortalUser.objects.filter(pk=self.outsider.pk).update(class_selection_required=True)
        self.outsider.refresh_from_db()
        self.client.force_login(self.outsider)
        response = self.client.get(self.state_url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['enabled'])
