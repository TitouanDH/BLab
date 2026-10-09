"""
Links between Ports (see CONTEXT.md): the one place that creates, finds and removes them.

A Link is recorded as two Ports sharing an SVLAN (Port.svlan); there is no Link table,
because production's code reads and writes Port.svlan on the same database (ADR 0002).
On the backbones, a Link is one Service per backbone it touches, holding that backbone's
UNIs. Trunks between backbones are configured by hand, not here.
"""
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import groupby
from typing import Callable, Dict, List, Optional, Set, Tuple

from django.db import connection, transaction
from django.db.models import F
from django.utils import timezone

from .backbone import APIRequestError, Service, backbone
from .models import Port

logger = logging.getLogger(__name__)

SVLAN_RANGE = range(1001, 4095)
SVLAN_LOCK = 0x424C4142  # pg advisory lock key ("BLAB"): one SVLAN allocation at a time
TEARDOWN_LOCK = 0x424C4154  # pg advisory lock key ("BLAT"), with the SVLAN: one teardown of a Link at a time
SVLAN_TRIES = 5  # SVLANs free in the database but taken on a backbone, skipped before giving up
# A Ghost Link not seen again for this long is no longer shown: whoever Reconciles now (production's
# older Link worker, say) doesn't record them, or a backbone it touches could not be read since
GHOST_SHOWN_FOR = timedelta(minutes=30)


NOT_GHOST = dict(ghost_svlan=None, ghost_reason=None, ghost_seen_at=None)


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
        """Records the Ports as unlinked, which also ends any disconnect asked for."""
        self._update_ports(svlan=None, teardown_requested_at=None, teardown_svlan=None, teardown_error=None,
                           **NOT_GHOST)

    def request_teardown(self) -> None:
        """Asks the Link worker to tear the Link down; asking again clears a past failure."""
        self._update_ports(teardown_requested_at=timezone.now(), teardown_svlan=self.svlan, teardown_error=None)

    def record_teardown_failure(self, error: Exception) -> None:
        self._update_ports(teardown_error=str(error))

    @property
    def teardown_requested_at(self) -> Optional[datetime]:
        return max((p.teardown_requested_at for p in self.ports if p.teardown_pending), default=None)

    @property
    def teardown_error(self) -> Optional[str]:
        return next((p.teardown_error for p in self.ports if p.teardown_pending and p.teardown_error), None)

    def record_ghost(self, reason: str, seen_at: datetime) -> None:
        """Records that the Reconcile run at seen_at found it a Ghost Link, and why."""
        # Only while the Ports still hold its SVLAN: it may have been torn down since it was read
        fields = dict(ghost_svlan=self.svlan, ghost_reason=reason, ghost_seen_at=seen_at)
        Port.objects.filter(id__in=[p.id for p in self.ports], svlan=self.svlan).update(**fields)
        for port in self.ports:
            for field, value in fields.items():
                setattr(port, field, value)

    def clear_ghost(self) -> None:
        """Records that it is carried again: no longer a Ghost Link."""
        self._update_ports(**NOT_GHOST)

    @property
    def ghost_reason(self) -> Optional[str]:
        """Why a recent Reconcile found it a Ghost Link, one line per backbone; None if none did."""
        return next((p.ghost_reason for p in self._ghost_ports()), None)

    @property
    def ghost_seen_at(self) -> Optional[datetime]:
        return max((p.ghost_seen_at for p in self._ghost_ports()), default=None)

    def _ghost_ports(self) -> List[Port]:
        recent = timezone.now() - GHOST_SHOWN_FOR
        return [p for p in self.ports if p.ghost and p.ghost_seen_at and p.ghost_seen_at >= recent]

    def is_shown(self) -> bool:
        """Whether its Topology shows it: not while being disconnected, unless that keeps failing."""
        return self.teardown_requested_at is None or self.teardown_error is not None

    def _update_ports(self, **fields) -> None:
        Port.objects.filter(id__in=[p.id for p in self.ports]).update(**fields)
        for port in self.ports:
            for field, value in fields.items():
                setattr(port, field, value)

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
    A Release and the Link worker may both get to the same Link: one at a time, and the
    second finds it gone and does nothing, even if its SVLAN went to a new Link meanwhile.
    """
    with _teardown_lock(link.svlan):
        recorded = set(Port.objects.filter(svlan=link.svlan).values_list('id', flat=True))
        if recorded != {p.id for p in link.ports}:
            logger.info("%s was already torn down or changed meanwhile: left alone", link)
            return
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


def request_disconnect(link: Link) -> None:
    """
    Records that the Link is to be torn down, and returns at once: the Link worker does it
    (api.link_worker). Asking again for a Link whose teardown failed makes it try again now.
    """
    link.request_teardown()
    logger.info("Disconnect requested for %s", link)


def requested_teardowns() -> List[Link]:
    """Every Link a disconnect was asked for and not done yet, oldest request first."""
    svlans = (Port.objects.filter(svlan__isnull=False, teardown_requested_at__isnull=False,
                                  teardown_svlan=F('svlan'))
              .values_list('svlan', flat=True).distinct())
    return sorted((_link(svlan) for svlan in set(svlans)), key=lambda link: link.teardown_requested_at)


def recorded_ghosts() -> List[Link]:
    """Every Link the last Reconcile recorded as a Ghost Link."""
    svlans = (Port.objects.filter(svlan__isnull=False, ghost_svlan=F('svlan'))
              .values_list('svlan', flat=True).distinct())
    return [_link(svlan) for svlan in sorted(set(svlans))]


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


@contextmanager
def _teardown_lock(svlan: int):
    if connection.vendor != 'postgresql':
        yield
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s, %s)", [TEARDOWN_LOCK, svlan])
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [TEARDOWN_LOCK, svlan])


@contextmanager
def svlan_lock():
    """
    A transaction holding the SVLAN lock: while it lasts, no other process (pre-prod or
    production) hands out an SVLAN or decides one is free.
    """
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [SVLAN_LOCK])
        yield


def _allocate_svlan(port_a: Port, port_b: Port, skipped: Set[int] = frozenset()) -> int:
    with svlan_lock():
        for port in (port_a, port_b):
            port.refresh_from_db(fields=['svlan', 'teardown_requested_at', 'teardown_svlan'])
            if port.teardown_pending:
                raise PortsBusy("One or both ports are still being disconnected. Try again in a moment.")
            if port.svlan is not None:
                raise PortsBusy("One or both ports are already connected. Disconnect them first.")
        first, last = SVLAN_RANGE[0], SVLAN_RANGE[-1]
        taken = set(Port.objects.filter(svlan__range=(first, last)).values_list('svlan', flat=True))
        svlan = next((n for n in SVLAN_RANGE if n not in taken and n not in skipped), None)
        if svlan is None:
            raise NoFreeSvlan(f"All SVLANs from {first} to {last} are in use.")
        Port.objects.filter(id__in=[port_a.id, port_b.id]).update(svlan=svlan, **NOT_GHOST)
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
