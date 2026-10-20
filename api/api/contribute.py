"""
Contribute a cable (see CONTEXT.md, Contribution; #58): a user adds a cable between a Switch
and a Backbone, which gives the Switch one more Port, guided and with no admin approval.

BLab proposes the cable: the Backbone the Switch already has Ports to (or one the user picks)
and its lowest free port, and the lowest unused front port of the Switch. Once the user says
it is plugged, BLab checks it is that cable before recording the Port. An unused Backbone
port is disabled, as a UNI without a Link is (api.links), so BLab enables the proposed one for
a moment: the Switch port must come up, and go down again once it is disabled again. That
proves the cable joins exactly those two ports, which reading LLDP would too, but LLDP is off
on every Backbone port, since a UNI must carry the users' own LLDP through (docs/adr/0009).

A free Backbone port is one BLab may safely enable for that moment: disabled, with no link,
named by no Port and nowhere in the VLAN config (a trunk to another Backbone is a tagged VLAN
member), and on a module that holds UNIs already. Only one check runs at a time, so two
cables plugged crosswise can't pass each other's check.

Allowed on a Switch nobody holds, or one the user may work on, but not while it is being
Cleaned up (it reloads). Never on a port that carries a Link: a proposed port is never a Port.
"""
import logging
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, List, Optional

from django.contrib.auth.models import User
from django.db import connection, transaction

from . import topology
from .backbone import APIRequestError, Backbone, backbone
from .inspection import FRONT_PANEL_PORT, MANAGEMENT_PORT, parse_link_states
from .lab_switch import LabSwitchError, lab_switch, port_order
from .missing_cables import watch_links
from .models import NO_MANAGEMENT_IP, PendingCleanup, Port, Reservation, Switch

logger = logging.getLogger(__name__)

CONTRIBUTION_LOCK = 0x424C4143  # pg advisory lock key ("BLAC"): one cable check at a time, across both stages
DISABLE_TRIES = 3  # times BLab tries to disable a Backbone port again before telling an admin


class Refused(Exception):
    """The Contribution can't go on: the message says why, in words the user acts on."""


@dataclass(frozen=True)
class Cable:
    """A cable between a Switch port and a port of a Backbone."""
    switch_port: str
    backbone: str
    backbone_port: str

    def __str__(self):
        return f"{self.switch_port} to port {self.backbone_port} of Backbone {self.backbone}"


@dataclass
class Proposal:
    switch_port: Optional[str]    # None: the Switch has no unused front port
    backbone: Optional[str]       # None: the user picks one of `backbones`
    backbone_port: Optional[str]  # None: that Backbone has no free port
    backbones: List[str]          # every Backbone BLab knows (those its Ports name)
    problem: Optional[str] = None  # why there is no complete proposal

    def as_dict(self) -> dict:
        return {'switch_port': self.switch_port, 'backbone': self.backbone, 'backbone_port': self.backbone_port,
                'backbones': self.backbones, 'problem': self.problem}


def why_not(user: User, switch: Switch) -> Optional[str]:
    """Why user may not contribute a cable to this Switch now, or None."""
    if switch.mngt_IP == NO_MANAGEMENT_IP:
        return "BLab can't reach this Switch: it has no management IP."
    if PendingCleanup.objects.filter(switch=switch).exists():
        return "This Switch is being Cleaned up. Try again once it is done."
    reservation = Reservation.objects.filter(switch=switch).first()
    if reservation is not None and not topology.may_work_on(user, reservation):
        return f"This Switch is reserved by {reservation.user.username}: only who may work on it may add a cable."
    return None


def contributable(user: User) -> List[Switch]:
    """The Switches user may contribute a cable to now, by model and IP."""
    return [s for s in Switch.objects.order_by('model', 'mngt_IP') if why_not(user, s) is None]


def backbones() -> List[str]:
    """The Backbones BLab knows: those its Ports are cabled to."""
    return sorted(set(Port.objects.values_list('backbone', flat=True)))


def _known(backbone_ip: str) -> str:
    if backbone_ip not in backbones():
        raise Refused(f"{backbone_ip} is not a Backbone BLab knows.")
    return backbone_ip


