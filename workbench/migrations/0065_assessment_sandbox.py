from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('workbench','0064_project_original_filename')]
    operations = [
        migrations.AddField('sqltest','sample_schema',models.TextField(blank=True)),
        migrations.AddField('projectexecution','activity_kind',models.CharField(max_length=10,default='file')),
        migrations.AddField('projectexecution','activity_id',models.PositiveIntegerField(null=True,blank=True)),
        migrations.AddField('projectexecution','setup_statement_count',models.PositiveIntegerField(default=0)),
        migrations.AddField('projectexecution','workspace_state',models.JSONField(default=dict,blank=True)),
        migrations.AlterField('projectexecution','status',models.CharField(max_length=16,db_index=True,default='pending',choices=[('pending','Pending'),('queued','Queued'),('ready','Ready'),('running','Running'),('paused','Paused on error'),('completed','Completed'),('cancelled','Cancelled')])),
    ]
