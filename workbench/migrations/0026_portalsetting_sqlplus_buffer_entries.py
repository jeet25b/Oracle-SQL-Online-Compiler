from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('workbench', '0025_compiler_paste_event')]
    operations = [migrations.AddField(model_name='portalsetting', name='sqlplus_buffer_entries',
                                     field=models.PositiveIntegerField(default=5000))]
