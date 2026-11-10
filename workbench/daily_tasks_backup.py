
from .daily_task_workflow import (ScheduleForm, stage_import, commit_import, review_context,
                                  submission_window_error, late_submission)


def _equivalent_answer_key(answer):
    """Match SQL answers by normalized whitespace and case, without changing stored text."""
    return " ".join(str(answer or "").split()).casefold()
from decimal import Decimal
import csv
import hashlib

from django import forms
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .models import (
    AuditEvent, Assignment, ClassSection, CompilerLab, LabEnrollment, CompilerQueryHistory, Enrollment,
    PortalUser, SQLTest, Submission, TeacherAssignment, TestAnswer, TestAttempt,
)


def available_daily_tasks_for_student(student):
    """Resolve visibility from current class membership, including later enrollments."""
    rostered_labs = LabEnrollment.objects.filter(active=True).values("lab_id")
    student_labs = LabEnrollment.objects.filter(student=student, active=True).values("lab_id")
    eligible_labs = CompilerLab.objects.filter(is_active=True).filter(
        Q(pk__in=student_labs) | ~Q(pk__in=rostered_labs)
    ).values("pk")
    return Assignment.objects.filter(Q(lab__isnull=True) | Q(lab_id__in=eligible_labs)).filter(
        published=True,
        class_section__is_active=True,
        class_section__enrollments__student=student,
        class_section__enrollments__student__is_active=True,
        class_section__enrollments__student__role=PortalUser.Role.STUDENT,
        class_section__enrollments__active=True,
    ).filter(Q(start_at__isnull=True) | Q(start_at__lte=timezone.now())).select_related("class_section__course")


@login_required(login_url="/portal/")
@require_GET
def student_daily_task_feed(request):
    from .portal_views import _active_portal_role
    if _active_portal_role(request) != PortalUser.Role.STUDENT:
        return JsonResponse({"error": "Student access required."}, status=403)
    completed_ids = set(Submission.objects.filter(
        student=request.user,
    ).filter(
        Q(is_correct=True) | ~Q(status__in=[Submission.Status.REVIEWED, Submission.Status.AUTO_GRADED]),
    ).values_list("assignment_id", flat=True))
    tasks = available_daily_tasks_for_student(request.user).order_by("pk").values(
        "pk", "title", "instructions", "general_instructions", "class_section__course__name",
        "class_section__name", "enabled_at", "starter_sql", "hint", "sample_schema", "tags", "assessment__code",
    )
    return JsonResponse({"tasks": [
        {
            "id": item["pk"], "code": item["assessment__code"] or "", "title": item["title"], "instructions": item["instructions"], "generalInstructions": item["general_instructions"],
            "starterSql": item["starter_sql"], "hint": item["hint"], "sampleSchema": item["sample_schema"], "tags": item["tags"],
            "course": item["class_section__course__name"],
            "className": item["class_section__name"],
            "enabledAt": item["enabled_at"].isoformat() if item["enabled_at"] else "",
        }
        for item in tasks if item["pk"] not in completed_ids
    ]}, headers={"Cache-Control": "no-store"})


@login_required(login_url="/portal/")
@require_GET
def daily_task_history(request):
    from .portal_views import _active_portal_role
    if _active_portal_role(request) != PortalUser.Role.STUDENT:
        return JsonResponse({"error": "Student access required."}, status=403)
    task_id = request.GET.get("task_id", "")
    task = available_daily_tasks_for_student(request.user).filter(
        pk=task_id if task_id.isdigit() else None,
    ).first()
    if not task:
        return JsonResponse({"error": "This question is not enabled for you."}, status=404)
    if Submission.objects.filter(assignment=task, student=request.user, is_correct=True).exists():
        return JsonResponse({"error": "Your answer is marked correct. Further attempts are locked."}, status=409)
    history = CompilerQueryHistory.objects.filter(student=request.user, status="success")
    requested_source = request.GET.get("source", "").strip()
    if requested_source in CompilerQueryHistory.Source.values:
        history = history.filter(source=requested_source)
    search = request.GET.get("q", "").strip()[:200]
    if search:
        history = history.filter(sql__icontains=search)
    before = request.GET.get("before", "")
    if before.isdigit():
        history = history.filter(pk__lt=int(before))
    records = list(history.order_by("-pk").values("id", "sql", "source", "executed_at")[:21])
    return JsonResponse({"statements": records[:20], "hasMore": len(records) > 20})


