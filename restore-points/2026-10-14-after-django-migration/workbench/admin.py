from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import (Announcement, Assignment, AttendanceSession, AuditEvent, ClassSection,
                     Course, Enrollment, PortalUser, SQLQuestion, SQLTest, StudyMaterial,
                     Submission, TeacherAssignment, TeacherMessage, TestAttempt)

@admin.register(PortalUser)
class PortalUserAdmin(UserAdmin):
    list_display = ("username", "email", "first_name", "last_name", "role", "is_active")
    list_filter = ("role", "is_active", "is_staff")
    fieldsets = UserAdmin.fieldsets + (("Learning portal", {"fields": ("role", "student_number", "teacher_number")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("Learning portal", {"fields": ("role", "email", "student_number", "teacher_number")}),)

for model in (Course, ClassSection, TeacherAssignment, Enrollment, AttendanceSession, Assignment,
              SQLQuestion, SQLTest, TestAttempt, Submission, StudyMaterial, Announcement,
              TeacherMessage, AuditEvent):
    admin.site.register(model)
