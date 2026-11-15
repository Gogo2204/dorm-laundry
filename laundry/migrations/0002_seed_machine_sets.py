from django.db import migrations

MACHINE_SET_NAMES = ["Set 1", "Set 2", "Set 3"]


def seed_machine_sets(apps, schema_editor):
    MachineSet = apps.get_model("laundry", "MachineSet")
    for name in MACHINE_SET_NAMES:
        MachineSet.objects.get_or_create(name=name, defaults={"is_active": True})


def unseed_machine_sets(apps, schema_editor):
    MachineSet = apps.get_model("laundry", "MachineSet")
    MachineSet.objects.filter(name__in=MACHINE_SET_NAMES, reservations__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("laundry", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_machine_sets, unseed_machine_sets),
    ]
