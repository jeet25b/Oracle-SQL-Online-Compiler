from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("workbench", "0059_project_execution")]
    operations = [migrations.CreateModel(name="ProjectRunnerConfiguration", fields=[
        ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
        ("updated_at", models.DateTimeField(auto_now=True)),
        ("sandbox_connection", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="project_runner_configuration", to="workbench.oracleconnectionprofile")),
    ])]
