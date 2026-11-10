from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workbench', '0066_sqltest_email_results_on_submit'),
    ]

    operations = [
        migrations.AddField(
            model_name='testattempt',
            name='allow_late_access',
            field=models.BooleanField(
                default=False,
                help_text='Allow student to take/resume test after scheduled window has ended.'
            ),
        ),
        migrations.AddField(
            model_name='testattempt',
            name='allow_full_duration',
            field=models.BooleanField(
                default=False,
                help_text='Allow student full test duration even if logging in late.'
            ),
        ),
    ]
