"""
Runs the Switch worker (api.switch_worker, docs/adr/0005): carries out the Cleanups that
Releases ask for, waits for each Switch to come back, Inspects it and Quarantines it if it
isn't clean. Production and pre-prod both run it; only the one holding the lock works.
"""
import logging
import time

from django.core.management.base import BaseCommand
from django.db import DatabaseError, connection

from api.link_worker import holds_worker_lock
from api.switch_worker import SWITCH_WORKER_LOCK, SwitchWorker

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Carries out the Cleanups that Releases ask for, Inspects and Quarantines'

    def add_arguments(self, parser):
        parser.add_argument('--poll', type=float, default=5.0,
                            help='Seconds between looks for Cleanups to move on (default: 5)')
        parser.add_argument('--once', action='store_true',
                            help='Move every due Cleanup one step on, and exit')

    def handle(self, *args, **options):
        worker = SwitchWorker()
        if options['once']:
            self.cycle(worker)
            return
        self.stdout.write(f"Switch worker: Cleanups every {options['poll']}s")
        working = None
        try:
            while True:
                try:
                    # The lock lives as long as this database session, which is kept open
                    held = holds_worker_lock(SWITCH_WORKER_LOCK)
                    if held != working:
                        working = held
                        self.stdout.write("Working." if working else "Another Switch worker holds the lock: standing by.")
                    if working:
                        self.cycle(worker)
                except DatabaseError:
                    logger.exception("Switch worker lost the database; connecting again")
                    connection.close()  # the lock went with the session: it is asked for again
                    working = None
                except Exception:
                    logger.exception("Switch worker cycle failed")
                time.sleep(options['poll'])
        except KeyboardInterrupt:
            self.stdout.write('Stopped.')

    def cycle(self, worker: SwitchWorker):
        for outcome in worker.work():
            logger.info(outcome)
            self.stdout.write(outcome)
