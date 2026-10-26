from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workbench", "0003_portaluser_deactivation_source_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="PortalSetting",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ("default_accent", models.CharField(default="violet", max_length=16)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
