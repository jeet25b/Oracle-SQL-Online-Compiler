from django.test import TestCase
from django.urls import reverse

from .models import (AttendanceSession, ClassSection, ClassSubject, CompilerLab, Course, Enrollment, LabEnrollment,
                     PortalUser, Subject, TeacherAssignment)


class ClassManagementTests(TestCase):
    def setUp(self):
        self.admin = PortalUser.objects.create_user(username="class-admin", role="ADMIN")
        self.teacher = PortalUser.objects.create_user(username="class-teacher", role="TEACHER")
        self.student = PortalUser.objects.create_user(username="class-student", role="STUDENT")
        self.course = Course.objects.create(code="BTECH", name="B.Tech", description="NCLOB programme description")
        self.section = ClassSection.objects.create(course=self.course, name="CS3-A-G1", branch="Computer Science", semester=3, section="A", group="G1")
        self.other = ClassSection.objects.create(course=self.course, name="CS3-A-G2", branch="Computer Science", semester=3, section="A", group="G2")
        self.subject = Subject.objects.create(code="DBMS", name="Database systems")
        self.offering = ClassSubject.objects.create(class_section=self.section, subject=self.subject, has_lecture=True, has_laboratory=True)
        self.lab = CompilerLab.objects.create(class_section=self.section, created_by=self.admin, name="SQL G1")
        self.other_lab = CompilerLab.objects.create(class_section=self.other, created_by=self.admin, name="SQL G2")
        self.url = f"/portal/classes/?class={self.section.pk}"

    def login_student(self):
        PortalUser.objects.filter(pk=self.student.pk).update(active_student_session_token="", active_student_session_key="")
        self.client.force_login(self.student)

    def test_admin_page_and_non_admin_permissions(self):
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, {"action": "enroll"}).status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertContains(response, "Allocate a teacher")
        self.assertContains(response, "Assign students")
        self.assertContains(response, "Laboratories")
        self.assertRedirects(self.client.get(reverse("admin:workbench_classsection_changelist")), "/portal/classes/", fetch_redirect_response=False)

    def test_class_requires_academic_fields_and_edits_in_place(self):
        self.client.force_login(self.admin)
        data = {"action": "class", "class-course": self.course.pk, "class-name": "New class", "class-is_active": "on"}
        self.assertEqual(self.client.post("/portal/classes/", data).status_code, 200)
        self.assertFalse(ClassSection.objects.filter(name="New class").exists())
        data.update({"class-branch": "IT", "class-semester": 2, "class-section": "B", "class-group": "G3"})
        self.assertEqual(self.client.post("/portal/classes/", data).status_code, 302)
        created = ClassSection.objects.get(name="New class")
        self.assertIn("Semester 2", str(created))
        self.assertEqual(self.client.post(f"/portal/classes/?class={created.pk}", {**data, "class-semester": 4}).status_code, 302)
        created.refresh_from_db()
        self.assertEqual(created.semester, 4)
        self.assertEqual(ClassSection.objects.filter(name="New class").count(), 1)

    def test_assign_students_to_class_and_lab_and_unassign(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.url, {"action": "enroll", "enroll-students": [self.student.pk], "enroll-lab": self.lab.pk}).status_code, 302)
        enrollment = Enrollment.objects.get(student=self.student, class_section=self.section)
        self.assertTrue(LabEnrollment.objects.get(student=self.student, lab=self.lab).active)
        self.assertEqual(self.client.post(self.url, {"action": "enroll", "enroll-students": [self.teacher.pk]}).status_code, 200)
        self.assertFalse(Enrollment.objects.filter(student=self.teacher).exists())
        self.client.post(self.url, {"action": "remove_enrollment", "target_id": enrollment.pk})
        enrollment.refresh_from_db()
        self.assertFalse(enrollment.active)
        self.assertFalse(LabEnrollment.objects.get(student=self.student, lab=self.lab).active)

    def test_cross_class_lab_is_rejected(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.url, {"action": "enroll", "enroll-students": [self.student.pk], "enroll-lab": self.other_lab.pk})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Enrollment.objects.exists())

    def test_lecture_and_practical_allocations_and_validation(self):
        self.client.force_login(self.admin)
        data = {"action": "allocation", "allocation-teacher": self.teacher.pk, "allocation-class_subject": self.offering.pk, "allocation-teaching_type": "lecture"}
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertEqual(self.client.post(self.url, {**data, "allocation-teaching_type": "laboratory", "allocation-lab": self.lab.pk}).status_code, 302)
        self.assertEqual(TeacherAssignment.objects.filter(teacher=self.teacher).count(), 2)
        response = self.client.post(self.url, {**data, "allocation-teaching_type": "laboratory", "allocation-lab": self.other_lab.pk})
        self.assertEqual(response.status_code, 200)
        response = self.client.post(self.url, {**data, "allocation-teaching_type": "laboratory"})
        self.assertContains(response, "Choose a lab")
        self.client.post(self.url, {**data, "allocation-lab": self.lab.pk})
        self.assertEqual(TeacherAssignment.objects.count(), 2)

    def test_no_duplicate_subject_or_lab_causes_server_error(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.url, {"action": "offering", "offering-subject": self.subject.pk, "offering-has_lecture": "on"})
        self.assertContains(response, "already offered")
        response = self.client.post(self.url, {"action": "lab", "lab-name": self.lab.name, "lab-is_active": "on"})
        self.assertContains(response, "already exists")

    def test_mandatory_first_login_cannot_be_bypassed_and_records_selection(self):
        self.student.class_selection_required = True
        self.student.save(update_fields=("class_selection_required",))
        self.login_student()
        self.assertRedirects(self.client.get("/portal/daily-tasks/"), "/portal/choose-class/", fetch_redirect_response=False)
        self.assertRedirects(self.client.get("/online-compiler"), "/portal/choose-class/", fetch_redirect_response=False)
        blocked = self.client.post("/api/execute", data="{}", content_type="application/json")
        self.assertEqual(blocked.status_code, 409)
        self.assertTrue(blocked.json()["classSelectionRequired"])
        self.assertContains(self.client.get("/portal/choose-class/"), "Group G1")
        attendance = AttendanceSession.objects.create(user=self.student)
        session = self.client.session
        session["attendance_session_id"] = attendance.pk
        session.save()
        response = self.client.post("/portal/choose-class/", {"class_section": self.section.pk})
        self.assertEqual(response.status_code, 302)
        self.student.refresh_from_db()
        self.assertFalse(self.student.class_selection_required)
        self.assertIsNotNone(self.student.class_selected_at)
        self.assertTrue(Enrollment.objects.get(student=self.student).active)
        attendance.refresh_from_db()
        self.assertEqual(attendance.class_section_id, self.section.pk)
        self.assertEqual(self.client.get("/portal/daily-tasks/").status_code, 200)
        self.client.post("/portal/choose-class/", {"class_section": self.other.pk})
        self.assertFalse(Enrollment.objects.filter(student=self.student, class_section=self.other).exists())

    def test_first_login_respects_preassignment_and_inactive_classes(self):
        self.student.class_selection_required = True
        self.student.save(update_fields=("class_selection_required",))
        Enrollment.objects.create(student=self.student, class_section=self.section)
        self.login_student()
        self.assertEqual(self.client.post("/portal/choose-class/", {"class_section": self.other.pk}).status_code, 200)
        self.section.is_active = False
        self.section.save()
        self.assertContains(self.client.get("/portal/choose-class/"), "No complete, active classes")
        self.assertEqual(self.client.post("/portal/choose-class/", {"class_section": self.section.pk}).status_code, 200)
        self.student.refresh_from_db()
        self.assertTrue(self.student.class_selection_required)

    def test_lab_roster_limits_selection_and_preserves_unrostered_labs(self):
        from .class_management import student_labs
        Enrollment.objects.create(student=self.student, class_section=self.section)
        other_student = PortalUser.objects.create_user(username="class-other-student", role="STUDENT")
        LabEnrollment.objects.create(student=other_student, lab=self.lab)
        self.assertNotIn(self.lab, student_labs(self.student))
        LabEnrollment.objects.create(student=self.student, lab=self.lab)
        self.assertIn(self.lab, student_labs(self.student))
        shared_lab = CompilerLab.objects.create(class_section=self.section, created_by=self.admin, name="Unrostered")
        self.assertIn(shared_lab, student_labs(self.student))

    def test_new_registrations_require_class_selection(self):
        from .portal_views import _create_registered_portal_user
        from django.contrib.auth.hashers import make_password
        user = _create_registered_portal_user({"first_name": "New", "last_name": "Student", "role": "student"}, {}, make_password("Test-password-239"))
        self.assertTrue(user.class_selection_required)

    def test_first_real_login_redirects_to_class_selection(self):
        import json
        self.student.set_password("Class-login-password-239")
        self.student.save()
        response = self.client.post("/api/portal/login", json.dumps({"username": self.student.username, "password": "Class-login-password-239"}), content_type="application/json", HTTP_ORIGIN="https://testserver", secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["next"], "/portal/choose-class/")
        self.student.refresh_from_db()
        self.assertTrue(self.student.class_selection_required)

    def test_teacher_class_setup_requires_complete_class_details(self):
        self.client.force_login(self.teacher)
        data = {"action": "setup_class", "course_code": self.course.code, "course_name": self.course.name, "new_class_name": "Teacher setup"}
        self.assertContains(self.client.post("/portal/tests/", data), "semester (1")
        self.assertFalse(ClassSection.objects.filter(name="Teacher setup").exists())
        data.update({"new_branch": "IT", "new_semester": 1, "new_section": "C", "new_group": "G1"})
        self.assertEqual(self.client.post("/portal/tests/", data).status_code, 302)
        self.assertEqual(ClassSection.objects.get(name="Teacher setup").branch, "IT")
