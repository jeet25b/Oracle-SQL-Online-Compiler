import csv
import io

from django import forms
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Q, Value
from django.db.models.functions import Lower, Replace
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import (AttendanceSession, AuditEvent, ClassSection, ClassSubject, Course, Enrollment,
                     PortalUser, Subject, TeacherAssignment)


def role_users(role):
    ids = [user.pk for user in PortalUser.objects.filter(is_active=True).only("pk", "role", "additional_roles") if role in user.portal_roles]
    return PortalUser.objects.filter(pk__in=ids).order_by("first_name", "last_name", "username")


def is_academic_admin(user):
    return user.is_superuser or PortalUser.Role.ADMIN in user.portal_roles


def can_manage_academics(user, permission):
    return is_academic_admin(user) or (PortalUser.Role.TEACHER in user.portal_roles and getattr(user, permission, False))


def managed_classes(user):
    classes = ClassSection.objects.all()
    if not is_academic_admin(user):
        classes = classes.filter(pk__in=TeacherAssignment.objects.filter(teacher=user).values("class_section_id"))
    return classes.select_related("course")


def ordered_classes(classes):
    """Keep class pickers alphabetized by the course name students see first."""
    normalized_course_name = Lower(Replace(Replace("course__name", Value("."), Value("")), Value(" "), Value("")))
    return classes.order_by(
        normalized_course_name, Lower("course__code"), Lower("name"),
        Lower("branch"), "semester", Lower("section"), Lower("group"),
        Lower("academic_year"), "pk",
    )


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ("code", "name")


