from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone
import uuid

from .account_notifications import notify_account_event
from .models import (
    AuditEvent, ClassSection, CompilerLab, CompilerOption, DashboardNavigationItem,
    Enrollment, LabEnrollment, PortalSetting, PortalUser, RegistrationField,
    PortalUserChangeLog, StudentManagement, TimetableEntry,
)
from .user_change_log import user_change_actor


@admin.register(TimetableEntry)
class TimetableAdmin(admin.ModelAdmin):
    def changelist_view(self, request, extra_context=None):
        from django.shortcuts import redirect
        return redirect("/portal/timetable/")


@admin.register(ClassSection)
class ClassManagementAdmin(admin.ModelAdmin):
    """Keep the administration entry point on the shared portal theme."""
    def changelist_view(self, request, extra_context=None):
        from django.shortcuts import redirect
        return redirect("/portal/classes/")

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        for name in ("branch", "semester", "section", "group"):
            if name in form.base_fields:
                form.base_fields[name].required = True
        return form

ADDITIONAL_ROLE_CHOICES = tuple(
    choice for choice in PortalUser.Role.choices
    if choice[0] in (PortalUser.Role.TEACHER, PortalUser.Role.STUDENT)
)


class PortalUserCreationForm(UserCreationForm):
    username = forms.CharField(required=False, widget=forms.HiddenInput())
    role = forms.ChoiceField(choices=(
        (PortalUser.Role.STUDENT, "Student"),
        (PortalUser.Role.TEACHER, "Teacher"),
    ))
    mobile_number = forms.CharField(required=False, max_length=16)
    can_approve_students = forms.BooleanField(required=False, label="Can approve student registrations")
    can_view_all_compiler_activity = forms.BooleanField(required=False)
    additional_roles = forms.MultipleChoiceField(
        choices=ADDITIONAL_ROLE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Also assign these roles",
        help_text="This account will use one login and one email for all selected roles.",
    )

    class Meta(UserCreationForm.Meta):
        model = PortalUser
        fields = ("username", "email", "first_name", "middle_name", "last_name", "role", "mobile_number", "can_approve_students", "can_view_all_compiler_activity", "can_manage_classes", "can_manage_timetable", "additional_roles")

    def clean_username(self):
        value = self.cleaned_data.get("username")
        return value or f"pending-{uuid.uuid4().hex}"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        primary_role = self.data.get(self.add_prefix("role"), self.initial.get("role", PortalUser.Role.STUDENT)) if self.is_bound else self.initial.get("role", PortalUser.Role.STUDENT)
        extra_roles = self.data.getlist(self.add_prefix("additional_roles")) if self.is_bound else self.initial.get("additional_roles", [])
        if primary_role != PortalUser.Role.TEACHER and PortalUser.Role.TEACHER not in extra_roles:
            self.fields["can_approve_students"].disabled = True
            self.fields["can_view_all_compiler_activity"].disabled = True
            self.fields["can_manage_classes"].disabled = True
            self.fields["can_manage_timetable"].disabled = True

    def clean_additional_roles(self):
        return [role for role in self.cleaned_data.get("additional_roles", []) if role != self.cleaned_data.get("role")]


