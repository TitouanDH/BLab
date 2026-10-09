from datetime import timedelta

from django.db import migrations
from django.db.models import Q
from django.utils import timezone


def cap_existing_reservations(apps, schema_editor):
    """
    Reservations with no end date, or ending more than 14 days from now, end 14 days from
    now (#20). The rule of api.reservations.cap_unbounded, frozen here as it was.
    """
    Reservation = apps.get_model('api', 'Reservation')
    limit = timezone.now() + timedelta(days=14)
    (Reservation.objects
     .filter(admin_exception=False, renewals=0)
     .filter(Q(end_date__isnull=True) | Q(end_date__gt=limit + timedelta(minutes=5)))
     .update(end_date=limit))


class Migration(migrations.Migration):
    # Only sets missing end dates or moves end dates earlier: main's code reads end_date as
    # it always has, and its expiry releases these Reservations when the date comes (docs/adr/0002)
    shared_db_safe = True

    dependencies = [
        ('api', '0006_reservation_limits'),
    ]

    operations = [
        migrations.RunPython(cap_existing_reservations, migrations.RunPython.noop),
    ]
