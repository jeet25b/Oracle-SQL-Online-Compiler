from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workbench", "0004_portalsetting"),
    ]

    operations = [
        migrations.AddField(
            model_name="portaluser",
            name="mobile_number",
            field=models.CharField(blank=True, db_index=True, max_length=16),
        ),
    ]
