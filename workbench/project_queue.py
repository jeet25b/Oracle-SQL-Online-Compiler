"""Persistent, teacher-first admission with database-serialized slot allocation."""
from concurrent.futures import ThreadPoolExecutor
import logging
import threading

from django.db import close_old_connections, transaction

from .models import ProjectExecution, ProjectRunnerConfiguration

_executor = ThreadPoolExecutor(max_workers=35, thread_name_prefix='project-file')
_start_lock = threading.Lock()
_started = False
_wake = threading.Event()
_jobs = set()
_resumptions = {}
_jobs_lock = threading.Lock()


def reserve_next():
    with transaction.atomic():
        try:
            config = ProjectRunnerConfiguration.objects.select_for_update().get(pk=1)
        except ProjectRunnerConfiguration.DoesNotExist:
            return None
        active = ProjectExecution.objects.filter(slot_reserved=True)
        total = min(35, config.teacher_active_checks + config.student_active_checks)
        if active.count() >= total:
            return None
        for role, limit in [('TEACHER', config.teacher_active_checks), ('STUDENT', config.student_active_checks)]:
            if active.filter(queue_role=role).count() >= limit:
                continue
            run = ProjectExecution.objects.filter(status='queued', slot_reserved=False,
                queue_role=role).order_by('created_at', 'pk').first()
            if run is not None:
                run.status, run.slot_reserved = 'running', True
                run.save(update_fields=['status', 'slot_reserved', 'updated_at'])
                return run.pk
    return None


def _execute(run_id, payload):
    close_old_connections()
    try:
        from .project_runner import execute_project_run
        run = ProjectExecution.objects.select_related('sandbox_connection','owner').get(pk=run_id)
        if run.status != 'cancelled':
            if run.activity_kind != 'file':
                from types import SimpleNamespace
                from .assessment_sandbox import assessment_for
                assessment_for(SimpleNamespace(user=run.owner,session={'portal_active_role':'STUDENT'}),run.activity_kind,run.activity_id)
            execute_project_run(run, payload)
    except Exception as exc:
        logging.getLogger(__name__).exception('Queued project execution failed: %s', run_id)
        failed = ProjectExecution.objects.filter(pk=run_id).first()
        if failed and failed.status != 'cancelled':
            results = list(failed.results)
            results.append({'index':failed.next_index,'status':'error','error':str(exc)})
            ProjectExecution.objects.filter(pk=run_id).exclude(status='cancelled').update(status='paused',results=results)
    finally:
        run = ProjectExecution.objects.filter(pk=run_id).first()
        if run and (run.status in {'completed', 'cancelled'} or not run.schema_name or run.schema_removed_at):
            ProjectExecution.objects.filter(pk=run_id).update(slot_reserved=False)
        with _jobs_lock:
            _jobs.discard(run_id)
            resumption = _resumptions.pop(run_id, None)
        if resumption is not None and run and run.status not in {'completed', 'cancelled'}:
            submit(run_id, resumption)
        close_old_connections()
        _wake.set()


def submit(run_id, payload=None):
    with _jobs_lock:
        if run_id in _jobs:
            _resumptions[run_id] = payload or {}
            return False
        _jobs.add(run_id)
    _executor.submit(_execute, run_id, payload or {})
    return True


def enqueue(run, payload):
    start_scheduler()
    with transaction.atomic():
        ProjectRunnerConfiguration.objects.select_for_update().get(pk=1)
        current = ProjectExecution.objects.select_for_update().get(pk=run.pk)
        if current.status in {'cancelled', 'completed', 'running', 'queued'}:
            return
        if current.slot_reserved:
            # Paused checks retain their existing slot and native variables.
            current.status = 'running'
            current.save(update_fields=['status', 'updated_at'])
            transaction.on_commit(lambda: submit(current.pk, payload))
        else:
            current.status = 'queued'
            current.save(update_fields=['status', 'updated_at'])
    _wake.set()


def _loop():
    while True:
        _wake.wait(timeout=1)
        _wake.clear()
        close_old_connections()
        try:
            # Drop cancelled/expired leases before admitting another file.
            from .project_schemas import cleanup_expired_schemas
            cleanup_expired_schemas()
            for _ in range(35):
                run_id = reserve_next()
                if run_id is None:
                    break
                submit(run_id)
        except Exception:
            logging.getLogger(__name__).exception('Project queue admission failed')
        finally:
            close_old_connections()


def start_scheduler():
    global _started
    with _start_lock:
        if not _started:
            _started = True
            threading.Thread(target=_loop, name='project-admission', daemon=True).start()
