"""
The Login check (#92): the Switch worker's look at every Switch in service, every CHECK_EVERY,
for logins lost to a reload BLab didn't start.

Each AOS release keeps its local users in a table of its own (/flash/system/userTable<n>, one
per release train: userTable8 to userTable11 on 10.69.144.132, read 2026-11-03), and a `user`
command writes only the table of the release running (docs/adr/0006, #74). So a Switch that
reloads into another release, whoever installed it, comes up with that release's older users:
`admin` may take only a factory password (`switch`, `Switch@123`), and the Switch accounts
made in the release it left are gone. BLab already repairs both after the reloads it starts
(a Cleanup, a Restore, a member's AOS install: lab_switch.secure_admin_after_reload,
switch_accounts.recreate). This covers the others: a member who upgrades from the CLI and
reloads, a power cut that boots certified/.

For each Switch in service with a management IP, not being Cleaned up, Restored or installed:
1. If `admin` takes only an older password of BLAB_SWITCH_ADMIN_PASSWORDS, BLab gives it the
   lab's secret one with onboarding's code (lab_switch.secure_admin, Titouan 2026-11-03), and
   makes every Switch account of the Switch again (switch_accounts.recreate).
2. Otherwise, a Switch account BLab made that show user no longer lists is made again, and so
   is one it lists without SU allowed (#95: every Switch account has it, so those made before
   get it within a CHECK_EVERY of the release that gives it).
AOS refuses `user` commands for a while after it comes up ("has not reached the ready state"):
that Switch is looked at again after LOOK_AGAIN_AFTER rather than CHECK_EVERY.

When each Switch was last checked lives in the worker's memory only: a restart checks them all
again, a few per cycle. A Switch whose last login in this process took an older admin password
(an Inspection or the Sweep logged in with one) comes first.
"""
import logging
from datetime import datetime, timedelta
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from django.db.models import Q

from . import switch_accounts
from .clean_state import parse_users
from .lab_switch import LabSwitchError, lab_switch, secure_admin, took_older_admin_password
from .models import NO_MANAGEMENT_IP, PendingCleanup, Switch, SwitchAccount
from .reboot import NOT_READY_ERROR

logger = logging.getLogger(__name__)

CHECK_EVERY = timedelta(minutes=15)       # between two checks of a Switch
LOOK_AGAIN_AFTER = timedelta(minutes=1)   # for a Switch whose AOS isn't ready for user commands yet
SWITCHES_PER_CYCLE = 2  # its share of a Switch worker cycle, so Cleanups don't wait long
ADMIN_SECURED = ("admin took only an older password (a reload BLab didn't start, into another AOS release): "
                 "given the lab's secret one again, and saved; its Switch accounts are made again")


class LoginCheck:
    def __init__(self, now: Callable[[], datetime]):
        self.now = now
        self.next_at: Dict[int, datetime] = {}  # Switch id -> when it is checked next
        self.last_at: Dict[int, datetime] = {}  # Switch id -> when it was last checked

    def work(self, skip: Iterable[int] = ()) -> List[str]:
        """Checks the Switches that are due, a few per call, but those in `skip`. What it found."""
        now = self.now()
        switches = list(Switch.objects.filter(Switch.in_service()).exclude(mngt_IP=NO_MANAGEMENT_IP)
                        .exclude(id__in=PendingCleanup.objects.values('switch_id')).exclude(id__in=set(skip))
                        .order_by('id'))
        older = {s.id for s in switches if took_older_admin_password(s.mngt_IP)}
        # One whose admin took an older password is due sooner, but not more than once a minute
        # (secure_admin may keep failing on it)
        due = [s for s in switches if self.next_at.get(s.id, now) <= now
               or (s.id in older and now - self.last_at.get(s.id, now) >= LOOK_AGAIN_AFTER)]
        due.sort(key=lambda s: (s.id not in older, self.next_at.get(s.id, now)))
        outcomes = []
        for switch in due[:SWITCHES_PER_CYCLE]:
            outcome = self.check(switch)
            if outcome:
                outcomes.append(f"Logins of {switch.mngt_IP}: {outcome}")
        return outcomes

    def check(self, switch: Switch) -> Optional[str]:
        """Repairs admin and the Switch accounts of one Switch: what it did or why it couldn't, or None."""
        self.last_at[switch.id] = self.now()
        self.next_at[switch.id] = self.now() + CHECK_EVERY
        try:
            secured = secure_admin(switch.mngt_IP)
        except LabSwitchError as e:
            return self._failed(switch, f"couldn't check admin's password: {e}")
        if secured:
            logger.warning("%s: %s", switch.mngt_IP, ADMIN_SECURED)
            failures = switch_accounts.recreate(switch, self.now())
            return '; '.join([ADMIN_SECURED] + failures)
        try:
            lost, without_su = accounts_to_make_again(switch)
        except LabSwitchError as e:
            return self._failed(switch, f"couldn't read its local users: {e}")
        if not lost and not without_su:
            return None
        found = [f"Switch accounts gone, made again: {', '.join(lost)}"] if lost else []
        if without_su:
            found.append(f"Switch accounts without SU allowed, given it: {', '.join(without_su)}")
        logger.warning("%s: %s", switch.mngt_IP, '; '.join(found))
        failures = switch_accounts.recreate(switch, self.now(), names=lost + without_su)
        return '; '.join(found + failures)

    def _failed(self, switch: Switch, why: str) -> str:
        if NOT_READY_ERROR in why:  # just up: AOS takes user commands soon
            self.next_at[switch.id] = self.now() + LOOK_AGAIN_AFTER
        return why


def accounts_to_make_again(switch: Switch) -> Tuple[List[str], List[str]]:
    """
    The Switch accounts BLab made on the Switch itself that show user doesn't list, and those it
    lists without SU allowed (a release without SU doesn't say: those are left alone). Only
    reads. One made on a VC master for a slave is the master's to check. Raises LabSwitchError.
    """
    made = set(SwitchAccount.objects.filter(switch=switch, created=True)
               .filter(Q(vc_master__isnull=True) | Q(vc_master='')).values_list('name', flat=True))
    if not made:
        return [], []
    answer = lab_switch(switch.mngt_IP).show('show user')
    users = parse_users(answer.output) if answer.status == 0 else None
    if users is None:  # admin is always there: an answer without it says nothing
        raise LabSwitchError((answer.error or answer.output).strip()[:120] or f"exit status {answer.status}")
    return sorted(made - set(users)), sorted(name for name in made & set(users) if users[name] is False)
