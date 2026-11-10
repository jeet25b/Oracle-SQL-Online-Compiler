from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("workbench", "0066_assignment_general_instructions"),
    ]

    operations = [
        migrations.CreateModel(
            name="PortalUserChangeLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("subject_id_snapshot", models.BigIntegerField(db_index=True)),
                ("username_snapshot", models.CharField(max_length=150)),
                ("action", models.CharField(choices=[("created", "Created"), ("updated", "Updated"), ("deleted", "Deleted")], db_index=True, max_length=8)),
                ("changed_fields", models.JSONField(blank=True, default=list)),
                ("actor_name_snapshot", models.CharField(blank=True, max_length=150)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="performed_user_changes", to=settings.AUTH_USER_MODEL)),
                ("subject", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="change_logs", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-created_at", "-id")},
        ),
    ]
