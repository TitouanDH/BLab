"""
Restore onto the same Switches (see CONTEXT.md), with Projects on (BLAB_PROJECTS): sets a
Project's Switches up again from one of its Snapshots (api.snapshots).

plan() says, for each Switch of the Snapshot, whether it can be Restored: a free one is reserved
into the Project, one already in it has its config replaced, and a busy one (someone else's,
being Cleaned up, in Quarantine, Out of service, or under a member's AOS install) is left out,
with when it is free if it is reserved. It reads each Switch's AOS release, to say when it
differs from the Snapshot's, and whether BLab can install the Snapshot's.

start() reserves the chosen free Switches into the Project for the member who asks (the default
Reservation length), puts the Snapshot's Topology layout back, and records a Restore. The Switch
worker then carries it out (RestoreWorker): for each Switch, its Switch accounts and banner,
the Snapshot's AOS release installed into working/ if the Switch runs another (#49: when it is
one of its family's, api.aos_release; else, or if the install fails, the config is loaded on the
release it runs, and the Restore says so), the Snapshot's config written into working/ and a
reload from it, and a wait until it is back (api.reboot: really rebooted, and AOS ready), with
its Switch accounts made again if it came back in another release;
once every Switch is settled, the Snapshot's Links between the Restored Switches are built
again. Each step is recorded before the next, so a redeploy only delays a Restore.

A busy Switch can instead be replaced by a free one the member picks (#46, api.replacement):
plan() offers the candidates, preview() shows its rewritten config as a diff, and start()
reserves the pick, which is then Restored with the rewritten config, its Links moved onto it.

The Switch worker still does one Switch at a time (docs/adr/0005); RestoreWorker.advance()
moves one Switch on and touches no other, so the worker can run several at once (#39).
"""
import logging
from datetime import datetime, timedelta
from typing import Callable, Dict, Iterable, List, Optional

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import aos_install, aos_release, links, projects, quarantine, reboot, replacement, snapshots, switch_accounts
from .lab_switch import LabSwitchError, SwitchConfig, lab_switch, secure_admin_after_reload
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
REPLACEABLE = (BUSY, UNAVAILABLE)  # a free Switch the member picks can stand in for it (#46)

# What became of each Link of the Snapshot, once the Switches are settled
BUILT = 'built'
ALREADY = 'already'      # its ports already carry it
LINK_LEFT_OUT = 'left_out'  # a Switch at one end wasn't Restored
LINK_FAILED = 'failed'


def _check(project: Project) -> None:
    if project.archived:
        raise ProjectError(f"{project.name} is archived: it can't be Restored into.")


