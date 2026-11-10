"""Assessment library and separately scheduled class/lab deliveries."""
from urllib.parse import urlencode

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods

from .daily_task_workflow import ScheduleForm
from .models import (AssessmentDefinition, Assignment, ClassSection, CompilerLab,
                     PortalUser, SQLTest, SQLTestQuestion, TeacherAssignment)


def teaching_classes(user):
    ids = TeacherAssignment.objects.filter(teacher=user).values("class_section_id")
    return ClassSection.objects.filter(pk__in=ids, is_active=True, course__is_active=True).select_related("course").order_by("course__name", "name", "section", "group")


class LabSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value and hasattr(value, "instance"):
            option["attrs"]["data-class"] = value.instance.class_section_id
        return option


class MappingForm(ScheduleForm):
    class_sections = forms.ModelMultipleChoiceField(queryset=ClassSection.objects.none(), label="Classes", widget=forms.SelectMultiple(attrs={"size": 5}))
    lab = forms.ModelChoiceField(queryset=CompilerLab.objects.none(), required=False, label="Lab (optional)", empty_label="Whole selected classes", widget=LabSelect)
    published = forms.BooleanField(required=False, label="Publish this mapping for students")
    frequency = forms.ChoiceField(choices=SQLTest.ScheduleFrequency.choices, initial="anytime", label="Availability")
    opens = forms.TimeField(required=False, label="Daily opening time", widget=forms.TimeInput(attrs={"type": "time"}))
    closes = forms.TimeField(required=False, label="Daily closing time", widget=forms.TimeInput(attrs={"type": "time"}))
    weekdays = forms.MultipleChoiceField(required=False, choices=list(enumerate(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])), widget=forms.CheckboxSelectMultiple)
    field_order = ("class_sections", "lab", "start_at", "due_at", "late_policy", "frequency", "opens", "closes", "weekdays", "published")

    def __init__(self, *args, user, kind, **kwargs):
        super().__init__(*args, **kwargs)
        self.kind = kind
        self.fields["class_sections"].queryset = teaching_classes(user)
        self.fields["lab"].queryset = CompilerLab.objects.filter(class_section__in=teaching_classes(user), is_active=True).select_related("class_section__course").order_by("class_section__name", "name")
        self.fields["lab"].help_text = "Choose one class and one of its labs, or leave blank for whole classes."
        if kind == "daily":
            for field in ("frequency", "opens", "closes", "weekdays"):
                self.fields.pop(field)
        else:
            self.fields.pop("late_policy")
            self.fields["due_at"].label = "Close submissions at (optional)"

    def clean(self):
        data = super().clean()
        classes = list(data.get("class_sections", []))
        lab = data.get("lab")
        if lab and (len(classes) != 1 or classes[0].pk != lab.class_section_id):
            self.add_error("lab", "Choose exactly one class and a lab belonging to it.")
        if self.kind == "test" and data.get("frequency") != "anytime":
            if not data.get("opens") or not data.get("closes") or data["opens"] == data["closes"]:
                self.add_error("opens", "Set different opening and closing times for scheduled tests.")
            if data.get("frequency") == "weekly" and not data.get("weekdays"):
                self.add_error("weekdays", "Choose at least one weekday.")
        return data


