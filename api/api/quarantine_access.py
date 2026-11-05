"""
Quarantine access (#118, see CONTEXT.md): the user a Quarantine names gets a read-write Switch
account, SU included, on the Quarantined Switch for as long as they are named, so they can fix
it. No Reservation is made. api.switch_accounts makes and removes the account like any other
(switch_accounts.entitled), the Switch worker retrying it; an Inspection doesn't count it as an
account left (api.inspection). The access ends when the holder is un-named, the Quarantine is
lifted, or a Cleanup of the Switch starts. Once given (Quarantine.access_given_at), the
Quarantine is lifted only by a Cleanup's own Inspection (api.quarantine.settle), so nothing done
with it stays. An Out of service Switch is left alone by the Switch worker, as for every account.

Behind the stage setting BLAB_QUARANTINE_ACCESS (off by default): production's older code would
remove the account as one left on a Switch nobody holds, and its Inspections would fail on it.
"""
from datetime import datetime
from typing import Optional

from django.conf import settings

from .models import NO_MANAGEMENT_IP, PendingCleanup, Quarantine, Reservation, Switch


def open_for(switch: Switch) -> Optional[Quarantine]:
    """
    The Switch's open Quarantine whose named holder has access to it now, or None: one naming
    somebody, on a Switch with a management IP that is neither reserved nor being Cleaned up.
    """
    if not settings.BLAB_QUARANTINE_ACCESS or switch.mngt_IP == NO_MANAGEMENT_IP:
        return None
    quarantine = (Quarantine.objects.filter(switch=switch, lifted_at__isnull=True, holder__isnull=False)
                  .order_by('-opened_at', '-id').first())
    if quarantine is None or Reservation.objects.filter(switch=switch).exists() \
            or PendingCleanup.objects.filter(switch=switch).exists():
        return None
    return quarantine


def holder_id(switch: Switch) -> Optional[int]:
    """The id of the user with Quarantine access to the Switch now, or None."""
    quarantine = open_for(switch)
    return quarantine.holder_id if quarantine else None


def note_given(switch: Switch, now: datetime) -> None:
    """Records that the Switch's Quarantine gives access, before BLab makes the account: its lift then Cleans it up."""
    quarantine = open_for(switch)
    if quarantine is not None and quarantine.access_given_at is None:
        Quarantine.objects.filter(pk=quarantine.pk, access_given_at__isnull=True).update(access_given_at=now)
