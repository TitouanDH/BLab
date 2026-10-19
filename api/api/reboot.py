"""
Whether a Switch is back from the reload a Cleanup started (api.switch_worker).

It counts as back only once it has really rebooted: its uptime (show system) is shorter than
the time since the reload started, or it was seen unreachable since. AOS 8.10 "verifies and
copies images" for minutes before it goes down, and answers with its old state meanwhile:
on 10.69.144.132 (8.10.86.R04, 2026-10-12) it still had its old uptime 2 minutes after the
reload (#50). Only reads.
"""
import logging
from dataclasses import dataclass
from datetime import timedelta

from .lab_switch import LabSwitchError, LoginRefused, lab_switch, uptime_parts
from .models import NO_MANAGEMENT_IP

logger = logging.getLogger(__name__)

# What a look at a reloading Switch finds
DOWN = 'down'                  # it doesn't answer: going down, or not up yet
REFUSED = 'refused'            # it answers, but refuses BLab's login
NOT_REBOOTED = 'not rebooted'  # it answers with an uptime from before the reload
UNKNOWN = 'unknown'            # it answers, but doesn't say how long it has been up
BACK = 'back'


@dataclass(frozen=True)
class Look:
    state: str
    detail: str = ''  # in plain words: why it isn't back


def look(ip: str, since_reload: timedelta, went_down: bool) -> Look:
    """
    Looks at a Switch whose reload started `since_reload` ago, and that was seen unreachable
    since if `went_down`.
    """
    if ip == NO_MANAGEMENT_IP:
        return Look(DOWN, 'no management IP')
    try:
        uptime = lab_switch(ip).uptime()
    except LoginRefused as e:
        return Look(REFUSED, str(e))
    except LabSwitchError as e:
        return Look(DOWN, str(e))
    if uptime is not None and uptime < since_reload:
        return Look(BACK)
    if uptime is None:
        return Look(BACK) if went_down else Look(UNKNOWN, "couldn't tell whether it rebooted: show system "
                                                          "doesn't say how long it has been up")
    return Look(NOT_REBOOTED, f"the reload didn't happen: still up for {shown(uptime)}, "
                              f"{shown(since_reload)} after it started")


def shown(duration: timedelta) -> str:
    """A duration in plain words, without the units that are 0: '13 days 2 hours 5 seconds'."""
    return ' '.join(f"{n} {unit}{'s' if n != 1 else ''}" for n, unit in uptime_parts(duration) if n) or '0 seconds'
