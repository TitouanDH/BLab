"""
The Switch worker (docs/adr/0005): carries out the Cleanups that Releases ask for, which take
too long for a request. For each PendingCleanup it removes the Switch accounts (docs/adr/0004),
updates the banner, brings the Switch back to its clean state (docs/adr/0006: removes the
Unwanted files and the other local users, and repairs init from its Golden init), restores
init and reloads the Switch, waits for it to come back (api.reboot: until it has really
rebooted and AOS is ready), Inspects it, and Quarantines it in its last holder's name if it isn't clean.
Between Cleanups, under a lock of its own (SWITCH_ACCOUNTS_LOCK), it creates and removes the
Switch accounts that are out of step (api.switch_accounts.sync_due), and writes again the
banners that couldn't be written (sync_banners): that never waits on whichever stage holds the
Cleanup lock. It also carries out the Restores members
start (api.restore), under the Cleanup lock.

Each step is recorded in PendingCleanup before the next, so a redeploy only delays a Cleanup.
Production and pre-prod each run one, like the Link worker, with a lock of its own: only one
works at a time.
"""
import logging
from datetime import datetime, timedelta
from typing import Callable, List, Optional, Tuple

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from . import clean_state, reboot, restore, switch_accounts
from .inspection import ACCOUNT_LEFT, UNREADABLE, InspectionResult, clean_state_findings, inspect, inspect_and_record
from .lab_switch import LabSwitchError, Repair, lab_switch
from .models import NO_MANAGEMENT_IP, PendingCleanup, Reservation, Switch, SwitchEvent
from .quarantine import holder_of, settle

logger = logging.getLogger(__name__)

SWITCH_WORKER_LOCK = 0x424C4153  # pg advisory lock key ("BLAS"): one Switch worker at a time
SWITCH_ACCOUNTS_LOCK = 0x424C4155  # ("BLAU"): one Switch account sync at a time, whoever does Cleanups
RELOAD_GRACE = timedelta(minutes=2)     # before the first look: the Switch is still going down
RELOAD_TIMEOUT = timedelta(minutes=20)  # then it is Inspected as it is, back or not
LOOK_AGAIN_AFTER = timedelta(seconds=30)  # between looks at a Switch that isn't back yet
BANNER_RETRY_AFTER = timedelta(minutes=5)  # between two writes of a banner out of step
BANNERS_PER_CYCLE = 3  # sync_banners' share of a cycle, so Cleanups don't wait long


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
    """
    Asks the Switch worker to Clean the Switch up, naming holder if it then isn't clean. A
    Cleanup already under way is left as it is: starting it again would reload the Switch twice.
    """
    with transaction.atomic():
        pending = PendingCleanup.objects.select_for_update().filter(switch=switch).first()
        if pending is not None and pending.started_at is not None:
            logger.warning("Cleanup of %s asked for again while under way: left as it is", switch.mngt_IP)
            return pending
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
        self.reboots = reboot.Watch()  # the reloading Switches of Cleanups and Restores alike

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
        return restore.RestoreWorker(self.now, self.grace, self.timeout, self.look_again_after, self.reboots).work()

    def sync_accounts(self) -> List[str]:
        """Creates and removes the Switch accounts that are out of step (a few Switches per call)."""
        try:
            return switch_accounts.sync_due(self.now())
        except Exception as e:
            logger.exception("Switch accounts could not be synced")
            return [f"Could not sync the Switch accounts: {e}"]

    def sync_banners(self) -> List[str]:
        """
        Writes again the banners that are out of step (Switch.banner_tried_at), a few Switches
        per call, BANNER_RETRY_AFTER their last attempt. A Switch being Cleaned up is left to
        its Cleanup, which writes it.
        """
        outcomes = []
        due = (Switch.objects.filter(banner_tried_at__lte=self.now() - BANNER_RETRY_AFTER)
               .exclude(id__in=PendingCleanup.objects.values('switch_id')).order_by('banner_tried_at'))
        for switch in due[:BANNERS_PER_CYCLE]:
            try:
                written = switch.changeBanner(self.now())
            except Exception:
                logger.exception("Banner of %s could not be written", switch.mngt_IP)
                written = False
            outcomes.append(f"Banner of {switch.mngt_IP}: {'written' if written else 'still out of step'}")
        return outcomes

    def advance(self, pending: PendingCleanup) -> Optional[str]:
        """Starts the Cleanup, or Inspects the Switch once it is due: what happened, or None if nothing did."""
        if pending.started_at is None:
            return self.start(pending)
        if self.now() < pending.next_inspection_at:
            return None
        # A Switch that couldn't be reloaded is Inspected as it is
        seen = (reboot.Look(reboot.BACK) if self.reload_failure(pending) is not None
                else self.reboots.look(pending.switch, pending.started_at, self.now()))
        result = inspect(pending.switch) if seen.state == reboot.BACK else None
        if (result is None or not result.reached) and self.now() < pending.give_up_at:
            pending.next_inspection_at = self.now() + self.look_again_after
            pending.save(update_fields=['next_inspection_at'])
            return None
        if result is None:  # not back in time: Inspected as it is, and why it isn't back is a reason
            result = inspect(pending.switch)
            if seen.state in reboot.GIVES_A_REASON:
                result.reasons.insert(0, seen.detail)
        return self.finish(pending, result, rebooted=seen.state == reboot.BACK)

    @staticmethod
    def reload_failure(pending: PendingCleanup) -> Optional[str]:
        """Why the Cleanup couldn't restore init and reload, or None if it did."""
        failed = (pending.switch.events.filter(kind=SwitchEvent.CLEANUP, ok=False, at__gte=pending.requested_at)
                  .order_by('-at').first())
        if failed is None:
            return None
        return f"the Cleanup couldn't reload it: {failed.reasons[0] if failed.reasons else 'no reason given'}"

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
        if not switch.changeBanner(self.now()):
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
        Cleanup is done only once its outcome is recorded. A Cleanup that couldn't reload never
        ends clean, since init never reached the Switch: why it couldn't is the first reason.
        The Quarantine names the holder, unless BLab itself is the likely cause (the reload
        couldn't be started, the Switch never came back, never rebooted or was never ready, it
        couldn't read the Switch ("unreadable: ..."), or all it found is what the Cleanup
        should have removed or repaired: Switch accounts, Unwanted files, local users, a
        changed init): an admin clears it then. A Cleanup the Sweep asked for has no holder,
        and lifts the Quarantine of a Switch it finds clean.
        """
        switch = pending.switch
        self.reboots.forget(switch, pending.started_at)
        reload_failure = self.reload_failure(pending)
        reload_failed = reload_failure is not None
        if reload_failed:
            result.reasons.insert(0, reload_failure)
        with transaction.atomic():
            inspect_and_record(switch, result)
            pending.delete()
            only_left_behind = all(reason.startswith(ACCOUNT_LEFT) or reason in result.repairable
                                   for reason in result.reasons)
            unreadable = any(reason.startswith(UNREADABLE) for reason in result.reasons)
            blab_failed = reload_failed or not rebooted or not result.reached or unreadable or only_left_behind
            holder = None if blab_failed else holder_of(pending.holder_id)
            outcome = settle(switch, result, holder, 'the Inspection after its Cleanup found it clean')
        return f"Cleaned up {switch.mngt_IP}: {outcome}"
