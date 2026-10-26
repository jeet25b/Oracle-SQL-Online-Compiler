from django.db import migrations


def backfill_execution_events(apps, schema_editor):
    History = apps.get_model("workbench", "CompilerQueryHistory")
    Event = apps.get_model("workbench", "CompilerExecutionEvent")
    alias = schema_editor.connection.alias
    batch = []
    for record in History.objects.using(alias).all().iterator(chunk_size=500):
        batch.append(Event(
            student_id=record.student_id,
            source=record.source,
            status=record.status,
            duration_ms=record.duration_ms,
            row_count=record.row_count,
            executed_at=record.executed_at,
        ))
        if len(batch) >= 500:
            Event.objects.using(alias).bulk_create(batch, batch_size=500)
            batch.clear()
    if batch:
        Event.objects.using(alias).bulk_create(batch, batch_size=500)


class Migration(migrations.Migration):
    dependencies = [("workbench", "0019_attendancesession_active_seconds_and_more")]
    operations = [migrations.RunPython(backfill_execution_events, migrations.RunPython.noop)]
