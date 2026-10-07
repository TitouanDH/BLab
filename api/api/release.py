"""
Release (see CONTEXT.md): the one way a Reservation ends, whether a user releases the
Switch or BLab expires the Reservation.

Every Link with an end on the Switch is torn down first. If any of them can't be, the
Reservation stays and nothing else is done, so the Switch is never handed back half wired.
Otherwise the Reservation is deleted, the banner is updated, and the Switch is Cleaned up
if asked (always on expiry). Banner and Cleanup failures don't undo the release, nor does
a banner failure stop the Cleanup; they are reported.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from django.contrib.auth.models import User

from . import links, topology
from .models import Reservation

logger = logging.getLogger(__name__)


class NotAllowed(Exception):
    """The actor may not release this Reservation."""


class AlreadyReleased(Exception):
    """The Reservation ended in the meantime (the Switch may already be someone else's)."""


@dataclass
class ReleaseResult:
    """What a release did. `failures` holds messages meant for the user."""
    released: bool = False
    cleaned_up: bool = False
    failures: List[str] = field(default_factory=list)


def may_release(user: User, reservation: Reservation) -> bool:
    """Whoever may work on the Topology the Switch is in: the rule is topology.may_work."""
    return topology.may_work(user, reservation.user_id)


def release(reservation: Reservation, actor: Optional[User], cleanup: bool = False) -> ReleaseResult:
    """
    Ends a Reservation on behalf of actor (None when BLab itself does it, on expiry).
    Raises NotAllowed if actor may not release it, and AlreadyReleased if it has ended
    since it was read; any other failure is in the result.
    """
    if actor is not None and not may_release(actor, reservation):
        raise NotAllowed(f"{actor.username} may not release {reservation.switch.mngt_IP}.")
    # A stale Reservation (say, read by expiry before its holder released it) must not
    # tear down the Links of whoever reserved the Switch since
    if not Reservation.objects.filter(pk=reservation.pk).exists():
        raise AlreadyReleased(f"The reservation of {reservation.switch.mngt_IP} has already ended.")
    switch = reservation.switch
    by = actor.username if actor else 'expiry'
    result = ReleaseResult()

    errors = links.disconnect_all(switch)
    if errors:
        for e in errors:
            logger.error("Release of %s (%s) by %s: a Link can't be torn down: %s",
                         switch.mngt_IP, reservation.user.username, by, e)
        result.failures = [str(e) for e in errors]
        logger.error("Switch %s stays reserved by %s: %d Link(s) still up",
                     switch.mngt_IP, reservation.user.username, len(errors))
        return result

    reservation.delete()
    result.released = True
    logger.info("Reservation of %s by %s released by %s", switch.mngt_IP, reservation.user.username, by)

    # The banner goes first: it lives outside the directories Cleanup replaces, so it
    # survives the reload, whereas a switch already reloading can't be written to
    if not switch.changeBanner():
        result.failures.append("The banner couldn't be updated.")
        logger.warning("Released %s, but its banner couldn't be updated", switch.mngt_IP)
    if cleanup:
        result.cleaned_up = switch.cleanup()
        if not result.cleaned_up:
            result.failures.append("Cleanup failed.")
            logger.warning("Released %s, but its Cleanup failed", switch.mngt_IP)
    return result


def expire(reservation: Reservation) -> ReleaseResult:
    """Expiry: a Release that BLab triggers itself, and that always Cleans up."""
    return release(reservation, actor=None, cleanup=True)
