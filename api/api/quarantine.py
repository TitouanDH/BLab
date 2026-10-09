"""
Quarantine (see CONTEXT.md): a Switch taken out of reservation because an Inspection after
Cleanup found it not clean. It names the last holder, who must clear it, and they can't make
new Reservations meanwhile. Anyone may ask for a Re-check: an Inspection that lifts the
Quarantine if the Switch is clean. Out of service is an admin's, and nothing here changes it.
"""
import logging
from typing import Dict, List, Optional

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from .inspection import InspectionResult, inspect, inspect_and_record
from .models import PendingCleanup, Quarantine, Reservation, Switch, SwitchEvent

logger = logging.getLogger(__name__)


class NotQuarantined(Exception):
    """Re-check asked for a Switch that isn't in Quarantine."""


class CleanupInProgress(Exception):
    """The Switch worker is Cleaning the Switch up: it Inspects it itself once done."""


def holder_of(holder_id: Optional[int]) -> Optional[User]:
    """The user with this id, or None: a holder may have been deleted since (no database constraint)."""
    return User.objects.filter(id=holder_id).first() if holder_id else None


def put_in_quarantine(switch: Switch, holder: Optional[User], reasons: List[str]) -> Quarantine:
    """Quarantines the Switch in holder's name (an admin's to clear when there is none)."""
    with transaction.atomic():
        quarantine = Quarantine.objects.create(switch=switch, holder=holder, reasons=reasons)
        SwitchEvent.objects.create(switch=switch, kind=SwitchEvent.QUARANTINE, ok=False, reasons=reasons,
                                   user=holder)
    logger.warning("Quarantined %s in the name of %s: %s", switch.mngt_IP,
                   holder.username if holder else 'nobody (an admin clears it)', '; '.join(reasons))
    return quarantine


def settle(switch: Switch, result: InspectionResult, holder: Optional[User], why_clean: str) -> str:
    """
    Acts on an Inspection of a Switch that isn't reserved, made after a Cleanup or by the
    Sweep: lifts its Quarantine if it is clean (saying why_clean), and otherwise Quarantines
    it in holder's name, unless it already is, is Out of service, or was reserved meanwhile.
    Call it inside a transaction. Returns what it did.
    """
    # Two at once (a Re-check, say): only one lifts or opens it
    quarantine = Quarantine.objects.select_for_update().filter(switch=switch, lifted_at__isnull=True).first()
    if result.clean:
        if quarantine is None:
            return 'clean'
        lift(quarantine, None, why_clean)
        return 'clean, Quarantine lifted'
    if switch.out_of_service:
        return 'not clean, but Out of service, so left alone'
    if Reservation.objects.filter(switch=switch).exists():
        logger.warning("%s is not clean, but reserved again meanwhile: not Quarantined", switch.mngt_IP)
        return 'not clean, but reserved again meanwhile'
    if quarantine is not None:
        return f"not clean, still in Quarantine: {'; '.join(result.reasons)}"
    put_in_quarantine(switch, holder, result.reasons)
    named = holder.username if holder else 'nobody, for an admin'
    return f"not clean, Quarantined naming {named}: {'; '.join(result.reasons)}"


def quarantine_naming(user: User) -> Optional[Quarantine]:
    """An open Quarantine naming user, if any: while there is one, they can't make new Reservations."""
    return Quarantine.objects.filter(holder=user, lifted_at__isnull=True).select_related('switch').first()


def recheck(switch: Switch, user: User) -> SwitchEvent:
    """
    Inspects a Quarantined Switch for user, and lifts its Quarantine if it is clean.
    Raises NotQuarantined or CleanupInProgress. Returns the Inspection recorded.
    """
    if PendingCleanup.objects.filter(switch=switch).exists():
        raise CleanupInProgress(f"{switch.mngt_IP} is being Cleaned up")
    if switch.open_quarantine() is None:
        raise NotQuarantined(f"{switch.mngt_IP} is not in Quarantine")
    result = inspect(switch)
    with transaction.atomic():
        event = inspect_and_record(switch, result, user=user)
        if result.clean:
            # Two Re-checks at once: only one lifts it
            quarantine = Quarantine.objects.select_for_update().filter(switch=switch, lifted_at__isnull=True).first()
            if quarantine is not None:
                lift(quarantine, user, f"Re-check by {user.username} found it clean")
    return event


def lift(quarantine: Quarantine, user: Optional[User], why: str) -> None:
    """Lifts an open Quarantine for user (None: BLab itself), and says why in the Switch history."""
    quarantine.lifted_at = timezone.now()
    quarantine.save(update_fields=['lifted_at'])
    # By id: main's code may have deleted the Switch (no database constraint)
    SwitchEvent.objects.create(switch_id=quarantine.switch_id, kind=SwitchEvent.QUARANTINE_LIFTED, ok=True,
                               reasons=[why], user=user)
    logger.info("Quarantine %s lifted: %s", quarantine.id, why)


def unavailable() -> Dict[int, dict]:
    """
    Why each Switch that can't be reserved now can't, by Switch id: {'state', 'reason'}, where
    state is 'out_of_service', 'quarantine' or 'cleaning_up' (the first that applies).
    """
    states = {}
    for switch_id in PendingCleanup.objects.values_list('switch_id', flat=True):
        states[switch_id] = {'state': 'cleaning_up',
                             'reason': 'Being Cleaned up: BLab is reloading and Inspecting it.'}
    for quarantine in Quarantine.objects.filter(lifted_at__isnull=True).select_related('holder'):
        named = f" Named: {quarantine.holder.username}." if quarantine.holder else ''
        states[quarantine.switch_id] = {'state': 'quarantine',
                                        'reason': f"In Quarantine: {'; '.join(quarantine.reasons)}.{named}"}
    for switch_id, reason in Switch.objects.exclude(out_of_service_reason__isnull=True) \
            .exclude(out_of_service_reason='').values_list('id', 'out_of_service_reason'):
        states[switch_id] = {'state': 'out_of_service', 'reason': f"Out of service: {reason}"}
    return states
