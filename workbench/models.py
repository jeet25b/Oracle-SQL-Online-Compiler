from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from decimal import Decimal
import re
import uuid
import base64
import hashlib
from datetime import timedelta


def _portal_secret_cipher():
    from cryptography.fernet import Fernet, MultiFernet

    def derive(secret):
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        return Fernet(key)

    dedicated = getattr(settings, "DB_CREDENTIALS_ENCRYPTION_KEY", "")
    legacy = derive(settings.SECRET_KEY)
    if not dedicated:
        return legacy

    ciphers = []
    for secret in (part.strip() for part in str(dedicated).split(",")):
        if secret:
            cipher = derive(secret)
            if cipher not in ciphers:
                ciphers.append(cipher)
    if not ciphers:
        return legacy
    ciphers.append(legacy)
    return MultiFernet(ciphers)


def normalize_mobile_number(value):
    value = str(value or "").strip()
    if not re.fullmatch(r"[+0-9().\-\s]+", value):
        return ""
    digits = re.sub(r"\D", "", value)
    if not 7 <= len(digits) <= 15:
        return ""
    return digits


def default_registration_required_fields():
    return ["first_name", "email", "mobile_number"]

def default_compiler_tab_appearance():
    return {
        "sqlplus": {"label": "SQLCL", "visible": True, "order": 1},
        "compiler": {"label": "SQL WorkSheet", "visible": False, "order": 2},
    }

class PortalUser(AbstractUser):
    can_manage_classes = models.BooleanField(default=False, help_text="Allow this teacher to allocate lecture and practical teachers in their assigned classes.")
    can_manage_timetable = models.BooleanField(default=False, help_text="Allow this teacher to manage timetables for their assigned classes.")
    class_selection_required = models.BooleanField(default=False)
    class_selected_at = models.DateTimeField(null=True, blank=True)
    unrestricted_session_time = models.BooleanField(default=False, help_text='Exempt this account from daily session allowances and the 30-minute inactivity timeout. Single-session security still applies.')
    active_student_session_token = models.CharField(max_length=64, blank=True, default='')
    active_student_session_key = models.CharField(max_length=40, blank=True, default='')
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        TEACHER = "TEACHER", "Teacher"
        ACCOUNTANT = "ACCOUNTANT", "Accountant"
        STUDENT = "STUDENT", "Student"
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.STUDENT, db_index=True)
    class RegistrationStatus(models.TextChoices):
        APPROVED = "APPROVED", "Approved"
        PENDING_ADMIN = "PENDING_ADMIN", "Pending admin review"
        PENDING_ACCOUNTANT = "PENDING_ACCOUNTANT", "Pending accountant approval"
        REJECTED = "REJECTED", "Rejected"
    registration_status = models.CharField(max_length=20, choices=RegistrationStatus.choices, default=RegistrationStatus.APPROVED, db_index=True)
    class DeactivationSource(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        TEACHER = "TEACHER", "Teacher"
        ACCOUNTANT = "ACCOUNTANT", "Accountant"
    deactivation_source = models.CharField(max_length=12, choices=DeactivationSource.choices, blank=True)
    middle_name = models.CharField(max_length=150, blank=True)
    avatar_image = models.ImageField(upload_to="portal-avatars/", blank=True)
    student_number = models.CharField(max_length=32, blank=True)
    teacher_number = models.CharField(max_length=32, blank=True)
    mobile_number = models.CharField(max_length=16, blank=True, db_index=True)
    oracle_username = models.CharField(max_length=128, blank=True)
    oracle_password_encrypted = models.TextField(blank=True, editable=False)
    oracle_connect_string = models.CharField(max_length=512, blank=True)
    email_verified = models.BooleanField(default=False)
    mobile_verified = models.BooleanField(default=False)
    approved_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="approved_registrations")
    approved_at = models.DateTimeField(null=True, blank=True)
    profile_data = models.JSONField(default=dict, blank=True)
    can_approve_students = models.BooleanField(default=False)
    can_view_all_compiler_activity = models.BooleanField(
        default=False,
        verbose_name="Can view all student compiler activity",
        help_text="Allow this teacher to monitor students, classes, and labs outside their teaching assignments.",
    )
    additional_roles = models.JSONField(default=list, blank=True)

    @property
    def portal_roles(self):
        roles = [self.role]
        if isinstance(self.additional_roles, list):
            valid = {choice for choice, _label in self.Role.choices}
            roles.extend(role for role in self.additional_roles if role in valid and role not in roles)
        return roles

    def has_portal_role(self, role):
        return role in self.portal_roles

    def get_full_name(self):
        return " ".join(part for part in (self.first_name, self.middle_name, self.last_name) if part).strip()

    def set_oracle_credentials(self, username, password, connect_string):
        self.oracle_username = str(username or "").strip()
        self.oracle_password_encrypted = _portal_secret_cipher().encrypt(str(password).encode("utf-8")).decode("ascii") if password else ""
        self.oracle_connect_string = str(connect_string or "").strip()

    def get_oracle_password(self):
        if not self.oracle_password_encrypted:
            return ""
        try:
            return _portal_secret_cipher().decrypt(self.oracle_password_encrypted.encode("ascii")).decode("utf-8")
        except Exception:
            return ""

    def has_oracle_credentials(self):
        return bool(self.oracle_username and self.oracle_password_encrypted and self.oracle_connect_string)

    def clear_oracle_credentials(self):
        self.oracle_username = ""
        self.oracle_password_encrypted = ""
        self.oracle_connect_string = ""
    @classmethod
    def next_account_id(cls, role):
        prefix_letter = "S" if role == cls.Role.STUDENT else "T" if role == cls.Role.TEACHER else ""
        if not prefix_letter:
            raise ValueError("Only Student and Teacher accounts receive portal IDs.")
        prefix = prefix_letter + timezone.localdate().strftime("%d%m%y")
        used = cls.objects.filter(username__startswith=prefix).values_list("username", flat=True)
        latest = max((int(value[-4:]) for value in used if len(value) == len(prefix) + 4 and value[-4:].isdigit()), default=0)
        if latest >= 9999:
            raise ValueError("The daily account ID limit has been reached.")
        return f"{prefix}{latest + 1:04d}"
    def assign_portal_number(self):
        if not self.pk:
            return ""
        if self.role == self.Role.STUDENT:
            field_name, prefix, current = "student_number", "STU", self.student_number
        elif self.role == self.Role.TEACHER:
            field_name, prefix, current = "teacher_number", "TCH", self.teacher_number
        else:
            return ""
        if current:
            return current

        number = f"{prefix}{self.pk:06d}"
        suffix = 2
        while type(self).objects.filter(**{f"{field_name}__iexact": number}).exclude(pk=self.pk).exists():
            number = f"{prefix}{self.pk:06d}-{suffix}"
            suffix += 1
        type(self).objects.filter(pk=self.pk).update(**{field_name: number})
        setattr(self, field_name, number)
        return number

    def save(self, *args, **kwargs):
        self.mobile_number = normalize_mobile_number(self.mobile_number)
        if self.pk and type(self).objects.filter(pk=self.pk, is_superuser=True).exists():
            self.is_superuser = True
        if self.is_superuser:
            self.role = self.Role.ADMIN
            self.registration_status = self.RegistrationStatus.APPROVED
        if self.role != self.Role.TEACHER and self.Role.TEACHER not in (self.additional_roles or []):
            self.can_approve_students = False
        valid_roles = {choice for choice, _label in self.Role.choices}
        if not isinstance(self.additional_roles, list):
            self.additional_roles = []
        self.additional_roles = list(dict.fromkeys(
            role for role in self.additional_roles
            if role in valid_roles and role != self.role and (role != self.Role.ADMIN or self.is_superuser)
        ))
        if self.Role.TEACHER not in [self.role, *self.additional_roles]:
            self.can_manage_classes = False
            self.can_manage_timetable = False
            self.can_approve_students = False
            self.can_view_all_compiler_activity = False
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"can_approve_students", "can_view_all_compiler_activity", "can_manage_classes", "can_manage_timetable"}
        self.is_staff = self.role == self.Role.ADMIN or self.is_superuser
        super().save(*args, **kwargs)
    def delete(self, *args, **kwargs):
        if self.is_superuser:
            raise ProtectedError("Superuser accounts cannot be deleted.", [self])
        return super().delete(*args, **kwargs)
    def has_perm(self, perm, obj=None):
        if self.is_active and self.role == self.Role.ADMIN:
            return True
        return super().has_perm(perm, obj)
    def has_module_perms(self, app_label):
        if self.is_active and self.role == self.Role.ADMIN:
            return True
        return super().has_module_perms(app_label)
    def __str__(self):
        return self.get_full_name() or self.username