def library_context(request, kind):
    model = Assignment if kind == "daily" else SQLTest
    owned = AssessmentDefinition.objects.filter(created_by=request.user, kind=kind, parent_task__isnull=True)
    linked = model.objects.filter(assessment_id=OuterRef("pk"))
    owned = owned.annotate(has_mappings=Exists(linked), has_published=Exists(linked.filter(published=True)))
    stats = {"total": owned.count(), "drafts": owned.filter(has_mappings=False).count(), "published": owned.filter(has_published=True).count()}
    query = request.GET.get("q", "").strip()[:200]
    status = request.GET.get("status", "")
    if query:
        owned = owned.filter(Q(code__icontains=query) | Q(title__icontains=query))
    if status == "draft":
        owned = owned.filter(has_mappings=False)
    elif status == "published":
        owned = owned.filter(has_published=True)
    elif status == "mapped":
        owned = owned.filter(has_mappings=True, has_published=False)
    page = Paginator(owned.order_by("-created_at", "-pk"), 12).get_page(request.GET.get("page"))
    records = list(page.object_list)
    ids = [entry.pk for entry in records]
    deliveries = model.objects.filter(assessment_id__in=ids).select_related("class_section__course", "lab").order_by("pk")
    groups = {}
    for delivery in deliveries:
        key = (delivery.assessment_id, delivery.class_section_id, delivery.lab_id) if kind == "daily" else (delivery.pk,)
        if key not in groups:
            delivery.question_count = 0
            groups[key] = delivery
        parent = groups[key]
        parent.question_count += 1
        parent.published = parent.published or delivery.published
    by_assessment = {}
    for delivery in groups.values():
        by_assessment.setdefault(delivery.assessment_id, []).append(delivery)
    for entry in records:
        entry.deliveries = by_assessment.get(entry.pk, [])
        questions = entry.payload.get("questions") or [entry.payload]
        entry.question_count = len(questions)
        entry.mapping_form = MappingForm(user=request.user, kind=kind, auto_id=f"map-{entry.pk}-%s")
        entry.preview_questions = [{**question, "prompt": question.get("prompt") or question.get("instructions", "")} for question in questions]
    return {"library_entries": records, "library_page": page, "library_stats": stats,
            "library_kind": kind, "library_q": query, "library_status": status,
            "library_query": urlencode({"view": "library", "q": query, "status": status}),
            "library_path": "/portal/daily-tasks/" if kind == "daily" else "/portal/tests/",
            "library_notice": request.session.pop("assessment_deleted_notice", "")}


def create_mappings(entry, data, user):
    """Lock the content parent so concurrent requests cannot duplicate a mapping."""
    model = Assignment if entry.kind == "daily" else SQLTest
    with transaction.atomic():
        entry = AssessmentDefinition.objects.select_for_update().get(pk=entry.pk, created_by=user)
        classes = list(data["class_sections"])
        lab = data.get("lab")
        # Recheck authorization and active records after obtaining the lock.
        permitted = set(teaching_classes(user).values_list("pk", flat=True))
        if any(section.pk not in permitted for section in classes):
            raise ValueError("Class access changed. Choose active assigned classes again.")
        if lab and not CompilerLab.objects.filter(pk=lab.pk, is_active=True, class_section_id=classes[0].pk).exists():
            raise ValueError("This lab is no longer available.")
        existing = model.objects.filter(assessment=entry, class_section__in=classes, lab=lab)
        if existing.exists():
            raise ValueError("A selected class/lab is already mapped. Manage its existing mapping below.")
        for section in classes:
            common = dict(assessment=entry, class_section=section, lab=lab, created_by=user, title=entry.title, published=data.get("published", False))
            payload = entry.payload
            if entry.kind == "daily":
                questions = payload.get("questions") or [payload]
                for index, question in enumerate(questions, 1):
                    content = {**payload, **question}
                    fields = {key: content[key] for key in ("instructions", "general_instructions", "starter_sql", "tags", "hint", "sample_schema", "max_score", "grading_mode", "expected_sql", "verification_sql") if key in content}
                    title = entry.title if len(questions) == 1 else f"{entry.title[:180]} · Q{index}"
                    model.objects.create(**{**common, "title": title}, **fields, start_at=data.get("start_at"), due_at=data.get("due_at"), late_policy=data.get("late_policy", "allow"), enabled_at=timezone.now() if common["published"] else None)
            else:
                fields = {key: payload[key] for key in ("instructions", "sample_schema", "duration_minutes", "total_marks", "grading_mode", "allow_typed_answer", "email_results_on_submit") if key in payload}
                test = model.objects.create(**common, **fields, course=section.course, starts_at=data.get("start_at"), ends_at=data.get("due_at"), schedule_frequency=data.get("frequency", "anytime"), schedule_start_time=data.get("opens"), schedule_end_time=data.get("closes"), schedule_weekdays=[int(day) for day in data.get("weekdays", [])] if data.get("frequency") == "weekly" else [])
                SQLTestQuestion.objects.bulk_create([SQLTestQuestion(test=test, position=index, prompt=question["prompt"], expected_sql=question.get("expected_sql", ""), verification_sql=question.get("verification_sql", ""), marks=question.get("marks", 1)) for index, question in enumerate(payload["questions"], 1)])


