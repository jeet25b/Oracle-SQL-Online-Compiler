from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("workbench", "0016_portaluser_oracle_credentials"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CompilerQueryHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source", models.CharField(choices=[("PL/SQL", "PL/SQL"), ("SQL*Plus", "SQL*Plus")], default="PL/SQL", max_length=16)),
                ("sql", models.TextField()),
                ("status", models.CharField(choices=[("success", "Success"), ("error", "Failed")], default="success", max_length=8)),
                ("duration_ms", models.PositiveIntegerField(default=0)),
                ("row_count", models.PositiveIntegerField(default=0)),
                ("message", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("executed_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="compiler_query_history", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ("-executed_at", "-id"),
                "indexes": [models.Index(fields=["student", "executed_at"], name="compiler_hist_user_time")],
            },
        ),
    ]
