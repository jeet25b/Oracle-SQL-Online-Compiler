from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("workbench", "0065_assessment_sandbox")]
    operations = [
        migrations.AddField(
            model_name="assignment",
            name="general_instructions",
            field=models.TextField(blank=True),
        ),
    ]
