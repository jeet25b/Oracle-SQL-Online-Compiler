from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("workbench", "0069_c_cpp_compilers")]
    operations = [migrations.AddField(
        model_name="assignment", name="lab",
        field=models.ForeignKey(blank=True, null=True,
            on_delete=django.db.models.deletion.PROTECT,
            related_name="daily_tasks", to="workbench.compilerlab"),
    )]