class StudentManagement(PortalUser):
    class Meta:
        proxy = True
        verbose_name = "Student management"
        verbose_name_plural = "Student management"

class Course(models.Model):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    def __str__(self): return f"{self.code} — {self.name}"

class ClassSection(models.Model):
    branch = models.CharField(max_length=80, blank=True)
    semester = models.PositiveSmallIntegerField(null=True, blank=True)
    group = models.CharField(max_length=32, blank=True)
    daily_session_minutes = models.PositiveIntegerField(default=120)
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="classes")
    name = models.CharField(max_length=80)
    academic_year = models.CharField(max_length=20, blank=True)
    section = models.CharField(max_length=32, blank=True)
    attendance_start_time = models.TimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    class Meta:
        unique_together = [("course", "name", "academic_year", "section", "branch", "semester", "group")]
        verbose_name_plural = "Class management"
    @property
    def display_label(self):
        roman = ("", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X")
        semester = roman[self.semester] if self.semester and self.semester <= 10 else str(self.semester or "")
        branch = self.branch if self.branch and self.branch not in {self.name, self.course.name} else ""
        section = self.section or self.group or ""
        suffix = "-".join(value for value in (section, self.group if self.section else "") if value)
        return " ".join(value for value in (self.course.name, branch, semester, suffix) if value).strip()
    def __str__(self):
        return " · ".join(str(value) for value in (
            self.course.code,
            self.name,
            self.course.name,
            self.branch if self.branch not in {self.name, self.course.name} else "",
            f"Semester {self.semester}" if self.semester else "",
            f"Section {self.section}" if self.section else "",
            f"Group {self.group}" if self.group else "",
            self.academic_year,
        ) if value)


class Subject(models.Model):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=160)
    is_active = models.BooleanField(default=True)

    def __str__(self): return f"{self.code} · {self.name}"


class ClassSubject(models.Model):
    class_section = models.ForeignKey(ClassSection, on_delete=models.CASCADE, related_name="class_subjects")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="class_offerings")
    has_lecture = models.BooleanField(default=True)
    has_laboratory = models.BooleanField(default=False)

    class Meta: unique_together = [("class_section", "subject")]

    def __str__(self): return str(self.subject)

class TeacherAssignment(models.Model):
    class TeachingType(models.TextChoices):
        LECTURE = "lecture", "Lecture"
        LABORATORY = "laboratory", "Laboratory / practical"
    class_subject = models.ForeignKey(ClassSubject, on_delete=models.CASCADE, null=True, blank=True, related_name="teacher_allocations")
    teacher = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="teaching_assignments", limit_choices_to={"role": PortalUser.Role.TEACHER})
    class_section = models.ForeignKey(ClassSection, on_delete=models.CASCADE, related_name="teacher_assignments")
    subject = models.CharField(max_length=120, blank=True)
    teaching_type = models.CharField(max_length=16, choices=TeachingType.choices, default=TeachingType.LECTURE)
    lab = models.ForeignKey("CompilerLab", on_delete=models.CASCADE, null=True, blank=True, related_name="teacher_assignments")
    class Meta: unique_together = [("teacher", "class_section", "class_subject", "teaching_type", "lab")]

    def clean(self):
        from django.core.exceptions import ValidationError
        errors = {}
        if self.class_subject_id:
            if self.class_subject.class_section_id != self.class_section_id:
                errors["class_subject"] = "Choose a subject offered in this class."
        if self.teacher_id and self.teacher.Role.TEACHER not in self.teacher.portal_roles:
            errors["teacher"] = "Choose a teacher account."
        if errors: raise ValidationError(errors)


