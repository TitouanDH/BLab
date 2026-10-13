"""
Snapshots (see CONTEXT.md), with Projects on (BLAB_PROJECTS): what a Project's Switches were set
up as at one moment, so that its members can Restore them later (api.restore). A member Saves
one, with an optional note, and BLab takes one before every Release. The last KEPT per Project
are kept.

A Snapshot holds each Switch's working/vcboot.cfg, its working/vcsetup.cfg if it has one, and
the AOS release in working/, all read over SSH; the Project's Links, port to port; and its
Topology layout. No files, no Switch accounts. The configs are only kept to be loaded again:
the API never gives them out.

Before a Release, one Snapshot per wind-down: Releasing a Project's Switches one after another
(by hand, or at expiry) adds each Switch to the Release Snapshot the first of those Releases
started, as long as nothing has been reserved into the Project or saved since. So that Snapshot
holds each Switch as it was right before its own Release, and Releasing a Project of ten
Switches doesn't push every Save out of the last KEPT.
"""
import logging
from typing import Dict, List, Optional

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from . import links, projects
from .lab_switch import LabSwitchError, lab_switch
from .models import NO_MANAGEMENT_IP, Project, Reservation, Restore, Snapshot, SnapshotSwitch, Switch
from .projects import ProjectError

logger = logging.getLogger(__name__)

KEPT = 10  # Snapshots kept per Project, the newest
NOTE_LENGTH = Snapshot._meta.get_field('note').max_length


def read_switch(switch: Switch) -> Dict:
    """What a Snapshot holds of one Switch, as SnapshotSwitch fields; `read_error` says why it couldn't be read."""
    entry = {'model': switch.model, 'read_at': timezone.now(), 'aos_release': '', 'vcboot': None, 'vcsetup': None,
             'read_error': None}
    if switch.mngt_IP == NO_MANAGEMENT_IP:
        return dict(entry, read_error='no management IP')
    try:
        config = lab_switch(switch.mngt_IP).read_config()
    except LabSwitchError as e:
        logger.warning("Snapshot of %s: %s", switch.mngt_IP, e)
        return dict(entry, read_error=str(e))
    return dict(entry, aos_release=config.aos_release, vcboot=config.vcboot, vcsetup=config.vcsetup)


def project_links(switch_ids) -> List[List[Dict]]:
    """The Links with both ends on these Switches, port to port, leaving out those being disconnected."""
    switch_ids = set(switch_ids)
    found = []
    for link in links.links_for(Switch.objects.filter(id__in=switch_ids)):
        if len(link.ports) != 2 or link.teardown_requested_at is not None:
            continue
        if all(port.switch_id in switch_ids for port in link.ports):
            found.append(sorted(({'switch': port.switch_id, 'port': port.port_switch} for port in link.ports),
                                key=lambda end: (end['switch'], end['port'])))
    return found


def busy_restoring(project: Project) -> bool:
    """Whether a Restore of the Project is under way."""
    return Restore.objects.filter(project=project, finished_at__isnull=True).exists()


def save(project: Project, user: User, note: str = '') -> Snapshot:
    """A member Saves the Project: a Snapshot of all its Switches now, with an optional note."""
    if not isinstance(note, str):
        raise ProjectError("The note must be text.")
    note = note.strip()
    if len(note) > NOTE_LENGTH:
        raise ProjectError(f"A Snapshot's note is {NOTE_LENGTH} characters at most.")
    if project.archived:
        raise ProjectError("This Project is archived: it holds no Switch to save.")
    switches = list(Switch.objects.filter(reservation__project=project).order_by('id'))
    if not switches:
        raise ProjectError("This Project holds no Switch: there is nothing to save.")
    if busy_restoring(project):
        raise ProjectError("A Restore of this Project is under way: save once it is done.")
    read = {switch.id: read_switch(switch) for switch in switches}  # over SSH: outside the transaction
    ids = [switch.id for switch in switches]
    with transaction.atomic():
        snapshot = Snapshot.objects.create(project=project, taken_by=user, reason=Snapshot.SAVE, note=note,
                                           links=project_links(ids), layout=projects.layout(project, ids))
        SnapshotSwitch.objects.bulk_create([SnapshotSwitch(snapshot=snapshot, switch_id=switch_id, **entry)
                                            for switch_id, entry in read.items()])
        prune(project)
    logger.info("%s saved a Snapshot of the Project %s (%d Switches)", user.username, project.id, len(switches))
    return snapshot


