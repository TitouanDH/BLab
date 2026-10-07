"""
Links between Ports (see CONTEXT.md): the one place that creates, finds and removes them.

A Link is recorded as two Ports sharing an SVLAN (Port.svlan); there is no Link table,
because production's code reads and writes Port.svlan on the same database (ADR 0002).
On the backbones, a Link is one Service per backbone it touches, holding that backbone's
UNIs. Trunks between backbones are configured by hand, not here.
"""
import logging
import time
from dataclasses import dataclass
from itertools import groupby
from typing import Callable, Dict, List, Optional, Set, Tuple

from django.db import connection, transaction

from .backbone import APIRequestError, Service, backbone
from .models import Port

logger = logging.getLogger(__name__)

SVLAN_RANGE = range(1001, 4095)
SVLAN_LOCK = 0x424C4142  # pg advisory lock key ("BLAB"): one SVLAN allocation at a time
SVLAN_TRIES = 5  # SVLANs free in the database but taken on a backbone, skipped before giving up


class LinkError(Exception):
    """A Link operation was refused or failed. The message is meant for the user."""


class SamePort(LinkError):
    pass


class PortsBusy(LinkError):
    pass


class NotLinked(LinkError):
    pass


class NoFreeSvlan(LinkError):
    pass


class BackboneFailure(LinkError):
    pass


@dataclass(frozen=True)
class Link:
    svlan: int
    ports: Tuple[Port, ...]  # two Ports; any other count is an inconsistency, kept so it can be removed

    def unis_by_backbone(self) -> Dict[str, List[str]]:
        ports = sorted(self.ports, key=lambda p: p.backbone)
        return {ip: [p.port_backbone for p in group] for ip, group in groupby(ports, lambda p: p.backbone)}

    def forget(self) -> None:
        """Records the Ports as unlinked."""
        Port.objects.filter(id__in=[p.id for p in self.ports]).update(svlan=None)
        for port in self.ports:
            port.svlan = None

    def __str__(self) -> str:
        return f"SVLAN {self.svlan} ({', '.join(f'{p.backbone}:{p.port_backbone}' for p in self.ports)})"


def service_name(svlan: int) -> str:
    return f"blab_{svlan}"


def connect(port_a: Port, port_b: Port) -> Link:
    """
    Wires two Ports together. If anything fails, whatever was configured is removed
    again and the Ports are left unlinked.
    """
    if port_a.id == port_b.id:
        raise SamePort("A port can't be connected to itself.")
    started = time.monotonic()
    link, before = _claim_free_svlan(port_a, port_b)
    name = service_name(link.svlan)
    try:
        for ip, unis in link.unis_by_backbone().items():
            backbone(ip).configure_service(link.svlan, name, unis, existing=before[ip])
        _bring_up(link, lambda service, unis: service.is_complete(name, unis))
    except Exception as e:
        logger.error("Connecting %s failed after %.1fs, undoing it: %s", link, time.monotonic() - started, e)
        _undo(link, before)
        if isinstance(e, (APIRequestError, BackboneFailure)):
            raise BackboneFailure(f"Ports failed to connect: {e}") from e
        raise
    logger.info("Connected %s in %.1fs", link, time.monotonic() - started)
    return link


def disconnect(link: Link) -> None:
    """
    Removes a Link from every backbone it touches, then forgets it. Safe to retry:
    if it fails halfway, the Link stays recorded and the next attempt finishes the job.
    """
    try:
        _set_admin_state(link, False)
        for ip in link.unis_by_backbone():
            backbone(ip).remove_service(link.svlan)
        for ip in link.unis_by_backbone():
            if not backbone(ip).wait_for_service(link.svlan, lambda s: s is None or s.is_removed()):
                raise BackboneFailure(f"Backbone {ip} still shows the service for SVLAN {link.svlan}.")
    except (APIRequestError, BackboneFailure) as e:
        logger.error("Disconnecting %s failed: %s", link, e)
        raise BackboneFailure(f"Ports failed to disconnect: {e}") from e
    link.forget()
    logger.info("Disconnected %s", link)


def restore(link: Link) -> None:
    """
    Builds a recorded Link again wherever a backbone lacks part of it, keeping the name its
    Service already has. Touches nothing if a backbone carries other UNIs on that SVLAN.
    """
    if len(link.ports) != 2:
        raise BackboneFailure(f"{link} is held by {len(link.ports)} ports, not 2: not restoring it.")
    by_backbone = link.unis_by_backbone()
    try:
        services = {ip: backbone(ip).read_service(link.svlan) for ip in by_backbone}
        for ip, service in services.items():
            foreign = set(service.unis) - set(by_backbone[ip]) if service else set()
            if foreign:
                raise BackboneFailure(f"Backbone {ip} carries other UNIs on SVLAN {link.svlan}: "
                                      f"{', '.join(sorted(foreign))}. Not restoring {link}.")
        for ip, unis in by_backbone.items():
            name = (services[ip] and services[ip].name) or service_name(link.svlan)
            backbone(ip).configure_service(link.svlan, name, unis, existing=services[ip])
        _bring_up(link, Service.carries)
    except APIRequestError as e:
        raise BackboneFailure(f"Restoring {link} failed: {e}") from e
    logger.info("Restored %s", link)