class ClassForm(forms.ModelForm):
    name = forms.ChoiceField(
        label="Class name",
        choices=(),
        required=True,
        help_text="Choose a subject name. Add the subject first if it is not listed.",
    )

    class Meta:
        model = ClassSection
        fields = ("course", "name", "branch", "semester", "section", "group", "academic_year", "is_active")
        labels = {"name": "Class name", "group": "Group", "is_active": "Active / available to students"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["course"].queryset = Course.objects.filter(is_active=True)
        subject_names = list(Subject.objects.filter(is_active=True).values_list("name", flat=True).distinct().order_by("name"))
        choices = [("", "Choose a subject name"), *((name, name) for name in subject_names)]
        if self.instance.pk and self.instance.name and self.instance.name not in subject_names:
            choices.insert(1, (self.instance.name, f"{self.instance.name} (current class name)"))
        self.fields["name"].choices = choices
        for name in ("branch", "semester", "section", "group"):
            self.fields[name].required = True
        self.fields["semester"].min_value = 1
        self.fields["semester"].max_value = 20

    def clean_semester(self):
        value = self.cleaned_data["semester"]
        if not 1 <= value <= 20:
            raise forms.ValidationError("Choose a semester from 1 to 20.")
        return value


class SubjectForm(forms.ModelForm):
    class Meta:
        model = Subject
        fields = ("code", "name")


class OfferingForm(forms.ModelForm):
    class Meta:
        model = ClassSubject
        fields = ("subject", "has_lecture", "has_laboratory")
        labels = {
            "has_lecture": "Lecture sessions",
            "has_laboratory": "Laboratory / practical sessions",
        }

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("has_lecture") and not cleaned.get("has_laboratory"):
            raise forms.ValidationError("Enable lecture sessions, laboratory / practical sessions, or both.")
        return cleaned

    def clean_subject(self):
        subject = self.cleaned_data["subject"]
        existing = ClassSubject.objects.filter(class_section=self.instance.class_section, subject=subject)
        if self.instance.pk:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise forms.ValidationError("This subject is already offered in the class.")
        return subject


class AllocationForm(forms.ModelForm):
    class_section = forms.ModelChoiceField(queryset=ClassSection.objects.none(), label="Class")

    class Meta:
        model = TeacherAssignment
        fields = ("teacher", "class_section", "class_subject", "teaching_type")
        labels = {"class_subject": "Subject", "teaching_type": "Session type"}

    def __init__(self, *args, section, classes, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.class_section = section
        self.fields["class_section"].queryset = ordered_classes(classes.select_related("course"))
        self.fields["class_section"].initial = section
        self.fields["class_section"].widget.attrs["data-allocation-class"] = ""
        self.fields["teacher"].queryset = role_users(PortalUser.Role.TEACHER)
        self.fields["class_subject"].queryset = ClassSubject.objects.filter(class_section=section, subject__is_active=True).select_related("subject")
        self.fields["class_subject"].required = True
        self.fields["teaching_type"].choices = TeacherAssignment.TeachingType.choices

    def clean(self):
        data = super().clean()
        section = data.get("class_section")
        offering = data.get("class_subject")
        if section:
            self.instance.class_section = section
        if offering and section and offering.class_section_id != section.pk:
            self.add_error("class_subject", "Choose a subject offered in the selected class.")
        elif offering:
            self.instance.subject = offering.subject.name[:120]
            teaching_type = data.get("teaching_type")
            if teaching_type == TeacherAssignment.TeachingType.LECTURE and not offering.has_lecture:
                self.add_error("teaching_type", "Lecture sessions are not enabled for this subject.")
            elif teaching_type == TeacherAssignment.TeachingType.LABORATORY and not offering.has_laboratory:
                self.add_error("teaching_type", "Laboratory / practical sessions are not enabled for this subject.")
        return data


class EnrollForm(forms.Form):
    students = forms.ModelMultipleChoiceField(queryset=PortalUser.objects.none(), required=False, widget=forms.SelectMultiple(attrs={"size": 8, "aria-label": "Selected students"}))
    roster_file = forms.FileField(required=False, label="Import roster CSV", help_text="CSV must include an account ID, roll number, username, or email column; a single-column list is also accepted.")

    def __init__(self, *args, section, **kwargs):
        super().__init__(*args, **kwargs)
        student_ids = self.data.getlist(self.add_prefix("students")) if self.is_bound else []
        self.fields["students"].queryset = PortalUser.objects.filter(
            pk__in=student_ids, role=PortalUser.Role.STUDENT, is_active=True,
        ).order_by("first_name", "last_name", "username")
        self.fields["students"].widget.attrs.pop("required", None)

    def clean_roster_file(self):
        uploaded = self.cleaned_data.get("roster_file")
        if not uploaded:
            return []
        if uploaded.size > 2 * 1024 * 1024:
            raise forms.ValidationError("Roster CSV must be 2 MB or smaller.")
        try:
            content = uploaded.read().decode("utf-8-sig")
        except (UnicodeDecodeError, AttributeError):
            raise forms.ValidationError("Roster CSV must use UTF-8 encoding.")
        rows = []
        try:
            for row in csv.reader(io.StringIO(content)):
                if len(rows) >= 10000:
                    raise forms.ValidationError("The roster file has too many rows to process.")
                rows.append(row)
        except csv.Error:
            raise forms.ValidationError("The roster CSV could not be parsed.")
        rows = [row for row in rows if any(value.strip() for value in row)]
        if not rows:
            raise forms.ValidationError("The roster CSV contains no student identifiers.")
        accepted_headers = {"account id", "account_id", "student id", "student_id", "roll number", "roll_number", "roll no", "roll_no", "student number", "student_number", "username", "email", "email id", "email_id"}
        header = [value.strip().casefold() for value in rows[0]]
        column = next((index for index, value in enumerate(header) if value in accepted_headers), None)
        if column is None:
            column = 0
        else:
            rows = rows[1:]
        identifiers = [row[column].strip() for row in rows if len(row) > column and row[column].strip()]
        if not identifiers:
            raise forms.ValidationError("The roster CSV contains no student identifiers.")
        if len(identifiers) > 5000:
            raise forms.ValidationError("A roster can contain at most 5,000 student identifiers.")
        return list(dict.fromkeys(identifiers))

    def clean(self):
        cleaned = super().clean()
        selected = list(cleaned.get("students") or [])
        identifiers = cleaned.get("roster_file") or []
        imported, unmatched = _match_roster_students(identifiers) if identifiers else ([], [])
        if unmatched:
            sample = ", ".join(unmatched[:5])
            more = " and more" if len(unmatched) > 5 else ""
            self.add_error("roster_file", f"Could not match {len(unmatched)} roster entr{'y' if len(unmatched) == 1 else 'ies'}: {sample}{more}.")
        students = {student.pk: student for student in [*selected, *imported]}
        if not students:
            self.add_error("students", "Select students or upload a roster CSV.")
        else:
            self.fields["students"].queryset = PortalUser.objects.filter(pk__in=students, role=PortalUser.Role.STUDENT, is_active=True).order_by("first_name", "last_name", "username")
        cleaned["students"] = list(students.values())
        return cleaned


def _match_roster_students(identifiers):
    """Resolve roster identifiers with bounded Oracle IN lists."""
    normalized = list(dict.fromkeys(value.strip().casefold() for value in identifiers if value.strip()))
    matches = {value: {} for value in normalized}
    for offset in range(0, len(normalized), 350):
        batch = normalized[offset:offset + 350]
        queryset = PortalUser.objects.filter(role=PortalUser.Role.STUDENT, is_active=True).annotate(
            _username_lower=Lower("username"),
            _student_number_lower=Lower("student_number"),
            _email_lower=Lower("email"),
        ).filter(
            Q(_username_lower__in=batch) | Q(_student_number_lower__in=batch) | Q(_email_lower__in=batch)
        ).values("pk", "username", "student_number", "email", "first_name", "last_name")
        for row in queryset:
            account = PortalUser(
                pk=row["pk"], username=row["username"], student_number=row["student_number"],
                email=row["email"], first_name=row["first_name"], last_name=row["last_name"],
            )
            for value in (row["username"], row["student_number"], row["email"]):
                key = (value or "").strip().casefold()
                if key in matches:
                    matches[key][account.pk] = account
    imported, unmatched = [], []
    for value, by_id in matches.items():
        if len(by_id) == 1:
            imported.append(next(iter(by_id.values())))
        else:
            unmatched.append(value)
    return imported, unmatched


def _bulk_activate_memberships(model, owner_field, owner, students):
    student_ids = list(dict.fromkeys(student.pk for student in students))
    owner_filter = {owner_field: owner}
    for offset in range(0, len(student_ids), 400):
        batch_ids = student_ids[offset:offset + 400]
        existing_rows = model.objects.filter(**owner_filter, student_id__in=batch_ids)
        existing_ids = set(existing_rows.values_list("student_id", flat=True))
        existing_rows.update(active=True)
        missing_ids = [student_id for student_id in batch_ids if student_id not in existing_ids]
        if not missing_ids:
            continue
        records = [model(student_id=student_id, **owner_filter, active=True) for student_id in missing_ids]
        try:
            # Nested atomic provides a savepoint so Oracle uniqueness races can
            # be recovered without aborting the surrounding enrollment transaction.
            with transaction.atomic():
                model.objects.bulk_create(records, batch_size=200)
        except IntegrityError:
            raced_rows = model.objects.filter(**owner_filter, student_id__in=missing_ids)
            raced_ids = set(raced_rows.values_list("student_id", flat=True))
            raced_rows.update(active=True)
            retry_ids = [student_id for student_id in missing_ids if student_id not in raced_ids]
            if retry_ids:
                model.objects.bulk_create(
                    [model(student_id=student_id, **owner_filter, active=True) for student_id in retry_ids],
                    batch_size=200,
                )


def page_context(request, **values):
    from .portal_views import _active_portal_role, _available_portal_roles, _dashboard_navigation_items, _dashboard_path, _global_default_accent
    role = _active_portal_role(request) or request.user.role
    return {"user": request.user, "portal_role": role, "global_default_accent": _global_default_accent(),
            "has_multiple_roles": len(_available_portal_roles(request.user)) > 1,
            "dashboard_navigation_items": _dashboard_navigation_items(role), "dashboard_home_path": _dashboard_path(role), **values}


@login_required(login_url="/portal/")
@require_http_methods(["GET", "POST"])
def class_management(request):
    from .portal_views import _render_dashboard
    if not can_manage_academics(request.user, "can_manage_classes"):
        return HttpResponseForbidden("Class management requires administrator authorization.")
    administrator = is_academic_admin(request.user)
    classes = managed_classes(request.user)
    selected = request.GET.get("class", "")
    action = request.POST.get("action", "") if request.method == "POST" else ""
    adding_class = request.GET.get("new") == "1" or (request.method == "POST" and request.POST.get("new_class") == "1")
    if action == "allocation":
        selected = request.POST.get("allocation-class_section") or selected
    section = classes.filter(pk=selected if selected.isdigit() else None).first()
    if selected and not section:
        return HttpResponseForbidden("This class is outside your management scope.")
    if not administrator and request.method == "POST" and action not in {"allocation", "remove_allocation"}:
        return HttpResponseForbidden("Your authorization allows teacher allocations only.")
    forms_by_action = {}
    if not section:
        forms_by_action.update({
            "course": CourseForm(request.POST if action == "course" else None, prefix="course"),
            "subject": SubjectForm(request.POST if action == "subject" else None, prefix="subject"),
            "class": ClassForm(request.POST if action == "class" else None, prefix="class"),
        })
    elif administrator and adding_class:
        forms_by_action["class"] = ClassForm(
            request.POST if action == "class" else None,
            prefix="class",
        )
    elif administrator and (request.GET.get("edit") == "1" or action == "class"):
        forms_by_action["class"] = ClassForm(
            request.POST if action == "class" else None,
            prefix="class",
            instance=section,
        )
    if section:
        forms_by_action.update({
            "offering": OfferingForm(request.POST if action == "offering" else None, prefix="offering", instance=ClassSubject(class_section=section)),
            "allocation": AllocationForm(request.POST if action == "allocation" else None, prefix="allocation", section=section, classes=classes),
            "enroll": EnrollForm(request.POST if action == "enroll" else None, prefix="enroll", section=section),
        })
    if not administrator:
        forms_by_action = {key: form for key, form in forms_by_action.items() if key == "allocation"}
    message = ""
    if action in {"update_offering", "remove_offering"} and section:
        target_id = request.POST.get("target_id", "")
        with transaction.atomic():
            # SQLite does not implement SELECT FOR UPDATE. Keep this simple
            # lookup portable across the configured SQLite and Oracle backends.
            offering = ClassSubject.objects.filter(
                pk=target_id if target_id.isdigit() else None,
                class_section=section,
            ).first()
            if not offering:
                message = "That subject offering could not be found in this class."
            elif action == "update_offering":
                has_lecture = "has_lecture" in request.POST
                has_laboratory = "has_laboratory" in request.POST
                if not has_lecture and not has_laboratory:
                    message = "Keep at least one session type enabled for this subject."
                else:
                    offering.has_lecture = has_lecture
                    offering.has_laboratory = has_laboratory
                    offering.save(update_fields=("has_lecture", "has_laboratory"))
                    AuditEvent.objects.create(actor=request.user, action="class_management_update_offering",
                        target=f"class:{section.pk}:subject:{offering.pk}",
                        details={"has_lecture": has_lecture, "has_laboratory": has_laboratory})
            elif TeacherAssignment.objects.filter(class_subject=offering).exists():
                message = "Remove this subject’s teacher allocations before removing the subject from the class."
            else:
                subject_label = str(offering.subject)
                offering.delete()
                AuditEvent.objects.create(actor=request.user, action="class_management_remove_offering",
                    target=f"class:{section.pk}:subject:{target_id}", details={"subject": subject_label})
        if not message:
            return redirect(f"/portal/classes/?class={section.pk}")
    if action in {"remove_allocation", "remove_enrollment"} and section:
        target_id = request.POST.get("target_id", "")
        with transaction.atomic():
            ClassSection.objects.select_for_update().get(pk=section.pk)
            if action == "remove_allocation":
                allocation = TeacherAssignment.objects.filter(pk=target_id if target_id.isdigit() else None, class_section=section).first()
                if allocation and allocation.timetable_entries.exists():
                    message = "Remove the allocation's timetable entries before removing the teacher allocation."
                elif allocation:
                    allocation.delete()
            elif action == "remove_enrollment":
                enrollment = Enrollment.objects.filter(pk=target_id if target_id.isdigit() else None, class_section=section).first()
                if enrollment:
                    enrollment.active = False
                    enrollment.save(update_fields=("active",))
            AuditEvent.objects.create(actor=request.user, action=action, target=f"class:{section.pk}:record:{target_id}")
        if not message:
            return redirect(f"/portal/classes/?class={section.pk}")
    if action in forms_by_action:
        form = forms_by_action[action]
        if form.is_valid():
            audit_details = {}
            with transaction.atomic():
                if action == "enroll":
                    ClassSection.objects.select_for_update().get(pk=section.pk)
                    selected_students = form.cleaned_data["students"]
                    _bulk_activate_memberships(Enrollment, "class_section", section, selected_students)
                    audit_details["student_count"] = len(selected_students)
                elif action == "allocation":
                    allocation = form.save(commit=False)
                    TeacherAssignment.objects.get_or_create(teacher=allocation.teacher, class_section=section,
                        class_subject=allocation.class_subject, subject=allocation.subject,
                        teaching_type=allocation.teaching_type, lab=None)
                else:
                    obj = form.save()
                    if action == "class": section = obj
                AuditEvent.objects.create(actor=request.user, action=f"class_management_{action}", target=f"class:{section.pk if section else 'new'}", details=audit_details)
            return redirect(f"/portal/classes/?class={section.pk}" if section else "/portal/classes/")
        message = "Please correct the highlighted fields."
    labels = {"course": "Add course", "class": "Add class" if adding_class or not section else "Edit class details", "subject": "Add subject", "offering": "Add subject to this class", "allocation": "Allocate a teacher", "enroll": "Assign students"}
    context = page_context(request, is_class_management_page=True, section=section, can_administer_classes=administrator,
        adding_class=adding_class,
        classes=ordered_classes(classes),
        management_forms=[{"action": key, "title": labels[key], "form": form} for key, form in forms_by_action.items()], message=message)
    if section:
        context.update(offerings=section.class_subjects.select_related("subject"),
            allocations=section.teacher_assignments.select_related("teacher", "class_subject__subject"),
            enrollments=section.enrollments.filter(active=True).select_related("student"))
    return _render_dashboard("class-management.html", context, request)


@login_required(login_url="/portal/")
@require_http_methods(["GET"])
def class_student_search(request):
    if not can_manage_academics(request.user, "can_manage_classes"):
        return JsonResponse({"error": "Class management authorization is required."}, status=403)
    class_id = request.GET.get("class", "")
    if not class_id.isdigit() or not managed_classes(request.user).filter(pk=class_id).exists():
        return JsonResponse({"error": "Choose a class within your management scope."}, status=403)
    query = request.GET.get("q", "").strip()[:80]
    if len(query) < 2:
        return JsonResponse({"students": []})
    matches = PortalUser.objects.filter(role=PortalUser.Role.STUDENT, is_active=True).filter(
        Q(first_name__icontains=query) | Q(last_name__icontains=query)
        | Q(username__icontains=query) | Q(student_number__icontains=query)
        | Q(email__icontains=query)
    ).order_by("first_name", "last_name", "username")[:20]
    return JsonResponse({"students": [
        {
            "id": student.pk,
            "name": student.get_full_name() or student.username,
            "accountId": student.student_number or student.username,
            "email": student.email,
        }
        for student in matches
    ]})


@login_required(login_url="/portal/")
@require_http_methods(["GET", "POST"])
def choose_class(request):
    from .portal_views import _active_portal_role, _render_dashboard
    if _active_portal_role(request) != PortalUser.Role.STUDENT:
        return redirect("/portal/")
    if not request.user.class_selection_required and Enrollment.objects.filter(student=request.user, active=True).exists():
        return redirect("/portal/student/")
    choices = ordered_classes(ClassSection.objects.filter(is_active=True, course__is_active=True, semester__isnull=False).exclude(branch="").exclude(section="").exclude(group="").select_related("course"))
    assigned_ids = Enrollment.objects.filter(student=request.user, active=True).values("class_section_id")
    if assigned_ids.exists(): choices = choices.filter(pk__in=assigned_ids)
    form = forms.Form(request.POST if request.method == "POST" else None)
    form.fields["class_section"] = forms.ModelChoiceField(queryset=choices, label="Course · Branch · Semester · Section · Group", empty_label="Choose your class", required=True)
    form.fields["class_section"].label_from_instance = lambda section: " · ".join(value for value in (
        section.course.code,
        section.name,
        section.course.name,
        section.branch if section.branch not in {section.name, section.course.name} else "",
        f"Semester {section.semester}" if section.semester else "",
        f"Section {section.section}" if section.section else "",
        f"Group {section.group}" if section.group else "",
        section.academic_year,
    ) if value)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            student = PortalUser.objects.select_for_update().get(pk=request.user.pk)
            if not student.class_selection_required and Enrollment.objects.filter(student=student, active=True).exists():
                return redirect("/portal/student/")
            section = form.cleaned_data["class_section"]
            Enrollment.objects.update_or_create(student=student, class_section=section, defaults={"active": True})
            student.class_selection_required = False
            student.class_selected_at = timezone.now()
            student.save(update_fields=("class_selection_required", "class_selected_at"))
            AttendanceSession.objects.filter(pk=request.session.get("attendance_session_id"), user=student, logout_at__isnull=True).update(class_section=section)
            AuditEvent.objects.create(actor=student, action="student_class_selected", target=f"class:{section.pk}")
        return redirect("/portal/student/")
    return _render_dashboard("choose-class.html", page_context(request, is_class_selection_page=True, selection_form=form, has_classes=choices.exists()), request)
