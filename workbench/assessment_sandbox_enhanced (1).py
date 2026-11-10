"""Tests and daily tasks share the file checker's isolated, resumable sandbox."""
import json
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django.core.exceptions import ObjectDoesNotExist

from .models import (ProjectExecution, ProjectRunnerConfiguration, SQLTest, TestAttempt,
    TestAnswer, CompilerQueryHistory)
from .project_runner import _payload, render_project_checker, PROJECT_CLIENT_COMMAND
from .project_schemas import finish_previous_checks, remove_schema
from .project_sqlplus import validate_project_command, close_project_session, project_session
from .sql_lexer import split_statements
from .views import same_origin


def assessment_for(request, kind, item_id):
    from .portal_views import _active_portal_role
    if _active_portal_role(request) != 'STUDENT':
        raise PermissionError('Student access is required.')
    if kind == 'test':
        from .daily_tasks import _available_tests_for_student
        item = next((test for test in _available_tests_for_student(request.user) if test.pk == item_id), None)
    elif kind == 'daily_task':
        from .daily_tasks import available_daily_tasks_for_student
        item = available_daily_tasks_for_student(request.user).filter(pk=item_id).first()
        if item:
            from .daily_task_workflow import submission_window_error
            if submission_window_error(item, timezone.now()):
                raise PermissionError('This daily task is outside its submission window.')
            from .models import Submission
            if Submission.objects.filter(assignment=item,student=request.user,is_correct=True).exists():
                raise PermissionError('This daily task is already marked correct.')
            if Submission.objects.filter(assignment=item,student=request.user,status='disputed').exists():
                raise PermissionError('This daily task is awaiting dispute review.')
    else:
        raise ValueError('Unknown assessment type.')
    if not item:
        raise PermissionError('This assessment is not currently available to you.')
    return item


def assessment_context(item, kind, run, user):
    from .daily_tasks import ordered_test_questions
    return {'kind':kind,'id':item.pk,'title':item.title,'instructions':(('Instructions\n' + item.general_instructions + '\n\nQuestion\n' + item.instructions) if kind == 'daily_task' and item.general_instructions else item.instructions),
        'state':run.workspace_state if run else {},
        'questions':[{'id':q.pk,'prompt':q.prompt,'position':index+1} for index,q in enumerate(ordered_test_questions(item,user))] if kind == 'test' else [],
        'starterSql':getattr(item,'starter_sql','')}


@login_required
@never_cache
@require_http_methods(['GET'])
def assessment_page(request, kind, item_id):
    try:
        item = assessment_for(request,kind,item_id)
    except (ValueError,PermissionError) as exc:
        return JsonResponse({'error':str(exc)},status=403)
    run = ProjectExecution.objects.filter(owner=request.user,activity_kind=kind,activity_id=item_id).order_by('-pk').first()
    return render_project_checker(request,assessment_context(item,kind,run,request.user),run)


def checked_statements(source):
    if not isinstance(source,str) or len(source) > 200000:
        raise ValueError('SQL must be text up to 200,000 characters.')
    statements = split_statements(source,sqlplus=True,client_command=PROJECT_CLIENT_COMMAND)
    for statement in statements:
        validate_project_command(statement)
    return statements


def save_answer(request,item,run,question_id,index):
    # Only an execution recorded as successful in this sandbox can become an answer.
    result = next((row for row in reversed(run.results) if row.get('index') == index and row.get('status') == 'success'),None)
    if result is None or not isinstance(index,int) or index < run.setup_statement_count:
        raise ValueError('Run your answer successfully in this sandbox before saving it.')
    if result.get('inputMethod') != 'typed':
        raise ValueError('Type and run the answer in this assessment sandbox before saving it.')
    sql = result.get('statement') or run.statements[index]
    if run.activity_kind == 'test':
        question = item.questions.get(pk=question_id)
        attempt,_ = TestAttempt.objects.get_or_create(test=item,student=request.user)
        if attempt.submitted_at:
            raise ValueError('This test has already been submitted.')
        TestAnswer.objects.update_or_create(attempt=attempt,question=question,defaults={'answer':sql,'is_correct':None,'score':None,'feedback':''})
    state = dict(run.workspace_state)
    answers = dict(state.get('answers',{}))
    answers[str(question_id if run.activity_kind == 'test' else item.pk)] = sql
    state['answers'] = answers
    run.workspace_state = state
    run.save(update_fields=['workspace_state'])
    return sql