def _state(entry: SnapshotSwitch, switch: Optional[Switch], project: Project, held: Dict[int, Reservation],
           unavailable: Dict[int, dict], installing: set) -> Dict:
    """What a Switch of the Snapshot is now: {'state', 'detail', 'free_on'}."""
    if switch is None:
        return {'state': NO_CONFIG, 'detail': 'No longer in the lab.', 'free_on': None}
    if entry.vcboot is None:
        return {'state': NO_CONFIG, 'detail': f"The Snapshot has no config for it: {entry.read_error}", 'free_on': None}
    if switch.mngt_IP == NO_MANAGEMENT_IP:
        return {'state': NO_CONFIG, 'detail': 'It has no management IP.', 'free_on': None}
    if switch.id in installing:
        return {'state': UNAVAILABLE, 'detail': 'An AOS release is being installed on it.', 'free_on': None}
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
    "state", "restorable", "detail", "free_on", "snapshot_release", "current_release", "release_differs",
    "release_installed", "replacements"}], "links": <how many>}. Reads the AOS release of each Switch
    that can be Restored; release_installed: it runs another than the Snapshot's, which BLab
    installs first. replacements: for a busy Switch, the free ones that can stand in for it
    (api.replacement.candidates), best first.
    """
    _check(project)
    entries = list(snapshot.switches.order_by('switch_id'))
    switches = Switch.objects.in_bulk([e.switch_id for e in entries])
    held = {r.switch_id: r for r in Reservation.objects.filter(switch_id__in=switches)}
    unavailable = quarantine.unavailable()
    installing = aos_install.busy_switch_ids()
    pool = _free_pool(entries, unavailable, installing)
    planned = []
    for entry in entries:
        switch = switches.get(entry.switch_id)
        row = {'id': entry.switch_id, 'model': entry.model, 'mngt_IP': switch.mngt_IP if switch else None,
               'snapshot_release': entry.aos_release,
               'current_release': None, 'release_differs': False, 'release_installed': False,
               **_state(entry, switch, project, held, unavailable, installing)}
        row['restorable'] = row['state'] in RESTORABLE
        needed = len(replacement.link_ports(snapshot.links, entry.switch_id))
        row['replacements'] = replacement.candidates(entry, needed, pool) if row['state'] in REPLACEABLE else []
        if row['restorable']:
            try:
                row['current_release'] = lab_switch(switch.mngt_IP).aos_release()
            except LabSwitchError as e:
                logger.warning("Restore plan: %s", e)
            row['release_differs'] = bool(row['current_release'] and entry.aos_release
                                          and row['current_release'] != entry.aos_release)
            row['release_installed'] = row['release_differs'] and installable(entry, switch)
        planned.append(row)
    return {'switches': planned, 'links': len(snapshot.links)}


def _free_pool(entries, unavailable: Dict[int, dict], installing: set, switches=None) -> List[Switch]:
    """The Switches free to stand in for a busy one: not reserved, not busy otherwise, not in the Snapshot."""
    out = ({e.switch_id for e in entries} | set(Reservation.objects.values_list('switch_id', flat=True))
           | set(unavailable) | set(installing))
    pool = switches if switches is not None else Switch.objects.exclude(mngt_IP=NO_MANAGEMENT_IP)
    return [s for s in pool if s.id not in out]


def _offered(snapshot: Snapshot, entry: SnapshotSwitch, switch: Optional[Switch], state: str,
             pool: List[Switch]) -> None:
    """ProjectError unless `switch` is among the candidates for the busy Switch of the entry."""
    if state not in REPLACEABLE:
        raise ProjectError(f"Switch {entry.switch_id} of the Snapshot isn't busy: it can't be replaced.")
    needed = len(replacement.link_ports(snapshot.links, entry.switch_id))
    if switch is None or switch.id not in {c['id'] for c in replacement.candidates(entry, needed, pool)}:
        raise ProjectError(f"{switch.mngt_IP if switch else 'That Switch'} can't stand in for Switch "
                           f"{entry.switch_id}: it must be free, with {needed} cabled "
                           f"port{'s' if needed != 1 else ''} free.")


def preview(project: Project, snapshot: Snapshot, old_id, new_id) -> Dict:
    """
    What a replacement would load, for the member to confirm: {"replaces", "id", "model",
    "mngt_IP", "port_map", "links" (the Snapshot's Links on it, rewritten), "notes", "diff",
    "vcsetup_diff", "current_release", "release_installed"}. ProjectError if it isn't offered.
    """
    _check(project)
    entries = list(snapshot.switches.order_by('switch_id'))
    entry = next((e for e in entries if str(e.switch_id) == str(old_id)), None)
    if entry is None:
        raise ProjectError("That Switch isn't in the Snapshot.")
    held = {r.switch_id: r for r in Reservation.objects.filter(switch_id__in=[e.switch_id for e in entries])}
    unavailable, installing = quarantine.unavailable(), aos_install.busy_switch_ids()
    found = _state(entry, Switch.objects.filter(pk=entry.switch_id).first(), project, held, unavailable, installing)
    switch = Switch.objects.filter(pk=new_id).first() if str(new_id).isdigit() else None
    _offered(snapshot, entry, switch, found['state'],
             _free_pool(entries, unavailable, installing, [switch] if switch else []))
    try:
        prepared = replacement.prepare(snapshot, entry, switch)
    except replacement.ReplacementError as e:
        raise ProjectError(f"{switch.mngt_IP} can't stand in for it: {e}.")
    try:
        current = lab_switch(switch.mngt_IP).aos_release()
    except LabSwitchError as e:
        logger.warning("Restore preview: %s", e)
        current = None
    moved = [ends for ends in replacement.rewrite_links(snapshot.links, {entry.switch_id: (switch.id, prepared.port_map)})
             if any(end['switch'] == switch.id for end in ends)]
    return {'replaces': entry.switch_id, 'id': switch.id, 'model': switch.model, 'mngt_IP': switch.mngt_IP,
            'port_map': prepared.port_map, 'links': moved, 'notes': prepared.notes,
            'diff': replacement.diff(entry.vcboot, prepared.vcboot),
            'vcsetup_diff': replacement.diff(entry.vcsetup, prepared.vcsetup),
            'current_release': current,
            'release_installed': bool(current and entry.aos_release and current != entry.aos_release
                                      and installable(entry, switch))}


def start(project: Project, snapshot: Snapshot, user: User, chosen: Optional[Iterable] = None,
          now: Optional[datetime] = None, replacements: Optional[Dict] = None) -> Restore:
    """
    Starts Restoring the Snapshot for user, a member: onto every Switch that can be Restored, or
    only those whose ids are `chosen`. Free ones are reserved into the Project in user's name.
    `replacements` ({busy Switch id: free Switch id}, #46): each pick is reserved too, and gets the
    busy one's config rewritten for it (api.replacement). ProjectError if a pick isn't offered.
    ProjectError if it can't start: archived, a Restore already under way, nothing to Restore;
    NotAllowed while a Quarantine names user, who can't make new Reservations.
    """
    now = now or timezone.now()
    chosen = None if chosen is None else {int(i) for i in chosen if str(i).isdigit()}
    refused = quarantine.refusal_while_named(user)
    if refused:
        raise NotAllowed(refused)
    picks = _picks(replacements)
    # Read over SSH (the replacements' ports) before anything is locked
    prepared = {}
    for old_id, new_id in picks.items():
        entry = snapshot.switches.filter(switch_id=old_id).first()
        new = Switch.objects.filter(pk=new_id).first()
        if entry is None or new is None:
            raise ProjectError("A replacement names a Switch that isn't there.")
        try:
            prepared[old_id] = replacement.prepare(snapshot, entry, new)
        except replacement.ReplacementError as e:
            raise ProjectError(f"{new.mngt_IP} can't stand in for Switch {old_id}: {e}.")
    with transaction.atomic():
        # Locked, as Reserve does: the Project isn't archived meanwhile, and two Restores don't start at once
        project = Project.objects.select_for_update().get(pk=project.pk)
        _check(project)
        if snapshots.busy_restoring(project):
            raise ProjectError("A Restore of this Project is already under way.")
        entries = list(snapshot.switches.order_by('switch_id'))
        # Locked in id order, as Reserve locks each: a Switch isn't reserved twice
        switches = {s.id: s for s in Switch.objects.select_for_update()
                    .filter(id__in=[e.switch_id for e in entries] + list(picks.values())).order_by('id')}
        held = {r.switch_id: r for r in Reservation.objects.filter(switch_id__in=switches)}
        unavailable = quarantine.unavailable()
        installing = aos_install.busy_switch_ids()
        restore = Restore.objects.create(project=project, snapshot=snapshot, started_by=user, started_at=now)
        rows, restored, stand_ins = [], [], {}
        pool = _free_pool(entries, unavailable, installing, [switches[i] for i in picks.values() if i in switches])
        for entry in entries:
            found = _state(entry, switches.get(entry.switch_id), project, held, unavailable, installing)
            row = RestoreSwitch(restore=restore, switch_id=entry.switch_id, state=RestoreSwitch.LEFT_OUT,
                                detail=found['detail'], free_on=found['free_on'], updated_at=now)
            if entry.switch_id in picks:
                new = switches.get(picks[entry.switch_id])
                _offered(snapshot, entry, new, found['state'], pool)
                ready = prepared[entry.switch_id]
                Reservation.objects.create(switch=new, user=user, project=project, end_date=now + DEFAULT_LENGTH)
                rows.append(RestoreSwitch(restore=restore, switch=new, state=RestoreSwitch.WAITING, updated_at=now,
                                          detail=f"Waiting for the Switch worker: stands in for "
                                                 f"{switches[entry.switch_id].mngt_IP}.",
                                          replaces_id=entry.switch_id, vcboot=ready.vcboot, vcsetup=ready.vcsetup,
                                          port_map=ready.port_map, warnings=ready.notes))
                row.detail = f"{row.detail} Replaced by {new.mngt_IP}."
                restored.append(new.id)
                stand_ins[entry.switch_id] = new.id
            elif found['state'] in RESTORABLE and chosen is not None and entry.switch_id not in chosen:
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
        # A replacement takes the place of the Switch it stands in for
        positions = {stand_ins.get(int(i), int(i)): (p['x'], p['y']) for i, p in snapshot.layout.items()
                     if stand_ins.get(int(i), int(i)) in restored}
        projects.save_layout(project, positions)
    logger.info("%s started Restoring Snapshot %s of the Project %s onto %s", user.username, snapshot.id,
                project.id, restored)
    return restore


def _picks(replacements) -> Dict[int, int]:
    """{busy Switch id: its replacement's id}, from the request; ProjectError if it doesn't read so."""
    if not replacements:
        return {}
    if not isinstance(replacements, dict) or not all(str(k).isdigit() and str(v).isdigit()
                                                     for k, v in replacements.items()):
        raise ProjectError('"replacements" must map a Switch id to the id of the Switch replacing it.')
    picks = {int(k): int(v) for k, v in replacements.items()}
    if len(set(picks.values())) != len(picks):
        raise ProjectError("A Switch can stand in for one Switch only.")
    return picks


def reloading_into_another_release() -> set:
    """The Switches a Restore under way installed another AOS release on, and that aren't back yet."""
    return set(RestoreSwitch.objects.filter(release_installed=True, state__in=RestoreSwitch.UNDER_WAY,
                                            restore__finished_at__isnull=True).values_list('switch_id', flat=True))


def installable(entry: SnapshotSwitch, switch: Switch) -> bool:
    """Whether BLab can install the Snapshot's AOS release on the Switch: one of its model's family."""
    return bool(entry.aos_release) and entry.aos_release in aos_release.compatible_releases(switch.model)


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
                      'detail': r.detail, 'warnings': r.warnings, 'free_on': r.free_on, 'updated_at': r.updated_at,
                      'replaces': r.replaces_id} for r in rows],
        'links': restore.links,
    }


