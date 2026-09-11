from django.core.management.base import BaseCommand, CommandError

from api.models import Switch
from api.services.health import inspect_switch


class Command(BaseCommand):
    help = 'Run non-destructive ALE health probes and record their results.'

    def add_arguments(self, parser):
        parser.add_argument('--switch', type=int, help='Inspect one switch by database id.')

    def handle(self, *args, **options):
        switches = Switch.objects.all().order_by('id')
        if options['switch']:
            switches = switches.filter(id=options['switch'])
            if not switches.exists():
                raise CommandError(f"Switch {options['switch']} does not exist.")

        for switch in switches:
            check = inspect_switch(switch)
            self.stdout.write(
                self.style.SUCCESS(
                    f'{switch} -> {check.status}: {check.error_message or "inspection recorded"}'
                )
            )
