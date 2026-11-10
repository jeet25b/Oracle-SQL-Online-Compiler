import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workbench', '0047_compilerqueryhistory_pasted_texts'),
    ]

    operations = [
        migrations.AlterField(
            model_name='teacheractivitypreference',
            name='teacher',
            field=models.OneToOneField(
                limit_choices_to={'role': 'TEACHER'},
                on_delete=django.db.models.deletion.CASCADE,
                related_name='activity_preference',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='teacheractivitypreference',
            name='independent_compiler_sessions_enabled',
            field=models.BooleanField(
                default=False,
                help_text='Allow assigned students to keep an Oracle compiler session separate from their portal login session.',
            ),
        ),
    ]