def executed_test_run(user,test,answers):
    return typed_assessment_run(user,'test',test.pk,answers)


def typed_assessment_run(user,kind,item_id,answers):
    run = ProjectExecution.objects.filter(owner=user,activity_kind=kind,activity_id=item_id,
        status='ready',schema_removed_at__isnull=True,schema_expires_at__gt=timezone.now()).order_by('-pk').first()
    if run is None:
        raise ValueError('Open the test sandbox and run your answers before submitting.')
    executed = {row.get('statement','').strip() for row in run.results
        if row.get('status') == 'success' and row.get('inputMethod') == 'typed' and row.get('index',-1) >= run.setup_statement_count}
    if any(not answer.strip() or answer.strip() not in executed for answer in answers):
        raise ValueError('Each test answer must be executed successfully in the test sandbox.')
    return run


def submit_assessment(request,item,run):
    if run.status != 'ready':
        raise ValueError('Finish the running SQL and supply any missing variables before submitting.')
    client = project_session(run)
    if not client.lock.acquire(blocking=False):
        raise ValueError('The sandbox is busy. Try submitting again shortly.')
    try:
        client.execute('COMMIT;')
        request._assessment_run = run
        if run.activity_kind == 'test':
            from .portal_views import _grade_sql_test_answers
            attempt,_ = TestAttempt.objects.get_or_create(test=item,student=request.user)
            if attempt.submitted_at:
                raise ValueError('This test has already been submitted.')
            questions = list(item.questions.all())
            answers = {answer.question_id:answer.answer for answer in attempt.answers.all()}
            if not questions or any(not answers.get(question.pk,'').strip() for question in questions):
                raise ValueError('Save an executed answer for every question first.')
            executed_test_run(request.user,item,answers.values())
            grades = _grade_sql_test_answers(request,item,questions,answers)
            with transaction.atomic():
                attempt = TestAttempt.objects.select_for_update().get(pk=attempt.pk)
                if attempt.submitted_at:
                    raise ValueError('This test has already been submitted.')
                for question in questions:
                    grade = grades.get(question.pk)
                    if grade:
                        TestAnswer.objects.filter(attempt=attempt,question=question).update(
                            is_correct=grade['is_correct'],score=grade['score'],feedback=grade['feedback'])
                attempt.status,attempt.submitted_at = 'submitted',timezone.now()
                attempt.score = sum((grade['score'] for grade in grades.values()),Decimal('0')) if grades else None
                attempt.save(update_fields=['status','submitted_at','score'])
            if attempt.score is not None:
                response = {
                    'ok': True,
                    'message': f'Test submitted successfully! Your score: {attempt.score} / {item.total_marks}',
                    'score': float(attempt.score),
                    'totalMarks': float(item.total_marks),
                    'isGraded': True
                }
            else:
                response = {
                    'ok': True,
                    'message': 'Test submitted. Your teacher can review your answers and sandbox report.',
                    'isGraded': False
                }
        else:
            from django.test import RequestFactory
            from .daily_tasks import submit_daily_task_answer
            sql = run.workspace_state.get('answers',{}).get(str(item.pk))
            if not sql:
                raise ValueError('Save a successfully executed answer first.')
            typed_assessment_run(request.user,'daily_task',item.pk,[sql])
            history = CompilerQueryHistory.objects.create(student=request.user,source='SQL*Plus',
                sql=sql,status='success',input_method='sandbox',message='Executed in daily-task sandbox')
            internal = RequestFactory().post('/api/portal/daily-tasks/submit-answer',
                {'taskId':item.pk,'historyId':history.pk},content_type='application/json')
            internal.user,internal.session,internal._assessment_run = request.user,request.session,run
            result = submit_daily_task_answer(internal)
            response = json.loads(result.content)
            if result.status_code != 200:
                raise ValueError(response.get('error','Unable to submit this task.'))
        run.status = 'completed'
        run.save(update_fields=['status','updated_at'])
    finally:
        client.lock.release()
    close_project_session(run.pk)
    remove_schema(run)
    return response


