from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('workbench', '0060_project_runner_configuration')]
    operations = [
        migrations.AddField('projectexecution', 'schema_name', models.CharField(max_length=30, blank=True, default='')),
        migrations.AddField('projectexecution', 'schema_password_encrypted', models.TextField(blank=True, default='', editable=False)),
        migrations.AddField('projectexecution', 'schema_expires_at', models.DateTimeField(null=True, blank=True)),
        migrations.AddField('projectexecution', 'schema_removed_at', models.DateTimeField(null=True, blank=True)),
        migrations.AddField('projectexecution', 'schema_cleanup_error', models.TextField(blank=True, default='')),
    ]
