"""
A member's AOS install (#49, see CONTEXT.md): whoever may work on a reserved Switch picks another
AOS release among those of its model's family (api.aos_release.compatible_releases), and BLab
installs it and reboots the Switch into it.

request() records it; the Switch worker carries it out (AosInstallWorker), on its threads, a
step at a time like a Restore: it downloads the release onto the BLab server and copies it into
working/ only (api.aos_release.install), reloads from working/, and waits until the Switch is
back (api.reboot). init/ and certified/ are left as they are, so the next Cleanup copies init/
over working/ and brings back the Golden init's release. Each step is recorded before the next,
so a redeploy only delays it; one cut short while installing is started again.

A failed download or copy leaves working/ as it was and nothing is reloaded: the Switch keeps its
release, and the failure is shown, never a Quarantine. While an install is under way, the Switch
isn't Released (but by an admin's forced Release) nor Restored, and its Cleanup waits for it.

Each AOS release keeps its local users in a table of its own (#74), so once the Switch is back
in another release its Switch accounts are made again (switch_accounts.recreate), and admin is
given the lab's secret password again if that release's table has an older one (#87).
"""
import logging
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import aos_release, reboot, switch_accounts, topology
from .lab_switch import LabSwitchError, lab_switch, secure_admin_after_reload
from .models import NO_MANAGEMENT_IP, AosInstall, Reservation, RestoreSwitch, Switch

logger = logging.getLogger(__name__)

UNREACHABLE_FOR = timedelta(minutes=5)  # how long an install waits for a Switch it can't log in to
TAKES = 'this takes 10 to 20 minutes'  # the download, the copy over SFTP and the reload (.132, 2026-10-21)


class InstallError(Exception):
    """An install refused. The message is meant for the user; status is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def under_way(switch_ids=None):
    """The installs not settled yet, of these Switches if given."""
    installs = AosInstall.objects.filter(state__in=AosInstall.UNDER_WAY)
    return installs if switch_ids is None else installs.filter(switch_id__in=switch_ids)


def busy_switch_ids() -> set:
    return set(under_way().values_list('switch_id', flat=True))


def refusal_to_release(switch: Switch) -> Optional[str]:
    """Why the Switch can't be Released now (an install under way), or None."""
    install = under_way([switch.id]).first()
    if install is None:
        return None
    return (f"AOS {install.release} is being installed on {switch.mngt_IP}: release it once that is done "
            f"({TAKES}).")


def _reservation(switch: Switch) -> Optional[Reservation]:
    return Reservation.objects.filter(switch=switch).first()


def may_install(user: User, switch: Switch) -> bool:
    reservation = _reservation(switch)
    return reservation is not None and topology.may_work_on(user, reservation)


def view(user: User, switch: Switch) -> Dict:
    """
    What the AOS release dialog shows: {"switch": {"id", "model", "mngt_IP"}, "current": the release
    BLab last saw it run, "lab_release": the Golden init's, "releases": [compatible, newest first],
    "install": the latest install, as describe() gives it, or null}. InstallError (403) for whoever
    may not work on it.
    """
    if not may_install(user, switch):
        raise InstallError("You may not work on this Switch.", status=403)
    latest = AosInstall.objects.filter(switch=switch).first()
    return {
        'switch': {'id': switch.id, 'model': switch.model, 'mngt_IP': switch.mngt_IP},
        'current': switch.aos_release or None,
        'lab_release': aos_release.lab_release(switch.model),
        'releases': aos_release.compatible_releases(switch.model),
        'install': describe(latest) if latest else None,
    }


def describe(install: AosInstall) -> Dict:
    return {
        'id': install.id, 'switch': install.switch_id, 'release': install.release,
        'previous_release': install.previous_release or None,
        'requested_by': install.requested_by.username if install.requested_by else None,
        'requested_at': install.requested_at, 'state': install.state, 'detail': install.detail,
        'warnings': install.warnings, 'updated_at': install.updated_at, 'finished_at': install.finished_at,
        'under_way': install.state in AosInstall.UNDER_WAY,
    }


