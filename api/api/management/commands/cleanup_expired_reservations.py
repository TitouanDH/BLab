import time
import logging
from django.core.management.base import BaseCommand
from django.utils import timezone
from api.models import Reservation

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Cleans up expired reservations automatically'

    def add_arguments(self, parser):
        parser.add_argument(
            '--interval',
            type=int,
            default=300,  # 5 minutes
            help='Interval in seconds between cleanup checks (default: 300)'
        )
        parser.add_argument(
            '--once',
            action='store_true',
            help='Run cleanup once and exit (instead of continuous monitoring)'
        )

    def handle(self, *args, **options):
        interval = options['interval']
        run_once = options['once']
        
        if run_once:
            self.cleanup_expired_reservations()
        else:
            self.stdout.write(f'Starting continuous cleanup monitoring (interval: {interval}s)')
            self.stdout.write('Press Ctrl+C to stop')
            
            try:
                while True:
                    self.cleanup_expired_reservations()
                    time.sleep(interval)
            except KeyboardInterrupt:
                self.stdout.write('\nStopping cleanup monitoring...')

    def cleanup_expired_reservations(self):
        """Clean up expired reservations"""
        try:
            now = timezone.now()
            expired_reservations = Reservation.objects.filter(
                end_date__lt=now
            ).exclude(end_date__isnull=True)
            
            cleaned_count = 0
            for reservation in expired_reservations:
                self.stdout.write(
                    f'Found expired reservation: {reservation.user.username} '
                    f'on switch {reservation.switch.mngt_IP} '
                    f'(expired: {reservation.end_date})'
                )
                
                if reservation.delete(reservation.user.username):
                    cleaned_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'✓ Successfully cleaned up expired reservation for {reservation.user.username}'
                        )
                    )
                else:
                    self.stdout.write(
                        self.style.ERROR(
                            f'✗ Failed to cleanup expired reservation for {reservation.user.username}'
                        )
                    )
            
            if cleaned_count > 0:
                logger.info(f"Cleaned up {cleaned_count} expired reservations")
                self.stdout.write(f'Cleaned up {cleaned_count} expired reservations')
            else:
                self.stdout.write('No expired reservations found')
                
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            self.stdout.write(
                self.style.ERROR(f'Error during cleanup: {e}')
            )