@login_required
@never_cache
@require_http_methods(['POST'])
def assessment_action(request,kind,item_id):
    if not same_origin(request):
        return JsonResponse({'error':'Cross-origin request blocked.'},status=403)
    try:
        item = assessment_for(request,kind,item_id)
        payload = json.loads(request.body or '{}')
        action = payload.get('action')
        if action == 'start':
            if kind == 'test' and TestAttempt.objects.filter(test=item,student=request.user,submitted_at__isnull=False).exists():
                raise ValueError('This test has already been submitted.')
            setup = checked_statements(item.sample_schema or 'BEGIN NULL; END;')
            config = ProjectRunnerConfiguration.objects.get(pk=1)
            with transaction.atomic():
                finish_previous_checks(request.user)
                run = ProjectExecution.objects.create(owner=request.user,student=request.user,
                    name=item.title,activity_kind=kind,activity_id=item_id,queue_role='STUDENT',
                    sandbox_connection=config.sandbox_connection,source_text=item.sample_schema,
                    statements=setup,setup_statement_count=len(setup))
                if kind == 'test':
                    attempt,_ = TestAttempt.objects.get_or_create(test=item,student=request.user)
                    if attempt.status == 'suspended':
                        attempt.status = 'in_progress'
                        attempt.save(update_fields=['status'])
                from .project_queue import enqueue
                transaction.on_commit(lambda:enqueue(run,{}))
            run.refresh_from_db()
            return JsonResponse(_payload(run),status=201)
        run = ProjectExecution.objects.get(pk=payload.get('runId'),owner=request.user,activity_kind=kind,activity_id=item_id)
        if run.status in {'cancelled','completed'}:
            raise ValueError('This sandbox was ended or replaced by another activity. Start a new sandbox to continue.')
        if run.schema_expires_at and run.schema_expires_at <= timezone.now():
            raise ValueError('This sandbox has expired. Start a new sandbox to continue.')
        if action == 'draft':
            if payload.get('inputMethod') != 'typed':
                raise ValueError('Assessment answers must be typed in the sandbox.')
            with transaction.atomic():
                run = ProjectExecution.objects.select_for_update().get(pk=run.pk)
                state = dict(run.workspace_state)
                state['editorSQL'] = str(payload.get('sql',''))[:200000]
                state['questionId'] = str(payload.get('questionId',''))
                drafts = dict(state.get('drafts',{}))
                drafts[state['questionId']] = state['editorSQL']
                state['drafts'] = drafts
                ProjectExecution.objects.filter(pk=run.pk).update(workspace_state=state)
            return JsonResponse({'saved':True})
        if action == 'run':
            if payload.get('inputMethod') != 'typed':
                raise ValueError('Assessment answers must be typed in the sandbox. History and file imports are not allowed.')
            statements = checked_statements(payload.get('sql',''))
            if not statements:
                raise ValueError('Enter SQL to execute.')
            with transaction.atomic():
                run = ProjectExecution.objects.select_for_update().get(pk=run.pk)
                if run.status not in {'ready','paused'}:
                    raise ValueError('Wait for the current execution to finish.')
                if run.next_index < run.setup_statement_count:
                    raise ValueError('Correct the failed sample setup statement first.')
                run.statements = run.statements[:run.next_index] + statements
                run.workspace_state = {**run.workspace_state,'editorSQL':payload['sql'],'questionId':str(payload.get('questionId',''))}
                run.save(update_fields=['statements','workspace_state'])
                from .project_queue import enqueue
                transaction.on_commit(lambda:enqueue(run,{}))
            run.refresh_from_db()
            return JsonResponse(_payload(run),status=202)
        if action == 'save_answer':
            with transaction.atomic():
                run = ProjectExecution.objects.select_for_update().get(pk=run.pk)
                save_answer(request,item,run,payload.get('questionId'),payload.get('index'))
            return JsonResponse({'message':'Executed answer saved.'})
        if action == 'submit':
            return JsonResponse(submit_assessment(request,item,run))
        raise ValueError('Unknown sandbox action.')
    except PermissionError as exc:
        return JsonResponse({'error':str(exc)},status=403)
    except ObjectDoesNotExist:
        return JsonResponse({'error':'Sandbox not found.'},status=404)
    except (ValueError,TypeError,AttributeError) as exc:
        return JsonResponse({'error':str(exc)},status=400)
