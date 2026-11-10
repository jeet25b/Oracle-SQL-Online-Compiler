from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("workbench", "0058_daily_task_workflow")]
    operations = [migrations.CreateModel(name="ProjectExecution", fields=[
        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
        ("name", models.CharField(max_length=180)),
        ("source_text", models.TextField()), ("statements", models.JSONField(default=list)),
        ("results", models.JSONField(default=list)), ("next_index", models.PositiveIntegerField(default=0)),
        ("status", models.CharField(choices=[("pending", "Pending"), ("running", "Running"), ("paused", "Paused on error"), ("completed", "Completed"), ("cancelled", "Cancelled")], db_index=True, default="pending", max_length=16)),
        ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
        ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="project_executions", to="workbench.portaluser")),
        ("sandbox_connection", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="project_executions", to="workbench.oracleconnectionprofile")),
        ("student", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="student_project_executions", to="workbench.portaluser")),
    ])]
