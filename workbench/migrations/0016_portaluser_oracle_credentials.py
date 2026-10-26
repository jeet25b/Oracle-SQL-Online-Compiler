from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("workbench", "0015_compileroption"),
    ]

    operations = [
        migrations.AddField(
            model_name="portaluser",
            name="oracle_username",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name="portaluser",
            name="oracle_password_encrypted",
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="portaluser",
            name="oracle_connect_string",
            field=models.CharField(blank=True, max_length=512),
        ),
    ]
