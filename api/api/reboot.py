"""
Whether a Switch is back from the reload a Cleanup (api.switch_worker) or a Restore
(api.restore) started. Only reads.

It counts as back only once it has really rebooted and AOS is ready:
- rebooted: its uptime (show system) is shorter than the time since the reload started, or it
  was seen unreachable since. AOS 8.10 "verifies and copies images" for minutes before it
  goes down, and answers with its old state meanwhile: on 10.69.144.132 (8.10.86.R04,
  2026-10-12) it still had its old uptime 2 minutes after the reload (#50).
- ready: show interfaces reads. For a while after it first answers, AOS refuses some commands
  with "ERROR: system has not reached the ready state, please try again later" (#51).
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Set, Tuple

from .inspection import parse_link_states
from .lab_switch import LabSwitchError, LoginRefused, lab_switch, uptime_parts
from .models import NO_MANAGEMENT_IP, Switch

logger = logging.getLogger(__name__)

# What a look at a reloading Switch finds
DOWN = 'down'                  # it doesn't answer: going down, or not up yet
REFUSED = 'refused'            # it answers, but refuses BLab's login
NOT_REBOOTED = 'not rebooted'  # it answers with an uptime from before the reload
UNKNOWN = 'unknown'            # it answers, but doesn't say how long it has been up
NOT_READY = 'not ready'        # it rebooted, but AOS doesn't answer show interfaces yet
BACK = 'back'
GIVES_A_REASON = (NOT_REBOOTED, UNKNOWN, NOT_READY)  # what an Inspection made then wouldn't say itself


@dataclass(frozen=True)
class Look:
    state: str
    detail: str = ''  # in plain words: why it isn't back


def look(ip: str, since_reload: timedelta, went_down: bool) -> Look:
    """
    Looks at a Switch whose reload started `since_reload` ago, and that was seen unreachable
    since if `went_down`: whether it rebooted, then whether AOS is ready.
    """
    if ip == NO_MANAGEMENT_IP:
        return Look(DOWN, 'no management IP')
    device = lab_switch(ip)
    try:
        uptime = device.uptime()
        if uptime is None and not went_down:
            return Look(UNKNOWN, "couldn't tell whether it rebooted: show system doesn't say how long it has "
                                 "been up")
        if uptime is not None and uptime >= since_reload:
            return Look(NOT_REBOOTED, f"the reload didn't happen: still up for {shown(uptime)}, "
                                      f"{shown(since_reload)} after it started")
        answer = device.show('show interfaces')
    except LoginRefused as e:
        return Look(REFUSED, str(e))
    except LabSwitchError as e:
        return Look(DOWN, str(e))
    if answer.status != 0 or parse_link_states(answer.output) is None:
        answered = (answer.error or answer.output).strip()[:80] or f'nothing (exit status {answer.status})'
        return Look(NOT_READY, f"AOS wasn't ready: show interfaces answered {answered}")
    return Look(BACK)


class Watch:
    """
    Looks at reloading Switches, and remembers which were seen unreachable since their reload.
    That memory is the worker's only: a restart, or the lock passing to the other stage's
    worker, forgets it, and the uptime then decides alone (docs/adr/0005).
    """

    def __init__(self):
        self.went_down: Set[Tuple[int, datetime]] = set()  # (Switch id, when its reload started)

    def look(self, switch: Switch, reloaded_at: datetime, now: datetime) -> Look:
        """Looks at a Switch whose reload started at reloaded_at (api.reboot.look)."""
        reload = (switch.id, reloaded_at)
        seen = look(switch.mngt_IP, now - reloaded_at, reload in self.went_down)
        if seen.state == DOWN:
            self.went_down.add(reload)
        return seen

    def forget(self, switch: Switch, reloaded_at: datetime) -> None:
        """Once the Switch is done with this reload."""
        self.went_down.discard((switch.id, reloaded_at))


def shown(duration: timedelta) -> str:
    """A duration in plain words, without the units that are 0: '13 days 2 hours 5 seconds'."""
    return ' '.join(f"{n} {unit}{'s' if n != 1 else ''}" for n, unit in uptime_parts(duration) if n) or '0 seconds'
