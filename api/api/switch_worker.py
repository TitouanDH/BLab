"""
The Switch worker (docs/adr/0005): carries out the Cleanups that Releases ask for, which take
too long for a request. For each PendingCleanup it updates the banner, restores init and
reloads the Switch, waits for it to come back, Inspects it, and Quarantines it in its last
holder's name if it isn't clean.

Each step is recorded in PendingCleanup before the next, so a redeploy only delays a Cleanup.
Production and pre-prod each run one, like the Link worker, with a lock of its own: only one
works at a time.
"""
import logging
from datetime import datetime, timedelta
from typing import Callable, List, Optional

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from .inspection import InspectionResult, inspect, inspect_and_record
from .lab_switch import LabSwitchError, lab_switch
from .models import NO_MANAGEMENT_IP, PendingCleanup, Reservation, Switch, SwitchEvent
from .quarantine import holder_of, put_in_quarantine

logger = logging.getLogger(__name__)

SWITCH_WORKER_LOCK = 0x424C4153  # pg advisory lock key ("BLAS"): one Switch worker at a time
RELOAD_GRACE = timedelta(minutes=2)     # before the first look: the Switch is still going down
RELOAD_TIMEOUT = timedelta(minutes=20)  # then it is Inspected as it is, reachable or not
LOOK_AGAIN_AFTER = timedelta(seconds=30)  # between looks at a Switch that isn't back yet


def request_cleanup(switch: Switch, holder: Optional[User]) -> PendingCleanup:
    """Asks the Switch worker to Clean the Switch up, naming holder if it then isn't clean."""
    pending, _ = PendingCleanup.objects.update_or_create(
        switch=switch, defaults={'holder': holder, 'requested_at': timezone.now(), 'started_at': None,
                                 'next_inspection_at': None, 'give_up_at': None})
    return pending


class SwitchWorker:
    def __init__(self, now: Callable[[], datetime] = timezone.now, grace: timedelta = RELOAD_GRACE,
                 timeout: timedelta = RELOAD_TIMEOUT, look_again_after: timedelta = LOOK_AGAIN_AFTER):
        self.now = now
        self.grace = grace
        self.timeout = timeout
        self.look_again_after = look_again_after

    def work(self) -> List[str]:
        """Moves every PendingCleanup that is due one step on."""
        outcomes = []
        for pending in PendingCleanup.objects.select_related('switch').order_by('requested_at'):
            try:
                outcome = self.advance(pending)
            except Exception as e:
                # One broken Switch must not stop the others; it is tried again next cycle
                logger.exception("Cleanup of %s failed", pending.switch.mngt_IP)
                outcome = f"Could not go on with the Cleanup of {pending.switch.mngt_IP}: {e}"
            if outcome:
                outcomes.append(outcome)
        return outcomes

    def advance(self, pending: PendingCleanup) -> Optional[str]:
        """Starts the Cleanup, or Inspects the Switch once it is due: what happened, or None if nothing did."""
        if pending.started_at is None:
            return self.start(pending)
        if self.now() < pending.next_inspection_at:
            return None
        result = inspect(pending.switch)
        if not result.reached and self.now() < pending.give_up_at:
            pending.next_inspection_at = self.now() + self.look_again_after
            pending.save(update_fields=['next_inspection_at'])
            return None
        return self.finish(pending, result)

    def start(self, pending: PendingCleanup) -> str:
        """Restores init and reloads. If that fails, the Switch is Inspected as it is, at once."""
        switch, holder = pending.switch, holder_of(pending.holder_id)
        skipped = None
        if switch.mngt_IP == NO_MANAGEMENT_IP:
            skipped = 'no management IP: nothing to Clean up'
        elif Reservation.objects.filter(switch=switch).exists():
            # Only production's code from before Cleanups were deferred reserves a Switch meanwhile
            skipped = 'reserved again before its Cleanup: skipped'
        if skipped:
            with transaction.atomic():
                SwitchEvent.objects.create(switch=switch, kind=SwitchEvent.CLEANUP, ok=False, reasons=[skipped],
                                           user=holder)
                pending.delete()
            logger.warning("Cleanup of %s: %s", switch.mngt_IP, skipped)
            return f"Cleanup of {switch.mngt_IP}: {skipped}"

        # The banner lives outside the directories Cleanup replaces, so it survives the reload,
        # whereas a switch already reloading can't be written to
        warnings = [] if switch.changeBanner() else ["the banner couldn't be updated"]
        try:
            lab_switch(switch.mngt_IP).restore_init_and_reload()
            reasons, wait = [], self.grace
        except LabSwitchError as e:
            reasons, wait = [str(e)], timedelta(0)
        now = self.now()
        with transaction.atomic():
            SwitchEvent.objects.create(switch=switch, kind=SwitchEvent.CLEANUP, ok=not reasons, reasons=reasons,
                                       warnings=warnings, user=holder)
            pending.started_at = now
            pending.next_inspection_at = now + wait
            pending.give_up_at = now + self.timeout
            pending.save(update_fields=['started_at', 'next_inspection_at', 'give_up_at'])
        if reasons:
            logger.error("Cleanup of %s could not reload it: %s", switch.mngt_IP, reasons[0])
            return f"Cleanup of {switch.mngt_IP} could not reload it, Inspecting it as it is: {reasons[0]}"
        logger.info("Cleanup of %s: reloading", switch.mngt_IP)
        return f"Cleanup of {switch.mngt_IP}: reloading"

    def finish(self, pending: PendingCleanup, result: InspectionResult) -> str:
        """
        Records the Inspection, and Quarantines the Switch if it isn't clean, all at once: the
        Cleanup is done only once its outcome is recorded. The Quarantine names the holder,
        unless BLab itself is the likely cause (the reload couldn't be started, or the Switch
        never came back): an admin clears it then.
        """
        switch = pending.switch
        with transaction.atomic():
            inspect_and_record(switch, result)
            pending.delete()
            if result.clean:
                return f"Cleaned up {switch.mngt_IP}: clean"
            if switch.out_of_service:
                return f"Cleaned up {switch.mngt_IP}: not clean, but Out of service, so left alone"
            if Reservation.objects.filter(switch=switch).exists():
                logger.warning("Cleanup of %s: not clean, but reserved again meanwhile: not Quarantined", switch.mngt_IP)
                return f"Cleaned up {switch.mngt_IP}: not clean, but reserved again meanwhile"
            reload_failed = switch.events.filter(kind=SwitchEvent.CLEANUP, ok=False,
                                                 at__gte=pending.requested_at).exists()
            holder = holder_of(pending.holder_id) if result.reached and not reload_failed else None
            put_in_quarantine(switch, holder, result.reasons)
        named = holder.username if holder else 'nobody, for an admin'
        return f"Cleaned up {switch.mngt_IP}: not clean, Quarantined naming {named}: {'; '.join(result.reasons)}"
