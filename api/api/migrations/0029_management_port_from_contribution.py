# #115: production's older code onboarded Contributions without an EMP (#81) and created their Switches
# without a Management port (the column's db_default ''), so Inspections took the port for an Unwanted
# cable and Cleanups wrote a Golden VC setup without the Switch's management. Each such Switch gets the
# Management port of its latest Contribution (same address and model). One still being onboarded goes back
# to Recorded: the onboarding installs its Golden init and VC setup again, now with its management, and
# reloads it once. Data only: safe on the shared database (ADR 0002).

from django.db import migrations


def management_port_from_contribution(apps, schema_editor):
    Switch = apps.get_model('api', 'Switch')
    SwitchContribution = apps.get_model('api', 'SwitchContribution')
    Onboarding = apps.get_model('api', 'Onboarding')
    for switch in Switch.objects.filter(management_port=''):
        contribution = (SwitchContribution.objects.filter(mngt_IP=switch.mngt_IP, model=switch.model)
                        .order_by('-created_at', '-id').first())
        if contribution and not contribution.has_emp and contribution.management_port:
            switch.management_port = contribution.management_port
            switch.save(update_fields=['management_port'])
            Onboarding.objects.filter(mngt_IP=switch.mngt_IP, step__in=('back', 'cabled')).update(
                step='recorded', detail='its Management port was missing (#115): reloaded again with it')


class Migration(migrations.Migration):
    # Fills Switch.management_port (0027), which main's code neither reads nor writes, and moves Onboarding
    # rows of those Switches back a step, which main's onboarding carries on from as it would anyway
    shared_db_safe = True

    dependencies = [
        ('api', '0028_guest_logins'),
    ]

    operations = [
        migrations.RunPython(management_port_from_contribution, migrations.RunPython.noop),
    ]
