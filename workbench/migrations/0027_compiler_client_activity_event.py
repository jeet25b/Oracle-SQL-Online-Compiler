import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("workbench", "0026_portalsetting_sqlplus_buffer_entries"),
    ]

    operations = [
        migrations.AddField(
            model_name="compilerpasteevent",
            name="attendance_session",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="paste_events", to="workbench.attendancesession"),
        ),
        migrations.CreateModel(
            name="CompilerClientActivityEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source", models.CharField(default="Workspace", max_length=12)),
                ("kind", models.CharField(choices=[("page_hidden", "Compiler tab hidden"), ("page_visible", "Compiler tab visible"), ("window_blur", "Browser window lost focus"), ("window_focus", "Browser window focused")], max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("attendance_session", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="client_activity_events", to="workbench.attendancesession")),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="compiler_client_activity_events", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddIndex(
            model_name="compilerclientactivityevent",
            index=models.Index(fields=["student", "created_at"], name="client_event_user_time"),
        ),
    ]
