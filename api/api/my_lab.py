"""
My lab (#42): every Switch a user may work on, grouped the way its Topology
draws it, and the Quarantines naming them.

With Projects on (BLAB_PROJECTS), one group per active Project of the user's, an empty one
included, so they can reserve into it again. With Projects off, one for the user's own
Reservations and one per Topology shared with them, an empty one included. A Reservation
without a Project while Projects are on (made by older code, before the conversion) goes into
a group of its holder's, as its Topology draws it (api.projects.topology_key).

Each Switch comes with its Reservation, how many Links it has and how many of them are Ghost
Links, and the user's own Switch account on it (api.switch_accounts.accounts_for).
"""
from typing import Dict, List

from django.contrib.auth.models import User

from . import links, projects, switch_accounts, topology
from .models import PendingCleanup, Quarantine, Reservation, TopologyShare
from .reservations import renewals_left
from .switch_details import Viewer


def read(user: User) -> Dict:
    """{"projects": whether Projects are on, "groups": [...], "quarantines": [...]} for user (see views.my_lab)."""
    viewer = Viewer(user)
    projects_on = projects.enabled()
    reservations = list(topology.workable_reservations(user).select_related('switch', 'user', 'project')
                        .order_by('switch__model', 'switch_id'))
    groups = _empty_groups(user, projects_on)
    counts = _link_counts([r.switch for r in reservations])
    accounts = {a['switch']: a for a in switch_accounts.accounts_for(user)}
    for reservation in reservations:
        key = projects.topology_key(reservation) if projects_on else ('holder', reservation.user_id)
        if key not in groups:
            groups[key] = (_project_group(projects.summary(user, reservation.project)) if key[0] == 'project'
                           else _holder_group(reservation.user))
        groups[key]['switches'].append(viewer.redact_switch(_row(reservation, counts, accounts)))
    return {'projects': projects_on, 'groups': list(groups.values()), 'quarantines': quarantines_naming(user, viewer)}


def _empty_groups(user: User, projects_on: bool) -> Dict:
    """The groups shown even while they hold nothing, by topology_key, in the order shown."""
    if projects_on:
        active = projects.mine(user).filter(archived_at__isnull=True)
        return {('project', p['id']): _project_group(p) for p in projects.summaries(user, active)}
    groups = {('holder', user.id): _holder_group(user)}
    for share in TopologyShare.objects.filter(target=user).select_related('owner').order_by('owner__username'):
        groups[('holder', share.owner_id)] = _holder_group(share.owner)
    return groups


def _project_group(summary: Dict) -> Dict:
    """A Project's group, from the Project as api.projects.summaries gives it."""
    return {'kind': 'project', 'project': summary, 'switches': []}


def _holder_group(holder: User) -> Dict:
    return {'kind': 'holder', 'holder': {'id': holder.id, 'username': holder.username}, 'switches': []}


def _row(reservation: Reservation, counts: Dict, accounts: Dict) -> Dict:
    switch = reservation.switch
    shown, ghosts = counts.get(switch.id, (0, 0))
    return {
        'id': switch.id,
        'model': switch.model,
        'mngt_IP': switch.mngt_IP,
        'holder': reservation.user.username,
        'holder_id': reservation.user_id,
        'end_date': reservation.end_date,
        'renewals_left': renewals_left(reservation),
        'admin_exception': reservation.admin_exception,
        'links': shown,
        'ghost_links': ghosts,
        'account': accounts.get(switch.id),  # None for a Switch without a management IP
    }


def _link_counts(switches) -> Dict[int, tuple]:
    """{switch id: (Links its Topology shows, how many of them are Ghost Links)}, counted on each end."""
    ids = {s.id for s in switches}
    counts = {}
    for link in links.links_for(switches):
        if len(link.ports) != 2 or not link.is_shown():  # as a Topology shows them (api.topology)
            continue
        ghost = link.ghost_reason is not None
        for switch_id in {p.switch_id for p in link.ports} & ids:
            shown, ghosts = counts.get(switch_id, (0, 0))
            counts[switch_id] = (shown + 1, ghosts + ghost)
    return counts


def quarantines_naming(user: User, viewer: Viewer) -> List[Dict]:
    """The open Quarantines naming user, newest first, which they must clear before reserving again."""
    cleaning_up = set(PendingCleanup.objects.values_list('switch_id', flat=True))
    return [viewer.redact_switch({
        'id': q.switch_id,
        'model': q.switch.model,
        'mngt_IP': q.switch.mngt_IP,
        'quarantine': {'opened_at': q.opened_at, 'reasons': viewer.redact_all(q.reasons)},
        'cleaning_up': q.switch_id in cleaning_up,
    }) for q in Quarantine.objects.filter(holder=user, lifted_at__isnull=True).select_related('switch')]
