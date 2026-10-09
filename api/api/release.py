"""
Release (see CONTEXT.md): the one way a Reservation ends, whether a user releases the
Switch or BLab expires the Reservation.

Every Link with an end on the Switch is torn down first. If any of them can't be, the
Reservation stays and nothing else is done, so the Switch is never handed back half wired.
Otherwise the Reservation is deleted and the Switch worker is asked to Clean the Switch up:
it updates the banner, reloads, Inspects the Switch, and Quarantines it in the holder's name
if it isn't clean.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from django.contrib.auth.models import User
from django.db import transaction

from . import links, topology
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


def may_release(user: User, reservation: Reservation) -> bool:
    """Whoever may work on the Topology the Switch is in: the rule is topology.may_work."""
    return topology.may_work(user, reservation.user_id)


def release(reservation: Reservation, actor: Optional[User]) -> ReleaseResult:
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
    holder = reservation.user
    by = actor.username if actor else 'expiry'
    result = ReleaseResult()

    errors = links.disconnect_all(switch)
    if errors:
        for e in errors:
            logger.error("Release of %s (%s) by %s: a Link can't be torn down: %s",
                         switch.mngt_IP, holder.username, by, e)
        result.failures = [str(e) for e in errors]
        logger.error("Switch %s stays reserved by %s: %d Link(s) still up",
                     switch.mngt_IP, holder.username, len(errors))
        return result

    # In one go, so that nobody can reserve the Switch between the Release and its Cleanup
    with transaction.atomic():
        reservation.delete()
        SwitchEvent.objects.create(switch=switch, kind=SwitchEvent.RELEASE, ok=True, user=holder,
                                   reasons=['expired' if actor is None else f'released by {by}'])
        request_cleanup(switch, holder)
    result.released = result.cleanup_requested = True
    logger.info("Reservation of %s by %s released by %s; Cleanup requested", switch.mngt_IP, holder.username, by)
    return result


def expire(reservation: Reservation) -> ReleaseResult:
    """Expiry: a Release that BLab triggers itself."""
    return release(reservation, actor=None)