def latest(project: Project) -> Optional[Restore]:
    return project.restores.select_related('started_by').first()


def source(row: RestoreSwitch) -> int:
    """The Snapshot's Switch a row Restores: the one it stands in for, or itself."""
    return row.replaces_id or row.switch_id


class RestoreWorker:
    """Carries Restores out, a step at a time, as the Switch worker's cycle calls work()."""

    def __init__(self, now: Callable[[], datetime], grace: timedelta, timeout: timedelta,
                 look_again_after: timedelta, reboots: reboot.Watch, steps):
        self.now = now
        self.grace = grace
        self.timeout = timeout
        self.look_again_after = look_again_after
        self.reboots = reboots
        self.steps = steps  # the Switch worker's (api.switch_worker.Steps), shared with its Cleanups

    def work(self) -> List[str]:
        """
        Moves every Switch of a Restore that is due one step on, as the Switch worker's threads
        allow (api.switch_worker.Steps, #39), then finishes the Restores that are settled. What
        the steps done since the last call did, Cleanups' included.
        """
        # Whether it is due is decided here: one that isn't mustn't take a thread from one that is.
        # A row without its next time is broken: its step says so, every cycle
        due = (RestoreSwitch.objects.filter(state__in=RestoreSwitch.UNDER_WAY, restore__finished_at__isnull=True)
               .filter(Q(state=RestoreSwitch.WAITING) | Q(next_check_at__isnull=True) | Q(next_check_at__lte=self.now()))
               .select_related('switch', 'restore').order_by('restore__started_at', 'switch_id'))
        for row in due:
            self.steps.run(row.switch_id, lambda row=row: self.guarded(row))
        outcomes = self.steps.done()
        for restore in Restore.objects.filter(finished_at__isnull=True).order_by('started_at'):
            # A step that just settled its Switch may still be running: the Links wait for it
            if not restore.switches.filter(Q(state__in=RestoreSwitch.UNDER_WAY) | Q(switch_id__in=self.steps.busy())
                                           ).exists():
                outcomes.append(self.finish(restore))
        return outcomes

    def guarded(self, row: RestoreSwitch) -> Optional[str]:
        """One step of the Switch's Restore: what happened, or why it couldn't go on."""
        try:
            return self.advance(row)
        except Exception as e:
            # One broken Switch must not stop the others; it is tried again next cycle
            logger.exception("Restore of %s failed", row.switch.mngt_IP)
            return f"Could not go on with the Restore of {row.switch.mngt_IP}: {e}"

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
        entry = SnapshotSwitch.objects.filter(snapshot_id=restore.snapshot_id, switch_id=source(row)).first()
        if entry is None or entry.vcboot is None:
            return self._record(row, RestoreSwitch.FAILED, 'The Snapshot no longer holds its config.')
        # A replacement loads the config rewritten for it (#46)
        config = (SwitchConfig(row.vcboot, row.vcsetup, entry.aos_release) if row.vcboot is not None
                  else SwitchConfig(entry.vcboot, entry.vcsetup, entry.aos_release))
        # Accounts live outside working/, so they survive the reload; what fails now (a Switch
        # just back from its Cleanup may not be ready) is tried again once it is back (check_back)
        switch_accounts.sync(switch, self.now())
        warnings = list(row.warnings) + ([] if switch.changeBanner(self.now()) else ["the banner couldn't be updated"])
        device = lab_switch(switch.mngt_IP)
        try:
            release = device.aos_release()
            if entry.aos_release and release != entry.aos_release:
                warnings.extend(self.install_release(row, entry, release))
            device.load_config_and_reload(config)
        except LabSwitchError as e:
            logger.error("Restore of %s: %s", switch.mngt_IP, e)
            return self._record(row, RestoreSwitch.FAILED, f"Couldn't load the Snapshot's config: {e}",
                              warnings=warnings)
        now = self.now()
        return self._record(row, RestoreSwitch.RELOADING, "Reloading with the Snapshot's config.", warnings=warnings,
                          next_check_at=now + self.grace, give_up_at=now + self.timeout)

    def install_release(self, row: RestoreSwitch, entry: SnapshotSwitch, release: str) -> List[str]:
        """
        Installs the Snapshot's AOS release into working/ (#49), before its config is loaded there,
        so that one reload boots both. What to warn of: why it couldn't, and the config is then
        loaded on the release the Switch runs.
        """
        switch, wanted = row.switch, entry.aos_release
        if not installable(entry, switch):
            return [f"AOS release differs: the Snapshot was taken on {wanted}, which BLab can't install on a "
                    f"{switch.model}. Its config was loaded on {release}."]
        row.detail, row.updated_at = f"Installing AOS {wanted}, the Snapshot's release: {aos_install.TAKES}.", self.now()
        row.save(update_fields=['detail', 'updated_at'])
        try:
            aos_release.install(switch, wanted, ['working'])
        except (LabSwitchError, aos_release.ImageError) as e:
            logger.warning("Restore of %s: AOS %s not installed: %s", switch.mngt_IP, wanted, e)
            return [f"AOS {wanted}, the Snapshot's release, couldn't be installed: {e}. Its config was loaded on "
                    f"{release}."]
        logger.info("Restore of %s: AOS %s installed in working/ (was %s)", switch.mngt_IP, wanted, release)
        row.release_installed = True  # saved with its next step
        # Out of the release it leaves, or they'd come back at the Cleanup that brings it back (#74)
        return [f"AOS {wanted} installed (was {release})."] + switch_accounts.remove_before_release_change(switch)

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
        try:
            running = lab_switch(switch.mngt_IP).aos_release()
        except LabSwitchError as e:
            running = None
            logger.warning("Restore of %s: release not read once back: %s", switch.mngt_IP, e)
        if running:
            Switch.objects.filter(pk=switch.pk).update(aos_release=running)
        warnings = list(row.warnings)
        if row.release_installed:
            wanted = (SnapshotSwitch.objects.filter(snapshot_id=row.restore.snapshot_id, switch_id=source(row))
                      .values_list('aos_release', flat=True).first())
            if running and wanted and running != wanted:
                warnings.append(f"Back running AOS {running}, not the Snapshot's {wanted}: AOS may have fallen back "
                                f"to certified/.")
        # Back in the Snapshot's release, its local users are that release's (#74)
        synced = (switch_accounts.recreate if row.release_installed else switch_accounts.sync)(switch, self.now())
        if reboot.refused_not_ready(synced) and self.now() < row.give_up_at:
            # Back, but AOS refuses user commands for a while yet: its accounts are made at the next look
            row.next_check_at = self.now() + self.look_again_after
            row.save(update_fields=['next_check_at'])
            return None
        # As after every reload BLab starts: admin must still have the lab's secret password (#87)
        warnings += secure_admin_after_reload(switch.mngt_IP)
        return self._record(row, RestoreSwitch.DONE, "Back, running the Snapshot's config.",
                            warnings=warnings + synced)

    def finish(self, restore: Restore) -> str:
        """Builds the Snapshot's Links again between the Switches it Restored, or to a Tool whose
        port is still free, and marks the Restore done."""
        rows = list(restore.switches.exclude(state=RestoreSwitch.LEFT_OUT))
        restored = {r.switch_id for r in rows}
        # The Links of a replaced Switch move onto its replacement (#46)
        moved = {r.replaces_id: (r.switch_id, r.port_map or {}) for r in rows if r.replaces_id}
        in_project = set(Reservation.objects.filter(project_id=restore.project_id).values_list('switch_id', flat=True))
        lab_tools = set(Tool.objects.values_list('id', flat=True))
        outcomes = []
        for ends in replacement.rewrite_links(restore.snapshot.links if restore.snapshot else [], moved):
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
