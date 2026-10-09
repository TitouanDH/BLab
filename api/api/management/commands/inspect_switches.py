"""
Inspects every Switch (or those given by --ips / --file), records each result in its history,
and prints a report. It only reads the Switches (see api.inspection).
"""
from django.core.management.base import BaseCommand

from api.inspection import inspect_and_record
from api.management.switch_ips import add_ip_arguments, ips_from
from api.models import Reservation, Switch


class Command(BaseCommand):
    help = 'Inspects the lab Switches (read-only), records the results and prints a report'

    def add_arguments(self, parser):
        add_ip_arguments(parser)

    def handle(self, *args, **options):
        switches = Switch.objects.order_by('mngt_IP')
        if options['ips'] or options['file']:
            switches = switches.filter(mngt_IP__in=ips_from(options))
        holders = dict(Reservation.objects.values_list('switch_id', 'user__username'))

        clean = dirty = 0
        for switch in switches:
            event = inspect_and_record(switch)
            holder = holders.get(switch.id)
            name = f"{switch.mngt_IP} {switch.model}" + (f" (reserved by {holder})" if holder else " (not reserved)")
            if event.ok:
                clean += 1
                self.stdout.write(self.style.SUCCESS(f"CLEAN      {name}"))
            else:
                dirty += 1
                self.stdout.write(self.style.ERROR(f"NOT CLEAN  {name}"))
                for reason in event.reasons:
                    self.stdout.write(f"    - {reason}")
            for warning in event.warnings:
                self.stdout.write(self.style.WARNING(f"    ! {warning}"))
        self.stdout.write(f"\n{clean} clean, {dirty} not clean.")
