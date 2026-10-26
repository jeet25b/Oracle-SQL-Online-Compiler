from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from decimal import Decimal

class PortalUser(AbstractUser):
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
    student_number = models.CharField(max_length=32, blank=True)
    teacher_number = models.CharField(max_length=32, blank=True)
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
        if self.is_superuser:
            self.registration_status = self.RegistrationStatus.APPROVED
        self.is_staff = self.role == self.Role.ADMIN or self.is_superuser
        super().save(*args, **kwargs)
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

class Course(models.Model):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    def __str__(self): return f"{self.code} — {self.name}"

class ClassSection(models.Model):
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="classes")
    name = models.CharField(max_length=80)
    academic_year = models.CharField(max_length=20, blank=True)
    section = models.CharField(max_length=32, blank=True)
    is_active = models.BooleanField(default=True)
    class Meta: unique_together = [("course", "name", "academic_year", "section")]
    def __str__(self): return f"{self.course.code} · {self.name} {self.section}".strip()

class TeacherAssignment(models.Model):
    teacher = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="teaching_assignments", limit_choices_to={"role": PortalUser.Role.TEACHER})
    class_section = models.ForeignKey(ClassSection, on_delete=models.CASCADE, related_name="teacher_assignments")
    subject = models.CharField(max_length=120, blank=True)
    class Meta: unique_together = [("teacher", "class_section", "subject")]

class Enrollment(models.Model):
    student = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="enrollments", limit_choices_to={"role": PortalUser.Role.STUDENT})
    class_section = models.ForeignKey(ClassSection, on_delete=models.CASCADE, related_name="enrollments")
    enrolled_at = models.DateTimeField(default=timezone.now)
    active = models.BooleanField(default=True)
    class Meta: unique_together = [("student", "class_section")]

class AttendanceSession(models.Model):
    user = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="attendance_sessions")
    login_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_activity_at = models.DateTimeField(default=timezone.now)
    logout_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=24, default="web")

class Assignment(models.Model):
    class_section = models.ForeignKey(ClassSection, on_delete=models.CASCADE, related_name="assignments")
    created_by = models.ForeignKey(PortalUser, on_delete=models.PROTECT, related_name="created_assignments")
    title = models.CharField(max_length=200)
    instructions = models.TextField(blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    max_score = models.DecimalField(max_digits=7, decimal_places=2, default=100)
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
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="tests")
    created_by = models.ForeignKey(PortalUser, on_delete=models.PROTECT)
    title = models.CharField(max_length=200)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    duration_minutes = models.PositiveIntegerField(default=60)
    total_marks = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    published = models.BooleanField(default=False)

class TestAttempt(models.Model):
    test = models.ForeignKey(SQLTest, on_delete=models.CASCADE, related_name="attempts")
    student = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="test_attempts")
    started_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True, blank=True)
    score = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, default="in_progress")
    class Meta: unique_together = [("test", "student")]

class Submission(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        REVIEWED = "reviewed", "Reviewed"
        LATE = "late", "Late"
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="submissions")
    student = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name="submissions")
    answer = models.TextField(blank=True)
    submitted_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SUBMITTED)
    score = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    feedback = models.TextField(blank=True)
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
    created_by = models.ForeignKey(PortalUser, on_delete=models.PROTECT)
    title = models.CharField(max_length=200)
    message = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    published = models.BooleanField(default=True)

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
