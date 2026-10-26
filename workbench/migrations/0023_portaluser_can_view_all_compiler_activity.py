from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workbench", "0022_compilerexecutionevent_edits_after_paste_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="portaluser",
            name="can_view_all_compiler_activity",
            field=models.BooleanField(
                default=False,
                help_text="Allow this teacher to monitor students, classes, and labs outside their teaching assignments.",
                verbose_name="Can view all student compiler activity",
            ),
        ),
    ]