def request(user: User, switch: Switch, release: str, now: Optional[datetime] = None) -> AosInstall:
    """
    Records an install of `release` on the Switch, for the Switch worker. InstallError if user may
    not work on it (403), or if the release isn't one of its family's, the Switch is Out of service,
    or it already has an install or a Restore under way.
    """
    now = now or timezone.now()
    with transaction.atomic():
        # Locked, as a Reservation locks it: two installs don't start at once
        switch = Switch.objects.select_for_update().get(pk=switch.pk)
        if not may_install(user, switch):
            raise InstallError("You may not work on this Switch.", status=403)
        if switch.mngt_IP == NO_MANAGEMENT_IP:
            raise InstallError("This Switch has no management IP.")
        if switch.out_of_service_reason:
            raise InstallError(f"This Switch is Out of service: {switch.out_of_service_reason}")
        if release not in aos_release.compatible_releases(switch.model):
            raise InstallError(f"{release} is not an AOS release BLab can install on a {switch.model}.")
        if under_way([switch.id]).exists():
            raise InstallError("An AOS release is already being installed on this Switch.")
        if RestoreSwitch.objects.filter(switch=switch, state__in=RestoreSwitch.UNDER_WAY,
                                        restore__finished_at__isnull=True).exists():
            raise InstallError("This Switch is being Restored: install a release once that is done.")
        install = AosInstall.objects.create(switch=switch, release=release, requested_by=user, requested_at=now,
                                            updated_at=now, detail='Waiting for the Switch worker.')
    logger.info("%s asked for AOS %s on %s", user.username, release, switch.mngt_IP)
    return install


