"""
Reconcile (see CONTEXT.md): compares the Links and Port states the database records with
what the backbones hold, and lists every Drift between them.

reconcile() only reads. repair() fixes the drifts the database is the truth for: it builds
Ghost Links again and records the real admin state of the UNIs. Orphans and anything it
can't read are reported only; removing them is left to whoever knows what they are.
"""
import logging
from collections import defaultdict
from dataclasses import dataclass
from itertools import groupby
from typing import Collection, List, Optional

from django.utils import timezone

from . import links
from .backbone import APIRequestError, Service, backbone
from .links import Link
from .models import Port

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Drift:
    """Something reconcile found; backbone is empty when the finding is in the database alone."""
    backbone: str


@dataclass(frozen=True)
class Orphan(Drift):
    """Ethernet-service config for an SVLAN that no Port on this backbone records."""
    service: Service

    def __str__(self):
        s = self.service
        parts = [f"name {s.name}" if s.name else "", f"UNIs {', '.join(s.unis)}" if s.unis else ""]
        what = '; '.join(p for p in parts if p) or "SVLAN only, no Service"
        return f"{self.backbone}: orphan SVLAN {s.svlan} ({what})"


@dataclass(frozen=True)
class GhostLink(Drift):
    """A recorded Link that this backbone doesn't fully carry, or whose UNIs are disabled."""
    link: Link
    service: Optional[Service]
    disabled_unis: tuple = ()

    def __str__(self):
        if self.service is None:
            problem = "no Service on the backbone"
        elif self.disabled_unis:
            problem = f"UNIs disabled: {', '.join(self.disabled_unis)}"
        else:
            problem = f"incomplete Service: {self.service}"
        return f"{self.backbone}: ghost Link {self.link}, {problem}"

    def reason(self) -> str:
        """Why, in words a user of the Link understands."""
        lacking = f"This Link is not carried by backbone {self.backbone}"
        if self.service is None:
            return f"{lacking}: its Service there is missing."
        if self.disabled_unis:
            facing = [f"{p.port_switch} on {p.switch.model} ({p.switch.mngt_IP})" for p in self.link.ports
                      if p.backbone == self.backbone and p.port_backbone in self.disabled_unis]
            return f"{lacking}: the backbone port facing {' and '.join(facing)} is disabled."
        return f"{lacking}: its Service there is incomplete."


@dataclass(frozen=True)
class StatusDrift(Drift):
    """A UNI whose real admin state is not the one its Port records."""
    port: Port
    actual: str

    def __str__(self):
        return (f"{self.backbone}: UNI {self.port.port_backbone} is {self.actual} "
                f"but recorded {self.port.status} (port {self.port.id})")


@dataclass(frozen=True)
class NotALink(Drift):
    """An SVLAN held by other than two Ports."""
    link: Link

    def __str__(self):
        return f"SVLAN {self.link.svlan} is held by {len(self.link.ports)} ports, not 2: {self.link}"


@dataclass(frozen=True)
class Unreadable(Drift):
    """An ethernet-service line the parser does not understand, so it cannot be compared."""
    line: str

    def __str__(self):
        return f"{self.backbone}: line not understood: {self.line}"


@dataclass(frozen=True)
class Unreachable(Drift):
    """A backbone that could not be read, so it cannot be compared."""
    error: str

    def __str__(self):
        return f"{self.backbone}: could not be read: {self.error}"


def reconcile() -> List[Drift]:
    """Every Drift between the database and the backbones its Ports are cabled to. Only reads."""
    recorded = links.all_links()
    drifts: List[Drift] = [NotALink('', link) for link in recorded if len(link.ports) != 2]
    ports = sorted(Port.objects.all(), key=lambda p: (p.backbone, p.id))
    for ip, backbone_ports in groupby(ports, lambda p: p.backbone):
        try:
            drifts += _backbone_drifts(ip, list(backbone_ports), recorded)
        except APIRequestError as e:
            drifts.append(Unreachable(ip, str(e)))
    return drifts


def _backbone_drifts(ip: str, ports: List[Port], recorded: List[Link]) -> List[Drift]:
    bb = backbone(ip)
    services, unreadable = bb.read_services()
    disabled = bb.disabled_unis()
    here = {link.svlan: link for link in recorded if ip in link.unis_by_backbone()}

    drifts: List[Drift] = [Unreadable(ip, line) for line in unreadable]
    drifts += [Orphan(ip, service) for svlan, service in sorted(services.items())
               if svlan not in here and not service.is_removed()]
    for svlan, link in sorted(here.items()):
        unis = link.unis_by_backbone()[ip]
        service = services.get(svlan)
        disabled_unis = tuple(uni for uni in unis if uni in disabled)
        if service is None or not service.carries(unis) or disabled_unis:
            drifts.append(GhostLink(ip, link, service, disabled_unis))
    for port in ports:
        actual = 'DOWN' if port.port_backbone in disabled else 'UP'
        if port.status != actual:
            drifts.append(StatusDrift(ip, port, actual))
    return drifts


def repair(drifts: List[Drift]) -> List[str]:
    """
    Builds Ghost Links again and records the real admin state of UNIs. Returns one line per
    drift acted on, saying what happened. Other drifts are left alone.
    """
    outcomes = []
    # An SVLAN held by other than two Ports is no Link to build: it would bridge a stray port
    ghosts = {d.link.svlan: d.link for d in drifts if isinstance(d, GhostLink) and len(d.link.ports) == 2}
    touched = set()
    for link in ghosts.values():
        try:
            links.restore(link)
        except links.LinkError as e:
            logger.error("Repairing %s failed: %s", link, e)
            outcomes.append(f"Could not restore {link}: {e}")
            continue
        link.clear_ghost()
        outcomes.append(f"Restored {link}")
        # Restoring set the state of its own UNIs: what was read before is stale for them
        touched |= {p.id for p in link.ports}
    for drift in drifts:
        if isinstance(drift, StatusDrift) and drift.port.id not in touched:
            outcomes.append(record_status(drift))
    return outcomes


def record_status(drift: StatusDrift) -> str:
    Port.objects.filter(id=drift.port.id).update(status=drift.actual)
    return f"Recorded UNI {drift.port.port_backbone} on {drift.backbone} as {drift.actual}"


def record_ghost_links(drifts: List[Drift], ghosts_to_record: Collection[int]) -> None:
    """
    Records on each Link what this Reconcile found: a Ghost Link and why, or carried. Only the
    database is written. A Link on a backbone that could not be read keeps what was recorded,
    unless a backbone that was read finds it a Ghost Link. Only the Ghost Links whose SVLAN is in
    ghosts_to_record are recorded, so the caller can wait until one shows twice; the others are
    left as they were.
    """
    unreachable = {d.backbone for d in drifts if isinstance(d, Unreachable)}
    found = defaultdict(list)
    for drift in drifts:
        # An SVLAN held by other than two Ports is no Link: no Topology shows it
        if isinstance(drift, GhostLink) and len(drift.link.ports) == 2:
            found[drift.link].append(drift.reason())
    now = timezone.now()
    for link, reasons in found.items():
        if link.svlan in ghosts_to_record:
            link.record_ghost('\n'.join(reasons), now)
    ghost_svlans = {link.svlan for link in found}
    for link in links.recorded_ghosts():
        if link.svlan not in ghost_svlans and not unreachable & set(link.unis_by_backbone()):
            link.clear_ghost()
