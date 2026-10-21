"""
Restore onto the same Switches (see CONTEXT.md), with Projects on (BLAB_PROJECTS): sets a
Project's Switches up again from one of its Snapshots (api.snapshots).

plan() says, for each Switch of the Snapshot, whether it can be Restored: a free one is reserved
into the Project, one already in it has its config replaced, and a busy one (someone else's,
being Cleaned up, in Quarantine, Out of service) is left out, with when it is free if it is
reserved. It reads each Switch's AOS release, to warn when it differs from the Snapshot's.

start() reserves the chosen free Switches into the Project for the member who asks (the default
Reservation length), puts the Snapshot's Topology layout back, and records a Restore. The Switch
worker then carries it out (RestoreWorker): for each Switch, its Switch accounts and banner,
the Snapshot's config written into working/ and a reload from it, and a wait until it is back
(api.reboot: really rebooted, and AOS ready);
once every Switch is settled, the Snapshot's Links between the Restored Switches are built
again. Each step is recorded before the next, so a redeploy only delays a Restore.

The Switch worker still does one Switch at a time (docs/adr/0005); RestoreWorker.advance()
moves one Switch on and touches no other, so the worker can run several at once (#39).
"""
import logging
from datetime import datetime, timedelta
from typing import Callable, Dict, Iterable, List, Optional

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from . import links, projects, quarantine, reboot, snapshots, switch_accounts
from .lab_switch import LabSwitchError, SwitchConfig, lab_switch
from .models import (NO_MANAGEMENT_IP, Port, Project, Reservation, Restore, RestoreSwitch, Snapshot, SnapshotSwitch,
                     Switch, Tool)
from .projects import NotAllowed, ProjectError
from .reservations import DEFAULT_LENGTH

logger = logging.getLogger(__name__)

# What plan() finds for each Switch of a Snapshot
FREE = 'free'                # reserved into the Project if chosen
IN_PROJECT = 'in_project'    # already in the Project: its config is replaced if chosen
BUSY = 'busy'                # someone else's Reservation: left out, free on its end date
UNAVAILABLE = 'unavailable'  # being Cleaned up, in Quarantine or Out of service: left out
NO_CONFIG = 'no_config'      # the Snapshot couldn't read its config: left out
RESTORABLE = (FREE, IN_PROJECT)

# What became of each Link of the Snapshot, once the Switches are settled
BUILT = 'built'
ALREADY = 'already'      # its ports already carry it
LINK_LEFT_OUT = 'left_out'  # a Switch at one end wasn't Restored
LINK_FAILED = 'failed'


def _check(project: Project) -> None:
    if project.archived:
        raise ProjectError(f"{project.name} is archived: it can't be Restored into.")


def _state(entry: SnapshotSwitch, switch: Optional[Switch], project: Project, held: Dict[int, Reservation],
           unavailable: Dict[int, dict]) -> Dict:
    """What a Switch of the Snapshot is now: {'state', 'detail', 'free_on'}."""
    if switch is None:
        return {'state': NO_CONFIG, 'detail': 'No longer in the lab.', 'free_on': None}
    if entry.vcboot is None:
        return {'state': NO_CONFIG, 'detail': f"The Snapshot has no config for it: {entry.read_error}", 'free_on': None}
    if switch.mngt_IP == NO_MANAGEMENT_IP:
        return {'state': NO_CONFIG, 'detail': 'It has no management IP.', 'free_on': None}
    reservation = held.get(switch.id)
    if reservation is not None:
        if reservation.project_id == project.id:
            return {'state': IN_PROJECT, 'detail': 'In the Project: its config is replaced by the Snapshot\'s.',
                    'free_on': None}
        return {'state': BUSY, 'detail': 'Reserved by someone else.', 'free_on': reservation.end_date}
    if switch.id in unavailable:
        return {'state': UNAVAILABLE, 'detail': unavailable[switch.id]['reason'], 'free_on': None}
    return {'state': FREE, 'detail': 'Free: reserved into the Project.', 'free_on': None}


