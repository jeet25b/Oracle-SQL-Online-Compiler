from django.db import migrations, models


def seed_compilers(apps, schema_editor):
    CompilerOption = apps.get_model("workbench", "CompilerOption")
    options = [
        ("MySQL", "MYSQL", "🐬", "MySQL SQL compiler"),
        ("Oracle", "ORACLE", "◉", "Oracle SQL compiler"),
        ("PostgreSQL", "POSTGRESQL", "🐘", "PostgreSQL compiler"),
        ("PL/SQL", "PLSQL", "◉", "Oracle PL/SQL compiler"),
    ]
    for position, (name, engine, icon, description) in enumerate(options):
        CompilerOption.objects.get_or_create(
            name=name,
            defaults={"engine": engine, "icon": icon, "description": description, "position": position},
        )


class Migration(migrations.Migration):
    dependencies = [("workbench", "0014_profileupdatechallenge")]

    operations = [
        migrations.CreateModel(
            name="CompilerOption",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=60)),
                ("engine", models.CharField(choices=[("ORACLE", "Oracle SQL"), ("MYSQL", "MySQL"), ("POSTGRESQL", "PostgreSQL"), ("PLSQL", "PL/SQL"), ("CUSTOM", "Custom compiler")], default="CUSTOM", help_text="Oracle SQL and PL/SQL use the current Oracle backend. Other engines appear as unavailable until their execution backend is implemented and configured.", max_length=16)),
                ("icon", models.CharField(blank=True, default="⌘", max_length=40)),
                ("description", models.CharField(blank=True, max_length=160)),
                ("is_active", models.BooleanField(default=True)),
                ("position", models.PositiveSmallIntegerField(default=0)),
            ],
            options={"ordering": ("position", "id")},
        ),
        migrations.RunPython(seed_compilers, migrations.RunPython.noop),
    ]
