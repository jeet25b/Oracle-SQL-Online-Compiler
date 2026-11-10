"""Staged imports, scheduling and teacher review for daily tasks."""
import re
from collections import Counter

from django import forms
from django.core import signing
from django.core.paginator import Paginator
from django.db import transaction
from django.utils import timezone

from .models import Assignment, Submission, AssessmentDefinition


SCHEDULE_FIELDS = ("start_at", "due_at", "late_policy")


class ScheduleForm(forms.Form):
    start_at = forms.DateTimeField(required=False, label="Release at", widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    due_at = forms.DateTimeField(required=False, label="Deadline", widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    late_policy = forms.ChoiceField(choices=Assignment._meta.get_field("late_policy").choices, initial="allow")

    def clean(self):
        data = super().clean()
        start, due = data.get("start_at"), data.get("due_at")
        if start and due and due <= start:
            self.add_error("due_at", "Deadline must be after release time.")
        return data


class PreviewQuestionForm(forms.Form):
    field_order = ("selected", "title", "general_instructions", "instructions", "starter_sql", "grading_mode", "expected_sql", "verification_sql", "tags", "hint", "sample_schema")
    grading_mode = forms.ChoiceField(required=False, choices=Assignment.GradingMode.choices, initial="manual", label="Grading method")
    expected_sql = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), max_length=200000, label="Reference SQL answer")

    verification_sql = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), max_length=200000, label="Verification SQL / test calls (required for stored programs and triggers)")
    selected = forms.BooleanField(required=False, initial=True, label="Include")
    title = forms.CharField(max_length=200, required=False)
    general_instructions = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), max_length=200000, label="Instructions")
    instructions = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}), max_length=200000, label="Question")
    starter_sql = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), max_length=200000)
    tags = forms.CharField(required=False, max_length=300)
    hint = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), max_length=20000)
    sample_schema = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), max_length=100000, label="Sample schema SQL / notes")

    def clean(self):
        data = super().clean()
        data["grading_mode"] = data.get("grading_mode") or "manual"
        if data.get("selected"):
            if data["grading_mode"] != "manual" and not data.get("expected_sql", "").strip():
                self.add_error("expected_sql", "Add a reference SQL answer for automatic grading.")
            elif data["grading_mode"] == "result_match":
                from .execution_grading import validate_reference
                try:
                    validate_reference(data.get("expected_sql", ""), data.get("verification_sql", ""), data.get("sample_schema", ""))
                except ValueError as exc:
                    self.add_error("expected_sql", str(exc))
            for name in ("title", "instructions"):
                if not data.get(name):
                    self.add_error(name, "This field is required for selected questions.")
        return data


PreviewFormSet = forms.formset_factory(PreviewQuestionForm, extra=0, max_num=200, validate_max=True, absolute_max=200)


def split_prompt(text):
    """Only split explicit starter blocks, leaving ordinary SQL questions intact."""
    match = re.search(r"(?im)^\s*(?:starter\s*(?:sql|code)|query\s*template)\s*:\s*|^\s*```(?:sql|plsql)\s*\n", text)
    if not match:
        raw = re.search(r"(?im)^(?:SELECT\b|WITH\s+\w+\s+AS\s*\(|INSERT\s+INTO\b|UPDATE\s+\w+|DELETE\s+FROM\b|CREATE\s+TABLE\b|DECLARE\b|BEGIN\b)", text)
        if raw and text[:raw.start()].strip():
            return text[:raw.start()].strip(), text[raw.start():].strip()
        return text.strip(), ""
    instructions = text[:match.start()].strip()
    starter = re.sub(r"\n\s*```\s*$", "", text[match.end():]).strip()
    return instructions or "Complete the SQL query below.", starter


def stage_import(user, data):
    schedule = {key: (data[key].isoformat() if hasattr(data.get(key), "isoformat") else data.get(key)) for key in SCHEDULE_FIELDS}
    schedule["late_policy"] = schedule.get("late_policy") or "allow"
    token = signing.dumps({"user": user.pk, "classes": [],
                           "published": False, "lab": None, "schedule": schedule}, salt="daily-task-preview", compress=True)
    rows = []
    for index, question in enumerate(data["questions"], 1):
        record = data.get("question_records", [{}] * len(data["questions"]))[index-1]
        instructions, starter = split_prompt(question)
        starter = record.get("starter_sql") or starter
        rows.append({"selected": True, "title": data["title"] if len(data["questions"]) == 1 else f"{data['title'][:185]} · {index}",
                     "instructions": instructions, "general_instructions": data.get("general_instructions", ""), "starter_sql": starter, "tags": data.get("tags", ""),
                     "hint": data.get("hint", ""), "sample_schema": data.get("sample_schema", ""),
                     "grading_mode": data.get("grading_mode") or "manual", "expected_sql": record.get("expected_sql") or data.get("expected_sql", ""), "verification_sql": record.get("verification_sql") or data.get("verification_sql", "")})
    return token, PreviewFormSet(initial=rows, prefix="questions")


