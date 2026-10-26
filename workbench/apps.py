from django.apps import AppConfig
from django.db.models.deletion import ProtectedError


def protect_superuser_deletion(sender, instance, **kwargs):
    if instance.is_superuser:
        raise ProtectedError("Superuser accounts cannot be deleted.", [instance])

class WorkbenchConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workbench"
    verbose_name = "Oracle SQL Workbench"

    def ready(self):
        from django.db.models.signals import pre_delete
        from .models import PortalUser
        pre_delete.connect(protect_superuser_deletion, sender=PortalUser, dispatch_uid="workbench.protect_superuser_deletion")
