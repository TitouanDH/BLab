"""
Runs the Switch worker (api.switch_worker, docs/adr/0005): carries out the Cleanups that
Releases ask for, waits for each Switch to come back, Inspects it and Quarantines it if it
isn't clean; the nightly Sweep (api.sweep); the Restores members start (api.restore); and the
onboarding of contributed Switches (api.contribute_switch). Production and pre-prod both run it; only
the one holding the lock works. It moves up to BLAB_SWITCH_WORKER_THREADS Switches' Cleanups and
Restores on at once (#39); --once moves them one at a time.
"""
import logging
import time

from django.conf import settings
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
        if options['once']:
            worker = SwitchWorker()
            self.cycle(worker)
            self.report(worker.sync_accounts())
            self.report(worker.sync_banners())
            return
        threads = settings.BLAB_SWITCH_WORKER_THREADS
        worker = SwitchWorker(threads=threads)
        self.stdout.write(f"Switch worker: Cleanups every {options['poll']}s, {threads} Switch(es) at once")
        working = None
        try:
            while True:
                try:
                    # The lock lives as long as this database session, which is kept open
                    held = holds_worker_lock(SWITCH_WORKER_LOCK)
                    if held != working:
                        if not held:
                            # It starts nothing more; the steps under way finish, as one step did before #39
                            self.report(worker.steps.done(wait=True))
                        working = held
                        self.stdout.write("Working." if working else "Another Switch worker holds the lock: standing by.")
                    if working:
                        self.cycle(worker)
                    # Switch accounts have a lock of their own: a stage whose older code holds the
                    # Cleanup lock without knowing about them doesn't stop them (docs/adr/0004).
                    # Banners out of step are written again under the same lock.
                    if holds_worker_lock(SWITCH_ACCOUNTS_LOCK):
                        self.report(worker.sync_accounts())
                        self.report(worker.sync_banners())
                except DatabaseError:
                    logger.exception("Switch worker lost the database; connecting again")
                    connection.close()  # the lock went with the session: it is asked for again
                    self.report(worker.steps.done(wait=True))  # its steps start nothing more, but finish
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
        self.report(worker.restores())
        self.report(worker.contributions())

    def report(self, outcomes):
        for outcome in outcomes:
            logger.info(outcome)
            self.stdout.write(outcome)