class PortalUserChangeForm(UserChangeForm):
    oracle_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        label="Oracle password",
        help_text="Never displayed. Leave blank to keep the saved password, enter a new value to replace it, or select Clear saved Oracle credentials to remove it.",
    )
    clear_oracle_credentials = forms.BooleanField(
        required=False,
        label="Clear saved Oracle credentials",
    )
    additional_roles = forms.MultipleChoiceField(
        choices=ADDITIONAL_ROLE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Additional roles",
        help_text="The user signs in once and can switch between assigned workspaces.",
    )

    class Meta(UserChangeForm.Meta):
        model = PortalUser
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["additional_roles"].initial = [
                role for role in self.instance.portal_roles if role != self.instance.role
            ]
        if self.is_bound:
            primary_role = self.data.get(self.add_prefix("role"), self.instance.role)
            extra_roles = self.data.getlist(self.add_prefix("additional_roles"))
        else:
            primary_role = self.instance.role
            extra_roles = [role for role in self.instance.portal_roles if role != self.instance.role]
        if primary_role != PortalUser.Role.TEACHER and PortalUser.Role.TEACHER not in extra_roles:
            self.fields["can_approve_students"].disabled = True
            self.fields["can_view_all_compiler_activity"].disabled = True
            self.fields["can_manage_classes"].disabled = True
            self.fields["can_manage_timetable"].disabled = True

    def clean_additional_roles(self):
        return [role for role in self.cleaned_data.get("additional_roles", []) if role != self.cleaned_data.get("role")]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("clear_oracle_credentials"):
            return cleaned
        username = cleaned.get("oracle_username", "").strip()
        connect_string = cleaned.get("oracle_connect_string", "").strip()
        has_password = bool(cleaned.get("oracle_password")) or bool(self.instance.oracle_password_encrypted)
        if any((username, connect_string, has_password)) and not all((username, connect_string, has_password)):
            raise forms.ValidationError("Enter the Oracle username and connect string, and save a password, or clear all saved Oracle credentials.")
        return cleaned


