from django.db import migrations


OLD_DEFAULT = {
    "sqlplus": {"label": "SQLCL", "visible": True, "order": 1},
    "compiler": {"label": "SQL WorkSheet", "visible": True, "order": 2},
}
NEW_DEFAULT = {
    "sqlplus": {"label": "SQLCL", "visible": True, "order": 1},
    "compiler": {"label": "SQL WorkSheet", "visible": False, "order": 2},
}


def set_sqlcl_only_as_default(apps, schema_editor):
    PortalSetting = apps.get_model("workbench", "PortalSetting")
    for setting in PortalSetting.objects.all().iterator():
        if setting.compiler_tab_appearance == OLD_DEFAULT:
            setting.compiler_tab_appearance = NEW_DEFAULT
            setting.save(update_fields=["compiler_tab_appearance"])


class Migration(migrations.Migration):

    dependencies = [
        ("workbench", "0031_compilertabappearanceoverride"),
    ]

    operations = [
        migrations.RunPython(set_sqlcl_only_as_default, migrations.RunPython.noop),
    ]
