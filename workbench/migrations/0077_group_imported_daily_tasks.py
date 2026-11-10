"""Group only recognizable, unmapped imports; retain every previous code as an alias."""
import re
from collections import defaultdict
from django.db import migrations


def group_imports(apps, schema_editor):
    Definition = apps.get_model("workbench", "AssessmentDefinition")
    Assignment = apps.get_model("workbench", "Assignment")
    alias = schema_editor.connection.alias
    batches = defaultdict(list)
    for entry in Definition.objects.using(alias).filter(kind="daily", parent_task__isnull=True).order_by("created_at", "pk"):
        match = re.match(r"^(.*?)\s+·\s+(\d+)$", entry.title)
        if match and not entry.payload.get("questions"):
            batches[(entry.created_by_id, match[1])].append((int(match[2]), entry))
    for (_, title), rows in batches.items():
        if len(rows) < 2 or [number for number, _ in rows] != list(range(1, len(rows)+1)):
            continue
        entries = [entry for _, entry in rows]
        if (entries[-1].created_at-entries[0].created_at).total_seconds() > 60:
            continue
        ids = [entry.pk for entry in entries]
        if Assignment.objects.using(alias).filter(assessment_id__in=ids).exists():
            continue
        parent = entries[0]
        payload = {**parent.payload, "title": title, "questions": [entry.payload for entry in entries], "legacy_question_codes": [entry.code for entry in entries]}
        Definition.objects.using(alias).filter(pk=parent.pk).update(title=title, payload=payload)
        Definition.objects.using(alias).filter(pk__in=ids[1:]).update(parent_task_id=parent.pk)


class Migration(migrations.Migration):
    dependencies = [("workbench", "0076_task_level_mapping")]
    operations = [migrations.RunPython(group_imports, migrations.RunPython.noop)]