@admin.register(PortalUser)
class PortalUserAdmin(UserAdmin):
    add_form = PortalUserCreationForm
    form = PortalUserChangeForm
    list_display = ("username", "get_full_name", "email", "mobile_number", "get_portal_roles", "registration_status", "approval_record", "can_approve_students", "can_view_all_compiler_activity", "date_joined", "is_active", "is_superuser")
    list_filter = ("role", "registration_status", "can_view_all_compiler_activity", "is_active", "is_superuser")
    search_fields = ("username", "first_name", "middle_name", "last_name", "email", "mobile_number", "teacher_number", "student_number")
    fieldsets = (
        UserAdmin.fieldsets[0],
        ("Personal info", {"fields": ("first_name", "middle_name", "last_name", "email")} ),
        *UserAdmin.fieldsets[2:],
        ('Session time', {'fields': ('unrestricted_session_time',), 'description': 'Admin-role accounts and members of the Admin group are always exempt from time limits.'}),
        ("Portal access", {"fields": ("role", "additional_roles", "registration_status", "mobile_number", "email_verified", "mobile_verified", "teacher_number", "student_number", "profile_data", "can_approve_students", "can_view_all_compiler_activity", "can_manage_classes", "can_manage_timetable", "approval_record", "approved_at")}),
        ("Oracle connection credentials", {"fields": ("oracle_username", "oracle_password", "oracle_connect_string", "clear_oracle_credentials"), "description": "The password is stored encrypted and is never displayed here. Leave its field blank to preserve the saved password."}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "first_name", "middle_name", "last_name", "role", "additional_roles", "mobile_number", "can_approve_students", "can_view_all_compiler_activity", "can_manage_classes", "can_manage_timetable", "password1", "password2")}),)
    actions = ("approve_student_registrations", "reject_student_registrations", "delete_selected_users")

    class Media:
        js = ("workbench/admin/portaluser-role-permissions.js?v=20261029-class-timetable",)

    readonly_fields = ("email_verified", "mobile_verified", "approval_record", "approved_at")

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj and obj.is_superuser:
            readonly.extend(("is_superuser", "role"))
        return tuple(readonly)

    @admin.display(description="Roles")
    def get_portal_roles(self, obj):
        labels = dict(PortalUser.Role.choices)
        return ", ".join(labels.get(role, role) for role in obj.portal_roles)

    @admin.display(description="Approved by")
    def approval_record(self, obj):
        if obj.approved_by_id:
            return obj.approved_by.username
        if obj.registration_status == PortalUser.RegistrationStatus.APPROVED and obj.approved_at:
            return "Automatic"
        return "—"

    def has_delete_permission(self, request, obj=None):
        if obj and obj.is_superuser:
            return False
        return super().has_delete_permission(request, obj)

    @admin.action(description="Delete selected portal users (protected superusers are skipped)")
    def delete_selected_users(self, request, queryset):
        if not self.has_delete_permission(request):
            self.message_user(request, "You do not have permission to delete portal users.", level=messages.ERROR)
            return
        protected_count = queryset.filter(is_superuser=True).count()
        delete_count = 0
        # Isolate each account so protected teaching records do not abort the
        # entire selection or leave the database transaction unusable.
        for user in queryset.exclude(is_superuser=True).iterator():
            try:
                with transaction.atomic(using=queryset.db):
                    with user_change_actor(request.user):
                        user.delete(using=queryset.db)
            except ProtectedError as error:
                related_types = ", ".join(sorted({
                    str(record._meta.verbose_name_plural)
                    for record in error.protected_objects
                }))
                self.message_user(
                    request,
                    f"Cannot delete {user.username}: linked {related_types} must be "
                    "reassigned or removed first. You can deactivate the account "
                    "by clearing Active to prevent login while keeping its records.",
                    level=messages.ERROR,
                )
            else:
                delete_count += 1
        if delete_count:
            self.message_user(request, f"Deleted {delete_count} portal user(s).", level=messages.SUCCESS)
        if protected_count:
            self.message_user(request, f"Skipped {protected_count} protected superuser account(s).", level=messages.WARNING)

    def save_model(self, request, obj, form, change):
        just_approved = False
        if not change and PortalUser.Role.STUDENT in obj.portal_roles:
            obj.class_selection_required = True
        if change:
            previous = PortalUser.objects.filter(pk=obj.pk).only("registration_status").first()
            if previous and previous.registration_status != PortalUser.RegistrationStatus.APPROVED and obj.registration_status == PortalUser.RegistrationStatus.APPROVED:
                just_approved = True
                obj.approved_by = request.user
                obj.approved_at = timezone.now()
                obj.is_active = True
            elif previous and obj.registration_status == PortalUser.RegistrationStatus.REJECTED:
                obj.is_active = False
        if not change and obj.role in (PortalUser.Role.TEACHER, PortalUser.Role.STUDENT):
            account_number = PortalUser.next_account_id(obj.role)
            obj.username = account_number
        else:
            account_number = obj.username
        assigned_roles = set(obj.portal_roles)
        if PortalUser.Role.TEACHER in assigned_roles and not obj.teacher_number:
            obj.teacher_number = account_number
        if PortalUser.Role.STUDENT in assigned_roles and not obj.student_number:
            obj.student_number = account_number
        if form.cleaned_data.get("clear_oracle_credentials"):
            obj.clear_oracle_credentials()
        elif form.cleaned_data.get("oracle_password"):
            obj.set_oracle_credentials(
                obj.oracle_username,
                form.cleaned_data["oracle_password"],
                obj.oracle_connect_string,
            )
        with user_change_actor(request.user):
            super().save_model(request, obj, form, change)
        if just_approved:
            failures = notify_account_event(obj, "approved", request=request)
            if failures:
                self.message_user(request, "The account was approved, but its email or WhatsApp confirmation could not be sent. Check the server log and notification settings.", level=messages.WARNING)

    def delete_model(self, request, obj):
        with user_change_actor(request.user):
            super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        with user_change_actor(request.user):
            super().delete_queryset(request, queryset)


    @admin.action(description="Approve selected pending student or teacher registrations")
    def approve_student_registrations(self, request, queryset):
        registrations = queryset.filter(role__in=(PortalUser.Role.STUDENT, PortalUser.Role.TEACHER), registration_status=PortalUser.RegistrationStatus.PENDING_ADMIN)
        with transaction.atomic(using=queryset.db):
            users = list(registrations.select_for_update().only("pk", "role", "username", "first_name", "email", "mobile_number"))
            registrations.update(registration_status=PortalUser.RegistrationStatus.APPROVED, is_active=True, approved_by=request.user, approved_at=timezone.now())
            PortalUserChangeLog.objects.using(queryset.db).bulk_create([
                PortalUserChangeLog(subject=user, subject_id_snapshot=user.pk,
                                    username_snapshot=user.username, action=PortalUserChangeLog.Action.UPDATED,
                                    changed_fields=["registration_status", "is_active", "approved_by", "approved_at"],
                                    actor=request.user, actor_name_snapshot=request.user.username)
                for user in users
            ])
        failed_users = sum(bool(notify_account_event(user, "approved", request=request)) for user in users)
        for user in users:
            AuditEvent.objects.create(actor=request.user, action=f"{user.role.lower()}_registration_approved", target=str(user.pk))
        self.message_user(request, f"Approved {len(users)} student or teacher registration(s).", level=messages.SUCCESS)
        if failed_users:
            self.message_user(request, f"Confirmation delivery failed for {failed_users} account(s). Check the server log and notification settings.", level=messages.WARNING)

    @admin.action(description="Reject selected pending student or teacher registrations")
    def reject_student_registrations(self, request, queryset):
        registrations = queryset.filter(role__in=(PortalUser.Role.STUDENT, PortalUser.Role.TEACHER), registration_status=PortalUser.RegistrationStatus.PENDING_ADMIN)
        with transaction.atomic(using=queryset.db):
            users = list(registrations.select_for_update().only("pk", "role", "username"))
            registrations.update(registration_status=PortalUser.RegistrationStatus.REJECTED, is_active=False)
            PortalUserChangeLog.objects.using(queryset.db).bulk_create([
                PortalUserChangeLog(subject=user, subject_id_snapshot=user.pk,
                                    username_snapshot=user.username, action=PortalUserChangeLog.Action.UPDATED,
                                    changed_fields=["registration_status", "is_active"],
                                    actor=request.user, actor_name_snapshot=request.user.username)
                for user in users
            ])
        for user in users:
            AuditEvent.objects.create(actor=request.user, action=f"{user.role.lower()}_registration_rejected", target=str(user.pk))
        self.message_user(request, f"Rejected {len(users)} student or teacher registration(s).", level=messages.SUCCESS)