@login_required(login_url="/portal/")
@require_POST
def manage_mapping(request, assessment_id):
    from .portal_views import (_active_portal_role, _render_dashboard, _available_portal_roles,
                               _global_default_accent, _dashboard_path, _dashboard_navigation_items)
    if _active_portal_role(request) != PortalUser.Role.TEACHER:
        return HttpResponseForbidden("Teacher access required.")
    entry = get_object_or_404(AssessmentDefinition, pk=assessment_id, created_by=request.user, parent_task__isnull=True)
    path = "/portal/daily-tasks/" if entry.kind == "daily" else "/portal/tests/"
    form = MappingForm(request.POST, user=request.user, kind=entry.kind)
    error = ""
    if request.POST.get("action") in {"publish", "pause"}:
        model = Assignment if entry.kind == "daily" else SQLTest
        with transaction.atomic():
            mapping = get_object_or_404(model.objects.select_for_update(), pk=request.POST.get("mapping_id") if request.POST.get("mapping_id", "").isdigit() else None, assessment=entry, created_by=request.user)
            if not teaching_classes(request.user).filter(pk=mapping.class_section_id).exists() or (mapping.lab_id and not mapping.lab.is_active):
                error = "This class or lab is inactive or no longer assigned to you."
            else:
                mapping.published = request.POST["action"] == "publish"
                fields = ["published"]
                if entry.kind == "daily" and mapping.published and not mapping.enabled_at:
                    mapping.enabled_at = timezone.now()
                    fields.append("enabled_at")
                if entry.kind == "daily":
                    members = Assignment.objects.filter(assessment=entry, class_section_id=mapping.class_section_id, lab_id=mapping.lab_id, created_by=request.user)
                    if mapping.published:
                        members.filter(Q(published=False) | Q(enabled_at__isnull=True)).update(enabled_at=timezone.now())
                    members.update(published=mapping.published)
                else:
                    mapping.save(update_fields=fields)
        if not error:
            return redirect(path + "?view=library#assessment-" + str(entry.pk))
    elif form.is_valid():
        try:
            create_mappings(entry, form.cleaned_data, request.user)
        except ValueError as exc:
            error = str(exc)
        else:
            return redirect(path + "?view=library#assessment-" + str(entry.pk))
    context = {"user": request.user, "portal_role": "TEACHER", "entry": entry, "mapping_form": form, "mapping_error": error,
               "library_path": path, "is_daily_tasks_page": entry.kind == "daily", "is_tests_page": entry.kind == "test",
               "has_multiple_roles": len(_available_portal_roles(request.user)) > 1, "global_default_accent": _global_default_accent(),
               "dashboard_home_path": _dashboard_path("TEACHER"), "dashboard_navigation_items": _dashboard_navigation_items("TEACHER")}
    return _render_dashboard("assessment-mapping.html", context, request)



def _deletion_counts(entry):
    from .models import Submission, TestAttempt, ProjectExecution
    ids = [entry.pk, *entry.question_drafts.values_list("pk", flat=True)]
    daily = Assignment.objects.filter(assessment_id__in=ids)
    tests = SQLTest.objects.filter(assessment_id__in=ids)
    mappings = len(set(daily.values_list("class_section_id", "lab_id"))) if entry.kind == "daily" else tests.count()
    runs = ProjectExecution.objects.filter(
        Q(activity_kind="daily_task", activity_id__in=daily.values("pk")) |
        Q(activity_kind="test", activity_id__in=tests.values("pk"))
    )
    return {"questions": len(entry.payload.get("questions") or [entry.payload]),
            "mappings": mappings, "daily_submissions": Submission.objects.filter(assignment__in=daily).count(),
            "test_attempts": TestAttempt.objects.filter(test__in=tests).count(),
            "active_sandboxes": runs.exclude(status__in=["completed", "cancelled"]).count()}


