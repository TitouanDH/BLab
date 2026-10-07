"""
Topology (see CONTEXT.md): a user's Switches and every Link with an end on one of them.

A Link whose other end is on a Switch outside the Topology still belongs to it: that far
Switch and Port come along, marked as outside, so the Link can be seen and removed.
"""
from django.contrib.auth.models import User

from . import links
from .models import Port, Switch, TopologyShare
from .serializers import PortSerializer, SwitchSerializer


def may_see(user: User, owner_id: int) -> bool:
    """The owner, or a user the owner shares their Topology with."""
    return owner_id == user.id or TopologyShare.objects.filter(owner_id=owner_id, target=user).exists()


def may_work(user: User, owner_id: int) -> bool:
    """
    The one place that decides who may work on a user's Topology: connect and disconnect
    its Links, release its Switches (and so Clean them up). Today whoever may see it.
    """
    return may_see(user, owner_id)


def read(owner: User) -> dict:
    """
    The owner's Switches, their Ports, and every Link with an end on them. Far ends of
    Links leaving the Topology come along with in_topology False. SVLANs held by other
    than two Ports are not Links and are left out. A Link being disconnected is left out
    too, unless its teardown failed: then it comes with the reason, to be asked again.
    """
    own_switches = list(Switch.objects.filter(reservation__user=owner).order_by('id'))
    own_ids = {s.id for s in own_switches}
    topology_links = [link for link in links.links_for(own_switches)
                      if len(link.ports) == 2  # links_for has already logged any other count
                      and link.is_shown()]

    far_ports = [p for link in topology_links for p in link.ports if p.switch_id not in own_ids]
    far_switches = Switch.objects.filter(id__in={p.switch_id for p in far_ports}).order_by('id')
    ports = list(Port.objects.filter(switch__in=own_switches)) + far_ports
    return {
        'switches': ([dict(SwitchSerializer(s).data, in_topology=True) for s in own_switches]
                     + [dict(SwitchSerializer(s).data, in_topology=False) for s in far_switches]),
        'ports': PortSerializer(sorted(ports, key=lambda p: p.id), many=True).data,
        'links': [{'svlan': link.svlan, 'ports': [p.id for p in link.ports], 'teardown_error': link.teardown_error}
                  for link in topology_links],
    }
