"""
The Switch worker (docs/adr/0005): carries out the Cleanups that Releases ask for, which take
too long for a request. For each PendingCleanup it removes the Switch accounts (docs/adr/0004),
updates the banner, brings the Switch back to its clean state (docs/adr/0006: removes the
Unwanted files and the other local users, and repairs init from its Golden init), restores
init and reloads the Switch, waits for it to come back (api.reboot: until it has really
rebooted), Inspects it, and Quarantines it in its last holder's name if it isn't clean.
Between Cleanups, under a lock of its own (SWITCH_ACCOUNTS_LOCK), it creates and removes the
Switch accounts that are out of step (api.switch_accounts.sync_due): that never waits on
whichever stage holds the Cleanup lock. It also carries out the Restores members
start (api.restore), under the Cleanup lock.

Each step is recorded in PendingCleanup before the next, so a redeploy only delays a Cleanup.
Production and pre-prod each run one, like the Link worker, with a lock of its own: only one
works at a time.
"""
import logging
from datetime import datetime, timedelta
from typing import Callable, List, Optional, Set, Tuple

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from . import clean_state, reboot, restore, switch_accounts
from .inspection import ACCOUNT_LEFT, InspectionResult, clean_state_findings, inspect, inspect_and_record
from .lab_switch import LabSwitchError, Repair, lab_switch
from .models import NO_MANAGEMENT_IP, PendingCleanup, Reservation, Switch, SwitchEvent
from .quarantine import holder_of, settle

logger = logging.getLogger(__name__)

SWITCH_WORKER_LOCK = 0x424C4153  # pg advisory lock key ("BLAS"): one Switch worker at a time
SWITCH_ACCOUNTS_LOCK = 0x424C4155  # ("BLAU"): one Switch account sync at a time, whoever does Cleanups
RELOAD_GRACE = timedelta(minutes=2)     # before the first look: the Switch is still going down
RELOAD_TIMEOUT = timedelta(minutes=20)  # then it is Inspected as it is, back or not
LOOK_AGAIN_AFTER = timedelta(seconds=30)  # between looks at a Switch that isn't back yet


