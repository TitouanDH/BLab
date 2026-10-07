"""
Reconcile: lists every Drift between the Links the database records and the backbones,
through api.reconcile. It only reads, unless --repair is given: then it builds Ghost
Links again and records the real admin state of the UNIs. Orphans are only reported.
"""
from django.core.management.base import BaseCommand

from api.reconcile import reconcile, repair


class Command(BaseCommand):
    help = 'Lists drift between the recorded Links and the backbones (read-only unless --repair)'

    def add_arguments(self, parser):
        parser.add_argument('--repair', action='store_true',
                            help='Build Ghost Links again and record the real UNI admin states')

    def handle(self, *args, **options):
        drifts = reconcile()
        if not drifts:
            self.stdout.write(self.style.SUCCESS('No drift: the backbones hold exactly the recorded Links.'))
            return
        for drift in drifts:
            self.stdout.write(str(drift))
        self.stdout.write(self.style.WARNING(f"{len(drifts)} drift(s)."))
        if options['repair']:
            for outcome in repair(drifts):
                self.stdout.write(outcome)