@admin.register(PortalUserChangeLog)
class PortalUserChangeLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "username_snapshot", "subject_id_snapshot", "actor_name_snapshot", "origin", "changed_field_names")
    list_filter = ("action", "origin", "created_at")
    search_fields = ("username_snapshot", "actor_name_snapshot")
    readonly_fields = ("created_at", "action", "origin", "subject", "subject_id_snapshot", "username_snapshot", "changed_fields", "actor", "actor_name_snapshot")
    list_select_related = ("actor",)

    @admin.display(description="Changed fields")
    def changed_field_names(self, obj):
        return ", ".join(obj.changed_fields)

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_staff and (
            request.user.is_superuser or request.user.role == PortalUser.Role.ADMIN
        )

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PortalSettingAdminForm(forms.ModelForm):
    gmail_app_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        label="Gmail app password",
        help_text="Enter a Google App Password. It is stored encrypted; leave blank to keep the saved password.",
    )
    registration_required_fields = forms.MultipleChoiceField(
        choices=(
            ("first_name", "First name"),
            ("middle_name", "Middle name"),
            ("last_name", "Last name"),
            ("email", "Email address"),
            ("mobile_number", "Mobile number"),
        ),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Required student registration fields",
        help_text="Check a field to require it on the student registration form; uncheck it to make that field optional. Password is always required.",
    )

    class Meta:
        model = PortalSetting
        fields = (
            "default_accent", "registration_required_fields", "restrict_student_session_time",
            "email_verification_required", "mobile_verification_required", "auto_approve_students",
            "gmail_login_email", "gmail_sender_email", "gmail_app_password", "whatsapp_default_country_code",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["gmail_login_email"].label = "Gmail login email"
        self.fields["gmail_login_email"].help_text = "The Gmail account authenticated with its app password over SMTP."
        self.fields["gmail_sender_email"].label = "From email shown to users"
        self.fields["gmail_sender_email"].help_text = "Defaults to no-reply@gmail.com. Gmail must have this address configured as the account or a verified send-as alias."
        self.fields["whatsapp_default_country_code"].label = "Default WhatsApp country code"
        self.fields["whatsapp_default_country_code"].help_text = "PyWhatKit opens WhatsApp Web in the server user's default browser, sends OTPs automatically, and closes the tab after sending. Sign in there first and keep that Windows session unlocked while sending OTPs."
        self.fields["email_verification_required"].label = "Email verification and confirmation required"
        self.fields["mobile_verification_required"].label = "Mobile verification and confirmation required"
        if self.instance and self.instance.pk:
            if self.instance.has_gmail_app_password():
                self.fields["gmail_app_password"].help_text = "A Gmail app password is saved (encrypted). Leave blank to keep it, or enter a replacement."

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("email_verification_required"):
            if not cleaned.get("gmail_login_email"):
                self.add_error("gmail_login_email", "Set the Gmail account used to authenticate with SMTP.")
            if not cleaned.get("gmail_sender_email"):
                self.add_error("gmail_sender_email", "Set the From address shown to recipients.")
            if not cleaned.get("gmail_app_password") and not self.instance.has_gmail_app_password():
                self.add_error("gmail_app_password", "Enter and save the Gmail app password before enabling email verification.")
        return cleaned


class RegistrationFieldInlineForm(forms.ModelForm):
    required = forms.BooleanField(
        required=False,
        label="Required for student registration",
        help_text="Applicants must complete this field before submitting the form.",
    )

    class Meta:
        model = RegistrationField
        fields = ("label", "input_type", "required", "is_active", "position")


class RegistrationFieldInline(admin.StackedInline):
    model = RegistrationField
    form = RegistrationFieldInlineForm
    extra = 1
    verbose_name = "Additional registration field"
    verbose_name_plural = "Additional student registration fields"
    fields = ("label", "input_type", "required", "is_active", "position")
    ordering = ("position", "id")


class DashboardNavigationItemInline(admin.TabularInline):
    model = DashboardNavigationItem
    extra = 1
    verbose_name = "Dashboard navigation option"
    verbose_name_plural = "Additional dashboard navigation options"
    fields = ("label", "icon", "target", "audience", "position", "is_active")
    ordering = ("position", "id")


@admin.register(PortalSetting)
class PortalSettingAdmin(admin.ModelAdmin):
    form = PortalSettingAdminForm
    fieldsets = (
        ("Website appearance", {"fields": ("default_accent",)}),
        ('Student session time', {'fields': ('restrict_student_session_time',)}),
        ("Built-in student registration fields", {"fields": ("registration_required_fields",)}),
        ("Registration verification, confirmation and approval", {"fields": ("email_verification_required", "mobile_verification_required", "auto_approve_students")}),
        ("Gmail delivery for verification and account notices", {"fields": ("gmail_login_email", "gmail_sender_email", "gmail_app_password")}),
        ("WhatsApp Web delivery settings", {"fields": ("whatsapp_default_country_code",)}),
    )
    list_display = ("id", "default_accent", "updated_at")
    inlines = (RegistrationFieldInline, DashboardNavigationItemInline)

    def has_add_permission(self, request):
        return not PortalSetting.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        gmail_password = form.cleaned_data.get("gmail_app_password")
        if gmail_password:
            obj.set_gmail_app_password(gmail_password.replace(" ", ""))
        super().save_model(request, obj, form, change)


@admin.register(CompilerOption)
class CompilerOptionAdmin(admin.ModelAdmin):
    list_display = ("name", "engine", "is_active", "position")
    list_filter = ("engine", "is_active")
    search_fields = ("name", "description")
    ordering = ("position", "id")
    fields = ("name", "engine", "icon", "description", "is_active", "position")
    actions = ("enable_compilers", "disable_compilers")

    @admin.action(description="Enable selected compiler options")
    def enable_compilers(self, request, queryset):
        queryset.update(is_active=True)

    @admin.action(description="Remove selected compiler options from the chooser")
    def disable_compilers(self, request, queryset):
        queryset.update(is_active=False)
class StudentMembershipAdminForm(forms.ModelForm):
    """Safe student-management form with membership controls only."""
    classes = forms.ModelMultipleChoiceField(
        label="Classes",
        queryset=ClassSection.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Select the classes this student belongs to. Clear all selections to remove class memberships.",
    )
    labs = forms.ModelMultipleChoiceField(
        label="Compiler labs",
        queryset=CompilerLab.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Select the compiler labs this student can use. Clear all selections to remove lab memberships.",
    )

    class Meta:
        model = PortalUser
        fields = ("username", "first_name", "middle_name", "last_name", "email", "mobile_number", "student_number", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["classes"].queryset = ClassSection.objects.filter(is_active=True).select_related("course").order_by("course__code", "name")
        self.fields["labs"].queryset = CompilerLab.objects.filter(is_active=True).select_related("class_section", "class_section__course").order_by("class_section__name", "name")
        if self.instance and self.instance.pk:
            self.initial["classes"] = Enrollment.objects.filter(student=self.instance, active=True).values_list("class_section_id", flat=True)
            self.initial["labs"] = LabEnrollment.objects.filter(student=self.instance, active=True).values_list("lab_id", flat=True)

    def save(self, commit=True):
        student = super().save(commit=commit)
        self._student_for_memberships = student
        if commit:
            self.save_m2m()
        return student

    def _save_m2m(self):
        super()._save_m2m()
        student = getattr(self, "_student_for_memberships", None)
        if not student or not student.pk:
            return
        selected_classes = set(self.cleaned_data.get("classes", ()).values_list("pk", flat=True))
        selected_labs = set(self.cleaned_data.get("labs", ()).values_list("pk", flat=True))
        with transaction.atomic():
            for membership in Enrollment.objects.filter(student=student):
                membership.active = membership.class_section_id in selected_classes
                membership.save(update_fields=("active",))
            existing = set(Enrollment.objects.filter(student=student).values_list("class_section_id", flat=True))
            Enrollment.objects.bulk_create([Enrollment(student=student, class_section_id=pk) for pk in selected_classes - existing])
            for membership in LabEnrollment.objects.filter(student=student):
                membership.active = membership.lab_id in selected_labs
                membership.save(update_fields=("active",))
            existing_labs = set(LabEnrollment.objects.filter(student=student).values_list("lab_id", flat=True))
            LabEnrollment.objects.bulk_create([LabEnrollment(student=student, lab_id=pk) for pk in selected_labs - existing_labs])


@admin.register(StudentManagement)
class StudentManagementAdmin(PortalUserAdmin):
    """Student-only directory with safe class and lab membership controls."""
    form = StudentMembershipAdminForm
    fieldsets = (
        ("Student", {"fields": ("username", "first_name", "middle_name", "last_name", "email", "mobile_number", "student_number", "is_active")}),
        ("Class and lab membership", {"fields": ("classes", "labs")}),
    )
    readonly_fields = ("username", "student_number")
    list_display = ("username", "get_full_name", "email", "student_number", "membership_summary", "is_active")
    search_fields = ("username", "first_name", "middle_name", "last_name", "email", "student_number")
    list_filter = ("is_active",)

    def get_queryset(self, request):
        return super().get_queryset(request).filter(role=PortalUser.Role.STUDENT)

    def save_related(self, request, form, formsets, change):
        # Django's change form saves custom (non-model) membership fields in
        # the related-save phase. Keep this explicit so deselected memberships
        # are reliably deactivated after the student row is saved.
        super().save_related(request, form, formsets, change)
        self.message_user(request, "Student class and lab memberships updated.", level=messages.SUCCESS)

    @admin.display(description="Classes / labs")
    def membership_summary(self, obj):
        classes = Enrollment.objects.filter(student=obj, active=True).count()
        labs = LabEnrollment.objects.filter(student=obj, active=True).count()
        return f"{classes} class(es), {labs} lab(s)"