def plan_repair(switch: Switch) -> Tuple[Repair, List[str]]:
    """
    Reads the Switch, and decides what its Cleanup repairs: the Unwanted files, the local users
    but admin and the lab managers', and init's config if it isn't the Golden init. Until
    BLAB_CLEAN_STATE_MODE is 'enforce', it repairs nothing and only says what it would.
    Returns the repair, and what to note in the Cleanup's history.
    """
    try:
        found = clean_state_findings(switch, lab_switch(switch.mngt_IP).read_for_inspection())
    except LabSwitchError as e:
        return Repair(), [f"couldn't read what to clean up: {e}"]
    notes = [f'unreadable: {what}' for what in found.unreadable]
    init_config = clean_state.golden_init_config(switch.model) if found.init_changes else None
    files = [path for path in found.files if clean_state.removable(path)]
    if not clean_state.enforcing():
        would = []
        if found.logins:
            would.append('local users ' + clean_state.shown(found.logins))
        if files:
            would.append(f'{len(files)} Unwanted file(s): ' + clean_state.shown(files))
        if init_config:
            would.append('init/vcboot.cfg rewritten from the Golden init')
        if would:
            notes.append('Would remove (BLAB_CLEAN_STATE_MODE=report): ' + '; '.join(would))
        return Repair(), notes
    return Repair(users=found.logins, files=files, init_config=init_config), notes


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
        # The reloads (Switch id, started at) during which the Switch was seen unreachable: it
        # went down. Forgotten on a restart, when its uptime alone tells whether it rebooted.
        self.went_down: Set[Tuple[int, datetime]] = set()

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

    def restores(self) -> List[str]:
        """Moves every Restore under way one step on (api.restore)."""
        return restore.RestoreWorker(self.now, self.grace, self.timeout, self.look_again_after).work()

    def sync_accounts(self) -> List[str]:
        """Creates and removes the Switch accounts that are out of step (a few Switches per call)."""
        try:
            return switch_accounts.sync_due(self.now())
        except Exception as e:
            logger.exception("Switch accounts could not be synced")
            return [f"Could not sync the Switch accounts: {e}"]

    def advance(self, pending: PendingCleanup) -> Optional[str]:
        """Starts the Cleanup, or Inspects the Switch once it is due: what happened, or None if nothing did."""
        if pending.started_at is None:
            return self.start(pending)
        if self.now() < pending.next_inspection_at:
            return None
        # A Switch that couldn't be reloaded is Inspected as it is
        seen = reboot.Look(reboot.BACK) if self.reload_failed(pending) else self.look(pending)
        result = inspect(pending.switch) if seen.state == reboot.BACK else None
        if (result is None or not result.reached) and self.now() < pending.give_up_at:
            pending.next_inspection_at = self.now() + self.look_again_after
            pending.save(update_fields=['next_inspection_at'])
            return None
        if result is None:  # not back in time: Inspected as it is, and why it isn't back is a reason
            result = inspect(pending.switch)
            if seen.state in (reboot.NOT_REBOOTED, reboot.UNKNOWN):
                result.reasons.insert(0, seen.detail)
        return self.finish(pending, result, rebooted=seen.state == reboot.BACK)

    def look(self, pending: PendingCleanup) -> reboot.Look:
        """Whether the Switch is back from the Cleanup's reload, remembering whether it went down meanwhile."""
        reload = (pending.switch_id, pending.started_at)
        seen = reboot.look(pending.switch.mngt_IP, self.now() - pending.started_at, reload in self.went_down)
        if seen.state == reboot.DOWN:
            self.went_down.add(reload)
        return seen

    @staticmethod
    def reload_failed(pending: PendingCleanup) -> bool:
        """Whether the Cleanup couldn't restore init and reload."""
        return pending.switch.events.filter(kind=SwitchEvent.CLEANUP, ok=False, at__gte=pending.requested_at).exists()

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

        # Switch accounts and the banner live outside the directories Cleanup replaces, so they
        # survive the reload, whereas a switch already reloading can't be written to. An account
        # left here fails the Inspection afterwards; it is retried once the Cleanup is done.
        warnings = switch_accounts.sync(switch, self.now())
        if not switch.changeBanner():
            warnings.append("the banner couldn't be updated")
        repair, notes = plan_repair(switch)
        warnings.extend(notes)
        try:
            warnings.extend(lab_switch(switch.mngt_IP).restore_init_and_reload(repair))
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

    def finish(self, pending: PendingCleanup, result: InspectionResult, rebooted: bool = True) -> str:
        """
        Records the Inspection, and Quarantines the Switch if it isn't clean, all at once: the
        Cleanup is done only once its outcome is recorded. The Quarantine names the holder,
        unless BLab itself is the likely cause (the reload couldn't be started, the Switch
        never came back or never rebooted, or all it found is what the Cleanup should have
        removed or repaired: Switch accounts, Unwanted files, local users, a changed init): an
        admin clears it then. A Cleanup the Sweep asked for has no holder, and lifts the
        Quarantine of a Switch it finds clean.
        """
        switch = pending.switch
        self.went_down.discard((pending.switch_id, pending.started_at))
        reload_failed = self.reload_failed(pending)
        with transaction.atomic():
            inspect_and_record(switch, result)
            pending.delete()
            only_left_behind = all(reason.startswith(ACCOUNT_LEFT) or reason in result.repairable
                                   for reason in result.reasons)
            blab_failed = reload_failed or not rebooted or not result.reached or only_left_behind
            holder = None if blab_failed else holder_of(pending.holder_id)
            outcome = settle(switch, result, holder, 'the Inspection after its Cleanup found it clean')
        return f"Cleaned up {switch.mngt_IP}: {outcome}"