class TeacherActivityPreference(models.Model):
    """Per-teacher live monitor preferences."""
    teacher = models.OneToOneField(
        PortalUser, on_delete=models.CASCADE, related_name="activity_preference",
        limit_choices_to={"role": PortalUser.Role.TEACHER},
    )
    guided_setup_enabled = models.BooleanField(default=False)
    guided_setup_revision = models.PositiveIntegerField(default=1)
    presence_offline_timeout_seconds = models.PositiveSmallIntegerField(default=90)
    independent_compiler_sessions_enabled = models.BooleanField(
        default=False,
        help_text="Allow assigned students to keep an Oracle compiler session separate from their portal login session.",
    )
    updated_at = models.DateTimeField(auto_now=True)


class TimetableEntry(models.Model):
    class Frequency(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"
    allocation = models.ForeignKey(TeacherAssignment, on_delete=models.PROTECT, related_name="timetable_entries")
    frequency = models.CharField(max_length=8, choices=Frequency.choices, default=Frequency.WEEKLY)
    start_date = models.DateField(default=timezone.localdate)
    end_date = models.DateField(null=True, blank=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    weekdays = models.JSONField(default=list, blank=True)
    month_days = models.JSONField(default=list, blank=True)
    room = models.CharField(max_length=80, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(PortalUser, on_delete=models.PROTECT, related_name="created_timetable_entries")

    class Meta:
        ordering = ("start_time", "pk")
        verbose_name_plural = "Timetable"

    @property
    def recurrence_label(self):
        if self.frequency == self.Frequency.DAILY: return "Daily"
        if self.frequency == self.Frequency.WEEKLY:
            labels = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
            return "Weekly · " + ", ".join(labels[day] for day in self.weekdays if isinstance(day, int) and 0 <= day <= 6)
        return "Monthly · dates " + ", ".join(str(day) for day in self.month_days)

    def clean(self):
        from django.core.exceptions import ValidationError
        errors = {}
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            errors["end_time"] = "End time must be after start time on the same day."
        if self.start_date and self.end_date and self.end_date < self.start_date:
            errors["end_date"] = "End date cannot be before start date."
        for field, needed, maximum in (("weekdays", self.frequency == "weekly", 6), ("month_days", self.frequency == "monthly", 31)):
            values = getattr(self, field)
            minimum = 0 if field == "weekdays" else 1
            if needed and (not isinstance(values, list) or not values or any(type(day) is not int or not minimum <= day <= maximum for day in values)):
                errors[field] = "Choose the days on which this session repeats."
            if not needed: setattr(self, field, [])
        if self.allocation_id:
            allocation = self.allocation
            if not allocation.class_subject_id:
                errors["allocation"] = "Allocate a teacher to a class subject first."
            elif self.is_active:
                if not allocation.class_section.is_active or not allocation.class_subject.subject.is_active or not allocation.teacher.is_active:
                    errors["allocation"] = "Choose an active class, subject, and teacher."
                try:
                    allocation.clean()
                except ValidationError:
                    errors["allocation"] = "The teacher allocation is no longer valid. Update it in Class management."
        if errors: raise ValidationError(errors)
        if self.is_active and self.allocation_id and self.start_date and self.start_time and self.end_time:
            from .timetable import validate_conflicts
            validate_conflicts(self)

class Enrollment(models.Model):
    student = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="enrollments", limit_choices_to={"role": PortalUser.Role.STUDENT})
    class_section = models.ForeignKey(ClassSection, on_delete=models.CASCADE, related_name="enrollments")
    enrolled_at = models.DateTimeField(default=timezone.now)
    active = models.BooleanField(default=True)
    class Meta: unique_together = [("student", "class_section")]

class StudentDailyAccessReset(models.Model):
    student = models.ForeignKey(PortalUser, on_delete=models.CASCADE)
    day = models.DateField()
    reset_at = models.DateTimeField(default=timezone.now)
    reset_by = models.ForeignKey(PortalUser, null=True, on_delete=models.SET_NULL, related_name="student_access_resets")

    class Meta:
        constraints = [models.UniqueConstraint(fields=("student", "day"), name="student_daily_access_reset")]


class AttendanceSession(models.Model):
    user = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="attendance_sessions")
    class_section = models.ForeignKey("ClassSection", on_delete=models.SET_NULL, null=True, blank=True, related_name="attendance_sessions")
    lab = models.ForeignKey("CompilerLab", on_delete=models.SET_NULL, null=True, blank=True, related_name="attendance_sessions")
    login_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_activity_at = models.DateTimeField(default=timezone.now)
    last_heartbeat_at = models.DateTimeField(default=timezone.now, db_index=True)
    logout_at = models.DateTimeField(null=True, blank=True)
    logout_estimated = models.BooleanField(default=False)
    end_reason = models.CharField(max_length=16, blank=True)
    active_seconds = models.PositiveIntegerField(default=0)
    idle_seconds = models.PositiveIntegerField(default=0)
    is_active_now = models.BooleanField(default=False)
    compiler_active = models.BooleanField(default=False)
    current_page = models.CharField(max_length=16, default="portal")
    source = models.CharField(max_length=24, default="web")


class CompilerLab(models.Model):
    daily_session_minutes = models.PositiveIntegerField(null=True, blank=True)
    class_section = models.ForeignKey(ClassSection, on_delete=models.CASCADE, related_name="compiler_labs")
    created_by = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="created_compiler_labs")
    name = models.CharField(max_length=120)
    attendance_start_time = models.TimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    class Meta:
        ordering = ("class_section__name", "name")
        unique_together = (("class_section", "name"),)
    def __str__(self): return f"{self.class_section} · {self.name}"


