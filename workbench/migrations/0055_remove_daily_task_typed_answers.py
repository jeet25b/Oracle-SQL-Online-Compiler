from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("workbench", "0054_allow_typed_assessment_answers"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="assignment",
            name="allow_typed_answer",
        ),
    ]
