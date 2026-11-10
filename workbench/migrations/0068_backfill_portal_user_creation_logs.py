from django.db import migrations, models


def backfill_current_accounts(apps, schema_editor):
    User = apps.get_model("workbench", "PortalUser")
    Log = apps.get_model("workbench", "PortalUserChangeLog")
    alias = schema_editor.connection.alias
    existing = set(Log.objects.using(alias).filter(action="created").values_list("subject_id_snapshot", flat=True))
    batch = []
    for user in User.objects.using(alias).only("pk", "username", "date_joined").iterator(chunk_size=500):
        if user.pk in existing:
            continue
        batch.append(Log(
            subject_id_snapshot=user.pk,
            username_snapshot=user.username,
            action="created",
            origin="backfilled",
            created_at=user.date_joined,
        ))
        if len(batch) == 500:
            Log.objects.using(alias).bulk_create(batch)
            batch.clear()
    if batch:
        Log.objects.using(alias).bulk_create(batch)


class Migration(migrations.Migration):
    dependencies = [("workbench", "0067_portal_user_change_log")]

    operations = [
        migrations.AddField(
            model_name="portaluserchangelog",
            name="origin",
            field=models.CharField(
                choices=[("live", "Recorded at event time"), ("backfilled", "Reconstructed for existing account")],
                default="live", max_length=12,
            ),
        ),
        migrations.RunPython(backfill_current_accounts, migrations.RunPython.noop),
    ]
