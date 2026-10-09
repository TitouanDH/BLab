"""
Expiry: releases every Reservation whose end date has passed, through api.release.expire.
Like every Release, it asks the Switch worker for a Cleanup.

Each cycle first gives an end date within the limits to the Reservations that production's
older code made without one (api.reservations.cap_unbounded).

Only production runs it (docs/adr/0002): the docker-compose `expiry` service. A switch
whose Links can't be torn down stays reserved and is logged again on every cycle.
"""
import logging
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from api.models import Reservation
from api.release import AlreadyReleased, expire
from api.reservations import cap_unbounded

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Releases expired reservations, with Cleanup'

    def add_arguments(self, parser):
        parser.add_argument('--interval', type=int, default=300,
                            help='Interval in seconds between checks (default: 300)')
        parser.add_argument('--once', action='store_true',
                            help='Check once and exit (instead of checking continuously)')

    def handle(self, *args, **options):
        if options['once']:
            self.expire_reservations()
            return
        self.stdout.write(f"Releasing expired reservations every {options['interval']}s")
        try:
            while True:
                self.expire_reservations()
                time.sleep(options['interval'])
        except KeyboardInterrupt:
            self.stdout.write('Stopped.')

    def expire_reservations(self):
        capped = cap_unbounded(timezone.now())
        if capped:
            self.stdout.write(self.style.WARNING(f"Gave {capped} Reservation(s) an end date within the limits"))
        expired = (Reservation.objects.filter(end_date__lt=timezone.now())
                   .select_related('switch', 'user').order_by('end_date'))
        for reservation in expired:
            what = f"{reservation.user.username} on {reservation.switch.mngt_IP} (ended {reservation.end_date})"
            try:
                result = expire(reservation)
            except AlreadyReleased:
                self.stdout.write(f"Already released: {what}")
                continue
            except Exception:
                # One broken switch must not stop the others from being released
                logger.exception("Expiring %s failed", what)
                self.stdout.write(self.style.ERROR(f"Failed to expire {what}"))
                continue
            if not result.released:
                self.stdout.write(self.style.ERROR(f"Still reserved, links stuck: {what}"))
            elif result.failures:
                self.stdout.write(self.style.WARNING(f"Released {what}, but: {' '.join(result.failures)}"))
            else:
                self.stdout.write(self.style.SUCCESS(f"Released {what}"))
