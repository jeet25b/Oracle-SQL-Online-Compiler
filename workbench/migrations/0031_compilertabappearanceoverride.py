from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import workbench.models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("workbench", "0030_portalsetting_compiler_tab_appearance"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompilerTabAppearanceOverride",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("appearance", models.JSONField(default=workbench.models.default_compiler_tab_appearance)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("student", models.OneToOneField(limit_choices_to={"role": "STUDENT"}, on_delete=django.db.models.deletion.CASCADE, related_name="compiler_tab_appearance_override", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="compiler_tab_appearance_changes", to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
