from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("workbench", "0027_compiler_client_activity_event")]

    operations = [
        migrations.AddField(
            model_name="classsection",
            name="attendance_start_time",
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="compilerlab",
            name="attendance_start_time",
            field=models.TimeField(blank=True, null=True),
        ),
    ]
