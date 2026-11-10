from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('workbench', '0061_project_file_schemas')]
    operations = [migrations.AddField('projectrunnerconfiguration', 'max_active_checks',
        models.PositiveSmallIntegerField(default=30))]
