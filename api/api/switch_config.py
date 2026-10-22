"""
A Switch's Config (#44): what BLab shows of a Switch's config to the members of its Project and
to admins. Read-only: BLab only reads the Switch, with show commands, as admin.

Each config shown has a source:
- `live`: the running config (show configuration snapshot), for whoever may work on the Switch
  now (api.topology.may_work_on) and for admins. BLab starts at most one live read per Switch
  every LIVE_READ_INTERVAL, whoever asks; meanwhile it gives the last one, with when it was read.
  So a page reloaded in a loop, or every member opening it at once, costs the Switch one SSH
  login per interval.
- `golden`: the Golden init of the Switch's model (api.clean_state), what a Cleanup brings it back
  to. It is the lab's template, so whoever sees any of the Switch's configs sees it too.
- `snapshot-<id>`: working/vcboot.cfg as that Snapshot holds it (api.snapshots), and its
  vcsetup.cfg, for the members of the Snapshot's Project, even once the Switch has left it, and
  for admins. Only with Projects on.

Any two compare (diff()): lines only in one are added or removed, and a line both hold but in
another place is no difference, since AOS writes its config in its own order (the Golden init
is written by hand in another).
"""
import difflib
import logging
from collections import Counter
from datetime import timedelta
from typing import Dict, List, Optional

from django.contrib.auth.models import User
from django.utils import timezone

from . import clean_state, projects, topology
from .lab_switch import LabSwitchError, lab_switch
from .models import NO_MANAGEMENT_IP, LiveConfig, Reservation, SnapshotSwitch, Switch

logger = logging.getLogger(__name__)

LIVE = 'live'
GOLDEN = 'golden'
SNAPSHOT = 'snapshot'  # the kind of a Snapshot's source, whose key is SNAPSHOT_PREFIX + its id
SNAPSHOT_PREFIX = 'snapshot-'
LIVE_READ_INTERVAL = timedelta(seconds=30)  # at most one live read per Switch this often


class ConfigError(Exception):
    """A Config refused. The message is meant for the user."""
    status = 404


class NotAllowed(ConfigError):
    status = 403


def may_work_on(user: User, switch: Switch) -> bool:
    reservation = Reservation.objects.filter(switch=switch).first()
    return reservation is not None and topology.may_work_on(user, reservation)


def project_of(user: User, switch: Switch) -> Optional[Dict]:
    """The Project the Switch is reserved into, {"id", "name"}, if user is one of its members: the page links back to it."""
    reservation = Reservation.objects.filter(switch=switch, project__isnull=False).select_related('project').first()
    if not projects.enabled() or reservation is None or not projects.is_member(user, reservation.project):
        return None
    return {'id': reservation.project_id, 'name': reservation.project.name}


def snapshot_entries(user: User, switch: Switch) -> List[SnapshotSwitch]:
    """The Snapshots holding this Switch that user may see, newest first: those of their Projects; every one for an admin."""
    if not projects.enabled():
        return []
    entries = SnapshotSwitch.objects.filter(switch=switch)
    if not user.is_staff:
        entries = entries.filter(snapshot__project__members__user=user)
    return list(entries.select_related('snapshot__project', 'snapshot__taken_by')
                .order_by('-snapshot__taken_at', '-snapshot__id').distinct())


class Sources:
    """The configs of one Switch that one user may see."""

    def __init__(self, user: User, switch: Switch):
        self.user, self.switch = user, switch
        works_on = user.is_staff or may_work_on(user, switch)
        self.snapshots = {f'{SNAPSHOT_PREFIX}{e.snapshot_id}': e for e in snapshot_entries(user, switch)}
        if not works_on and not self.snapshots:
            raise NotAllowed("Only the members of this Switch's Project, and admins, may see its config.")
        self.live = works_on and switch.mngt_IP != NO_MANAGEMENT_IP
        self.golden = bool(switch.model)

    def keys(self) -> List[str]:
        return [key for key, shown in ((LIVE, self.live), (GOLDEN, self.golden)) if shown] + list(self.snapshots)

    def default(self) -> Optional[str]:
        """The live config if user may see it, else the newest Snapshot's, else the Golden init."""
        if self.live:
            return LIVE
        return next(iter(self.snapshots), GOLDEN if self.golden else None)

    def listing(self) -> List[Dict]:
        """Each source, as a picker shows it."""
        found = []
        if self.live:
            found.append({'key': LIVE, 'kind': LIVE, 'label': 'Live'})
        if self.golden:
            found.append({'key': GOLDEN, 'kind': GOLDEN, 'label': 'Golden init', 'model': self.switch.model})
        for key, entry in self.snapshots.items():
            snapshot = entry.snapshot
            found.append({'key': key, 'kind': SNAPSHOT, 'label': 'Snapshot', 'id': snapshot.id,
                          'taken_at': snapshot.taken_at, 'reason': snapshot.reason, 'note': snapshot.note,
                          'taken_by': snapshot.taken_by.username if snapshot.taken_by else None,
                          'project': {'id': snapshot.project_id, 'name': snapshot.project.name},
                          'aos_release': entry.aos_release, 'read_error': entry.read_error})
        return found

    def read(self, key: str) -> Dict:
        """One source's config: {"key", "kind", "config" (None if it couldn't be read), "vcsetup", "read_at", "error"}."""
        if key not in self.keys():
            raise ConfigError("No such config of this Switch among those you may see.")
        if key == LIVE:
            row = read_live(self.switch, self.user)
            return {'key': key, 'kind': LIVE, 'config': row.config, 'vcsetup': None, 'read_at': row.read_at,
                    'read_by': row.read_by.username if row.read_by else None, 'error': row.error,
                    'reading': row.config is None and row.error is None,
                    'next_read_at': row.started_at + LIVE_READ_INTERVAL}
        if key == GOLDEN:
            return {'key': key, 'kind': GOLDEN, 'config': clean_state.golden_init_config(self.switch.model),
                    'vcsetup': None, 'read_at': None, 'error': None}
        entry = self.snapshots[key]
        return {'key': key, 'kind': SNAPSHOT, 'config': entry.vcboot, 'vcsetup': entry.vcsetup,
                'read_at': entry.read_at, 'error': entry.read_error, 'aos_release': entry.aos_release}


