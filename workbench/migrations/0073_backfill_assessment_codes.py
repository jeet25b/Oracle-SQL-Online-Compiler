from django.db import migrations
from django.utils import timezone


def backfill(apps, schema_editor):
    Definition = apps.get_model("workbench", "AssessmentDefinition")
    Assignment = apps.get_model("workbench", "Assignment")
    SQLTest = apps.get_model("workbench", "SQLTest")
    Question = apps.get_model("workbench", "SQLTestQuestion")
    alias = schema_editor.connection.alias
    daily_fields = ("title", "instructions", "general_instructions", "starter_sql", "tags", "hint", "sample_schema", "max_score", "grading_mode", "expected_sql")
    test_fields = ("title", "instructions", "sample_schema", "duration_minutes", "total_marks", "grading_mode", "allow_typed_answer")
    for kind, Model, fields in (("daily", Assignment, daily_fields), ("test", SQLTest, test_fields)):
        for item in Model.objects.using(alias).filter(assessment__isnull=True).iterator(chunk_size=100):
            payload = {key: getattr(item, key) for key in fields}
            if kind == "test":
                payload["questions"] = list(Question.objects.using(alias).filter(test_id=item.pk).order_by("position", "pk").values("prompt", "expected_sql", "marks"))
            created = getattr(item, "created_at", None) or timezone.now()
            entry = Definition.objects.using(alias).create(kind=kind, title=item.title, created_by_id=item.created_by_id, payload=payload, created_at=created)
            prefix = "DT" if kind == "daily" else "TS"
            Definition.objects.using(alias).filter(pk=entry.pk).update(code=f"{prefix}-{created.year}-{entry.pk:06d}")
            Model.objects.using(alias).filter(pk=item.pk).update(assessment_id=entry.pk)


class Migration(migrations.Migration):
    dependencies = [("workbench", "0072_assessment_library")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
