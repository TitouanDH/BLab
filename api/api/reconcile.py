"""
Reconcile (see CONTEXT.md): compares the Links and Port states the database records with
what the backbones hold, and lists every Drift between them.

reconcile() only reads. repair() fixes the drifts the database is the truth for: it builds
Ghost Links again and records the real admin state of the UNIs. Orphans and anything it
can't read are reported only; removing them is left to whoever knows what they are.
"""
import logging
from dataclasses import dataclass
from itertools import groupby
from typing import List, Optional

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