def propose(switch: Switch, backbone_ip: Optional[str] = None) -> Proposal:
    """The cable to plug. Reads the Switch's links and the Backbone's ports. Raises Refused if it can't."""
    known = backbones()
    if backbone_ip is None:
        here = Counter(Port.objects.filter(switch=switch).values_list('backbone', flat=True))
        backbone_ip = here.most_common(1)[0][0] if here else (known[0] if len(known) == 1 else None)
    else:
        _known(backbone_ip)
    proposal = Proposal(min(free_switch_ports(switch, _read_links(switch)), key=port_order, default=None),
                        backbone_ip, None, known)
    if backbone_ip is not None:
        proposal.backbone_port = min(free_backbone_ports(backbone_ip), key=port_order, default=None)
    if proposal.switch_port is None:
        proposal.problem = (f"{switch.model} {switch.mngt_IP} has no unused front port: every one has its link up, "
                            "is a Port already, or is cabled for good.")
    elif backbone_ip is None:
        proposal.problem = "Pick the Backbone you will plug the cable into."
    elif proposal.backbone_port is None:
        proposal.problem = f"Backbone {backbone_ip} has no free port. Pick another Backbone."
    return proposal


def free_switch_ports(switch: Switch, states: Dict[str, str]) -> List[str]:
    """The front ports whose link is down, that no Port records and an admin hasn't marked as cabled for good."""
    taken = switch.ports_cabled_on_purpose()
    return [port for port, state in states.items()
            if state != 'up' and port not in taken and FRONT_PANEL_PORT.fullmatch(port)
            and not MANAGEMENT_PORT.fullmatch(port)]


def free_backbone_ports(backbone_ip: str) -> List[str]:
    """The ports of the Backbone BLab may enable for a moment to check a cable (see the module docstring)."""
    try:
        bb = backbone(backbone_ip)
        disabled, up, configured = bb.disabled_unis(), bb.links_up(), bb.ports_in_vlan_config()
    except APIRequestError as e:
        logger.warning("Contribute: could not read backbone %s: %s", backbone_ip, e)
        raise Refused(f"BLab couldn't read Backbone {backbone_ip}. Try again in a moment.")
    unis = set(Port.objects.filter(backbone=backbone_ip).values_list('port_backbone', flat=True))
    modules = {_module(uni) for uni in unis}
    return [port for port, is_up in up.items()
            if port in disabled and not is_up and port not in unis and port not in configured
            and _module(port) in modules]


def _module(port: str) -> str:
    """'1/3' for '1/3/40': chassis and slot."""
    return port.rpartition('/')[0]


@contextmanager
def _one_check_at_a_time():
    """A transaction holding the Contribution lock: two checks at once could pass each other's cables."""
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [CONTRIBUTION_LOCK])
        yield


def check_and_record(user: User, switch: Switch, cable: Cable) -> Port:
    """
    Checks that the cable the user plugged joins those two ports, and records the Port. Raises
    Refused, saying what is wrong, when it isn't so or can't be checked.
    """
    _known(cable.backbone)
    with _one_check_at_a_time():
        _check_allowed(user, switch, cable)
        _check_plugged(switch, cable)
        refusal = why_not(user, switch)  # a Reserve or a Cleanup may have started meanwhile
        if refusal:
            raise Refused(refusal)
        port = Port.objects.create(switch=switch, port_switch=cable.switch_port, backbone=cable.backbone,
                                   port_backbone=cable.backbone_port, status='DOWN')
    logger.info("Contribution by %s: cable %s %s recorded (Port %s)", user.username, switch.mngt_IP, cable, port.id)
    return port


def _check_allowed(user: User, switch: Switch, cable: Cable) -> None:
    refusal = why_not(user, switch)
    if refusal:
        raise Refused(refusal)
    if (cable.switch_port in switch.ports_cabled_on_purpose() or not FRONT_PANEL_PORT.fullmatch(cable.switch_port)
            or MANAGEMENT_PORT.fullmatch(cable.switch_port)):
        raise Refused(f"{cable.switch_port} on {switch.mngt_IP} can't take a new cable: it is a Port already, "
                      "cabled for good, or not a front port.")
    if cable.backbone_port not in free_backbone_ports(cable.backbone):
        raise Refused(f"Port {cable.backbone_port} of Backbone {cable.backbone} is not free: it is in use, a trunk, "
                      "or already cabled. Plug the cable as proposed.")