@login_required(login_url="/portal/")
@require_POST
def submit_daily_task_answer(request):
    import json

    from .portal_views import _active_portal_role
    if _active_portal_role(request) != PortalUser.Role.STUDENT:
        return JsonResponse({"error": "Student access required."}, status=403)
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid request."}, status=400)
    task_id = str(payload.get("taskId", ""))
    history_id = str(payload.get("historyId", ""))
    if not task_id.isdigit() or (history_id and not history_id.isdigit()):
        return JsonResponse({"error": "Choose a daily task and provide an answer."}, status=400)
    if history_id:
        history = CompilerQueryHistory.objects.filter(
            pk=int(history_id), student=request.user,
            status=CompilerQueryHistory.ExecutionStatus.SUCCESS,
        ).first()
        if not history:
            return JsonResponse({"error": "That statement is not available in successful SQL history."}, status=404)
        answer = history.sql.strip()
    else:
        answer = ""
    if not answer:
        return JsonResponse({"error": "Choose a successfully executed statement from SQL history."}, status=400)
    if len(answer) > 200000:
        return JsonResponse({"error": "The selected statement is too long to submit."}, status=400)
    from .assessment_sandbox import typed_assessment_run
    try:
        request._assessment_run = typed_assessment_run(request.user,'daily_task',int(task_id),[answer])
    except ValueError as exc:
        return JsonResponse({'error':str(exc)},status=409)

    now = timezone.now()
    with transaction.atomic():
        locked_tasks = Assignment.objects.select_for_update().filter(pk=int(task_id))
        task = next(iter(locked_tasks), None)
        if not task or not available_daily_tasks_for_student(request.user).filter(pk=int(task_id)).exists():
            return JsonResponse({"error": "This question is not currently enabled for you."}, status=404)
        now = timezone.now()
        window_error = submission_window_error(task, now)
        if window_error:
            return JsonResponse({"error": window_error}, status=403)
        locked_submissions = Submission.objects.select_for_update().only(
            "pk", "is_correct", "first_attempt_at", "submitted_at",
        ).filter(assignment=task, student=request.user)
        submission = next(iter(locked_submissions), None)
        if submission is None:
            submission = Submission.objects.create(
                assignment=task, student=request.user, answer="",
                first_attempt_at=now, last_attempt_at=now, submitted_at=now,
            )
        if submission.is_correct:
            return JsonResponse({"error": "Your answer is marked correct. Further attempts are locked."}, status=409)
        if submission.status == Submission.Status.DISPUTED:
            return JsonResponse({"error": "Your answer is under dispute review. Wait for your teacher’s response before retrying."}, status=409)
        submission.answer = answer
        submission.first_attempt_at = submission.first_attempt_at or submission.submitted_at or now
        submission.last_attempt_at = now
        submission.submitted_at = now
        submission.is_late = late_submission(task, now)
        submission.status = Submission.Status.SUBMITTED
        submission.reviewed_at = None
        submission.feedback = ""
        submission.score = None
        submission.dispute_message = ""
        submission.disputed_at = None
        submission.dispute_response = ""
        submission.dispute_resolved_at = None
        submission.save(update_fields=("is_late", "answer", "first_attempt_at", "last_attempt_at", "submitted_at", "status", "reviewed_at", "feedback", "score", "dispute_message", "disputed_at", "dispute_response", "dispute_resolved_at"))
    grade = _apply_daily_auto_grade(request, task, submission)
    message = f"Automatically evaluated: {'Correct' if grade['is_correct'] else 'Incorrect'}." if grade else "Your answer was submitted for teacher review."
    return JsonResponse({"ok": True, "message": message, "evaluated": bool(grade), "isCorrect": grade["is_correct"] if grade else None})


@login_required(login_url="/portal/")
@require_POST
def submit_latest_sqlcl_daily_task_answer(request):
    """Submit the student's latest successful SQLCL execution for one task."""
    import json

    from .portal_views import _active_portal_role
    if _active_portal_role(request) != PortalUser.Role.STUDENT:
        return JsonResponse({"error": "Student access required."}, status=403)
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid request."}, status=400)
    task_id = str(payload.get("taskId", ""))
    if not task_id.isdigit():
        return JsonResponse({"error": "Choose a daily task first."}, status=400)

    task_id = int(task_id)
    now = timezone.now()
    with transaction.atomic():
        # Lock just the task row, then re-check current enrollment and publication.
        locked_tasks = Assignment.objects.select_for_update().filter(pk=task_id)
        task = next(iter(locked_tasks), None)
        if not task or not available_daily_tasks_for_student(request.user).filter(pk=task_id).exists():
            return JsonResponse({"error": "This question is not currently enabled for you."}, status=404)
        now = timezone.now()
        window_error = submission_window_error(task, now)
        if window_error:
            return JsonResponse({"error": window_error}, status=403)
        locked_submissions = Submission.objects.select_for_update().only(
            "pk", "is_correct", "first_attempt_at", "submitted_at",
        ).filter(assignment=task, student=request.user)
        submission = next(iter(locked_submissions), None)
        created = submission is None
        if submission is None:
            submission = Submission.objects.create(
                assignment=task, student=request.user, answer="",
                first_attempt_at=now, last_attempt_at=now, submitted_at=now,
            )
        if submission.is_correct:
            return JsonResponse({"error": "Your answer is marked correct. Further attempts are locked."}, status=409)
        if submission.status == Submission.Status.DISPUTED:
            return JsonResponse({"error": "Your answer is under dispute review. Wait for your teacher’s response before retrying."}, status=409)
        statement = CompilerQueryHistory.objects.filter(
            student=request.user,
            source=CompilerQueryHistory.Source.SQLPLUS,
            status=CompilerQueryHistory.ExecutionStatus.SUCCESS,
        ).order_by("-executed_at", "-pk").first()
        if not statement:
            if created:
                submission.delete()
            return JsonResponse({"error": "No successful SQLCL statement is available yet. Execute a statement in SQLCL first."}, status=404)
        submission.answer = statement.sql
        submission.first_attempt_at = submission.first_attempt_at or submission.submitted_at or now
        submission.last_attempt_at = now
        submission.submitted_at = now
        submission.is_late = late_submission(task, now)
        submission.status = Submission.Status.SUBMITTED
        submission.reviewed_at = None
        submission.feedback = ""
        submission.score = None
        submission.dispute_message = ""
        submission.disputed_at = None
        submission.dispute_response = ""
        submission.dispute_resolved_at = None
        submission.save(update_fields=("is_late", "answer", "first_attempt_at", "last_attempt_at", "submitted_at", "status", "reviewed_at", "feedback", "score", "dispute_message", "disputed_at", "dispute_response", "dispute_resolved_at"))
    grade = _apply_daily_auto_grade(request, task, submission)
    message = f"Automatically evaluated: {'Correct' if grade['is_correct'] else 'Incorrect'}." if grade else "Latest successful SQLCL statement submitted for teacher review."
    return JsonResponse({"ok": True, "message": message, "evaluated": bool(grade), "isCorrect": grade["is_correct"] if grade else None})