def _close_deleted_runs(runs):
    import logging
    from .project_sqlplus import close_project_session
    from .project_schemas import remove_schema
    for run in runs:
        try:
            close_project_session(run.pk)
        except Exception:
            logging.getLogger(__name__).exception("Could not close deleted assessment run %s", run.pk)
        try:
            remove_schema(run)
        except Exception:
            # Existing lease cleanup retries cancelled runs; the assessment stays deleted.
            logging.getLogger(__name__).exception("Cleanup pending for deleted assessment run %s", run.pk)


@login_required(login_url="/portal/")
@require_http_methods(["GET", "POST"])
def delete_assessment(request, assessment_id):
    from django.db.models.deletion import ProtectedError
    from .models import AuditEvent, ProjectExecution
    from .portal_views import (_active_portal_role, _render_dashboard, _available_portal_roles,
                               _global_default_accent, _dashboard_path, _dashboard_navigation_items)
    if _active_portal_role(request) != PortalUser.Role.TEACHER:
        return HttpResponseForbidden("Teacher access required.")
    entry = get_object_or_404(AssessmentDefinition, pk=assessment_id, created_by=request.user, parent_task__isnull=True)
    path = "/portal/daily-tasks/" if entry.kind == "daily" else "/portal/tests/"
    error = ""
    status = 200
    if request.method == "POST":
        if request.POST.get("confirm_delete") != "yes":
            error = "Confirm that you want to permanently delete this entire task or test."
            status = 400
        else:
            try:
                with transaction.atomic():
                    entry = get_object_or_404(AssessmentDefinition.objects.select_for_update(), pk=entry.pk, created_by=request.user, parent_task__isnull=True)
                    aliases = list(entry.question_drafts.select_for_update())
                    if any(alias.created_by_id != request.user.pk for alias in aliases):
                        return HttpResponseForbidden("Linked content belongs to another author.")
                    ids = [entry.pk, *[alias.pk for alias in aliases]]
                    daily = Assignment.objects.filter(assessment_id__in=ids)
                    tests = SQLTest.objects.filter(assessment_id__in=ids)
                    # Serialize deletion against submissions before removing their deliveries.
                    list(daily.select_for_update().values_list("pk", flat=True))
                    list(tests.select_for_update().values_list("pk", flat=True))
                    counts = _deletion_counts(entry)
                    runs = list(ProjectExecution.objects.select_related("sandbox_connection").filter(
                        Q(activity_kind="daily_task", activity_id__in=daily.values("pk")) |
                        Q(activity_kind="test", activity_id__in=tests.values("pk"))
                    ))
                    if runs:
                        ProjectExecution.objects.filter(pk__in=[run.pk for run in runs]).update(status="cancelled", slot_reserved=False, source_text="", statements=[], results=[], workspace_state={})
                    code = entry.code
                    AuditEvent.objects.create(actor=request.user, action="assessment_deleted", target=code,
                        details={"kind": entry.kind, "title": entry.title, **counts, "retired_codes": [code]+[alias.code for alias in aliases]})
                    daily.delete()
                    tests.delete()
                    if aliases:
                        AssessmentDefinition.objects.filter(pk__in=[alias.pk for alias in aliases]).delete()
                    entry.delete()
                    transaction.on_commit(lambda: _close_deleted_runs(runs))
                request.session["assessment_deleted_notice"] = code
                return redirect(path + "?view=library")
            except ProtectedError:
                error = "This assessment still has protected linked content. Nothing was deleted."
                status = 409
    context = {"user": request.user, "portal_role": "TEACHER", "entry": entry,
        "delete_counts": _deletion_counts(entry), "delete_error": error, "library_path": path,
        "is_daily_tasks_page": entry.kind == "daily", "is_tests_page": entry.kind == "test",
        "has_multiple_roles": len(_available_portal_roles(request.user)) > 1,
        "global_default_accent": _global_default_accent(), "dashboard_home_path": _dashboard_path("TEACHER"),
        "dashboard_navigation_items": _dashboard_navigation_items("TEACHER")}
    response = _render_dashboard("assessment-delete.html", context, request)
    response.status_code = status
    return response
