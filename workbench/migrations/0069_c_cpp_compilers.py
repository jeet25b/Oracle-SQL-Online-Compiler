from django.db import migrations, models


def add_compilers(apps, schema_editor):
    Compiler = apps.get_model('workbench', 'CompilerOption')
    for engine, name, description, position in [
        ('C', 'C', 'C17 compiler with standard input and output', 10),
        ('CPP', 'C++', 'C++20 compiler with standard input and output', 11),
    ]:
        Compiler.objects.using(schema_editor.connection.alias).get_or_create(engine=engine, defaults={
            'name': name, 'description': description, 'icon': '</>', 'position': position, 'is_active': True})


class Migration(migrations.Migration):
    dependencies = [('workbench', '0068_backfill_portal_user_creation_logs')]
    operations = [
        migrations.AlterField(model_name='compileroption', name='engine', field=models.CharField(
            choices=[('ORACLE', 'Oracle SQL'), ('MYSQL', 'MySQL'), ('POSTGRESQL', 'PostgreSQL'),
                     ('PLSQL', 'PL/SQL'), ('C', 'C compiler'), ('CPP', 'C++ compiler'), ('CUSTOM', 'Custom compiler')],
            default='CUSTOM', max_length=16,
            help_text='Oracle SQL and PL/SQL use the current Oracle backend. Other engines appear as unavailable until their execution backend is implemented and configured.')),
        migrations.RunPython(add_compilers, migrations.RunPython.noop),
    ]