def _available_tests_for_student(student, now=None, only_open=True):
    from .portal_views import _test_schedule_is_open

    now = now or timezone.now()
    enrolled_classes = Enrollment.objects.filter(
        student=student, active=True, class_section__is_active=True,
    ).values("class_section_id")
    rostered_labs = LabEnrollment.objects.filter(active=True).values("lab_id")
    student_labs = LabEnrollment.objects.filter(student=student, active=True).values("lab_id")
    eligible_labs = CompilerLab.objects.filter(is_active=True).filter(Q(pk__in=student_labs) | ~Q(pk__in=rostered_labs)).values("pk")
    test_ids = SQLTest.objects.filter(Q(lab__isnull=True) | Q(lab_id__in=eligible_labs)).filter(published=True).filter(
        Q(class_section_id__in=enrolled_classes)
        | Q(
            class_section__isnull=True,
            course__classes__is_active=True,
            course__classes__enrollments__student=student,
            course__classes__enrollments__student__is_active=True,
            course__classes__enrollments__student__role=PortalUser.Role.STUDENT,
            course__classes__enrollments__active=True,
        )
    ).order_by().values("pk")
    tests = SQLTest.objects.filter(pk__in=test_ids).select_related(
        "course", "class_section", "assessment",
    ).prefetch_related("questions").order_by("pk")
    if not only_open:
        return list(tests)
    return [test for test in tests if
            (not test.starts_at or now >= test.starts_at)
            and (not test.ends_at or now <= test.ends_at)
            and _test_schedule_is_open(test, now)]


def ordered_test_questions(test, student):
    """Return a stable, student-specific question order for a test."""
    questions = list(test.questions.all())
    if len(questions) < 2:
        return questions
    return sorted(
        questions,
        key=lambda question: hashlib.sha256(
            f"{test.pk}:{student.pk}:{question.pk}".encode("utf-8")
        ).digest(),
    )


@login_required(login_url="/portal/")
@require_GET
def student_test_feed(request):
    from .portal_views import _active_portal_role
    if _active_portal_role(request) != PortalUser.Role.STUDENT:
        return JsonResponse({"error": "Student access required."}, status=403)
    tests = _available_tests_for_student(request.user)
    attempts = {
        attempt.test_id: attempt
        for attempt in TestAttempt.objects.filter(student=request.user, test__in=tests)
        .prefetch_related("answers")
    }
    items = []
    for test in tests:
        attempt = attempts.get(test.pk)
        if attempt and attempt.submitted_at:
            continue
        saved_answers = {answer.question_id: answer for answer in attempt.answers.all()} if attempt else {}
        for question in ordered_test_questions(test, request.user):
            saved = saved_answers.get(question.pk)
            completed = bool(saved and saved.answer.strip() and (
                test.grading_mode == SQLTest.GradingMode.MANUAL or saved.is_correct is True
            ))
            if completed:
                continue
            items.append({
                "testId": test.pk,
                "testTitle": test.title,
                "testCode": test.assessment.code if test.assessment_id else "",
                "questionId": question.pk,
                "position": question.position,
                "prompt": question.prompt,
                "allowTypedAnswer": test.allow_typed_answer,
                "completed": False,
                "isCorrect": saved.is_correct if saved else None,
            })
    return JsonResponse({"items": items}, headers={"Cache-Control": "no-store"})


def _submit_test_history_answer(request, test_id, question_id, history=None, answer_text=""):
    from .portal_views import _grade_sql_test_answers

    now = timezone.now()
    test = next((item for item in _available_tests_for_student(request.user, now) if item.pk == test_id), None)
    if not test:
        return JsonResponse({"error": "This test is not currently available."}, status=404)
    question = test.questions.filter(pk=question_id).first()
    if not question:
        return JsonResponse({"error": "This question does not belong to the selected test."}, status=404)
    if history is not None:
        answer_text = history.sql
    answer_text = str(answer_text or "").strip()
    if not answer_text:
        return JsonResponse({"error": "Enter an answer or choose a successful SQL history statement."}, status=400)
    if len(answer_text) > 200000:
        return JsonResponse({"error": "The answer is too long to submit."}, status=400)
    if history is None and not test.allow_typed_answer:
        return JsonResponse({"error": "Typing answers is disabled for this test. Choose a successful SQL history statement."}, status=403)
    from .assessment_sandbox import executed_test_run
    try:
        request._assessment_run = executed_test_run(request.user,test,[answer_text])
    except ValueError as exc:
        return JsonResponse({'error':str(exc)},status=409)

    grade = None
    if test.grading_mode != SQLTest.GradingMode.MANUAL:
        try:
            grade = _grade_sql_test_answers(request, test, [question], {question.pk: answer_text}).get(question.pk)
        except Exception as error:
            return JsonResponse({"error": f"Automatic evaluation could not run: {str(error)[:300]}"}, status=503)

    with transaction.atomic():
        attempt, _ = TestAttempt.objects.get_or_create(test=test, student=request.user)
        attempt = TestAttempt.objects.select_for_update().get(pk=attempt.pk)
        if attempt.submitted_at:
            return JsonResponse({"error": "This test has already been submitted."}, status=409)
        TestAnswer.objects.update_or_create(
            attempt=attempt,
            question=question,
            defaults={
                "answer": answer_text,
                "is_correct": grade.get("is_correct") if grade else None,
                "score": grade.get("score") if grade else None,
                "feedback": grade.get("feedback", "") if grade else "",
            },
        )
        answers = list(attempt.answers.all())
        all_answered = len(answers) == test.questions.count() and all(answer.answer.strip() for answer in answers)
        all_correct = all(answer.is_correct is True for answer in answers)
        completed = all_answered and (test.grading_mode == SQLTest.GradingMode.MANUAL or all_correct)
        if completed:
            attempt.submitted_at = now
            attempt.status = "submitted"
            attempt.score = sum((answer.score or 0 for answer in answers), Decimal("0")) if test.grading_mode != SQLTest.GradingMode.MANUAL else None
            attempt.save(update_fields=("submitted_at", "status", "score"))
    is_correct = grade.get("is_correct") if grade else None
    if grade:
        message = "Correct." if is_correct else "Incorrect. Choose another successful statement and try again."
    else:
        message = "Answer saved. Continue to the next test question."
    return JsonResponse({
        "ok": True, "message": message, "isCorrect": is_correct,
        "questionCompleted": bool(grade is None or is_correct), "testCompleted": completed,
    })


