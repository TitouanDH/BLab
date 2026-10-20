"""
Missing cables (see CONTEXT.md, #57): a Port whose link is down, on the Switch or on its UNI.

A UNI without a Link is disabled on its backbone (api.links), so the link of every Port of a
Switch nobody holds is down, cable or no cable. To tell, an Inspection of such a Switch
enables, for a few seconds, the disabled UNIs of its Ports that are down, reads the Switch's
links again, and disables them again (docs/adr/0009). The backbone has VLAN 1 disabled, so a
UNI enabled outside any SAP carries nothing meanwhile (10.69.144.130, 2026-10-20). A UNI is
disabled again under the SVLAN lock, and only while its Port still has no Link, so a Connect
that took the Port meanwhile keeps its UNI enabled.

On a reserved Switch nothing is enabled: only the Ports carrying a Link are judged, their
UNIs being enabled, and a Missing cable is only shown, never a failure. The Link worker shows
the same on the Link's Topology (api.reconcile) and on the Lab status page.

BLAB_MISSING_CABLE_MODE (settings): 'off' (the default) enables nothing, so a Switch nobody
holds isn't judged; 'report' looks, and only warns; 'enforce' fails the Inspection on them.
"""
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import groupby
from typing import Callable, Dict, List, Optional, Tuple

from django.conf import settings

from . import fake_devices, links
from .backbone import APIRequestError, backbone
from .lab_switch import port_order
from .models import Port, Switch

logger = logging.getLogger(__name__)

MISSING_CABLE = 'Missing cable: '  # how the reason for Missing cables starts
REPORTED_PREFIX = 'Missing cable (BLAB_MISSING_CABLE_MODE=report): '  # how the warning starts, in report mode
LINK_UP_WITHIN = 15.0  # seconds a link may take to come up once its UNI is enabled
LOOK_EVERY = 2.0       # seconds between two reads of the Switch's links meanwhile


def mode() -> str:
    return settings.BLAB_MISSING_CABLE_MODE


def describe(port: Port) -> str:
    return f"{port.port_switch} (UNI {port.port_backbone} on backbone {port.backbone})"


def shown_on(port: Port) -> str:
    """How a Missing cable on a Link starts, as its Topology shows it (api.reconcile.MissingCable)."""
    return f"{MISSING_CABLE}{port.port_switch} on {port.switch.model} ({port.switch.mngt_IP})"


@dataclass
class Found:
    missing: List[Port] = field(default_factory=list)  # the Ports whose link is down, in port order
    unreadable: List[str] = field(default_factory=list)  # what couldn't be read or done, so not judged
    warnings: List[str] = field(default_factory=list)    # what went wrong but didn't stop the check


def find(switch: Switch, states: Dict[str, str], reserved: bool,
         read_states: Callable[[], Optional[Dict[str, str]]]) -> Found:
    """
    The Missing cables of a Switch, given the link states its show interfaces gave. On a Switch
    nobody holds (unless BLAB_MISSING_CABLE_MODE is 'off'), the disabled UNIs of the Ports
    without a Link that are down are enabled while read_states() reads the links again (None:
    it couldn't), then disabled again. A Port show interfaces doesn't list is unreadable.
    """
    found = Found()
    ports = sorted(Port.objects.filter(switch=switch), key=lambda p: port_order(p.port_switch))
    found.unreadable = [f'show interfaces port {p.port_switch}' for p in ports if p.port_switch not in states]
    down = [p for p in ports if states.get(p.port_switch, 'up') != 'up']
    if reserved:
        # Only a Link's UNIs are enabled: any other Port of a held Switch is down whatever its cable
        found.missing = [p for p in down if p.svlan is not None]
        return found
    # A Link left on a Switch nobody holds is a teardown under way or a Ghost Link: the Link worker's
    down = [p for p in down if p.svlan is None]
    if mode() == 'off' or not down:
        return found
    enabled: List[Tuple[str, str]] = []
    try:
        looked = _enable_unis(down, enabled, found)
        if looked and enabled:
            down, looked = _wait_for_links(down, read_states, found)
    finally:
        _disable_again(enabled, found)
    if looked:
        found.missing = down
    return found