def link_between(port_a: Port, port_b: Port) -> Link:
    if port_a.svlan is None or port_a.svlan != port_b.svlan or port_a.id == port_b.id:
        raise NotLinked("These ports are not connected to each other.")
    return _link(port_a.svlan)


def links_for(switches) -> List[Link]:
    """Every Link with at least one end on these switches."""
    svlans = (Port.objects.filter(switch__in=switches, svlan__isnull=False)
              .values_list('svlan', flat=True).distinct())
    return [_link(svlan) for svlan in sorted(svlans)]


def all_links() -> List[Link]:
    """Every Link recorded, including SVLANs held by other than two Ports."""
    svlans = Port.objects.filter(svlan__isnull=False).values_list('svlan', flat=True).distinct()
    return [_link(svlan) for svlan in sorted(svlans)]


def disconnect_all(switch) -> List[LinkError]:
    """Disconnects every Link of a switch, carrying on past failures, which it returns."""
    errors = []
    for link in links_for([switch]):
        try:
            disconnect(link)
        except LinkError as e:
            errors.append(e)
    return errors


def _link(svlan: int) -> Link:
    ports = tuple(Port.objects.filter(svlan=svlan).order_by('id'))
    if len(ports) != 2:
        logger.warning("SVLAN %s is held by %s ports instead of 2: %s",
                       svlan, len(ports), [p.id for p in ports])
    return Link(svlan, ports)


def _claim_free_svlan(port_a: Port, port_b: Port) -> Tuple[Link, Dict[str, Optional[Service]]]:
    """
    Records the Ports on the lowest SVLAN free in the database and on every backbone the
    Link touches, with what each of those backbones already holds for it: nothing, a bare
    SVLAN, or leftovers of this very Link. An SVLAN carrying someone else's Service is
    skipped, never touched.
    """
    skipped: Set[int] = set()
    for _ in range(SVLAN_TRIES):
        link = Link(_allocate_svlan(port_a, port_b, skipped), (port_a, port_b))
        try:
            before = {ip: backbone(ip).read_service(link.svlan) for ip in link.unis_by_backbone()}
        except APIRequestError as e:
            link.forget()
            raise BackboneFailure(f"Ports failed to connect: {e}") from e
        except Exception:
            link.forget()
            raise
        name = service_name(link.svlan)
        if all(before[ip] is None or before[ip].can_carry(name, unis)
               for ip, unis in link.unis_by_backbone().items()):
            return link, before
        logger.warning("SVLAN %s is free in the database but carries another Service on a backbone: %s",
                       link.svlan, [s for s in before.values() if s is not None])
        link.forget()
        skipped.add(link.svlan)
    raise NoFreeSvlan(f"SVLANs {', '.join(map(str, sorted(skipped)))} are free in BLab but in use on the "
                      "backbone. An administrator needs to look at them.")


def _allocate_svlan(port_a: Port, port_b: Port, skipped: Set[int] = frozenset()) -> int:
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [SVLAN_LOCK])
        for port in (port_a, port_b):
            port.refresh_from_db(fields=['svlan'])
            if port.svlan is not None:
                raise PortsBusy("One or both ports are already connected. Disconnect them first.")
        first, last = SVLAN_RANGE[0], SVLAN_RANGE[-1]
        taken = set(Port.objects.filter(svlan__range=(first, last)).values_list('svlan', flat=True))
        svlan = next((n for n in SVLAN_RANGE if n not in taken and n not in skipped), None)
        if svlan is None:
            raise NoFreeSvlan(f"All SVLANs from {first} to {last} are in use.")
        Port.objects.filter(id__in=[port_a.id, port_b.id]).update(svlan=svlan)
    port_a.svlan = port_b.svlan = svlan
    return svlan


def _bring_up(link: Link, built: Callable[[Service, List[str]], bool]) -> None:
    """Enables the UNIs, then waits until every backbone shows its Service built() for them."""
    _set_admin_state(link, True)
    for ip, unis in link.unis_by_backbone().items():
        if not backbone(ip).wait_for_service(link.svlan, lambda s, unis=unis: s is not None and built(s, unis)):
            raise BackboneFailure(f"Backbone {ip} doesn't show the service for SVLAN {link.svlan}.")


def _set_admin_state(link: Link, enabled: bool) -> None:
    for port in link.ports:
        backbone(port.backbone).set_uni_admin_state(port.port_backbone, enabled)
        port.status = 'UP' if enabled else 'DOWN'
        port.save(update_fields=['status'])


def _undo(link: Link, before: Dict[str, Optional[Service]]) -> None:
    """
    Takes back what connect built, leaving what the backbones held before. Best effort:
    a failure here is logged, and the Ports are forgotten regardless.
    """
    try:
        _set_admin_state(link, False)
    except Exception as e:
        logger.error("Undoing %s, bringing UNIs down: %s", link, e)
    for ip, unis in link.unis_by_backbone().items():
        try:
            had_svlan = before[ip] is not None and before[ip].svlan_configured
            backbone(ip).unbuild_service(link.svlan, service_name(link.svlan), unis, keep_svlan=had_svlan)
        except Exception as e:
            logger.error("Undoing %s on backbone %s: %s", link, ip, e)
    link.forget()
