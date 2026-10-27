from django.db import migrations, models
import workbench.models


class Migration(migrations.Migration):

    dependencies = [
        ("workbench", "0029_compilerqueryhistory_input_method"),
    ]

    operations = [
        migrations.AddField(
            model_name="portalsetting",
            name="compiler_tab_appearance",
            field=models.JSONField(blank=True, default=workbench.models.default_compiler_tab_appearance),
        ),
    ]
