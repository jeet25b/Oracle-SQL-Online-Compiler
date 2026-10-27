from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workbench", "0032_default_student_compiler_tab_sqlcl_only"),
    ]

    operations = [
        migrations.AddField(
            model_name="compilerqueryhistory",
            name="input_source_map",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
