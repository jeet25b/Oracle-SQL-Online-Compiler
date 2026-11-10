from django.db import migrations, models


def split_capacity(apps, schema_editor):
    Config = apps.get_model('workbench', 'ProjectRunnerConfiguration')
    for config in Config.objects.all():
        total = max(1, min(35, config.max_active_checks))
        teacher = min(5, max(1, total // 6))
        config.teacher_active_checks = teacher
        config.student_active_checks = total - teacher
        config.save(update_fields=['teacher_active_checks', 'student_active_checks'])
    Run = apps.get_model('workbench', 'ProjectExecution')
    from django.db.models import Q
    Run.objects.filter(Q(owner__role__in=['TEACHER', 'ADMIN']) | Q(owner__is_staff=True)).update(queue_role='TEACHER')


class Migration(migrations.Migration):
    dependencies = [('workbench', '0062_project_check_limit')]
    operations = [
        migrations.AddField('projectrunnerconfiguration', 'teacher_active_checks', models.PositiveSmallIntegerField(default=5)),
        migrations.AddField('projectrunnerconfiguration', 'student_active_checks', models.PositiveSmallIntegerField(default=25)),
        migrations.AddField('projectexecution', 'queue_role', models.CharField(max_length=7, default='STUDENT')),
        migrations.AddField('projectexecution', 'slot_reserved', models.BooleanField(default=False, db_index=True)),
        migrations.AlterField('projectexecution', 'status', models.CharField(max_length=16, db_index=True, default='pending', choices=[('pending','Pending'),('queued','Queued'),('running','Running'),('paused','Paused on error'),('completed','Completed'),('cancelled','Cancelled')])),
        migrations.RunPython(split_capacity, migrations.RunPython.noop),
    ]