class LabEnrollment(models.Model):
    student = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="lab_enrollments")
    lab = models.ForeignKey(CompilerLab, on_delete=models.CASCADE, related_name="enrollments")
    active = models.BooleanField(default=True)
    enrolled_at = models.DateTimeField(default=timezone.now)
    class Meta: unique_together = (("student", "lab"),)


class CompilerExecutionEvent(models.Model):
    student = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="compiler_execution_events")
    attendance_session = models.ForeignKey(AttendanceSession, on_delete=models.SET_NULL, null=True, blank=True, related_name="compiler_executions")
    class_section = models.ForeignKey(ClassSection, on_delete=models.SET_NULL, null=True, blank=True, related_name="compiler_executions")
    lab = models.ForeignKey(CompilerLab, on_delete=models.SET_NULL, null=True, blank=True, related_name="compiler_executions")
    source = models.CharField(max_length=16, default="PL/SQL")
    status = models.CharField(max_length=12)
    duration_ms = models.PositiveIntegerField(default=0)
    row_count = models.PositiveIntegerField(default=0)
    typed_chars = models.PositiveIntegerField(null=True, blank=True, default=None)
    pasted_chars = models.PositiveIntegerField(null=True, blank=True, default=None)
    paste_count = models.PositiveIntegerField(null=True, blank=True, default=None)
    edits_after_paste = models.PositiveIntegerField(null=True, blank=True, default=None)
    executed_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-executed_at", "-id")
        indexes = [models.Index(fields=("student", "executed_at"), name="compiler_event_user_time")]

class AssessmentDefinition(models.Model):
    parent_task = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="question_drafts", editable=False)
    """Reusable, immutable content; delivery rows hold class schedules and results."""
    kind = models.CharField(max_length=8, choices=[("daily", "Daily task"), ("test", "Test")])
    code = models.CharField(max_length=40, unique=True, null=True, blank=True, editable=False)
    title = models.CharField(max_length=200)
    created_by = models.ForeignKey(PortalUser, on_delete=models.PROTECT, related_name="assessment_library")
    payload = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-created_at", "-pk")

    def save(self, *args, **kwargs):
        from django.db import transaction
        with transaction.atomic():
            super().save(*args, **kwargs)
            if not self.code:
                prefix = "DT" if self.kind == "daily" else "TS"
                self.code = f"{prefix}-{timezone.localtime(self.created_at).year}-{self.pk:06d}"
                type(self).objects.filter(pk=self.pk).update(code=self.code)

    def __str__(self):
        return f"{self.code} · {self.title}"


class Assignment(models.Model):
    assessment = models.ForeignKey(AssessmentDefinition, null=True, blank=True, on_delete=models.PROTECT, related_name="daily_mappings")

    lab = models.ForeignKey(CompilerLab, null=True, blank=True, on_delete=models.PROTECT, related_name="daily_tasks")

    verification_sql = models.TextField(blank=True)

    start_at = models.DateTimeField(null=True, blank=True)
    lab_start_at = models.DateTimeField(null=True, blank=True)
    lab_end_at = models.DateTimeField(null=True, blank=True)
    late_policy = models.CharField(max_length=8, choices=[("allow", "Allow late submissions"), ("lock", "Lock at deadline")], default="allow")
    starter_sql = models.TextField(blank=True)
    tags = models.CharField(max_length=300, blank=True)
    hint = models.TextField(blank=True)
    sample_schema = models.TextField(blank=True)

    class GradingMode(models.TextChoices):
        MANUAL = "manual", "Teacher review"
        SQL_MATCH = "sql_match", "Match SQL statement"
        RESULT_MATCH = "result_match", "Compare query results (SELECT)"

    enabled_at = models.DateTimeField(null=True, blank=True)
    class_section = models.ForeignKey(ClassSection, on_delete=models.CASCADE, related_name="assignments")
    created_by = models.ForeignKey(PortalUser, on_delete=models.PROTECT, related_name="created_assignments")
    title = models.CharField(max_length=200)
    instructions = models.TextField(blank=True)
    general_instructions = models.TextField(blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    max_score = models.DecimalField(max_digits=7, decimal_places=2, default=100)
    grading_mode = models.CharField(max_length=16, choices=GradingMode.choices, default=GradingMode.MANUAL)
    expected_sql = models.TextField(blank=True)
    published = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

class SQLQuestion(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="sql_questions")
    created_by = models.ForeignKey(PortalUser, on_delete=models.PROTECT)
    title = models.CharField(max_length=200)
    prompt = models.TextField()
    starter_sql = models.TextField(blank=True)
    expected_output = models.TextField(blank=True)
    marks = models.DecimalField(max_digits=7, decimal_places=2, default=1)
    is_active = models.BooleanField(default=True)

class SQLTest(models.Model):
    assessment = models.ForeignKey(AssessmentDefinition, null=True, blank=True, on_delete=models.PROTECT, related_name="test_mappings")

    class ScheduleFrequency(models.TextChoices):
        ANYTIME = "anytime", "Anytime"
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
    class GradingMode(models.TextChoices):
        MANUAL = "manual", "Teacher review"
        SQL_MATCH = "sql_match", "Match SQL statement"
        RESULT_MATCH = "result_match", "Compare query results (SELECT)"
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="tests")
    created_by = models.ForeignKey(PortalUser, on_delete=models.PROTECT)
    class_section = models.ForeignKey(ClassSection, on_delete=models.CASCADE, related_name="sql_tests", null=True, blank=True)
    lab = models.ForeignKey(CompilerLab, on_delete=models.CASCADE, related_name="sql_tests", null=True, blank=True)
    title = models.CharField(max_length=200)
    instructions = models.TextField(blank=True)
    sample_schema = models.TextField(blank=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    duration_minutes = models.PositiveIntegerField(default=60)
    total_marks = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    published = models.BooleanField(default=False)
    schedule_frequency = models.CharField(max_length=8, choices=ScheduleFrequency.choices, default=ScheduleFrequency.ANYTIME)
    schedule_start_time = models.TimeField(null=True, blank=True)
    schedule_end_time = models.TimeField(null=True, blank=True)
    schedule_weekdays = models.JSONField(default=list, blank=True)
    grading_mode = models.CharField(max_length=16, choices=GradingMode.choices, default=GradingMode.MANUAL)
    allow_typed_answer = models.BooleanField(default=False)
    email_results_on_submit = models.BooleanField(default=False, help_text="Automatically email result scorecard to student on submission.")

class TestAttempt(models.Model):
    test = models.ForeignKey(SQLTest, on_delete=models.CASCADE, related_name="attempts")
    student = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="test_attempts")
    started_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True, blank=True)
    score = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, default="in_progress")
    allow_late_access = models.BooleanField(default=False, help_text="Allow student to take/resume test after scheduled window has ended.")
    allow_full_duration = models.BooleanField(default=False, help_text="Allow student full test duration even if logging in late.")
    class Meta: unique_together = [("test", "student")]

