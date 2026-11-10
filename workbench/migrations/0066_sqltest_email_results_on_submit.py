from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workbench', '0065_assessment_sandbox'),
    ]

    operations = [
        migrations.AddField(
            model_name='sqltest',
            name='email_results_on_submit',
            field=models.BooleanField(
                default=False,
                help_text='Automatically email result scorecard to student on submission.'
            ),
        ),
    ]
