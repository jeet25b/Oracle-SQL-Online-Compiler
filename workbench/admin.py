from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import (Announcement, Assignment, AttendanceSession, AuditEvent, ClassSection,
                     Course, Enrollment, PortalUser, SQLQuestion, SQLTest, StudyMaterial,
                     Submission, TeacherAssignment, TeacherMessage, TestAttempt, StudentFee, StudentFeePayment)

@admin.register(PortalUser)
class PortalUserAdmin(UserAdmin):
    list_display = ("username", "email", "first_name", "last_name", "role", "student_number", "teacher_number", "registration_status", "is_active")
    list_filter = ("role", "registration_status", "is_active", "is_staff")
    actions = ("approve_student_registrations", "reject_student_registrations", "deactivate_students", "activate_students")
    readonly_fields = ("is_active", "registration_status", "deactivation_source", "student_number", "teacher_number")
    fieldsets = UserAdmin.fieldsets + (("Learning portal", {"fields": ("role", "registration_status", "student_number", "teacher_number")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("Learning portal", {"fields": ("role", "email")}),)

    @admin.action(description="Approve selected pending student registrations")
    def approve_student_registrations(self, request, queryset):
        pending = queryset.filter(role=PortalUser.Role.STUDENT, registration_status=PortalUser.RegistrationStatus.PENDING_ADMIN)
        count = 0
        for student in pending:
            student.registration_status = PortalUser.RegistrationStatus.PENDING_ACCOUNTANT
            student.is_active = False
            student.save(update_fields=("registration_status", "is_active"))
            student.assign_portal_number()
            AuditEvent.objects.create(actor=request.user, action="student_registration_sent_to_accounts", target=student.username)
            count += 1
        self.message_user(request, f"Approved {count} student registration(s).")

    @admin.action(description="Reject selected pending student registrations")
    def reject_student_registrations(self, request, queryset):
        pending = queryset.filter(role=PortalUser.Role.STUDENT, registration_status=PortalUser.RegistrationStatus.PENDING_ADMIN)
        count = 0
        for student in pending:
            student.registration_status = PortalUser.RegistrationStatus.REJECTED
            student.is_active = False
            student.save(update_fields=("registration_status", "is_active"))
            AuditEvent.objects.create(actor=request.user, action="student_registration_rejected", target=student.username)
            count += 1
        self.message_user(request, f"Rejected {count} student registration(s).")

    @admin.action(description="Deactivate selected students")
    def deactivate_students(self, request, queryset):
        students = queryset.filter(role=PortalUser.Role.STUDENT)
        count = students.update(is_active=False, deactivation_source=PortalUser.DeactivationSource.ADMIN)
        for student in students:
            AuditEvent.objects.create(actor=request.user, action="student_deactivated_admin", target=student.username)
        self.message_user(request, f"Deactivated {count} student(s).")

    @admin.action(description="Activate selected students (except account-team deactivations)")
    def activate_students(self, request, queryset):
        students = queryset.filter(role=PortalUser.Role.STUDENT,
            registration_status=PortalUser.RegistrationStatus.APPROVED).exclude(
            deactivation_source=PortalUser.DeactivationSource.ACCOUNTANT)
        count = students.update(is_active=True, deactivation_source="")
        for student in students:
            AuditEvent.objects.create(actor=request.user, action="student_activated_admin", target=student.username)
        self.message_user(request, f"Activated {count} student(s). Account-team deactivations remain inactive.")

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        # Status changes must go through actions that enforce the deactivation-source rules.
        visible = []
        for name, options in fieldsets:
            fields = tuple(field for field in options.get("fields", ())
                           if field not in {"is_active", "registration_status", "deactivation_source"})
            if fields:
                visible.append((name, {**options, "fields": fields}))
        if obj is not None:
            if obj.role == PortalUser.Role.STUDENT:
                visible.append(("Student number", {"fields": ("student_number",)}))
            elif obj.role == PortalUser.Role.TEACHER:
                visible.append(("Teacher number", {"fields": ("teacher_number",)}))
            visible.append(("Account status", {"fields": ("is_active", "registration_status", "deactivation_source")}))
        return tuple(visible)

    def save_model(self, request, obj, form, change):
        if not change and obj.role == PortalUser.Role.STUDENT:
            obj.registration_status = PortalUser.RegistrationStatus.PENDING_ACCOUNTANT
            obj.is_active = False
        super().save_model(request, obj, form, change)
        obj.assign_portal_number()

for model in (Course, ClassSection, TeacherAssignment, Enrollment, AttendanceSession, Assignment,
              SQLQuestion, SQLTest, TestAttempt, Submission, StudyMaterial, Announcement,
              TeacherMessage, AuditEvent):
    admin.site.register(model)

admin.site.register(StudentFee)
admin.site.register(StudentFeePayment)