def _enable_unis(ports: List[Port], enabled: List[Tuple[str, str]], found: Found) -> bool:
    """Enables the disabled UNIs of the Ports, adding (backbone, UNI) of each to enabled. False if it failed."""
    for ip, group in groupby(sorted(ports, key=lambda p: p.backbone), key=lambda p: p.backbone):
        try:
            bb = backbone(ip)
            disabled = bb.disabled_unis()
            for port in group:
                if port.port_backbone in disabled:
                    bb.set_uni_admin_state(port.port_backbone, True)
                    enabled.append((ip, port.port_backbone))
        except APIRequestError as e:
            logger.warning("Missing cables: could not enable the UNIs on backbone %s: %s", ip, e)
            found.unreadable.append(f'backbone {ip}')
            return False
    if enabled:
        logger.info("Missing cables: enabled UNIs %s for a moment", ', '.join(f'{ip} {uni}' for ip, uni in enabled))
    return True


def watch_links(read_states: Callable[[], Optional[Dict[str, str]]],
                done: Callable[[Dict[str, str]], bool]) -> Optional[Dict[str, str]]:
    """
    Reads a Switch's links every LOOK_EVERY until done(states), for LINK_UP_WITHIN at most (at
    once against fake devices). The last read, or None once one fails. Also api.contribute's.
    """
    within, every = (0.0, 0.0) if fake_devices.devices_are_fake() else (LINK_UP_WITHIN, LOOK_EVERY)
    deadline = time.monotonic() + within
    while True:
        time.sleep(every)
        states = read_states()
        if states is None or done(states) or time.monotonic() >= deadline:
            return states


def _wait_for_links(down: List[Port], read_states: Callable[[], Optional[Dict[str, str]]],
                    found: Found) -> Tuple[List[Port], bool]:
    """The Ports still down once their links had time to come up; and False if the Switch couldn't be read meanwhile."""
    states = watch_links(read_states, lambda states: all(states.get(p.port_switch) == 'up' for p in down))
    if states is None:
        found.unreadable.append('show interfaces')
        return down, False
    return [p for p in down if states.get(p.port_switch) != 'up'], True


def _disable_again(enabled: List[Tuple[str, str]], found: Found) -> None:
    """
    Disables the UNIs enabled for the check, under the SVLAN lock and only while their Port
    still has no Link: a Connect made meanwhile enables them itself, after taking that lock.
    One that fails stays enabled, carrying nothing: the Link worker records it.
    """
    for ip, uni in enabled:
        try:
            with links.svlan_lock():
                if Port.objects.filter(backbone=ip, port_backbone=uni, svlan__isnull=False).exists():
                    logger.info("Missing cables: UNI %s on %s carries a Link meanwhile, left enabled", uni, ip)
                    continue
                backbone(ip).set_uni_admin_state(uni, False)
        except APIRequestError as e:
            logger.error("Missing cables: could not disable UNI %s on backbone %s again: %s", uni, ip, e)
            found.warnings.append(f"UNI {uni} on backbone {ip} was enabled to look for Missing cables "
                                  f"and couldn't be disabled again: {e}")


def recorded_on_links() -> Dict[int, List[str]]:
    """
    The Missing cables the Link worker last recorded on Links (api.reconcile), by the id of the
    Switch at that end, for the Lab status page.
    """
    found = defaultdict(list)
    for port in Port.objects.filter(ghost_reason__contains=MISSING_CABLE).select_related('switch'):
        if port.ghost:
            found[port.switch_id] += [line for line in port.ghost_reason.splitlines()
                                      if line.startswith(shown_on(port) + ' ')]
    return dict(found)
