"""
Topology (see CONTEXT.md): the Switches drawn together and every Link with an end on one of
them. With Projects on (BLAB_PROJECTS, api.projects), a Project's picture: the Switches reserved
into it. With Projects off, a user's: the Switches they hold, shared with other users.

A Link whose other end is on a Switch outside the Topology still belongs to it: that far
Switch and Port come along, marked as outside, so the Link can be seen and removed.

This module is the one place that decides who may work on a reserved Switch (Renew, Release,
Connect, Disconnect, its Switch accounts and its details): may_work_on.
"""
import math
import re
from typing import TYPE_CHECKING

from django.contrib.auth.models import User

from . import links, projects, tools
from .models import Port, Project, Reservation, Switch, SwitchPosition, Tool, TopologyShare
from .serializers import PortSerializer, ReservationSerializer, SwitchSerializer

if TYPE_CHECKING:  # api.switch_details imports this module
    from .switch_details import Viewer


def may_see(user: User, owner_id: int) -> bool:
    """A user's Topology: the owner, or, with Projects off, a user the owner shares it with.
    With Projects on, Topologies are Projects' (api.projects), and a user's own is theirs alone."""
    if projects.enabled():
        return owner_id == user.id
    return owner_id == user.id or TopologyShare.objects.filter(owner_id=owner_id, target=user).exists()


def may_work(user: User, owner_id: int) -> bool:
    """Who may work on a user's Topology (connect, disconnect and arrange it): whoever may see it."""
    return may_see(user, owner_id)


def workable_owners(user: User) -> set:
    """With Projects off, the ids of every owner whose Topology user may work on (may_work), in one query."""
    return {user.id, *TopologyShare.objects.filter(target=user).values_list('owner_id', flat=True)}


def workable_reservations(user: User):
    """
    Every Reservation user may work on now. With Projects off: those of an owner whose Topology
    they may work on (workable_owners). With Projects on: api.projects.workable_reservations.
    """
    if projects.enabled():
        return projects.workable_reservations(user)
    return Reservation.objects.filter(user_id__in=workable_owners(user))


def workable_switches(user: User) -> set:
    """
    The ids of every Switch user may work on now: those of workable_reservations, in one query.
    Who sees a Switch's details follows it (api.switch_details).
    """
    return set(workable_reservations(user).values_list('switch_id', flat=True))


def may_work_on(user: User, reservation: Reservation) -> bool:
    """
    Whether user may work on this reserved Switch: Renew, Release, Connect and Disconnect. With
    Projects off, whoever may work on the holder's Topology; on, api.projects.may_work_on.
    """
    if projects.enabled():
        return projects.may_work_on(user, reservation)
    return may_work(user, reservation.user_id)


def users_who_may_work_on(reservation: Reservation) -> set:
    """The ids of the users may_work_on lets work on this reserved Switch: who gets a Switch account."""
    if projects.enabled():
        return projects.users_who_may_work_on(reservation)
    return users_who_may_work(reservation.user_id)


def users_who_may_work(owner_id: int) -> set:
    """The ids of the users may_work lets work on owner's Topology: the owner and whoever it is shared with."""
    return {owner_id} | set(TopologyShare.objects.filter(owner_id=owner_id).values_list('target_id', flat=True))


def read(owner: User, viewer: 'Viewer') -> dict:
    """
    The owner's Topology: their Switches, their Ports, and every Link with an end on them.
    Far ends of Links leaving the Topology come along with in_topology False; the owner's come
    with their Reservation (end date, Renewals left, admin exception). SVLANs held by other
    than two Ports are not Links and are left out. A Link being disconnected is left out
    too, unless its teardown failed: then it comes with the reason, to be asked again. A Link
    the Link worker last found a Ghost Link comes with why (ghost_reason) and when (ghost_seen_at).
    Everything is as `viewer` (an api.switch_details.Viewer) may see it: a far Switch they
    may not work on comes without its details.

    A Tool (api.tools) a Link reaches comes as a far end, with is_tool. Every Tool of the lab
    also comes in "tools", with its free ports in "tool_ports", for a New Link to choose from.
    """
    return _read(Reservation.objects.filter(user=owner), viewer, lambda ids: layout(owner, ids))


