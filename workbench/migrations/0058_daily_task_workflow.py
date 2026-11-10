from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("workbench", "0057_saved_oracle_connections")]
    operations = [
        migrations.AddField(
            model_name='assignment',
            name='hint',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='assignment',
            name='lab_end_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='assignment',
            name='lab_start_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='assignment',
            name='late_policy',
            field=models.CharField(choices=[('allow', 'Allow late submissions'), ('lock', 'Lock at deadline')], default='allow', max_length=8),
        ),
        migrations.AddField(
            model_name='assignment',
            name='sample_schema',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='assignment',
            name='start_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='assignment',
            name='starter_sql',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='assignment',
            name='tags',
            field=models.CharField(blank=True, max_length=300),
        ),
        migrations.AddField(
            model_name='submission',
            name='is_late',
            field=models.BooleanField(default=False),
        ),
    ]