class SQLTestQuestion(models.Model):
    verification_sql = models.TextField(blank=True)

    test = models.ForeignKey(SQLTest, on_delete=models.CASCADE, related_name="questions")
    position = models.PositiveIntegerField(default=1)
    prompt = models.TextField()
    marks = models.DecimalField(max_digits=7, decimal_places=2, default=1)
    expected_sql = models.TextField(blank=True)

    class Meta:
        ordering = ("position", "id")

class TestAnswer(models.Model):
    attempt = models.ForeignKey(TestAttempt, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(SQLTestQuestion, on_delete=models.CASCADE, related_name="answers")
    answer = models.TextField(blank=True)
    is_correct = models.BooleanField(null=True, blank=True)
    score = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    feedback = models.TextField(blank=True)
    class Meta:
        unique_together = [("attempt", "question")]

class Submission(models.Model):
    is_late = models.BooleanField(default=False)

    first_attempt_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    is_correct = models.BooleanField(default=False)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        AUTO_GRADED = "auto_graded", "Automatically evaluated"
        DISPUTED = "disputed", "Disputed"
        REVIEWED = "reviewed", "Reviewed"
        LATE = "late", "Late"
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="submissions")
    student = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="submissions")
    answer = models.TextField(blank=True)
    submitted_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SUBMITTED)
    score = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    feedback = models.TextField(blank=True)
    dispute_message = models.TextField(blank=True)
    disputed_at = models.DateTimeField(null=True, blank=True)
    dispute_response = models.TextField(blank=True)
    dispute_resolved_at = models.DateTimeField(null=True, blank=True)
    @property
    def review_state(self):
        if self.is_correct:
            return "completed"
        if self.status in {self.Status.AUTO_GRADED, self.Status.REVIEWED}:
            return "revision"
        return "submitted"

    @property
    def review_label(self):
        if self.status == self.Status.DISPUTED and not self.is_correct:
            return "Under review"
        return {"completed": "Correct", "revision": "Needs revision", "submitted": "Awaiting review"}[self.review_state]

    class Meta: unique_together = [("assignment", "student")]

class StudyMaterial(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="materials")
    uploaded_by = models.ForeignKey(PortalUser, on_delete=models.PROTECT)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    file = models.FileField(upload_to="study-materials/%Y/%m/")
    created_at = models.DateTimeField(default=timezone.now)

class Announcement(models.Model):
    class_section = models.ForeignKey(ClassSection, on_delete=models.CASCADE, related_name="announcements", null=True, blank=True)
    lab = models.ForeignKey(CompilerLab, on_delete=models.CASCADE, related_name="announcements", null=True, blank=True)
    student = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="student_announcements", null=True, blank=True, limit_choices_to={"role": PortalUser.Role.STUDENT})
    created_by = models.ForeignKey(PortalUser, on_delete=models.PROTECT)
    title = models.CharField(max_length=200)
    message = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    published = models.BooleanField(default=True)


class SharedCompilerCode(models.Model):
    class EditorKind(models.TextChoices):
        SQLCL = "sqlplus", "SQLCL"
        WORKSHEET = "worksheet", "SQL WorkSheet"

    class_section = models.ForeignKey(ClassSection, on_delete=models.CASCADE, related_name="shared_compiler_code", null=True, blank=True)
    lab = models.ForeignKey(CompilerLab, on_delete=models.CASCADE, related_name="shared_compiler_code", null=True, blank=True)
    student = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="shared_compiler_code", null=True, blank=True, limit_choices_to={"role": PortalUser.Role.STUDENT})
    all_students = models.BooleanField(default=False)
    created_by = models.ForeignKey(PortalUser, on_delete=models.PROTECT, related_name="shared_compiler_code_created")
    title = models.CharField(max_length=200)
    editor_kind = models.CharField(max_length=12, choices=EditorKind.choices)
    code = models.TextField()
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    published = models.BooleanField(default=True)

    class Meta:
        ordering = ("-created_at",)

class TeacherMessage(models.Model):
    teacher = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="teacher_messages")
    student = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="received_teacher_messages")
    body = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True, blank=True)

