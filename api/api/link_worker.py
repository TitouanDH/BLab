"""
The Link worker (docs/adr/0003): tears down the Links whose disconnect was asked for,
removes the Orphans that BLab made, and records the real admin state of the UNIs.

Production and pre-prod each run one (the `link_worker` compose service). They share the
database and the backbones, so a Postgres advisory lock lets only one of them work at a
time; the other waits as a standby and takes over if the first one stops.
"""
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Set

from django.contrib.auth.models import User
from django.db import connection

from . import links, reconcile
from .backbone import Service, backbone
from .models import Port

logger = logging.getLogger(__name__)

WORKER_LOCK = 0x424C4157  # pg advisory lock key ("BLAW"): one Link worker at a time
RETRY_DELAYS = (10, 30, 60, 300)  # seconds before trying a failed teardown again, then every 5 min
SHOWN_AFTER_FAILURES = 2  # a teardown failing this many times in a row shows its Link again, with why
ACTED_ON = (reconcile.Orphan, reconcile.StatusDrift)  # the drifts the worker fixes; others are logged


def holds_worker_lock() -> bool:
    """Whether this process is the Link worker, taking the lock if nobody holds it."""
    if connection.vendor != 'postgresql':
        return True
    with connection.cursor() as cursor:
        # Asking again for a lock this session holds would stack it: check first
        cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted"
                       " AND pid = pg_backend_pid() AND classid = 0 AND objid = %s AND objsubid = 1)",
                       [WORKER_LOCK])
        if cursor.fetchone()[0]:
            return True
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [WORKER_LOCK])
        return cursor.fetchone()[0]


def made_by_blab(service: Service, usernames: Set[str]) -> bool:
    """A bare SVLAN, or a Service named the way BLab names them: blab_<svlan> or <user>_<svlan>."""
    if service.is_bare_svlan():
        return True
    match = re.fullmatch(r"(.+)_(\d+)", service.name or "")
    return bool(match) and int(match.group(2)) == service.svlan and match.group(1) in usernames | {'blab'}


@dataclass
class Retry:
    request: Optional[datetime]  # the request it is for: asking again starts over
    failures: int = 0
    next_try: float = 0.0


class LinkWorker:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.retries: Dict[int, Retry] = {}  # by SVLAN
        self.suspects: Set[tuple] = set()  # the drifts seen at the last reconcile, by _drift_key

    def tear_down_requested(self) -> List[str]:
        """Tries every requested teardown that is due. A failed one waits longer each time."""
        outcomes = []
        requested = links.requested_teardowns()
        self.retries = {svlan: r for svlan, r in self.retries.items() if svlan in {l.svlan for l in requested}}
        for link in requested:
            retry = self.retries.get(link.svlan)
            if retry is None or retry.request != link.teardown_requested_at:
                retry = Retry(link.teardown_requested_at)
            if self.clock() < retry.next_try:
                continue
            try:
                links.disconnect(link)
            except links.LinkError as e:
                retry.failures += 1
                delay = RETRY_DELAYS[min(retry.failures, len(RETRY_DELAYS)) - 1]
                retry.next_try = self.clock() + delay
                self.retries[link.svlan] = retry
                if retry.failures >= SHOWN_AFTER_FAILURES:
                    link.record_teardown_failure(e)
                outcomes.append(f"Could not tear down {link}, trying again in {delay}s: {e}")
                continue
            self.retries.pop(link.svlan, None)
            outcomes.append(f"Tore down {link}")
        return outcomes

    def reconcile(self) -> List[str]:
        """
        Removes the Orphans BLab made and records the real UNI states, each only once it shows
        on two reconciles in a row: a Link being built or torn down meanwhile is never taken
        for one. Anything else reconcile finds is logged for an administrator (audit_links).
        """
        drifts = reconcile.reconcile()
        seen = {_drift_key(d): d for d in drifts if isinstance(d, ACTED_ON)}
        confirmed = [d for key, d in seen.items() if key in self.suspects]
        self.suspects = set(seen)
        for drift in drifts:
            if not isinstance(drift, ACTED_ON):
                logger.warning("Drift left for an administrator: %s", drift)

        outcomes = []
        usernames = set(User.objects.values_list('username', flat=True))
        for drift in confirmed:
            try:
                if isinstance(drift, reconcile.StatusDrift):
                    outcomes.append(reconcile.record_status(drift))
                elif made_by_blab(drift.service, usernames):
                    outcomes.append(remove_orphan(drift))
                else:
                    logger.warning("Orphan not named by BLab, left alone: %s", drift)
            except Exception as e:
                # One backbone failing must not stop the rest; the drift shows again next time
                logger.exception("Acting on %s failed", drift)
                outcomes.append(f"Could not act on {drift}: {e}")
        return outcomes


def remove_orphan(orphan: reconcile.Orphan) -> str:
    """
    Removes an Orphan if it is still one: no Port records its SVLAN and the backbone still holds
    exactly what was seen. The SVLAN lock keeps BLab's connect from taking that SVLAN meanwhile.
    """
    svlan = orphan.service.svlan
    with links.svlan_lock():
        if Port.objects.filter(svlan=svlan).exists():
            return f"SVLAN {svlan} on {orphan.backbone} is recorded again: left alone"
        bb = backbone(orphan.backbone)
        if bb.read_service(svlan) != orphan.service:
            return f"SVLAN {svlan} on {orphan.backbone} changed since it was seen: left alone"
        bb.remove_service(svlan)
    if Port.objects.filter(svlan=svlan).exists():
        # Only production's code from before the SVLAN lock can get here (docs/adr/0003)
        logger.error("SVLAN %s was taken by a connect while its Orphan was being removed on %s: "
                     "run manage.py audit_links --repair", svlan, orphan.backbone)
        return f"Removed {orphan}, but SVLAN {svlan} was taken meanwhile: run audit_links --repair"
    logger.info("Removed %s", orphan)
    return f"Removed {orphan}"


def _drift_key(drift: reconcile.Drift) -> tuple:
    if isinstance(drift, reconcile.StatusDrift):
        return ('status', drift.port.id, drift.actual)
    return ('orphan', drift.backbone, drift.service)