def plan(project: Project, snapshot: Snapshot) -> Dict:
    """
    What a Restore of this Snapshot would do, for its confirm dialog: {"switches": [{"id", "model",
    "state", "restorable", "detail", "free_on", "snapshot_release", "current_release", "release_differs"}],
    "links": <how many>}. Reads the AOS release of each Switch that can be Restored.
    """
    _check(project)
    entries = list(snapshot.switches.order_by('switch_id'))
    switches = Switch.objects.in_bulk([e.switch_id for e in entries])
    held = {r.switch_id: r for r in Reservation.objects.filter(switch_id__in=switches)}
    unavailable = quarantine.unavailable()
    planned = []
    for entry in entries:
        switch = switches.get(entry.switch_id)
        row = {'id': entry.switch_id, 'model': entry.model, 'mngt_IP': switch.mngt_IP if switch else None,
               'snapshot_release': entry.aos_release,
               'current_release': None, 'release_differs': False,
               **_state(entry, switch, project, held, unavailable)}
        row['restorable'] = row['state'] in RESTORABLE
        if row['restorable']:
            try:
                row['current_release'] = lab_switch(switch.mngt_IP).aos_release()
            except LabSwitchError as e:
                logger.warning("Restore plan: %s", e)
            row['release_differs'] = bool(row['current_release'] and entry.aos_release
                                          and row['current_release'] != entry.aos_release)
        planned.append(row)
    return {'switches': planned, 'links': len(snapshot.links)}


def start(project: Project, snapshot: Snapshot, user: User, chosen: Optional[Iterable] = None,
          now: Optional[datetime] = None) -> Restore:
    """
    Starts Restoring the Snapshot for user, a member: onto every Switch that can be Restored, or
    only those whose ids are `chosen`. Free ones are reserved into the Project in user's name.
    ProjectError if it can't start: archived, a Restore already under way, nothing to Restore;
    NotAllowed while a Quarantine names user, who can't make new Reservations.
    """
    now = now or timezone.now()
    chosen = None if chosen is None else {int(i) for i in chosen if str(i).isdigit()}
    refused = quarantine.refusal_while_named(user)
    if refused:
        raise NotAllowed(refused)
    with transaction.atomic():
        # Locked, as Reserve does: the Project isn't archived meanwhile, and two Restores don't start at once
        project = Project.objects.select_for_update().get(pk=project.pk)
        _check(project)
        if snapshots.busy_restoring(project):
            raise ProjectError("A Restore of this Project is already under way.")
        entries = list(snapshot.switches.order_by('switch_id'))
        # Locked in id order, as Reserve locks each: a Switch isn't reserved twice
        switches = {s.id: s for s in Switch.objects.select_for_update().filter(id__in=[e.switch_id for e in entries])
                    .order_by('id')}
        held = {r.switch_id: r for r in Reservation.objects.filter(switch_id__in=switches)}
        unavailable = quarantine.unavailable()
        restore = Restore.objects.create(project=project, snapshot=snapshot, started_by=user, started_at=now)
        rows, restored = [], []
        for entry in entries:
            found = _state(entry, switches.get(entry.switch_id), project, held, unavailable)
            row = RestoreSwitch(restore=restore, switch_id=entry.switch_id, state=RestoreSwitch.LEFT_OUT,
                                detail=found['detail'], free_on=found['free_on'], updated_at=now)
            if found['state'] in RESTORABLE and chosen is not None and entry.switch_id not in chosen:
                row.detail = 'Not chosen.'
            elif found['state'] in RESTORABLE:
                if found['state'] == FREE:
                    Reservation.objects.create(switch_id=entry.switch_id, user=user, project=project,
                                               end_date=now + DEFAULT_LENGTH)
                row.state, row.detail = RestoreSwitch.WAITING, 'Waiting for the Switch worker.'
                restored.append(entry.switch_id)
            rows.append(row)
        if not restored:
            raise ProjectError("None of the Snapshot's Switches can be Restored now.")
        RestoreSwitch.objects.bulk_create(rows)
        if any(held.get(i) is None for i in restored):
            projects.mark_used(user, project)
        positions = {int(i): (p['x'], p['y']) for i, p in snapshot.layout.items() if int(i) in restored}
        projects.save_layout(project, positions)
    logger.info("%s started Restoring Snapshot %s of the Project %s onto %s", user.username, snapshot.id,
                project.id, restored)
    return restore