def _check_plugged(switch: Switch, cable: Cable) -> None:
    """Enables the Backbone port: the Switch port must come up; disables it: it must go down."""
    before = _read_links(switch)
    if before.get(cable.switch_port) == 'up':
        raise Refused(f"{cable.switch_port} on {switch.mngt_IP} has its link up before BLab enabled the Backbone "
                      "port: it is cabled to something else.")
    bb = backbone(cable.backbone)
    try:
        bb.set_uni_admin_state(cable.backbone_port, True)
        try:
            after = watch_links(lambda: _links(switch), lambda states: states.get(cable.switch_port) == 'up')
            backbone_up = bb.links_up().get(cable.backbone_port)
        finally:
            _disable_again(bb, cable)
    except APIRequestError as e:
        logger.warning("Contribute: backbone %s failed: %s", cable.backbone, e)
        raise Refused(f"BLab couldn't drive Backbone {cable.backbone}. Try again in a moment.")
    if after is None:
        raise Refused(_unreadable(switch))
    if after.get(cable.switch_port) != 'up':
        # Ports carrying a Link may come and go meanwhile: never the cable's
        taken = switch.ports_cabled_on_purpose()
        came_up = sorted((p for p, state in after.items()
                          if state == 'up' and before.get(p) != 'up' and p not in taken), key=port_order)
        if came_up:
            raise Refused(f"The link came up on {', '.join(came_up)}, not on {cable.switch_port}: move the cable to "
                          f"{cable.switch_port} on the Switch, then press Plugged again.")
        raise Refused(f"No link between {cable.switch_port} on {switch.mngt_IP} and port {cable.backbone_port} of "
                      f"Backbone {cable.backbone}. Check the cable is in at both ends (and the optics, if any), then "
                      "press Plugged again.")
    if not backbone_up:
        raise Refused(f"{cable.switch_port} came up, but port {cable.backbone_port} of Backbone {cable.backbone} "
                      "didn't: the cable is in another Backbone port. Move it, then press Plugged again.")
    gone = watch_links(lambda: _links(switch), lambda states: states.get(cable.switch_port) != 'up')
    if gone is None:
        raise Refused(_unreadable(switch))
    if gone.get(cable.switch_port) == 'up':
        raise Refused(f"{cable.switch_port} stayed up once port {cable.backbone_port} of Backbone {cable.backbone} "
                      "was disabled again: the cable goes elsewhere. Check where it is plugged, then press Plugged "
                      "again.")


def _disable_again(bb: Backbone, cable: Cable) -> None:
    """Disables the Backbone port again, trying a few times. One that stays enabled is for an admin to see to."""
    for attempt in range(1, DISABLE_TRIES + 1):
        try:
            bb.set_uni_admin_state(cable.backbone_port, False)
            return
        except APIRequestError as e:
            logger.error("Contribute: could not disable port %s of backbone %s again (attempt %s): %s",
                         cable.backbone_port, cable.backbone, attempt, e)
    raise Refused(f"BLab enabled port {cable.backbone_port} of Backbone {cable.backbone} to check the cable and "
                  "couldn't disable it again. Tell an admin, so they disable it by hand.")


def _links(switch: Switch) -> Optional[Dict[str, str]]:
    """Each port's link state on the Switch (show interfaces), or None if it can't be read."""
    try:
        answer = lab_switch(switch.mngt_IP).show('show interfaces')
    except LabSwitchError as e:
        logger.warning("Contribute: could not read %s: %s", switch.mngt_IP, e)
        return None
    return parse_link_states(answer.output) if answer.status == 0 else None


def _read_links(switch: Switch) -> Dict[str, str]:
    states = _links(switch)
    if states is None:
        raise Refused(_unreadable(switch))
    return states


def _unreadable(switch: Switch) -> str:
    return f"BLab couldn't read the ports of {switch.mngt_IP}. Try again in a moment."