def read_project(project: Project, viewer: 'Viewer') -> dict:
    """A Project's Topology, as read() gives a user's: the Switches reserved into it, and its layout."""
    return _read(Reservation.objects.filter(project=project), viewer, lambda ids: projects.layout(project, ids))


def _read(reservations, viewer: 'Viewer', layout_of) -> dict:
    """The Topology of the Switches of these Reservations; layout_of(switch ids) gives their positions."""
    reservations = {r.switch_id: ReservationSerializer(r).data for r in reservations}
    own_switches = list(Switch.objects.filter(id__in=reservations).order_by('id'))
    own_ids = {s.id for s in own_switches}
    topology_links = [link for link in links.links_for(own_switches)
                      if len(link.ports) == 2  # links_for has already logged any other count
                      and link.is_shown()]

    far_ports = [p for link in topology_links for p in link.ports if p.switch_id not in own_ids]
    far_switches = Switch.everything.filter(id__in={p.switch_id for p in far_ports}).order_by('id')
    ports = list(Port.objects.filter(switch__in=own_switches)) + far_ports
    lab_tools = list(Tool.objects.order_by('model', 'id'))
    return {
        'switches': ([viewer.redact_switch(dict(SwitchSerializer(s).data, in_topology=True,
                                         reservation=reservations.get(s.id)))
                      for s in own_switches]
                     + [viewer.redact_switch(dict(SwitchSerializer(s).data, in_topology=False)) for s in far_switches]),
        'ports': PortSerializer(sorted(ports, key=lambda p: p.id), many=True).data,
        'links': [{'svlan': link.svlan, 'ports': [p.id for p in link.ports],
                   'teardown_error': viewer.redact(link.teardown_error),
                   'ghost_reason': viewer.redact(link.ghost_reason), 'ghost_seen_at': link.ghost_seen_at}
                  for link in topology_links],
        'layout': layout_of(own_ids | {s.id for s in far_switches}),
        'tools': [viewer.redact_switch(dict(SwitchSerializer(t).data, in_topology=False)) for t in lab_tools],
        'tool_ports': PortSerializer(tools.free_ports(lab_tools), many=True).data,
    }


# The Topology layout: where each Switch is drawn, the same for everyone viewing the Topology

MAX_POSITIONS = 1000  # far more Switches than the lab has


def layout(owner: User, switch_ids) -> dict:
    """The saved positions of these Switches on owner's Topology: {"<switch id>": {"x", "y"}}."""
    return {str(p.switch_id): {'x': p.x, 'y': p.y}
            for p in SwitchPosition.objects.filter(owner=owner, switch_id__in=switch_ids)}


def parse_positions(positions) -> dict:
    """{switch id: (x, y)} from {"<switch id>": {"x": number, "y": number}}; ValueError if it isn't that."""
    not_positions = ValueError('"positions" must map Switch ids to {"x", "y"}.')
    if not isinstance(positions, dict) or len(positions) > MAX_POSITIONS:
        raise not_positions
    parsed = {}
    for switch_id, position in positions.items():
        if not re.fullmatch(r'[0-9]{1,18}', str(switch_id)) or not isinstance(position, dict):
            raise not_positions
        x, y = position.get('x'), position.get('y')
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (x, y)):
            raise ValueError('A position needs "x" and "y" as numbers.')
        parsed[int(switch_id)] = (float(x), float(y))
    return parsed


def save_layout(owner: User, positions: dict):
    """Saves these positions ({switch id: (x, y)}) on owner's Topology; other Switches keep theirs.
    Switches that no longer exist are left out."""
    existing = set(Switch.everything.filter(id__in=positions).values_list('id', flat=True))
    SwitchPosition.objects.bulk_create(
        [SwitchPosition(owner=owner, switch_id=switch_id, x=x, y=y)
         for switch_id, (x, y) in positions.items() if switch_id in existing],
        update_conflicts=True, unique_fields=['owner', 'switch'], update_fields=['x', 'y'],
    )


def forget_layout(owner: User):
    """Forgets every saved position on owner's Topology (Re-arrange)."""
    SwitchPosition.objects.filter(owner=owner).delete()
