from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workbench', '0046_portalsetting_restrict_student_session_time_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='compilerqueryhistory',
            name='pasted_texts',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
