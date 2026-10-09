"""
Topology (see CONTEXT.md): a user's Switches and every Link with an end on one of them.

A Link whose other end is on a Switch outside the Topology still belongs to it: that far
Switch and Port come along, marked as outside, so the Link can be seen and removed.
"""
import math
import re

from django.contrib.auth.models import User

from . import links
from .models import Port, Reservation, Switch, SwitchPosition, TopologyShare
from .serializers import PortSerializer, ReservationSerializer, SwitchSerializer


def may_see(user: User, owner_id: int) -> bool:
    """The owner, or a user the owner shares their Topology with."""
    return owner_id == user.id or TopologyShare.objects.filter(owner_id=owner_id, target=user).exists()


def may_work(user: User, owner_id: int) -> bool:
    """
    The one place that decides who may work on a user's Topology: connect and disconnect
    its Links, release its Switches (and so Clean them up). Today whoever may see it.
    """
    return may_see(user, owner_id)


def workable_owners(user: User) -> set:
    """The ids of every owner whose Topology user may work on (may_work), in one query."""
    return {user.id, *TopologyShare.objects.filter(target=user).values_list('owner_id', flat=True)}


def users_who_may_work(owner_id: int) -> set:
    """The ids of the users may_work lets work on owner's Topology: the owner and whoever it is shared with."""
    return {owner_id} | set(TopologyShare.objects.filter(owner_id=owner_id).values_list('target_id', flat=True))


def read(owner: User) -> dict:
    """
    The owner's Switches, their Ports, and every Link with an end on them. Far ends of
    Links leaving the Topology come along with in_topology False; the owner's come with
    their Reservation (end date, Renewals left, admin exception). SVLANs held by other
    than two Ports are not Links and are left out. A Link being disconnected is left out
    too, unless its teardown failed: then it comes with the reason, to be asked again.
    """
    own_switches = list(Switch.objects.filter(reservation__user=owner).order_by('id'))
    own_ids = {s.id for s in own_switches}
    topology_links = [link for link in links.links_for(own_switches)
                      if len(link.ports) == 2  # links_for has already logged any other count
                      and link.is_shown()]

    reservations = {r.switch_id: ReservationSerializer(r).data
                    for r in Reservation.objects.filter(switch__in=own_switches, user=owner)}
    far_ports = [p for link in topology_links for p in link.ports if p.switch_id not in own_ids]
    far_switches = Switch.objects.filter(id__in={p.switch_id for p in far_ports}).order_by('id')
    ports = list(Port.objects.filter(switch__in=own_switches)) + far_ports
    return {
        'switches': ([dict(SwitchSerializer(s).data, in_topology=True, reservation=reservations.get(s.id))
                      for s in own_switches]
                     + [dict(SwitchSerializer(s).data, in_topology=False) for s in far_switches]),
        'ports': PortSerializer(sorted(ports, key=lambda p: p.id), many=True).data,
        'links': [{'svlan': link.svlan, 'ports': [p.id for p in link.ports], 'teardown_error': link.teardown_error}
                  for link in topology_links],
        'layout': layout(owner, own_ids | {s.id for s in far_switches}),
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
    existing = set(Switch.objects.filter(id__in=positions).values_list('id', flat=True))
    SwitchPosition.objects.bulk_create(
        [SwitchPosition(owner=owner, switch_id=switch_id, x=x, y=y)
         for switch_id, (x, y) in positions.items() if switch_id in existing],
        update_conflicts=True, unique_fields=['owner', 'switch'], update_fields=['x', 'y'],
    )


def forget_layout(owner: User):
    """Forgets every saved position on owner's Topology (Re-arrange)."""
    SwitchPosition.objects.filter(owner=owner).delete()