class AosInstallWorker:
    """Carries installs out, a step at a time, as the Switch worker's cycle calls work()."""

    def __init__(self, now: Callable[[], datetime], grace: timedelta, timeout: timedelta,
                 look_again_after: timedelta, reboots: reboot.Watch, steps):
        self.now = now
        self.grace = grace
        self.timeout = timeout
        self.look_again_after = look_again_after
        self.reboots = reboots
        self.steps = steps  # the Switch worker's (api.switch_worker.Steps): one step per Switch at a time

    def work(self) -> List[str]:
        """Moves every install that is due one step on, as the threads allow. What the steps done since did."""
        # An install cut short while installing (a redeploy) is due again: it starts over
        due = (AosInstall.objects.filter(state__in=AosInstall.UNDER_WAY)
               .filter(Q(state__in=(AosInstall.WAITING, AosInstall.INSTALLING)) | Q(next_check_at__isnull=True)
                       | Q(next_check_at__lte=self.now()))
               .select_related('switch').order_by('requested_at'))
        for install in due:
            self.steps.run(install.switch_id, lambda install=install: self.guarded(install))
        return self.steps.done()

    def guarded(self, install: AosInstall) -> Optional[str]:
        try:
            return self.advance(install)
        except Exception as e:
            # One broken Switch must not stop the others; it is tried again next cycle
            logger.exception("AOS install on %s failed", install.switch.mngt_IP)
            return f"Could not go on with the AOS install on {install.switch.mngt_IP}: {e}"

    def advance(self, install: AosInstall) -> Optional[str]:
        if install.state in (AosInstall.WAITING, AosInstall.INSTALLING):
            return self.install(install)
        if self.now() < install.next_check_at:
            return None
        return self.check_back(install)

    def _record(self, install: AosInstall, state: str, detail: str, **fields) -> str:
        if install.state == AosInstall.RELOADING and state != AosInstall.RELOADING:
            self.reboots.forget(install.switch, install.updated_at)
        install.state, install.detail, install.updated_at = state, detail, self.now()
        if state not in AosInstall.UNDER_WAY:
            install.finished_at = self.now()
        for name, value in fields.items():
            setattr(install, name, value)
        install.save()
        return f"AOS install on {install.switch.mngt_IP}: {detail}"

    def install(self, install: AosInstall) -> str:
        """Downloads the release, copies it into working/ and reloads; or says why it couldn't."""
        switch, release = install.switch, install.release
        if _reservation(switch) is None:
            return self._record(install, AosInstall.FAILED, 'Released before it could be installed.')
        device = lab_switch(switch.mngt_IP)
        try:
            previous = device.microcode('working').release
        except LabSwitchError as e:
            if self.now() - install.requested_at < UNREACHABLE_FOR:
                # Nothing is done yet: a Switch that drops a login now and then is tried again next cycle
                return self._record(install, AosInstall.WAITING, f"Couldn't reach the Switch yet, trying again: {e}")
            return self._record(install, AosInstall.FAILED, f"Couldn't read the Switch's AOS release: {e}")
        if previous == release:
            Switch.objects.filter(pk=switch.pk).update(aos_release=release)
            return self._record(install, AosInstall.DONE, f"The Switch already runs AOS {release}.",
                                previous_release=previous)
        self._record(install, AosInstall.INSTALLING,
                     f"Downloading AOS {release} and copying it to the Switch: {TAKES}.", previous_release=previous)
        try:
            aos_release.install(switch, release, ['working'])
        except (LabSwitchError, aos_release.ImageError) as e:
            logger.warning("AOS %s not installed on %s: %s", release, switch.mngt_IP, e)
            return self._record(install, AosInstall.FAILED,
                                f"Couldn't install AOS {release}: {e}. The Switch still runs {previous}.")
        # Out of the release it leaves, or they'd come back at the Cleanup that brings it back (#74)
        warnings = switch_accounts.remove_before_release_change(switch)
        try:
            device.reload()
        except LabSwitchError as e:
            switch_accounts.sync(switch, self.now())  # it still runs that release: its accounts back in it
            return self._record(install, AosInstall.FAILED,
                                f"AOS {release} is in working/, but the Switch couldn't be reloaded into it: {e}. "
                                f"It runs {previous} until it reloads.")
        now = self.now()
        return self._record(install, AosInstall.RELOADING, f"Reloading into AOS {release}.", warnings=warnings,
                            next_check_at=now + self.grace, give_up_at=now + self.timeout)

    def check_back(self, install: AosInstall) -> Optional[str]:
        """Whether the reloading Switch is back, and in the release asked for; its Switch accounts made again."""
        switch = install.switch
        # updated_at is when it started reloading: looking again leaves it as it is
        seen = self.reboots.look(switch, install.updated_at, self.now())
        if seen.state != reboot.BACK:
            if self.now() < install.give_up_at:
                install.next_check_at = self.now() + self.look_again_after
                install.save(update_fields=['next_check_at'])
                return None
            minutes = int(self.timeout.total_seconds() // 60)
            return self._record(install, AosInstall.FAILED,
                                f"Didn't come back within {minutes} minutes of its reload: {seen.detail.rstrip('.')}.")
        try:
            running = lab_switch(switch.mngt_IP).aos_release()
        except LabSwitchError as e:
            running = None
            logger.warning("AOS install on %s: release not read once back: %s", switch.mngt_IP, e)
        if running:
            Switch.objects.filter(pk=switch.pk).update(aos_release=running)
        made = switch_accounts.recreate(switch, self.now())
        if reboot.refused_not_ready(made) and self.now() < install.give_up_at:
            # Back, but AOS refuses user commands for a while yet: its accounts are made at the next look
            install.next_check_at = self.now() + self.look_again_after
            install.save(update_fields=['next_check_at'])
            return None
        # As after every reload BLab starts: admin must still have the lab's secret password (#87)
        warnings = install.warnings + secure_admin_after_reload(switch.mngt_IP) + made
        if running and running != install.release:
            return self._record(install, AosInstall.FAILED,
                                f"Back, but running AOS {running}, not {install.release}: AOS may have fallen back "
                                f"to certified/.", warnings=warnings)
        return self._record(install, AosInstall.DONE, f"Back, running AOS {install.release}.", warnings=warnings)
