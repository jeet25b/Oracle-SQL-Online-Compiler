from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workbench", "0051_authorized_teachers_and_timetable"),
    ]

    operations = [
        migrations.AddField(
            model_name="sqltest",
            name="grading_mode",
            field=models.CharField(
                choices=[
                    ("manual", "Teacher review"),
                    ("sql_match", "Match SQL statement"),
                    ("result_match", "Compare query results (SELECT)"),
                ],
                default="manual",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="sqltestquestion",
            name="expected_sql",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="testanswer",
            name="is_correct",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="testanswer",
            name="score",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=7, null=True),
        ),
        migrations.AddField(
            model_name="testanswer",
            name="feedback",
            field=models.TextField(blank=True),
        ),
    ]