@login_required(login_url="/portal/")
@require_POST
def submit_test_history_answer(request):
    import json
    from .portal_views import _active_portal_role
    if _active_portal_role(request) != PortalUser.Role.STUDENT:
        return JsonResponse({"error": "Student access required."}, status=403)
    try:
        payload = json.loads(request.body or b"{}")
        test_id = int(payload.get("testId") or 0)
        question_id = int(payload.get("questionId") or 0)
        raw_history_id = str(payload.get("historyId") or "")
        answer_text = str(payload.get("answer") or "")
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Choose a test, question, and successful history statement."}, status=400)
    if not test_id or not question_id or (not raw_history_id and not answer_text.strip()):
        return JsonResponse({"error": "Choose a test question and provide an answer."}, status=400)
    if raw_history_id:
        if not raw_history_id.isdigit():
            return JsonResponse({"error": "Choose a valid successful history statement."}, status=400)
        history = CompilerQueryHistory.objects.filter(
            pk=int(raw_history_id), student=request.user,
            status=CompilerQueryHistory.ExecutionStatus.SUCCESS,
        ).first()
        if not history:
            return JsonResponse({"error": "That statement is not available in successful SQL history."}, status=404)
        return _submit_test_history_answer(request, test_id, question_id, history=history)
    return _submit_test_history_answer(request, test_id, question_id, answer_text=answer_text)


@login_required(login_url="/portal/")
@require_POST
def submit_latest_sqlcl_test_answer(request):
    import json
    from .portal_views import _active_portal_role
    if _active_portal_role(request) != PortalUser.Role.STUDENT:
        return JsonResponse({"error": "Student access required."}, status=403)
    try:
        payload = json.loads(request.body or b"{}")
        test_id, question_id = int(payload.get("testId") or 0), int(payload.get("questionId") or 0)
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Choose a test and question first."}, status=400)
    history = CompilerQueryHistory.objects.filter(
        student=request.user,
        source=CompilerQueryHistory.Source.SQLPLUS,
        status=CompilerQueryHistory.ExecutionStatus.SUCCESS,
    ).order_by("-executed_at", "-pk").first()
    if not history:
        return JsonResponse({"error": "No successful SQLCL statement is available yet."}, status=404)
    return _submit_test_history_answer(request, test_id, question_id, history)


class DailyTaskForm(ScheduleForm):
    field_order = ["class_sections", "lab", "title", "general_instructions", "instructions", "question_file", "tags", "hint", "sample_schema", "schema_file", "grading_mode", "expected_sql", "verification_sql", "start_at", "due_at", "late_policy", "published"]
    tags = forms.CharField(required=False, max_length=300)
    hint = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    sample_schema = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Sample-table setup SQL (optional)", help_text="Runs once in each daily-task sandbox. Prefix notes with --.")
    schema_file = forms.FileField(required=False, label="Upload sample-table setup code (.sql or .txt)", help_text="Optional UTF-8 SQL/TXT file, up to 100 KB; executed once when the sandbox starts.", widget=forms.ClearableFileInput(attrs={"accept": ".sql,.txt"}))
    class_sections = forms.ModelMultipleChoiceField(
        queryset=ClassSection.objects.none(), label="Classes",
        widget=forms.SelectMultiple(attrs={"size": 7, "data-daily-class-select": ""}),
    )
    lab = forms.ModelChoiceField(
        queryset=CompilerLab.objects.none(), required=False,
        label="Lab (optional)", empty_label="Whole selected classes",
        help_text="For a lab task, select one class and one of its labs. Leave blank for a whole-class task.",
    )
    title = forms.CharField(max_length=200)
    instructions = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 5}), label="Questions")
    general_instructions = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Instructions", max_length=200000)
    question_file = forms.FileField(required=False, label="Upload questions", widget=forms.ClearableFileInput(attrs={"accept": ".txt,.csv,.docx"}))
    grading_mode = forms.ChoiceField(required=False, choices=Assignment.GradingMode.choices, initial="manual", label="Default grading method")
    expected_sql = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Default reference SQL (optional)", help_text="Set or adjust each question's grading and reference answer in the preview before saving.")
    verification_sql = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Verification SQL (optional)", help_text="Run calls and checks after the answer. Required for stored programs and triggers; use the same test inputs for both solutions.")
    published = forms.BooleanField(required=False, label="Enable questions for students")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Drafts are private content. Audience and schedule are set at mapping time.
        for name in ("class_sections", "lab", "start_at", "due_at", "late_policy", "published"):
            self.fields.pop(name, None)

    def clean(self):
        from .question_import import parse_upload, parse_text
        data = super().clean()
        lab = data.get("lab")
        classes = list(data.get("class_sections", []))
        if lab and (len(classes) != 1 or classes[0].pk != lab.class_section_id):
            self.add_error("lab", "Select exactly one class and a lab belonging to that class.")
        text = data.get("instructions", "").strip()
        upload = data.get("question_file")
        questions = []
        pasted_questions = []
        if text:
            try:
                pasted_questions = parse_text(text)
            except ValueError as error:
                self.add_error("instructions", str(error))
        if upload:
            try:
                questions = parse_upload(upload)
            except ValueError as error:
                self.add_error("question_file", str(error))
        if not text and not questions and not self.errors:
            self.add_error("instructions", "Enter a question or upload a question file.")
        schema_file = data.get("schema_file")
        if schema_file:
            from pathlib import Path
            if schema_file.size > 100000 or Path(schema_file.name).suffix.lower() not in {".sql", ".txt"}:
                self.add_error("schema_file", "Use a SQL or TXT schema file up to 100 KB.")
            else:
                try:
                    data["sample_schema"] = (data.get("sample_schema", "") + "\n" + schema_file.read().decode("utf-8-sig")).strip()
                except UnicodeDecodeError:
                    self.add_error("schema_file", "Schema files must use UTF-8 text.")
        if len(pasted_questions + questions) > 200:
            self.add_error("question_file" if upload else "instructions", "Preview at most 200 questions at a time.")
        data["question_records"] = pasted_questions + questions
        data["questions"] = [item["prompt"] for item in data["question_records"]]
        data["instructions"] = text
        return data


