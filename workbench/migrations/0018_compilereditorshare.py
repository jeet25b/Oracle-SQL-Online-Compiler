from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("workbench", "0017_compilerqueryhistory"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CompilerEditorShare",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("access_mode", models.CharField(choices=[("private", "Private"), ("statement", "One statement"), ("session", "This session"), ("always", "Always allow")], default="private", max_length=12)),
                ("editor_text", models.TextField(blank=True)),
                ("sqlplus_text", models.TextField(blank=True)),
                ("session_token", models.CharField(blank=True, max_length=64)),
                ("statement_snapshot_ready", models.BooleanField(default=False)),
                ("revision", models.PositiveIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("student", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="compiler_editor_share", to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
