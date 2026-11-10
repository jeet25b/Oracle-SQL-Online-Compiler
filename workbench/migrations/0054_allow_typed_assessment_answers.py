from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workbench", "0053_daily_task_auto_grading_disputes"),
    ]

    operations = [
        migrations.AddField(
            model_name="assignment",
            name="allow_typed_answer",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="sqltest",
            name="allow_typed_answer",
            field=models.BooleanField(default=False),
        ),
    ]
