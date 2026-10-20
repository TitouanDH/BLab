"""
Release (see CONTEXT.md): the one way a Reservation ends, whether a user releases the
Switch or BLab expires the Reservation.

With Projects on, the Switch first goes into a Snapshot of its Project (api.snapshots), so the
Project can be Restored. Then every Link with an end on the Switch is torn down. If any of them can't be, the
Reservation stays and nothing else is done, so the Switch is never handed back half wired.
It stays too if a Connect recorded a new Link on the Switch meanwhile: the user or expiry
releases it again.
Otherwise the Reservation is deleted and the Switch worker is asked to Clean the Switch up:
it updates the banner, reloads, Inspects the Switch, and Quarantines it in the holder's name
if it isn't clean.

An admin may Release any Reservation (the Django admin, api.admin): the Quarantine that may follow then
names nobody, and a forced Release forgets the Links that can't be torn down instead of keeping
the Reservation, leaving what stays on a backbone as Orphans for the Link worker.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from django.contrib.auth.models import User

from . import links, snapshots, topology
from .models import Reservation, SwitchEvent
from .switch_worker import request_cleanup

logger = logging.getLogger(__name__)


class NotAllowed(Exception):
    """The actor may not release this Reservation."""


class AlreadyReleased(Exception):
    """The Reservation ended in the meantime (the Switch may already be someone else's)."""


@dataclass
class ReleaseResult:
    """What a release did. `failures` holds messages meant for the user."""
    released: bool = False
    cleanup_requested: bool = False
    failures: List[str] = field(default_factory=list)
    forgotten: List[str] = field(default_factory=list)  # Links a forced Release forgot, and why


def _ended(reservation: Reservation) -> AlreadyReleased:
    """The error for a Reservation that ended since it was read."""
    return AlreadyReleased(f"The reservation of {reservation.switch.mngt_IP} has already ended.")


def may_release(user: User, reservation: Reservation) -> bool:
    """Whoever may work on the reserved Switch: the rule is topology.may_work_on."""
    return topology.may_work_on(user, reservation)


def release(reservation: Reservation, actor: Optional[User], by_admin: bool = False,
            force: bool = False) -> ReleaseResult:
    """
    Ends a Reservation on behalf of actor (None when BLab itself does it, on expiry).
    Raises NotAllowed if actor may not release it, and AlreadyReleased if it has ended
    since it was read; any other failure is in the result. by_admin: an admin's Release, which
    any Reservation allows and whose Quarantine names nobody; force (an admin's only): Links
    that can't be torn down are forgotten rather than keeping the Reservation.
    """
    by_admin = by_admin or force
    if by_admin and not (actor is not None and actor.is_staff):
        raise NotAllowed("Only an admin may release as an admin.")
    if actor is not None and not by_admin and not may_release(actor, reservation):
        raise NotAllowed(f"{actor.username} may not release {reservation.switch.mngt_IP}.")
    # A stale Reservation (say, read by expiry before its holder released it) must not
    # tear down the Links of whoever reserved the Switch since
    if not Reservation.objects.filter(pk=reservation.pk).exists():
        raise _ended(reservation)
    switch = reservation.switch
    holder = reservation.user
    by = (f'{actor.username} (admin)' if by_admin else actor.username) if actor else 'expiry'
    result = ReleaseResult()

    snapshots.before_release(reservation, actor)  # its Links are only known until they are torn down
    errors, result.forgotten = links.disconnect_all(switch, force)
    if errors:
        for e in errors:
            logger.error("Release of %s (%s) by %s: a Link can't be torn down: %s",
                         switch.mngt_IP, holder.username, by, e)
        result.failures = [str(e) for e in errors]
        logger.error("Switch %s stays reserved by %s: %d Link(s) still up",
                     switch.mngt_IP, holder.username, len(errors))
        return result

    # In one go, so that nobody can reserve the Switch between the Release and its Cleanup. The
    # Reservation is locked: of two Releases at once (expiry and its holder, or production and
    # pre-prod), only the first gets here, and the second must not start its Cleanup again. And
    # under the SVLAN lock, which a Connect takes to record a Link: either the Connect sees the
    # Switch released and refuses, or this sees its Link and the Switch stays reserved.
    with links.svlan_lock():
        if not Reservation.objects.select_for_update().filter(pk=reservation.pk).exists():
            raise _ended(reservation)
        if links.has_links(switch):
            result.failures = ["A Link was made on the Switch while it was being released: release it again."]
            if result.forgotten:  # forgotten all the same: the history must say so
                SwitchEvent.objects.create(switch=switch, kind=SwitchEvent.RELEASE, ok=False, user=holder,
                                           reasons=[f'released by {by}, refused: a Link was made meanwhile'],
                                           warnings=[f'forgotten: {f}' for f in result.forgotten])
            logger.error("Switch %s stays reserved by %s: a Link was made on it during its Release by %s",
                         switch.mngt_IP, holder.username, by)
            return result
        reservation.delete()
        SwitchEvent.objects.create(switch=switch, kind=SwitchEvent.RELEASE, ok=True, user=holder,
                                   reasons=['expired' if actor is None else f'released by {by}'],
                                   warnings=[f'forgotten: {f}' for f in result.forgotten])
        request_cleanup(switch, None if by_admin else holder)
    result.released = result.cleanup_requested = True
    logger.info("Reservation of %s by %s released by %s; Cleanup requested", switch.mngt_IP, holder.username, by)
    return result


def expire(reservation: Reservation) -> ReleaseResult:
    """Expiry: a Release that BLab triggers itself."""
    return release(reservation, actor=None)