def _evaluate_daily_answer(request, task, answer):
    """Run the same safe, reference-based grading used by SQL tests."""
    from types import SimpleNamespace
    from .portal_views import _grade_sql_test_answers

    test = SimpleNamespace(grading_mode=task.grading_mode, sample_schema=task.sample_schema)
    question = SimpleNamespace(pk=task.pk, expected_sql=task.expected_sql, verification_sql=task.verification_sql, marks=task.max_score)
    return _grade_sql_test_answers(request, test, [question], {task.pk: answer}).get(task.pk)


def _apply_daily_auto_grade(request, task, submission):
    if task.grading_mode == Assignment.GradingMode.MANUAL:
        return None
    attempt_time = submission.last_attempt_at
    try:
        grade = _evaluate_daily_answer(request, task, submission.answer)
        if grade is None:
            return None
        Submission.objects.filter(pk=submission.pk, last_attempt_at=attempt_time).update(
            is_correct=grade["is_correct"], status=Submission.Status.AUTO_GRADED,
            score=grade["score"], feedback=grade["feedback"], reviewed_at=timezone.now(),
        )
        return grade
    except Exception:
        Submission.objects.filter(pk=submission.pk, last_attempt_at=attempt_time).update(
            status=Submission.Status.SUBMITTED,
            feedback="Automatic execution grading is unavailable or its reference checks need attention. Your answer was saved for teacher review.",
            reviewed_at=None,
        )
        return None


def _daily_task_report(teacher):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="daily-task-report.csv"'
    response["Cache-Control"] = "no-store"
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(["Question ID", "Assessment code", "Question", "Course", "Class", "Enabled", "Question date",
                     "Student username", "Student name", "Submission status", "Result", "Score",
                     "Maximum score", "First attempt", "Last attempt"])

    def date(value):
        return timezone.localtime(value).isoformat() if value else ""

    def safe(value):
        # Keep user-entered labels from becoming spreadsheet formulas.
        text = str(value)
        return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text

    tasks = Assignment.objects.filter(created_by=teacher).select_related("class_section__course", "assessment", "lab").prefetch_related(
        "submissions__student", "class_section__enrollments__student",
    ).order_by("pk")
    for task in tasks:
        submissions = {item.student_id: item for item in task.submissions.all()}
        students = {item.student_id: item.student for item in task.class_section.enrollments.all()
                    if item.active and item.student.is_active and item.student.role == PortalUser.Role.STUDENT}
        if task.lab_id and LabEnrollment.objects.filter(lab_id=task.lab_id, active=True).exists():
            roster = set(LabEnrollment.objects.filter(lab_id=task.lab_id, active=True).values_list("student_id", flat=True))
            students = {pk: student for pk, student in students.items() if pk in roster}
        students.update({item.student_id: item.student for item in submissions.values()})
        for student in sorted(students.values(), key=lambda item: item.username):
            submission = submissions.get(student.pk)
            result = ""
            if submission and submission.status in {Submission.Status.AUTO_GRADED, Submission.Status.REVIEWED}:
                result = "Correct" if submission.is_correct else "Incorrect"
            writer.writerow([safe(value) for value in [
                task.pk, task.assessment.code if task.assessment_id else "", task.title, task.class_section.course.name, task.class_section.name,
                "Yes" if task.published else "No", date(task.enabled_at), student.username,
                student.get_full_name(), submission.get_status_display() if submission else "Not submitted",
                result, submission.score if submission and submission.score is not None else "", task.max_score,
                date(submission.first_attempt_at or submission.submitted_at) if submission else "",
                date(submission.last_attempt_at or submission.submitted_at) if submission else "",
            ]])
    return response