def before_release(reservation: Reservation, actor: Optional[User]) -> Optional[Snapshot]:
    """
    Before a Release (actor None: at expiry), adds the Switch to the Project's Release Snapshot
    (see the module docstring), or starts one. Never stops the Release: a Switch that can't be
    read is recorded with why, and anything else that goes wrong is only logged. Does nothing
    with Projects off, or for a Reservation without a Project.
    """
    if not projects.enabled() or reservation.project_id is None:
        return None
    try:
        return _add_before_release(reservation, actor)
    except Exception:
        logger.exception("No Snapshot before the Release of %s", reservation.switch.mngt_IP)
        return None


def _add_before_release(reservation: Reservation, actor: Optional[User]) -> Snapshot:
    switch = reservation.switch
    entry = read_switch(switch)  # over SSH: outside the transaction
    with transaction.atomic():
        # Locked: two Releases in the same Project at once add to the same Snapshot
        project = Project.objects.select_for_update().get(pk=reservation.project_id)
        snapshot = winding_down(project)
        if snapshot is None:
            snapshot = Snapshot.objects.create(project=project, taken_by=actor, reason=Snapshot.RELEASE)
        SnapshotSwitch.objects.update_or_create(snapshot=snapshot, switch=switch, defaults=entry)
        in_project = Reservation.objects.filter(project=project).values_list('switch_id', flat=True)
        snapshot.links += [link for link in project_links(in_project)
                           if switch.id in {end['switch'] for end in link} and link not in snapshot.links]
        snapshot.layout.update(projects.layout(project, [switch.id]))
        snapshot.save(update_fields=['links', 'layout'])
        prune(project)
    logger.info("Snapshot %s of the Project %s holds %s before its Release", snapshot.id, project.id, switch.mngt_IP)
    return snapshot


def winding_down(project: Project) -> Optional[Snapshot]:
    """The Release Snapshot the next Release adds to: the newest Snapshot, if it is one and nothing
    has been reserved into the Project since it was started."""
    newest = project.snapshots.first()
    if newest is None or newest.reason != Snapshot.RELEASE:
        return None
    if Reservation.objects.filter(project=project, creation_date__gt=newest.taken_at).exists():
        return None
    return newest


def prune(project: Project) -> int:
    """Removes the Project's Snapshots beyond the newest KEPT, but none a Restore under way reads."""
    old = [s.id for s in project.snapshots.all()[KEPT:]]
    in_use = set(Restore.objects.filter(snapshot_id__in=old, finished_at__isnull=True)
                 .values_list('snapshot_id', flat=True))
    pruned = [snapshot_id for snapshot_id in old if snapshot_id not in in_use]
    Snapshot.objects.filter(id__in=pruned).delete()
    return len(pruned)


def describe(snapshot: Snapshot) -> Dict:
    """A Snapshot as the API gives it to the Project's members: without the configs."""
    entries = sorted(snapshot.switches.all(), key=lambda e: e.switch_id)
    return {
        'id': snapshot.id,
        'taken_at': snapshot.taken_at,
        'taken_by': snapshot.taken_by.username if snapshot.taken_by else None,
        'reason': snapshot.reason,
        'note': snapshot.note,
        'switches': [{'id': e.switch_id, 'model': e.model, 'aos_release': e.aos_release, 'read_at': e.read_at,
                      'read_error': e.read_error, 'restorable': e.vcboot is not None} for e in entries],
        'links': len(snapshot.links),
    }


def listing(project: Project) -> List[Dict]:
    """The Project's Snapshots, newest first."""
    return [describe(s) for s in project.snapshots.select_related('taken_by').prefetch_related('switches')]


def of_project(project: Project, snapshot_id) -> Snapshot:
    """One of the Project's Snapshots; NotFound otherwise."""
    snapshot = project.snapshots.filter(pk=snapshot_id).first() if str(snapshot_id).isdigit() else None
    if snapshot is None:
        raise projects.NotFound("No such Snapshot in this Project.")
    return snapshot