class AuditEvent(models.Model):
    actor = models.ForeignKey(PortalUser, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=80, db_index=True)
    target = models.CharField(max_length=200, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

class PortalUserChangeLog(models.Model):
    class Action(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        DELETED = "deleted", "Deleted"

    class Origin(models.TextChoices):
        LIVE = "live", "Recorded at event time"
        BACKFILLED = "backfilled", "Reconstructed for existing account"

    # Creation and deletion records have no subject FK so they survive deletion.
    # Update records cascade when their subject is deleted.
    subject = models.ForeignKey(
        PortalUser, on_delete=models.CASCADE, null=True, blank=True,
        related_name="change_logs",
    )
    subject_id_snapshot = models.BigIntegerField(db_index=True)
    username_snapshot = models.CharField(max_length=150)
    action = models.CharField(max_length=8, choices=Action.choices, db_index=True)
    origin = models.CharField(max_length=12, choices=Origin.choices, default=Origin.LIVE)
    changed_fields = models.JSONField(default=list, blank=True)
    actor = models.ForeignKey(
        PortalUser, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="performed_user_changes",
    )
    actor_name_snapshot = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-created_at", "-id")

    def __str__(self):
        return f"{self.get_action_display()}: {self.username_snapshot}"


class PortalSetting(models.Model):
    restrict_student_session_time = models.BooleanField(default=True, help_text='Apply daily allowances and the 30-minute inactivity timeout to students. Admin accounts and the Admin group are always exempt.')
    """Single-row settings shared across the learning portal."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    default_accent = models.CharField(max_length=16, default="violet")
    sqlplus_buffer_entries = models.PositiveIntegerField(default=5000)
    compiler_tab_appearance = models.JSONField(default=default_compiler_tab_appearance, blank=True)
    registration_required_fields = models.JSONField(
        default=default_registration_required_fields, blank=True
    )
    email_verification_required = models.BooleanField(default=False)
    mobile_verification_required = models.BooleanField(default=False)
    auto_approve_students = models.BooleanField(default=False)
    gmail_login_email = models.EmailField(blank=True)
    gmail_sender_email = models.EmailField(default="no-reply@gmail.com")
    gmail_app_password_encrypted = models.TextField(blank=True)
    whatsapp_phone_number_id = models.CharField(max_length=80, blank=True)
    whatsapp_access_token_encrypted = models.TextField(blank=True)
    whatsapp_graph_version = models.CharField(max_length=16, default="v23.0")
    whatsapp_otp_template = models.CharField(max_length=100, default="registration_otp")
    whatsapp_template_language = models.CharField(max_length=12, default="en")
    whatsapp_default_country_code = models.CharField(max_length=4, default="91")
    updated_at = models.DateTimeField(auto_now=True)

    def _get_secret(self, encrypted_value):
        if not encrypted_value:
            return ""
        try:
            return _portal_secret_cipher().decrypt(encrypted_value.encode("ascii")).decode("utf-8")
        except Exception:
            return ""

    def _set_secret(self, value):
        return _portal_secret_cipher().encrypt(value.encode("utf-8")).decode("ascii") if value else ""

    def get_gmail_app_password(self):
        return self._get_secret(self.gmail_app_password_encrypted)

    def set_gmail_app_password(self, value):
        self.gmail_app_password_encrypted = self._set_secret(value)

    def has_gmail_app_password(self):
        return bool(self.gmail_app_password_encrypted)

    def get_whatsapp_access_token(self):
        return self._get_secret(self.whatsapp_access_token_encrypted)

    def set_whatsapp_access_token(self, value):
        self.whatsapp_access_token_encrypted = self._set_secret(value)

    def has_whatsapp_access_token(self):
        return bool(self.whatsapp_access_token_encrypted)


class CompilerTabAppearanceOverride(models.Model):
    """Shared SQLCL / SQL WorkSheet tab appearance for an individual student."""
    student = models.OneToOneField(
        PortalUser, on_delete=models.CASCADE, related_name="compiler_tab_appearance_override",
        limit_choices_to={"role": PortalUser.Role.STUDENT},
    )
    appearance = models.JSONField(default=default_compiler_tab_appearance)
    updated_by = models.ForeignKey(PortalUser, on_delete=models.SET_NULL, null=True, blank=True, related_name="compiler_tab_appearance_changes")
    updated_at = models.DateTimeField(auto_now=True)


def registration_otp_expiry():
    return timezone.now() + timedelta(minutes=10)


class PendingRegistration(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    registration_data = models.JSONField(default=dict)
    profile_data = models.JSONField(default=dict, blank=True)
    password_hash = models.CharField(max_length=256)
    email_code_hash = models.CharField(max_length=64, blank=True)
    mobile_code_hash = models.CharField(max_length=64, blank=True)
    email_verified = models.BooleanField(default=False)
    mobile_verified = models.BooleanField(default=False)
    attempts = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField(default=registration_otp_expiry, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ProfileUpdateChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile_update_challenges")
    email = models.EmailField(blank=True)
    mobile_number = models.CharField(max_length=16, blank=True)
    email_code_hash = models.CharField(max_length=64, blank=True)
    mobile_code_hash = models.CharField(max_length=64, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField(default=registration_otp_expiry, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)


def registration_field_key():
    return uuid.uuid4().hex


class RegistrationField(models.Model):
    class InputType(models.TextChoices):
        TEXT = "text", "Short text"
        EMAIL = "email", "Email"
        PHONE = "tel", "Phone"
        NUMBER = "number", "Number"
        DATE = "date", "Date"
        TEXTAREA = "textarea", "Long text"

    setting = models.ForeignKey(PortalSetting, on_delete=models.CASCADE, related_name="custom_registration_fields")
    key = models.CharField(max_length=32, unique=True, default=registration_field_key, editable=False)
    label = models.CharField(max_length=80)
    input_type = models.CharField(max_length=12, choices=InputType.choices, default=InputType.TEXT)
    required = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("position", "id")

    def __str__(self):
        return self.label


class DashboardNavigationItem(models.Model):
    class Audience(models.TextChoices):
        ALL = "ALL", "Students and teachers"
        STUDENT = "STUDENT", "Students"
        TEACHER = "TEACHER", "Teachers"

    setting = models.ForeignKey(PortalSetting, on_delete=models.CASCADE, related_name="dashboard_navigation_items")
    label = models.CharField(max_length=50)
    icon = models.CharField(max_length=8, blank=True, default="•")
    target = models.CharField(
        max_length=200,
        help_text="Use #section-id for a dashboard section or an internal path beginning with /.",
    )
    audience = models.CharField(max_length=8, choices=Audience.choices, default=Audience.ALL)
    position = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("position", "id")

    def clean(self):
        from django.core.exceptions import ValidationError
        target = (self.target or "").strip()
        valid_fragment = bool(re.fullmatch(r"#[A-Za-z][A-Za-z0-9_-]*", target))
        valid_path = target.startswith("/") and not target.startswith("//") and "\\" not in target
        if not (valid_fragment or valid_path):
            raise ValidationError({"target": "Enter a dashboard section such as #courses or an internal path beginning with /."})
        self.target = target

    def __str__(self):
        return self.label


class CompilerOption(models.Model):
    class Engine(models.TextChoices):
        ORACLE = "ORACLE", "Oracle SQL"
        MYSQL = "MYSQL", "MySQL"
        POSTGRESQL = "POSTGRESQL", "PostgreSQL"
        PLSQL = "PLSQL", "PL/SQL"
        CUSTOM = "CUSTOM", "Custom compiler"

    name = models.CharField(max_length=60)
    engine = models.CharField(
        max_length=16,
        choices=Engine.choices,
        default=Engine.CUSTOM,
        help_text="Oracle SQL and PL/SQL use the current Oracle backend. Other engines appear as unavailable until their execution backend is implemented and configured.",
    )
    icon = models.CharField(max_length=40, blank=True, default="⌘")
    description = models.CharField(max_length=160, blank=True)
    is_active = models.BooleanField(default=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("position", "id")

    @property
    def execution_ready(self):
        # The current execution API uses the Oracle driver. PL/SQL runs on Oracle.
        return self.engine in {self.Engine.ORACLE, self.Engine.PLSQL}

    def __str__(self):
        return self.name

class CompilerQueryHistory(models.Model):
    class Source(models.TextChoices):
        PLSQL = "PL/SQL", "PL/SQL"
        SQLPLUS = "SQL*Plus", "SQL*Plus"

    class ExecutionStatus(models.TextChoices):
        SUCCESS = "success", "Success"
        ERROR = "error", "Failed"

    student = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="compiler_query_history")
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.PLSQL)
    sql = models.TextField()
    status = models.CharField(max_length=8, choices=ExecutionStatus.choices, default=ExecutionStatus.SUCCESS)
    duration_ms = models.PositiveIntegerField(default=0)
    row_count = models.PositiveIntegerField(default=0)
    typed_chars = models.PositiveIntegerField(null=True, blank=True, default=None)
    pasted_chars = models.PositiveIntegerField(null=True, blank=True, default=None)
    paste_count = models.PositiveIntegerField(null=True, blank=True, default=None)
    pasted_texts = models.JSONField(default=list, blank=True)
    edits_after_paste = models.PositiveIntegerField(null=True, blank=True, default=None)
    input_method = models.CharField(max_length=20, blank=True, default="")
    input_source_map = models.JSONField(default=list, blank=True)
    message = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    executed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-executed_at", "-id")
        indexes = [models.Index(fields=("student", "executed_at"), name="compiler_hist_user_time")]

    def __str__(self):
        return f"{self.student} · {self.source} · {self.executed_at:%Y-%m-%d %H:%M}"

class CompilerEditorShare(models.Model):
    class AccessMode(models.TextChoices):
        PRIVATE = "private", "Private"
        STATEMENT = "statement", "One statement"
        SESSION = "session", "This session"
        ALWAYS = "always", "Always allow"

    student = models.OneToOneField(PortalUser, on_delete=models.CASCADE, related_name="compiler_editor_share")
    access_mode = models.CharField(max_length=12, choices=AccessMode.choices, default=AccessMode.PRIVATE)
    editor_text = models.TextField(blank=True)
    sqlplus_text = models.TextField(blank=True)
    session_token = models.CharField(max_length=64, blank=True)
    statement_snapshot_ready = models.BooleanField(default=False)
    edit_permission_requested_at = models.DateTimeField(null=True, blank=True)
    edit_permission_requested_by = models.ForeignKey(
        PortalUser, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="compiler_edit_permission_requests",
    )
    revision = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.student} · editor sharing: {self.access_mode}"


class CompilerRemoteTerminalCommand(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Waiting for student terminal"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="remote_terminal_commands")
    teacher = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="sent_remote_terminal_commands")
    request_id = models.UUIDField()
    command = models.TextField()
    editor_kind = models.CharField(max_length=12, choices=(("sqlplus", "SQLCL"), ("worksheet", "SQL WorkSheet")), default="sqlplus")
    run_script = models.BooleanField(default=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED, db_index=True)
    output = models.TextField(blank=True)
    error = models.TextField(blank=True)
    execution_finished = models.BooleanField(default=False)
    requested_at = models.DateTimeField(default=timezone.now, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-requested_at",)
        constraints = [models.UniqueConstraint(fields=("teacher", "request_id"), name="remote_terminal_teacher_request")]
        indexes = [models.Index(fields=("student", "status", "requested_at"), name="remote_term_student_status")]

    def __str__(self):
        return f"Remote SQLCL command for {self.student_id} · {self.status}"

class CompilerActivitySlice(models.Model):
    attendance_session = models.ForeignKey(AttendanceSession, on_delete=models.CASCADE, related_name="activity_slices")
    class_section = models.ForeignKey(ClassSection, on_delete=models.SET_NULL, null=True, blank=True, related_name="compiler_activity_slices")
    lab = models.ForeignKey(CompilerLab, on_delete=models.SET_NULL, null=True, blank=True, related_name="activity_slices")
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_heartbeat_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_activity_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    active_seconds = models.PositiveIntegerField(default=0)
    idle_seconds = models.PositiveIntegerField(default=0)
    is_active_now = models.BooleanField(default=False)
    compiler_active = models.BooleanField(default=False)
    current_page = models.CharField(max_length=16, default="portal")

    class Meta:
        ordering = ("started_at", "id")
        indexes = [models.Index(fields=("attendance_session", "started_at"), name="compiler_slice_sess_time")]


class StudentFee(models.Model):
    student = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="fee_invoices", limit_choices_to={"role": PortalUser.Role.STUDENT})
    title = models.CharField(max_length=160)
    academic_year = models.CharField(max_length=20, blank=True)
    amount_due = models.DecimalField(max_digits=10, decimal_places=2)
    due_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(PortalUser, on_delete=models.PROTECT, related_name="created_fee_invoices")
    created_at = models.DateTimeField(default=timezone.now)

    @property
    def amount_paid(self):
        if "payments" in self._prefetched_objects_cache:
            return sum((payment.amount for payment in self._prefetched_objects_cache["payments"]), Decimal("0.00"))
        return self.payments.aggregate(total=models.Sum("amount"))["total"] or Decimal("0.00")

    @property
    def balance_due(self):
        return self.amount_due - self.amount_paid

    def __str__(self):
        return f"{self.student} — {self.title}"

class StudentFeePayment(models.Model):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        BANK = "bank", "Bank transfer"
        CARD = "card", "Card"
        OTHER = "other", "Other"

    fee = models.ForeignKey(StudentFee, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=12, choices=Method.choices, default=Method.OTHER)
    reference = models.CharField(max_length=100, blank=True)
    note = models.CharField(max_length=240, blank=True)
    received_by = models.ForeignKey(PortalUser, on_delete=models.PROTECT, related_name="received_fee_payments")
    received_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.fee} — {self.amount}"


class PasswordResetChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    channel = models.CharField(max_length=10)
    destination = models.CharField(max_length=254)
    code_hash = models.CharField(max_length=64)
    password_snapshot = models.CharField(max_length=256)
    attempts = models.PositiveSmallIntegerField(default=0)
    used = models.BooleanField(default=False)
    expires_at = models.DateTimeField(default=registration_otp_expiry, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class CompilerPasteEvent(models.Model):
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    attendance_session = models.ForeignKey("AttendanceSession", on_delete=models.SET_NULL, null=True, blank=True, related_name="paste_events")
    source = models.CharField(max_length=10)
    text = models.TextField()
    truncated = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class CompilerClientActivityEvent(models.Model):
    class Kind(models.TextChoices):
        PAGE_HIDDEN = "page_hidden", "Compiler tab hidden"
        PAGE_VISIBLE = "page_visible", "Compiler tab visible"
        WINDOW_BLUR = "window_blur", "Browser window lost focus"
        WINDOW_FOCUS = "window_focus", "Browser window focused"

    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="compiler_client_activity_events")
    attendance_session = models.ForeignKey("AttendanceSession", on_delete=models.SET_NULL, null=True, blank=True, related_name="client_activity_events")
    source = models.CharField(max_length=12, default="Workspace")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=("student", "created_at"), name="client_event_user_time")]


class SetupGuideProgress(models.Model):
    student = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="setup_guide_progress")
    teacher = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="student_setup_guides")
    completed_revision = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("student", "teacher"), name="unique_student_setup_guide")]


class OracleConnectionProfile(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="oracle_connections")
    name = models.CharField(max_length=120)
    username = models.CharField(max_length=255)
    connect_string = models.TextField()
    password_encrypted = models.TextField(editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "name"], name="unique_oracle_connection_name")]

    def get_oracle_password(self):
        try:
            return _portal_secret_cipher().decrypt(self.password_encrypted.encode("ascii")).decode("utf-8")
        except Exception:
            return ""

    def set_oracle_password(self, password):
        self.password_encrypted = _portal_secret_cipher().encrypt(str(password).encode("utf-8")).decode("ascii")


class ProjectRunnerConfiguration(models.Model):
    """The single DBA-provisioned sandbox shared by project-file checks."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    max_active_checks = models.PositiveSmallIntegerField(default=30)
    teacher_active_checks = models.PositiveSmallIntegerField(default=5)
    student_active_checks = models.PositiveSmallIntegerField(default=25)
    sandbox_connection = models.OneToOneField(OracleConnectionProfile, on_delete=models.PROTECT, related_name="project_runner_configuration")
    updated_at = models.DateTimeField(auto_now=True)


class ProjectExecution(models.Model):
    """Resumable, statement-by-statement execution in a DBA-provided sandbox."""
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        QUEUED = 'queued', 'Queued'
        READY = 'ready', 'Ready'
        RUNNING = "running", "Running"
        PAUSED = "paused", "Paused on error"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="project_executions")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="student_project_executions", null=True, blank=True)
    name = models.CharField(max_length=180)
    original_file_name = models.CharField(max_length=255, blank=True, default='')
    activity_kind = models.CharField(max_length=10, default='file')
    activity_id = models.PositiveIntegerField(null=True, blank=True)
    setup_statement_count = models.PositiveIntegerField(default=0)
    workspace_state = models.JSONField(default=dict, blank=True)
    sandbox_connection = models.ForeignKey(OracleConnectionProfile, on_delete=models.PROTECT, related_name="project_executions")
    source_text = models.TextField()
    statements = models.JSONField(default=list)
    results = models.JSONField(default=list)
    next_index = models.PositiveIntegerField(default=0)
    queue_role = models.CharField(max_length=7, default='STUDENT')
    slot_reserved = models.BooleanField(default=False, db_index=True)
    schema_name = models.CharField(max_length=30, blank=True, default='')
    schema_password_encrypted = models.TextField(blank=True, default='', editable=False)
    schema_expires_at = models.DateTimeField(null=True, blank=True)
    schema_removed_at = models.DateTimeField(null=True, blank=True)
    schema_cleanup_error = models.TextField(blank=True, default='')
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
