from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("workbench", "0033_compilerhistory_input_source_map"),
    ]

    operations = [
        migrations.CreateModel(
            name="TeacherActivityPreference",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("presence_offline_timeout_seconds", models.PositiveSmallIntegerField(default=90)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("teacher", models.OneToOneField(limit_choices_to={"role": "teacher"}, on_delete=django.db.models.deletion.CASCADE, related_name="activity_preference", to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
