"""
Runs the Switch worker (api.switch_worker, docs/adr/0005): carries out the Cleanups that
Releases ask for, waits for each Switch to come back, Inspects it and Quarantines it if it
isn't clean; and the nightly Sweep (api.sweep). Production and pre-prod both run it; only
the one holding the lock works.
"""
import logging
import time

from django.core.management.base import BaseCommand
from django.db import DatabaseError, connection

from api.link_worker import holds_worker_lock
from api.sweep import Sweeper
from api.switch_worker import SWITCH_ACCOUNTS_LOCK, SWITCH_WORKER_LOCK, SwitchWorker

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Carries out the Cleanups that Releases ask for, Inspects and Quarantines, and the nightly Sweep'

    def add_arguments(self, parser):
        parser.add_argument('--poll', type=float, default=5.0,
                            help='Seconds between looks for Cleanups to move on (default: 5)')
        parser.add_argument('--once', action='store_true',
                            help='Move every due Cleanup, and the Sweep under way, one step on, and exit')

    def handle(self, *args, **options):
        worker = SwitchWorker()
        if options['once']:
            self.cycle(worker)
            self.report(worker.sync_accounts())
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
                    # Switch accounts have a lock of their own: a stage whose older code holds the
                    # Cleanup lock without knowing about them doesn't stop them (docs/adr/0004)
                    if holds_worker_lock(SWITCH_ACCOUNTS_LOCK):
                        self.report(worker.sync_accounts())
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
        # The Sweep first: the Cleanups it asks for start in the same cycle
        try:
            self.report(Sweeper(worker.now).step())
        except DatabaseError:
            raise
        except Exception:
            logger.exception("Sweep step failed")
        self.report(worker.work())

    def report(self, outcomes):
        for outcome in outcomes:
            logger.info(outcome)
            self.stdout.write(outcome)