@login_required(login_url="/portal/")
@require_http_methods(["GET", "POST"])
def daily_tasks(request):
    from .portal_views import (_active_portal_role, _available_portal_roles, _dashboard_path,
                               _dashboard_navigation_items, _global_default_accent, _render_dashboard)
    role = _active_portal_role(request)
    if role not in (PortalUser.Role.TEACHER, PortalUser.Role.STUDENT):
        return redirect("/portal/")
    teacher = role == PortalUser.Role.TEACHER
    if request.method == "GET" and request.GET.get("report") == "csv":
        if not teacher:
            return HttpResponse("Teacher access required.", status=403)
        return _daily_task_report(request.user)
    form = None
    message = ""
    preview = None
    stage_token = ""
    if teacher:
        # DISTINCT across a joined Course also compares its NCLOB description on Oracle.
        # A membership subquery deduplicates class IDs without comparing text columns.
        assigned_classes = TeacherAssignment.objects.filter(teacher=request.user).values("class_section_id")
        classes = ClassSection.objects.filter(pk__in=assigned_classes, is_active=True).select_related("course").order_by(
            "course__name", "name", "branch", "semester", "section", "group",
        )
        if request.method == "POST" and request.POST.get("action") == "commit_import":
            preview, message = commit_import(request, classes)
            stage_token = request.POST.get("stage_token", "")
            if not message:
                return redirect("/portal/daily-tasks/")
        if request.method == "POST" and request.POST.get("action") in {
            "delete_selected", "delete_all", "disable_selected", "disable_all",
            "enable_selected", "enable_all",
            "configure_grading_selected", "configure_grading_all",
        }:
            action = request.POST["action"]
            selected_action = action in {"delete_selected", "disable_selected", "enable_selected", "configure_grading_selected"}
            with transaction.atomic():
                owned_tasks = Assignment.objects.filter(created_by=request.user)
                if selected_action:
                    raw_ids = request.POST.getlist("task_ids")
                    if not raw_ids or any(not value.isdigit() for value in raw_ids):
                        message = "Select at least one daily question first."
                    else:
                        selected_ids = {int(value) for value in raw_ids}
                        owned_tasks = owned_tasks.filter(pk__in=selected_ids)
                if not message:
                    locked_ids = list(owned_tasks.select_for_update().order_by("pk").values_list("pk", flat=True))
                    if selected_action and set(locked_ids) != selected_ids:
                        message = "One or more selected questions could not be found or are not yours to manage. Nothing was changed."
                    elif not locked_ids:
                        message = "There are no daily questions to manage."
                    elif action in {"configure_grading_selected", "configure_grading_all"}:
                        mode = request.POST.get("grading_mode", Assignment.GradingMode.MANUAL)
                        if mode not in Assignment.GradingMode.values:
                            message = "Choose a valid evaluation method."
                        else:
                            tasks_to_update = list(owned_tasks.order_by("pk"))
                            missing_reference = [task for task in tasks_to_update if mode != Assignment.GradingMode.MANUAL and not task.expected_sql.strip()]
                            if missing_reference:
                                message = f"Automatic evaluation was not applied. Add a reference SQL answer to all {len(missing_reference)} selected question(s) first."
                            elif mode == Assignment.GradingMode.RESULT_MATCH:
                                from .execution_grading import validate_reference
                                try:
                                    for task in tasks_to_update:
                                        validate_reference(task.expected_sql, task.verification_sql, task.sample_schema)
                                except ValueError as exc:
                                    message = str(exc)
                            if not message:
                                owned_tasks.update(grading_mode=mode)
                                AuditEvent.objects.create(
                                    actor=request.user,
                                    action="daily_tasks_bulk_grading_configured",
                                    target=f"teacher:{request.user.pk}",
                                    details={"task_ids": locked_ids, "task_count": len(locked_ids), "grading_mode": mode},
                                )
                    elif action in {"enable_selected", "enable_all"}:
                        owned_tasks.filter(Q(published=False) | Q(enabled_at__isnull=True)).update(enabled_at=timezone.now())
                        owned_tasks.update(published=True)
                        AuditEvent.objects.create(
                            actor=request.user,
                            action="daily_tasks_bulk_enabled",
                            target=f"teacher:{request.user.pk}",
                            details={"task_ids": locked_ids, "task_count": len(locked_ids)},
                        )
                    elif action in {"disable_selected", "disable_all"}:
                        owned_tasks.update(published=False)
                        AuditEvent.objects.create(
                            actor=request.user,
                            action="daily_tasks_bulk_disabled",
                            target=f"teacher:{request.user.pk}",
                            details={"task_ids": locked_ids, "task_count": len(locked_ids)},
                        )
                    else:
                        AuditEvent.objects.create(
                            actor=request.user,
                            action="daily_tasks_bulk_deleted",
                            target=f"teacher:{request.user.pk}",
                            details={"task_ids": locked_ids, "task_count": len(locked_ids)},
                        )
                        owned_tasks.delete()
            if not message:
                return redirect("/portal/daily-tasks/")
        if request.method == "POST" and request.POST.get("action") in {"set_published", "review_answer", "delete_task", "configure_grading", "resolve_dispute", "configure_schedule"}:
            task_id = request.POST.get("task_id", "")
            with transaction.atomic():
                try:
                    task = Assignment.objects.select_for_update().get(
                        pk=task_id if task_id.isdigit() else None,
                        created_by=request.user,
                    )
                except Assignment.DoesNotExist:
                    task = None
                if not task:
                    message = "That daily task could not be found or is not yours to manage."
                elif request.POST.get("action") == "delete_task":
                    submission_count = task.submissions.count()
                    task_target = f"daily_task:{task.pk}"
                    AuditEvent.objects.create(
                        actor=request.user,
                        action="daily_task_deleted",
                        target=task_target,
                        details={"title": task.title, "submission_count": submission_count},
                    )
                    task.delete()
                elif request.POST.get("action") == "configure_schedule":
                    schedule_form = ScheduleForm(request.POST, prefix=f"schedule-{task.pk}")
                    if schedule_form.is_valid():
                        for field, value in schedule_form.cleaned_data.items():
                            setattr(task, field, value)
                        task.save(update_fields=tuple(schedule_form.cleaned_data))
                    else:
                        message = "Schedule not saved: " + str(schedule_form.errors.as_text())
                elif request.POST.get("action") == "set_published":
                    enabled = request.POST.get("published") == "1"
                    if enabled and (not task.published or not task.enabled_at):
                        task.enabled_at = timezone.now()
                    task.published = enabled
                    task.save(update_fields=("published", "enabled_at"))
                elif request.POST.get("action") == "configure_grading":
                    mode = request.POST.get("grading_mode", Assignment.GradingMode.MANUAL)
                    expected_sql = request.POST.get("expected_sql", "").strip()
                    verification_sql = request.POST.get("verification_sql", "").strip()
                    if mode not in Assignment.GradingMode.values:
                        message = "Choose a valid grading method."
                    elif mode != Assignment.GradingMode.MANUAL and not expected_sql:
                        message = "Enter a reference SQL statement to enable automatic evaluation."
                    elif mode == Assignment.GradingMode.RESULT_MATCH:
                        from .execution_grading import validate_reference
                        try:
                            validate_reference(expected_sql, verification_sql, task.sample_schema)
                        except ValueError as exc:
                            message = str(exc)
                    if not message:
                        task.grading_mode = mode
                        task.expected_sql = expected_sql if mode != Assignment.GradingMode.MANUAL else ""
                        task.verification_sql = verification_sql
                        task.save(update_fields=("grading_mode", "expected_sql", "verification_sql"))
                elif request.POST.get("action") == "resolve_dispute":
                    submission_id = request.POST.get("submission_id", "")
                    submission = Submission.objects.select_for_update().filter(
                        pk=submission_id if submission_id.isdigit() else None,
                        assignment=task,
                        status=Submission.Status.DISPUTED,
                    ).first()
                    verdict = request.POST.get("verdict")
                    response = request.POST.get("dispute_response", "").strip()
                    if not submission:
                        message = "That open student dispute could not be found."
                    elif verdict not in {"uphold", "accept"}:
                        message = "Choose whether to uphold or accept the dispute."
                    elif not response:
                        message = "Enter a response for the student."
                    else:
                        submission.is_correct = verdict == "accept"
                        submission.status = Submission.Status.REVIEWED
                        submission.score = task.max_score if submission.is_correct else 0
                        submission.feedback = response
                        submission.dispute_response = response
                        submission.dispute_resolved_at = timezone.now()
                        submission.reviewed_at = timezone.now()
                        submission.save(update_fields=("is_correct", "status", "score", "feedback", "dispute_response", "dispute_resolved_at", "reviewed_at"))
                        AuditEvent.objects.create(actor=request.user, action="daily_task_dispute_resolved", target=f"submission:{submission.pk}", details={"task_id": task.pk, "verdict": verdict})
                else:
                    submission_id = request.POST.get("submission_id", "")
                    try:
                        submission = Submission.objects.select_for_update().get(
                            pk=submission_id if submission_id.isdigit() else None,
                            assignment=task,
                        )
                    except Submission.DoesNotExist:
                        submission = None
                    if submission and not submission.is_correct:
                        # A teacher must review the answer version currently stored.
                        version = submission.last_attempt_at or submission.submitted_at
                        if request.POST.get("answer_version") != version.isoformat():
                            message = "The student submitted a newer answer. Review it before marking."
                        elif request.POST.get("verdict") in {"correct", "retry"}:
                            submission.is_correct = request.POST["verdict"] == "correct"
                            submission.status = Submission.Status.REVIEWED
                            submission.score = task.max_score if submission.is_correct else 0
                            submission.reviewed_at = timezone.now()
                            submission.feedback = request.POST.get("feedback", "").strip()
                            submission.save(update_fields=("is_correct", "status", "score", "reviewed_at", "feedback"))
                            if submission.is_correct:
                                # Once a teacher confirms one answer, apply that decision to every
                                # matching answer for the same question, including prior attempts.
                                answer_key = _equivalent_answer_key(submission.answer)
                                matching = [item.pk for item in Submission.objects.filter(assignment=task)
                                            if _equivalent_answer_key(item.answer) == answer_key]
                                Submission.objects.filter(pk__in=matching).update(
                                    is_correct=True, status=Submission.Status.REVIEWED,
                                    score=task.max_score, reviewed_at=submission.reviewed_at,
                                    feedback=submission.feedback,
                                )
            if not message:
                if request.POST.get("return_view") == "review":
                    from urllib.parse import urlencode
                    filters = {key: value for key, value in request.GET.items() if key in {"review_class", "review_status", "review_date", "review_page", "review_sort", "review_dir", "review_student", "review_task", "review_result", "review_review"}}
                    return redirect("/portal/daily-tasks/?" + urlencode({"view": "review", **filters}) + "#submission-review")
                return redirect("/portal/daily-tasks/")
        form = DailyTaskForm(request.POST if request.method == "POST" and request.POST.get("action", "") in ("", "preview_import") else None, request.FILES or None)
        if request.method == "POST" and request.POST.get("action", "") in ("", "preview_import") and form.is_valid():
            stage_token, preview = stage_import(request.user, form.cleaned_data)
        tasks = Assignment.objects.filter(created_by=request.user).select_related("class_section__course", "assessment", "lab").prefetch_related("submissions__student").order_by("pk")
    else:
        # Resolve the audience from live enrollments, not a recipient snapshot:
        # students enrolled after a task was created see it too.
        tasks = available_daily_tasks_for_student(request.user).order_by("pk")
        if request.method == "POST":
            if request.POST.get("action") == "dispute_answer":
                submission_id = request.POST.get("submission_id", "")
                reason = request.POST.get("dispute_message", "").strip()
                submission = Submission.objects.filter(
                    pk=submission_id if submission_id.isdigit() else None,
                    student=request.user,
                    status=Submission.Status.AUTO_GRADED,
                    is_correct=False,
                    assignment__in=tasks,
                ).first()
                if not submission:
                    message = "This answer cannot be disputed now. It may already be under review or resolved."
                elif not reason:
                    message = "Explain why you think the automatic evaluation is incorrect."
                elif len(reason) > 4000:
                    message = "Keep the dispute explanation under 4,000 characters."
                else:
                    submission.dispute_message = reason
                    submission.disputed_at = timezone.now()
                    submission.status = Submission.Status.DISPUTED
                    submission.save(update_fields=("dispute_message", "disputed_at", "status"))
                    AuditEvent.objects.create(actor=request.user, action="daily_task_disputed", target=f"submission:{submission.pk}", details={"task_id": submission.assignment_id})
                    return redirect("/portal/daily-tasks/")
            if request.POST.get("action") == "dispute_answer":
                pass
            else:
                task_id = request.POST.get("task_id", "")
                answer = ""
                history_id = request.POST.get("history_id", "")
                if not history_id:
                    message = "Choose a successfully executed statement from SQL history before submitting."
                else:
                    history = CompilerQueryHistory.objects.filter(
                        pk=history_id if history_id.isdigit() else None, student=request.user, status="success",
                    ).first()
                    if history:
                        answer = history.sql
                    else:
                        message = "That successful statement is no longer available. Choose another statement from SQL history."
                evaluated_task = None
                submitted_answer = ""
                submitted_at = None
                with transaction.atomic():
                    try:
                        task = tasks.select_for_update().get(pk=task_id if task_id.isdigit() else None)
                    except Assignment.DoesNotExist:
                        task = None
                    if message:
                        pass
                    elif not task:
                        message = "This question is not currently enabled for you."
                    elif submission_window_error(task):
                        message = submission_window_error(task)
                    elif not answer:
                        message = "Choose a successfully executed statement from SQL history before submitting."
                    else:
                        now = timezone.now()
                        submission, created = Submission.objects.select_for_update().get_or_create(
                            assignment=task, student=request.user,
                            defaults={"answer": answer, "is_late": late_submission(task, now), "first_attempt_at": now, "last_attempt_at": now, "submitted_at": now},
                        )
                        if submission.is_correct:
                            message = "Your answer is marked correct. Further attempts are locked."
                        elif submission.status == Submission.Status.DISPUTED:
                            message = "Your answer is under dispute review. Wait for your teacher’s response before retrying."
                        elif not created:
                            submission.first_attempt_at = submission.first_attempt_at or submission.submitted_at
                            submission.last_attempt_at = now
                            submission.submitted_at = now
                            submission.answer = answer
                            submission.is_late = late_submission(task, now)
                            submission.status = Submission.Status.SUBMITTED
                            submission.reviewed_at = None
                            submission.feedback = ""
                            submission.score = None
                            submission.dispute_message = ""
                            submission.disputed_at = None
                            submission.dispute_response = ""
                            submission.dispute_resolved_at = None
                            submission.save(update_fields=("is_late", "first_attempt_at", "last_attempt_at", "submitted_at", "answer", "status", "reviewed_at", "feedback", "score", "dispute_message", "disputed_at", "dispute_response", "dispute_resolved_at"))
                        if not message:
                            evaluated_task, submitted_answer, submitted_at = task, answer, submission.last_attempt_at
                if not message and evaluated_task:
                    if evaluated_task.grading_mode != Assignment.GradingMode.MANUAL:
                        _apply_daily_auto_grade(request, evaluated_task, submission)
                    return redirect("/portal/daily-tasks/")
        selected_class = request.GET.get("class", "")
        if selected_class.isdigit():
            tasks = tasks.filter(class_section_id=int(selected_class))
        selected_task = request.GET.get("task", "")
        if selected_task.isdigit():
            tasks = tasks.filter(pk=int(selected_task))
        submissions = {item.assignment_id: item for item in Submission.objects.filter(student=request.user, assignment__in=tasks)}
        tasks = list(tasks)
        for task in tasks:
            task.student_submission = submissions.get(task.pk)
            task.student_state = task.student_submission.review_state if task.student_submission else "pending"
            task.window_error = submission_window_error(task)
    if teacher:
        for task in tasks:
            task.schedule_form = ScheduleForm(prefix=f"schedule-{task.pk}", initial={field: getattr(task, field) for field in ("start_at", "due_at", "late_policy")})
            for submission in task.submissions.all():
                submission.answer_version = (submission.last_attempt_at or submission.submitted_at).isoformat()
    daily_view = request.GET.get("view", "library" if teacher else "tasks")
    if daily_view not in {"library", "tasks", "create", "review"}: daily_view = "library" if teacher else "tasks"
    if teacher and (preview is not None or (form and form.is_bound and request.POST.get("action", "") in {"", "preview_import", "commit_import"})):
        daily_view = "create"
    from .assessment_library import library_context
    return _render_dashboard("daily-tasks.html", {
        "user": request.user, "portal_role": role, "is_daily_tasks_page": True,
        "has_multiple_roles": len(_available_portal_roles(request.user)) > 1,
        "global_default_accent": _global_default_accent(), "dashboard_home_path": _dashboard_path(role),
        "dashboard_navigation_items": _dashboard_navigation_items(role),
        "tasks": tasks, "form": form, "message": message, "preview": preview, "stage_token": stage_token, "focus_task_id": request.GET.get("task", ""), "daily_view": daily_view,
        **(library_context(request, "daily") if teacher and daily_view == "library" else {}),
        **(review_context(request, classes) if teacher and daily_view == "review" else {}),
    }, request)
