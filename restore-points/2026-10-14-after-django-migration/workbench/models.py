from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

class PortalUser(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        TEACHER = "TEACHER", "Teacher"
        STUDENT = "STUDENT", "Student"
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.STUDENT, db_index=True)
    student_number = models.CharField(max_length=32, blank=True)
    teacher_number = models.CharField(max_length=32, blank=True)
    def save(self, *args, **kwargs):
        self.is_staff = self.role == self.Role.ADMIN or self.is_superuser
        super().save(*args, **kwargs)
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
