"""Audit account lifecycle without retaining deleted users' update logs."""

from contextlib import contextmanager
from contextvars import ContextVar

from django.db.models.signals import post_save, pre_delete, pre_save


_actor = ContextVar("portal_user_change_actor", default=None)

# Record field names only. Never put password hashes, credentials, or old/new
# personal values in the audit log.
TRACKED_FIELDS = (
    "username", "first_name", "middle_name", "last_name", "email",
    "mobile_number", "role", "additional_roles", "is_active",
    "registration_status", "student_number", "teacher_number",
    "class_selection_required", "unrestricted_session_time",
    "can_approve_students", "can_view_all_compiler_activity",
    "can_manage_classes", "can_manage_timetable", "avatar_image",
    "oracle_username", "oracle_password_encrypted", "oracle_connect_string",
    "password",
)


@contextmanager
def user_change_actor(actor):
    token = _actor.set(actor)
    try:
        yield
    finally:
        _actor.reset(token)


def _actor_fields():
    actor = _actor.get()
    if actor is None or not getattr(actor, "pk", None):
        return {"actor": None, "actor_name_snapshot": ""}
    return {"actor": actor, "actor_name_snapshot": actor.username}


def before_user_save(sender, instance, using, update_fields, **kwargs):
    instance._audit_changed_fields = []
    if instance._state.adding or not instance.pk:
        return
    candidates = set(TRACKED_FIELDS)
    if update_fields is not None:
        candidates.intersection_update(update_fields)
    if not candidates:
        return
    previous = sender.objects.using(using).filter(pk=instance.pk).values(*candidates).first()
    if previous is None:
        return
    instance._audit_changed_fields = sorted(
        field for field in candidates
        if str(previous[field]) != str(getattr(instance, field))
    )


def after_user_save(sender, instance, created, using, **kwargs):
    from .models import PortalUserChangeLog

    if created:
        PortalUserChangeLog.objects.using(using).create(
            subject_id_snapshot=instance.pk,
            username_snapshot=instance.username,
            action=PortalUserChangeLog.Action.CREATED,
            **_actor_fields(),
        )
    elif getattr(instance, "_audit_changed_fields", None):
        PortalUserChangeLog.objects.using(using).create(
            subject=instance,
            subject_id_snapshot=instance.pk,
            username_snapshot=instance.username,
            action=PortalUserChangeLog.Action.UPDATED,
            changed_fields=instance._audit_changed_fields,
            **_actor_fields(),
        )
    if hasattr(instance, "_audit_changed_fields"):
        del instance._audit_changed_fields


def before_user_delete(sender, instance, using, **kwargs):
    from .models import PortalUserChangeLog

    actor_fields = _actor_fields()
    if actor_fields["actor"] is not None and actor_fields["actor"].pk == instance.pk:
        actor_fields["actor"] = None
    PortalUserChangeLog.objects.using(using).create(
        subject_id_snapshot=instance.pk,
        username_snapshot=instance.username,
        action=PortalUserChangeLog.Action.DELETED,
        **actor_fields,
    )


def connect_signals():
    from .models import PortalUser, StudentManagement

    for model in (PortalUser, StudentManagement):
        label = model.__name__.lower()
        pre_save.connect(before_user_save, sender=model, dispatch_uid=f"workbench.user_log_pre_save.{label}")
        post_save.connect(after_user_save, sender=model, dispatch_uid=f"workbench.user_log_post_save.{label}")
        pre_delete.connect(before_user_delete, sender=model, dispatch_uid=f"workbench.user_log_pre_delete.{label}")
