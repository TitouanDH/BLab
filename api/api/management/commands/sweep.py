"""
Starts a Sweep (api.sweep) now, whatever the time: the Switch worker that holds the lock
carries it out, as it does the nightly one. Only reads and writes the database.
"""
from django.core.management.base import BaseCommand

from api import sweep


class Command(BaseCommand):
    help = 'Starts a Sweep now; the running Switch worker carries it out'

    def handle(self, *args, **options):
        started = sweep.start()
        self.stdout.write(f"{started} under way: the Switch worker carries it out a few Switches at a time, "
                          "and the Lab status page shows its report if something is wrong.")