def describe(restore: Restore) -> Dict:
    """A Restore and how far each of its Switches got, as the API gives it to the Project's members."""
    rows = list(restore.switches.select_related('switch').order_by('switch_id'))
    return {
        'id': restore.id,
        'snapshot': restore.snapshot_id,
        'started_by': restore.started_by.username if restore.started_by else None,
        'started_at': restore.started_at,
        'finished_at': restore.finished_at,
        'switches': [{'id': r.switch_id, 'model': r.switch.model, 'mngt_IP': r.switch.mngt_IP, 'state': r.state,
                      'detail': r.detail, 'warnings': r.warnings, 'free_on': r.free_on, 'updated_at': r.updated_at} for r in rows],
        'links': restore.links,
    }


def latest(project: Project) -> Optional[Restore]:
    return project.restores.select_related('started_by').first()


class RestoreWorker:
    """Carries Restores out, a step at a time, as the Switch worker's cycle calls work()."""

    def __init__(self, now: Callable[[], datetime], grace: timedelta, timeout: timedelta,
                 look_again_after: timedelta, reboots: reboot.Watch):
        self.now = now
        self.grace = grace
        self.timeout = timeout
        self.look_again_after = look_again_after
        self.reboots = reboots

    def work(self) -> List[str]:
        """Moves every Switch of a Restore that is due one step on, then finishes the Restores that are settled."""
        outcomes = []
        due = (RestoreSwitch.objects.filter(state__in=RestoreSwitch.UNDER_WAY, restore__finished_at__isnull=True)
               .select_related('switch', 'restore').order_by('restore__started_at', 'switch_id'))
        for row in due:
            try:
                outcome = self.advance(row)
            except Exception as e:
                # One broken Switch must not stop the others; it is tried again next cycle
                logger.exception("Restore of %s failed", row.switch.mngt_IP)
                outcome = f"Could not go on with the Restore of {row.switch.mngt_IP}: {e}"
            if outcome:
                outcomes.append(outcome)
        for restore in Restore.objects.filter(finished_at__isnull=True).order_by('started_at'):
            if not restore.switches.filter(state__in=RestoreSwitch.UNDER_WAY).exists():
                outcomes.append(self.finish(restore))
        return outcomes

    def advance(self, row: RestoreSwitch) -> Optional[str]:
        """Loads the config and reloads a waiting Switch, or looks whether a reloading one is back."""
        if row.state == RestoreSwitch.WAITING:
            return self.load(row)
        if self.now() < row.next_check_at:
            return None
        return self.check_back(row)

    def _record(self, row: RestoreSwitch, state: str, detail: str, **fields) -> str:
        """Saves how far the Switch got (its progress), and says so for the worker's log."""
        if row.state == RestoreSwitch.RELOADING and state != RestoreSwitch.RELOADING:
            self.reboots.forget(row.switch, row.updated_at)
        row.state, row.detail, row.updated_at = state, detail, self.now()
        for name, value in fields.items():
            setattr(row, name, value)
        row.save()
        return f"Restore of {row.switch.mngt_IP}: {detail}"

    def load(self, row: RestoreSwitch) -> str:
        switch, restore = row.switch, row.restore
        if not Reservation.objects.filter(switch=switch, project_id=restore.project_id).exists():
            return self._record(row, RestoreSwitch.FAILED, 'Released before it could be Restored.')
        entry = SnapshotSwitch.objects.filter(snapshot_id=restore.snapshot_id, switch=switch).first()
        if entry is None or entry.vcboot is None:
            return self._record(row, RestoreSwitch.FAILED, 'The Snapshot no longer holds its config.')
        # Accounts live outside working/, so they survive the reload; what fails now (a Switch
        # just back from its Cleanup may not be ready) is tried again once it is back (check_back)
        switch_accounts.sync(switch, self.now())
        warnings = [] if switch.changeBanner(self.now()) else ["the banner couldn't be updated"]
        device = lab_switch(switch.mngt_IP)
        try:
            release = device.aos_release()
            if entry.aos_release and release != entry.aos_release:
                warnings.append(f"AOS release differs: the Snapshot was taken on {entry.aos_release}, the Switch "
                                f"runs {release}. Its config was loaded anyway.")
            device.load_config_and_reload(SwitchConfig(entry.vcboot, entry.vcsetup, entry.aos_release))
        except LabSwitchError as e:
            logger.error("Restore of %s: %s", switch.mngt_IP, e)
            return self._record(row, RestoreSwitch.FAILED, f"Couldn't load the Snapshot's config: {e}",
                              warnings=warnings)
        now = self.now()
        return self._record(row, RestoreSwitch.RELOADING, "Reloading with the Snapshot's config.", warnings=warnings,
                          next_check_at=now + self.grace, give_up_at=now + self.timeout)

    def check_back(self, row: RestoreSwitch) -> Optional[str]:
        """
        Whether a reloading Switch is back: done once it has really rebooted and AOS is ready
        (api.reboot), with its Switch accounts made again if they couldn't be before. Failed if
        it is released meanwhile, or isn't back in time.
        """
        switch = row.switch
        if not Reservation.objects.filter(switch=switch, project_id=row.restore.project_id).exists():
            return self._record(row, RestoreSwitch.FAILED, 'Released during its Restore: BLab Cleans it up.')
        # updated_at is when it started reloading: looking again leaves it as it is
        seen = self.reboots.look(switch, row.updated_at, self.now())
        if seen.state != reboot.BACK:
            if self.now() < row.give_up_at:
                row.next_check_at = self.now() + self.look_again_after
                row.save(update_fields=['next_check_at'])
                return None
            minutes = int(self.timeout.total_seconds() // 60)
            return self._record(row, RestoreSwitch.FAILED,
                                f"Didn't come back within {minutes} minutes of its reload: {seen.detail.rstrip('.')}.")
        warnings = row.warnings + switch_accounts.sync(switch, self.now())
        return self._record(row, RestoreSwitch.DONE, "Back, running the Snapshot's config.", warnings=warnings)

    def finish(self, restore: Restore) -> str:
        """Builds the Snapshot's Links again between the Switches it Restored, or to a Tool whose
        port is still free, and marks the Restore done."""
        restored = set(restore.switches.exclude(state=RestoreSwitch.LEFT_OUT).values_list('switch_id', flat=True))
        in_project = set(Reservation.objects.filter(project_id=restore.project_id).values_list('switch_id', flat=True))
        lab_tools = set(Tool.objects.values_list('id', flat=True))
        outcomes = []
        for ends in (restore.snapshot.links if restore.snapshot else []):
            outcomes.append(dict(ends=ends, **self.build(ends, (restored & in_project) | lab_tools, restore.project_id)))
        restore.links = outcomes
        restore.finished_at = self.now()
        restore.save(update_fields=['links', 'finished_at'])
        built = sum(1 for o in outcomes if o['outcome'] == BUILT)
        logger.info("Restore %s of the Project %s done: %d of %d Links built", restore.id, restore.project_id,
                    built, len(outcomes))
        return f"Restore {restore.id} done: {built} of {len(outcomes)} Links built"

    @staticmethod
    def build(ends: List[Dict], restored: set, project_id: int) -> Dict:
        """Builds one Link of the Snapshot again: {"outcome": built, already, left_out or failed, "detail"}."""
        if not all(end['switch'] in restored for end in ends):
            return {'outcome': LINK_LEFT_OUT, 'detail': 'A Switch at one end was left out of the Restore.'}
        ports = [Port.objects.filter(switch_id=end['switch'], port_switch=end['port']).first() for end in ends]
        if None in ports:
            return {'outcome': LINK_FAILED, 'detail': 'A port at one end is no longer cabled to a backbone.'}
        port_a, port_b = ports
        if port_a.svlan is not None and port_a.svlan == port_b.svlan:
            return {'outcome': ALREADY, 'detail': 'Already there.'}
        try:
            # Only while both Switches are still reserved into the Project as the Link is recorded
            link = links.connect(port_a, port_b, may_work_on=lambda reservation: reservation.project_id == project_id)
        except links.LinkError as e:
            return {'outcome': LINK_FAILED, 'detail': str(e)}
        return {'outcome': BUILT, 'detail': f"Built on SVLAN {link.svlan}."}
