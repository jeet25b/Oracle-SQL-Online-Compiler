"""Per-file leases. Only the trusted provisioning account can invoke the broker."""
from datetime import timedelta
import re
import secrets

from django.db import transaction
from django.utils import timezone

from .models import ProjectExecution, _portal_secret_cipher

BROKER = 'PCHK_BROKER.SCHEMAS'
SCHEMA_NAME = re.compile(r'PCHK_[A-F0-9]{24}\Z')


def broker_connection(run):
    from .views import connect_options, open_oracle_connection
    profile = run.sandbox_connection
    return open_oracle_connection(connect_options({'user': profile.username,
        'password': profile.get_oracle_password(), 'connectString': profile.connect_string}))


def file_credentials(run):
    """Provision once per run, never per user, with an encrypted random password."""
    with transaction.atomic():
        lease = ProjectExecution.objects.select_for_update().get(pk=run.pk)
        if lease.schema_name:
            if lease.schema_removed_at or lease.schema_expires_at <= timezone.now():
                raise ValueError('This file schema has expired. Start a new file check.')
        else:
            lease.schema_name = 'PCHK_' + secrets.token_hex(12).upper()
            password = 'P' + secrets.token_hex(24)
            lease.schema_password_encrypted = _portal_secret_cipher().encrypt(password.encode()).decode()
            lease.schema_expires_at = timezone.now() + timedelta(minutes=30)
            # Commit the identity before Oracle DDL, which cannot be rolled back.
            lease.save(update_fields=['schema_name', 'schema_password_encrypted', 'schema_expires_at'])
    run.refresh_from_db()
    if not SCHEMA_NAME.fullmatch(run.schema_name):
        raise ValueError('Invalid project schema lease.')
    password = _portal_secret_cipher().decrypt(run.schema_password_encrypted.encode()).decode()
    connection = broker_connection(run)
    try:
        with connection.cursor() as cursor:
            try:
                cursor.callproc(BROKER + '.ENSURE_SCHEMA', [run.pk, run.schema_name, password])
            except Exception as exc:
                code = getattr(exc.args[0], 'code', None) if exc.args else None
                if code in {6550, 4043, 6508}:
                    raise ValueError('Per-file schema provisioning is not installed. The portal administrator must install the project schema broker.') from exc
                raise
    finally:
        connection.close()
    return run.schema_name, password, run.sandbox_connection.connect_string


def renew_schema(run):
    expires = timezone.now() + timedelta(minutes=30)
    ProjectExecution.objects.filter(pk=run.pk, schema_removed_at__isnull=True).update(schema_expires_at=expires)
    run.schema_expires_at = expires


def remove_schema(run):
    if not run.schema_name or run.schema_removed_at:
        return
    if not SCHEMA_NAME.fullmatch(run.schema_name):
        raise ValueError('Refusing to remove an unrecognized project schema.')
    try:
        connection = broker_connection(run)
        try:
            with connection.cursor() as cursor:
                cursor.callproc(BROKER + '.REMOVE_SCHEMA', [run.pk, run.schema_name])
        finally:
            connection.close()
    except Exception as exc:
        ProjectExecution.objects.filter(pk=run.pk).update(schema_cleanup_error=str(exc)[:2000])
        return False
    ProjectExecution.objects.filter(pk=run.pk).update(schema_removed_at=timezone.now(),
        schema_password_encrypted='', schema_cleanup_error='', slot_reserved=False)
    run.schema_removed_at = timezone.now()
    run.schema_password_encrypted = ''
    return True


def cleanup_expired_schemas():
    from .project_sqlplus import close_project_session
    leases = ProjectExecution.objects.select_related('sandbox_connection').filter(
        schema_removed_at__isnull=True).exclude(schema_name='')
    from django.db.models import Q
    leases = leases.filter(Q(schema_expires_at__lte=timezone.now()) | Q(status__in=['completed', 'cancelled']))
    for run in leases[:100]:
        close_project_session(run.pk)
        remove_schema(run)
        if run.schema_expires_at and run.schema_expires_at <= timezone.now() and run.status not in {'completed', 'cancelled'}:
            ProjectExecution.objects.filter(pk=run.pk).update(status='cancelled', slot_reserved=False)


def finish_previous_checks(owner):
    """A new file supersedes only this user's unfinished checks."""
    from .project_sqlplus import close_project_session
    with transaction.atomic():
        from .models import ProjectRunnerConfiguration
        try:
            ProjectRunnerConfiguration.objects.select_for_update().get(pk=1)
        except ProjectRunnerConfiguration.DoesNotExist:
            pass
        # Serialize starts for the same user, including across server workers.
        from .models import PortalUser
        PortalUser.objects.select_for_update().get(pk=owner.pk)
        previous = list(ProjectExecution.objects.select_related('sandbox_connection').filter(
            owner=owner, status__in=['pending', 'queued', 'running', 'paused', 'ready']).exclude(activity_kind='grading'))
        for run in previous:
            run.status = 'cancelled'
            run.slot_reserved = False
            run.save(update_fields=['status', 'slot_reserved', 'updated_at'])
            if run.activity_kind == 'test':
                from .models import TestAttempt
                TestAttempt.objects.filter(test_id=run.activity_id,student=owner,submitted_at__isnull=True).update(status='suspended')
    for run in previous:
        close_project_session(run.pk)
        remove_schema(run)
