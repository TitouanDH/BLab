"""
Runs the Link worker (api.link_worker, docs/adr/0003): tears down requested Links every few
seconds, and every few minutes reconciles, removing the Orphans BLab made and recording the
real UNI states. Production and pre-prod both run it; only the one holding the lock works.
"""
import logging
import time

from django.core.management.base import BaseCommand
from django.db import DatabaseError, connection

from api.link_worker import LinkWorker, holds_worker_lock

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Tears down requested Links, removes the Orphans BLab made and records real UNI states'

    def add_arguments(self, parser):
        parser.add_argument('--poll', type=float, default=2.0,
                            help='Seconds between looks for requested teardowns (default: 2)')
        parser.add_argument('--reconcile-every', type=float, default=300.0,
                            help='Seconds between reconciles (default: 300)')
        parser.add_argument('--once', action='store_true',
                            help='Tear down what is requested, reconcile once, and exit')

    def handle(self, *args, **options):
        if options['once']:
            self.cycle(LinkWorker(), reconcile=True)
            return
        self.stdout.write(f"Link worker: teardowns every {options['poll']}s, "
                          f"reconcile every {options['reconcile_every']}s")
        working = None
        try:
            while True:
                try:
                    # The lock lives as long as this database session, which is kept open
                    held = holds_worker_lock()
                    if held != working:
                        working = held
                        self.stdout.write("Working." if working else "Another Link worker holds the lock: standing by.")
                        # Taking over: what another worker did meanwhile isn't known here
                        worker, last_reconcile = LinkWorker(), None
                    if working:
                        due = last_reconcile is None or time.monotonic() - last_reconcile >= options['reconcile_every']
                        self.cycle(worker, reconcile=due)
                        if due:
                            last_reconcile = time.monotonic()
                except DatabaseError:
                    logger.exception("Link worker lost the database; connecting again")
                    connection.close()  # the lock went with the session: it is asked for again
                    working = None
                except Exception:
                    # The worker must outlive a broken backbone for a while
                    logger.exception("Link worker cycle failed")
                time.sleep(options['poll'])
        except KeyboardInterrupt:
            self.stdout.write('Stopped.')

    def cycle(self, worker: LinkWorker, reconcile: bool):
        outcomes = worker.tear_down_requested()
        if reconcile:
            outcomes += worker.reconcile()
        for outcome in outcomes:
            logger.info(outcome)
            self.stdout.write(outcome)
