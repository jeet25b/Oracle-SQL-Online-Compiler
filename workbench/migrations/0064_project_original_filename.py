from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('workbench', '0063_project_queue')]
    operations = [migrations.AddField('projectexecution', 'original_file_name',
        models.CharField(max_length=255, blank=True, default=''))]