def commit_import(request, classes):
    formset = PreviewFormSet(request.POST, prefix="questions")
    try:
        stage = signing.loads(request.POST.get("stage_token", ""), salt="daily-task-preview", max_age=7200)
        if stage["user"] != request.user.pk:
            raise signing.BadSignature()
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return None, "This preview expired or is invalid. Upload the questions again."
    schedule = ScheduleForm(stage["schedule"])
    if not formset.is_valid() or not schedule.is_valid():
        return formset, "Correct the preview fields before saving."
    selected = [form.cleaned_data for form in formset if form.cleaned_data.get("selected")]
    if not selected:
        return formset, "Select at least one question to save."
    with transaction.atomic():
        for row in selected:
            fields = {key: value for key, value in row.items() if key != "selected"}
            AssessmentDefinition.objects.create(
                kind="daily", title=fields["title"], created_by=request.user,
                payload={**fields, **schedule.cleaned_data},
            )
    return None, ""


def submission_window_error(task, now=None):
    now = now or timezone.now()
    if task.start_at and now < task.start_at:
        return "This task has not been released yet."
    if task.late_policy == "lock" and task.due_at and now > task.due_at:
        return "The deadline has passed. Submissions are locked."
    return ""


def late_submission(task, now):
    return bool(task.due_at and now > task.due_at)


FEEDBACK_MACROS = ("Check WHERE clause", "Column alias missing", "Check JOIN condition", "Check grouping and aggregates", "Check result ordering")


def review_context(request, classes):
    submissions = Submission.objects.filter(assignment__created_by=request.user).select_related("assignment__class_section", "student")
    section = request.GET.get("review_class", "")
    status = request.GET.get("review_status", "")
    date = request.GET.get("review_date", "")
    student_query = request.GET.get("review_student", "").strip()
    task_query = request.GET.get("review_task", "").strip()
    result_query = request.GET.get("review_result", "").strip()
    review_query_filter = request.GET.get("review_review", "").strip()
    if section.isdigit():
        submissions = submissions.filter(assignment__class_section_id=int(section))
    if status == "correct":
        submissions = submissions.filter(is_correct=True)
    elif status == "revision":
        submissions = submissions.filter(is_correct=False, status__in=[Submission.Status.REVIEWED, Submission.Status.AUTO_GRADED])
    elif status == "pending":
        submissions = submissions.exclude(status__in=[Submission.Status.REVIEWED, Submission.Status.AUTO_GRADED])
    if date:
        from datetime import date as date_type
        try:
            submissions = submissions.filter(submitted_at__date=date_type.fromisoformat(date))
        except ValueError:
            date = ""
    if student_query: submissions = submissions.filter(student__username__icontains=student_query) | submissions.filter(student__first_name__icontains=student_query) | submissions.filter(student__last_name__icontains=student_query)
    if task_query: submissions = submissions.filter(assignment__title__icontains=task_query)
    if result_query == "correct": submissions = submissions.filter(is_correct=True)
    elif result_query == "revision": submissions = submissions.filter(is_correct=False)
    if review_query_filter in {"pending", "reviewed", "disputed"}: submissions = submissions.filter(status=review_query_filter)
    mistakes = Counter()
    patterns = Counter()
    # Bounded summaries of the latest matching submissions; no SQL is executed.
    for submission in submissions.order_by("-submitted_at", "-pk")[:1000]:
        if not submission.is_correct:
            error = re.search(r"\b(?:ORA|PLS|DPY)-\d+\b", submission.feedback)
            if error:
                mistakes[error.group()] += 1
            elif submission.feedback:
                mistakes[submission.feedback[:160]] += 1
            if submission.status in (Submission.Status.REVIEWED, Submission.Status.AUTO_GRADED):
                normalized = re.sub(r"\s+", " ", submission.answer).strip()
                if normalized:
                    patterns[normalized] += 1
    sort_fields = {"student": "student__username", "task": "assignment__title", "submitted": "submitted_at", "result": "is_correct", "review": "status"}
    sort_key = request.GET.get("review_sort", "submitted")
    if sort_key not in sort_fields: sort_key = "submitted"
    sort_dir = "asc" if request.GET.get("review_dir") == "asc" else "desc"
    ordering = sort_fields[sort_key] if sort_dir == "asc" else "-" + sort_fields[sort_key]
    page = Paginator(submissions.order_by(ordering, "-pk"), 30).get_page(request.GET.get("review_page"))
    from .models import CompilerQueryHistory
    for submission in page:
        submission.answer_version = (submission.last_attempt_at or submission.submitted_at).isoformat()
        history = CompilerQueryHistory.objects.filter(student_id=submission.student_id, sql=submission.answer, executed_at__lte=submission.submitted_at).order_by("-executed_at").first()
        labels = {"typed": "Typed", "pasted": "Pasted", "file": "File", "history_recall": "History recall", "daily_task": "Daily task", "remote_code": "Remote code", "mixed": "Mixed", "internal_copy": "Internal copy"}
        submission.input_method_label = labels.get(history.input_method, "Not tracked") if history else "Not tracked"
    query = request.GET.copy()
    query.pop("review_page", None)
    query.pop("review_sort", None); query.pop("review_dir", None)
    return {"review_page": page, "review_classes": classes, "review_class": section, "review_status": status,
            "review_date": date, "review_student": student_query, "review_task": task_query, "review_result": result_query, "review_review": review_query_filter,
            "review_query": query.urlencode(), "review_sort": sort_key, "review_dir": sort_dir, "feedback_macros": FEEDBACK_MACROS,
            "mistake_groups": mistakes.most_common(10), "mistake_sql": patterns.most_common(10)}
