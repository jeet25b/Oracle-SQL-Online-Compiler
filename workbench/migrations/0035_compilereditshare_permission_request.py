from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("workbench", "0034_portalsetting_presence_offline_timeout_seconds"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="compilereditorshare",
            name="edit_permission_requested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="compilereditorshare",
            name="edit_permission_requested_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="compiler_edit_permission_requests", to=settings.AUTH_USER_MODEL),
        ),
    ]
