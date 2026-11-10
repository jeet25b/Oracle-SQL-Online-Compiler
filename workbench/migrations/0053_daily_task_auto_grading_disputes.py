from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workbench", "0052_automated_sql_test_grading"),
    ]

    operations = [
        migrations.AddField(
            model_name="assignment",
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
            model_name="assignment",
            name="expected_sql",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="submission",
            name="dispute_message",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="submission",
            name="disputed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="submission",
            name="dispute_response",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="submission",
            name="dispute_resolved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="submission",
            name="status",
            field=models.CharField(
                choices=[
                    ("submitted", "Submitted"),
                    ("auto_graded", "Automatically evaluated"),
                    ("disputed", "Disputed"),
                    ("reviewed", "Reviewed"),
                    ("late", "Late"),
                ],
                default="submitted",
                max_length=16,
            ),
        ),
    ]
