"""
What an admin does to Switches and Quarantines from the Django admin, beyond editing records:
Inspect now, Clean up now and Un-name the holder of a Quarantine. Releasing as an admin is
api.release's (by_admin, force).

Inspect now reads any Switch. On one that is neither reserved nor Out of service, it acts like
the Sweep (api.quarantine.settle): it lifts the Quarantine if the Switch is clean, and otherwise
Quarantines it naming nobody. Clean up now asks the Switch worker for a Cleanup naming nobody.
"""
import logging

from django.contrib.auth.models import User
from django.db import transaction

from .inspection import inspect, inspect_and_record
from .models import PendingCleanup, Quarantine, Reservation, Switch, SwitchEvent
from .onboarding import ONBOARDING
from .quarantine import CleanupInProgress, settle
from .switch_worker import request_cleanup

logger = logging.getLogger(__name__)


class Refused(Exception):
    """The action doesn't apply to this Switch now; the message says why."""


def inspect_now(switch: Switch, admin: User) -> str:
    """Inspects the Switch for admin, records it, and settles it if it is free. Returns what it did."""
    if PendingCleanup.objects.filter(switch=switch).exists():
        raise CleanupInProgress(f"{switch.mngt_IP} is being Cleaned up: its Cleanup Inspects it")
    result = inspect(switch)
    with transaction.atomic():
        # Reserved, Released or put Out of service while BLab was reading it: judged as it is now
        locked = Switch.objects.select_for_update().get(pk=switch.pk)
        inspect_and_record(locked, result, user=admin)
        found = 'clean' if result.clean else f"not clean: {'; '.join(result.reasons)}"
        if Reservation.objects.filter(switch=locked).exists():
            return f"{switch.mngt_IP} is reserved, so only recorded: {found}"
        if PendingCleanup.objects.filter(switch=locked).exists():
            return f"{switch.mngt_IP} is being Cleaned up since, so only recorded: {found}"
        if locked.out_of_service:
            return f"{switch.mngt_IP} is Out of service, so only recorded: {found}"
        why_clean = f"Inspection by {admin.username} (admin) found it clean"
        return f"{switch.mngt_IP} " + settle(locked, result, None, why_clean, by=admin)


def clean_up_now(switch: Switch, admin: User) -> str:
    """Asks the Switch worker to Clean the Switch up, naming nobody if it then isn't clean."""
    with transaction.atomic():
        locked = Switch.objects.select_for_update().get(pk=switch.pk)
        if Reservation.objects.filter(switch=locked).exists():
            raise Refused(f"{switch.mngt_IP} is reserved: Release it, which Cleans it up")
        if PendingCleanup.objects.filter(switch=locked).exists():
            raise CleanupInProgress(f"{switch.mngt_IP} is already being Cleaned up")
        if locked.out_of_service_reason == ONBOARDING:
            raise Refused(f"{switch.mngt_IP} is being onboarded: onboard_switches Cleans it up itself")
        request_cleanup(locked, None)
        SwitchEvent.objects.create(switch=locked, kind=SwitchEvent.CLEANUP, ok=None,
                                   reasons=[f"asked for by {admin.username} (admin)"], user=admin)
    logger.info("Cleanup of %s asked for by %s (admin)", switch.mngt_IP, admin.username)
    return f"{switch.mngt_IP} will be Cleaned up within minutes"


def unname(quarantine: Quarantine, admin: User) -> str:
    """Takes the holder's name off an open Quarantine: it stays open, for an admin to clear."""
    with transaction.atomic():
        quarantine = Quarantine.objects.select_for_update().get(pk=quarantine.pk)
        if quarantine.lifted_at is not None:
            raise Refused(f"{quarantine} is lifted already")
        holder = quarantine.holder
        if holder is None:
            raise Refused(f"{quarantine} names nobody already")
        quarantine.holder = None
        quarantine.save(update_fields=['holder'])
        # ok left empty: it is no new Quarantine
        SwitchEvent.objects.create(switch_id=quarantine.switch_id, kind=SwitchEvent.QUARANTINE, ok=None,
                                   reasons=[f"the Quarantine no longer names {holder.username}: un-named by "
                                            f"{admin.username} (admin)"], user=admin)
    logger.info("Quarantine %s no longer names %s (%s)", quarantine.id, holder.username, admin.username)
    return f"The Quarantine of {quarantine.switch.mngt_IP} no longer names {holder.username}; an admin clears it"
