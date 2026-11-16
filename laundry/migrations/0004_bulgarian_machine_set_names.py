from django.db import migrations

RENAMES = {"Set 1": "Комплект 1", "Set 2": "Комплект 2", "Set 3": "Комплект 3"}


def rename_to_bulgarian(apps, schema_editor):
    MachineSet = apps.get_model("laundry", "MachineSet")
    for old, new in RENAMES.items():
        MachineSet.objects.filter(name=old).update(name=new)


def rename_to_english(apps, schema_editor):
    MachineSet = apps.get_model("laundry", "MachineSet")
    for old, new in RENAMES.items():
        MachineSet.objects.filter(name=new).update(name=old)


class Migration(migrations.Migration):
    dependencies = [
        ("laundry", "0003_alter_machineset_options_alter_reservation_options_and_more"),
    ]

    operations = [
        migrations.RunPython(rename_to_bulgarian, rename_to_english),
    ]
