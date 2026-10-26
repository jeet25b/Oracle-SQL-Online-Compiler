from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workbench", "0028_attendance_start_times"),
    ]

    operations = [
        migrations.AddField(
            model_name="compilerqueryhistory",
            name="input_method",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
    ]
