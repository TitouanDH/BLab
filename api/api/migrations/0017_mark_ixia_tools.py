"""
Marks every IXIA as a Tool (#56), once, as the admin action api.tools.mark_tool would: each
Switch record whose model names an IXIA, unless it has Switch accounts (left for an admin). Its
Reservation ends without a Release, so its Links stay; a pending Cleanup is dropped; open
Quarantines are lifted, since a Tool is never Inspected again.
"""
from django.db import migrations
from django.utils import timezone


def mark_ixias(apps, schema_editor):
    Switch = apps.get_model('api', 'Switch')
    SwitchAccount = apps.get_model('api', 'SwitchAccount')
    Reservation = apps.get_model('api', 'Reservation')
    PendingCleanup = apps.get_model('api', 'PendingCleanup')
    Quarantine = apps.get_model('api', 'Quarantine')
    SwitchEvent = apps.get_model('api', 'SwitchEvent')
    now = timezone.now()
    for switch in Switch.objects.filter(model__icontains='ixia', is_tool=False):
        if SwitchAccount.objects.filter(switch_id=switch.id).exists():
            continue
        Reservation.objects.filter(switch_id=switch.id).delete()
        PendingCleanup.objects.filter(switch_id=switch.id).delete()
        for quarantine in Quarantine.objects.filter(switch_id=switch.id, lifted_at__isnull=True):
            quarantine.lifted_at = now
            quarantine.save(update_fields=['lifted_at'])
            SwitchEvent.objects.create(switch_id=switch.id, kind='quarantine_lifted', ok=True, at=now,
                                       reasons=['marked as a Tool (#56): never Inspected again'])
        switch.is_tool = True
        switch.save(update_fields=['is_tool'])


class Migration(migrations.Migration):
    # Checked by hand (ADR 0002): it only updates is_tool, which main's code doesn't read, and
    # removes the IXIA's Reservation, Cleanup and Quarantine rows, which main's code handles missing
    shared_db_safe = True

    dependencies = [
        ('api', '0016_tools'),
    ]

    operations = [
        migrations.RunPython(mark_ixias, migrations.RunPython.noop),
    ]