def read_live(switch: Switch, user: User) -> LiveConfig:
    """
    The Switch's last live read, after starting a new one (inside the request: a few seconds)
    if none started within LIVE_READ_INTERVAL. A read that fails says why and counts as one.
    """
    now = timezone.now()
    row, created = LiveConfig.objects.get_or_create(switch=switch, defaults={'started_at': now})
    # Claimed in one UPDATE, so two requests at once never both read
    if created or LiveConfig.objects.filter(pk=row.pk, started_at__lte=now - LIVE_READ_INTERVAL).update(started_at=now):
        try:
            config, error = lab_switch(switch.mngt_IP).running_config(), None
        except LabSwitchError as e:
            config, error = None, str(e)
        except Exception as e:  # never leave the row looking like a read under way
            logger.exception("Live config of %s", switch.mngt_IP)
            config, error = None, f"BLab couldn't read it: {e}"
        LiveConfig.objects.filter(pk=row.pk).update(read_at=timezone.now(), read_by=user, config=config, error=error)
    return LiveConfig.objects.select_related('read_by').get(pk=row.pk)


def view(user: User, switch: Switch, show: Optional[str] = None, against: Optional[str] = None) -> Dict:
    """
    What the Config of a Switch shows user: its sources, the one shown (`show`, or the default),
    and, given `against`, that one and the diff from it to the one shown.
    """
    sources = Sources(user, switch)
    key = show or sources.default()
    shown = sources.read(key) if key else None  # None: a Switch with neither model, management IP nor Snapshot
    other = sources.read(against) if against else None
    compared = None
    if shown and other and shown['config'] is not None and other['config'] is not None:
        compared = diff(other['config'], shown['config'])
    return {'switch': {'id': switch.id, 'model': switch.model, 'mngt_IP': switch.mngt_IP, 'project': project_of(user, switch)},
            'sources': sources.listing(), 'shown': shown, 'against': other, 'diff': compared,
            'live_read_interval': int(LIVE_READ_INTERVAL.total_seconds())}


def diff(old: str, new: str) -> Dict:
    """
    How `new` differs from `old`, line by line, in new's order, comments, blank lines and
    trailing spaces aside (clean_state.config_lines): {"rows": [{"op": " ", "+" or "-", "line"}],
    "added", "removed", "reordered"}. A line removed in one place and added in another is
    shown unchanged where new has it; "reordered" says the lines are otherwise the same but in
    another order.
    """
    a, b = clean_state.config_lines(old), clean_state.config_lines(new)
    rows = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == 'equal':
            rows += [{'op': ' ', 'line': line} for line in b[j1:j2]]
            continue
        rows += [{'op': '-', 'line': line} for line in a[i1:i2]]
        rows += [{'op': '+', 'line': line} for line in b[j1:j2]]
    moved = Counter(r['line'] for r in rows if r['op'] == '-') & Counter(r['line'] for r in rows if r['op'] == '+')
    if moved:
        drop, keep = Counter(moved), Counter(moved)
        kept = []
        for row in rows:
            if row['op'] == '-' and drop[row['line']] > 0:
                drop[row['line']] -= 1
                continue
            if row['op'] == '+' and keep[row['line']] > 0:
                keep[row['line']] -= 1
                row = {'op': ' ', 'line': row['line']}
            kept.append(row)
        rows = kept
    added = sum(r['op'] == '+' for r in rows)
    removed = sum(r['op'] == '-' for r in rows)
    return {'rows': rows, 'added': added, 'removed': removed, 'reordered': not added and not removed and a != b}
