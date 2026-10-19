"""
Tools (see CONTEXT.md): equipment that is not a Switch but whose ports are cabled to UNIs, so
that users can make Links to them, such as the IXIA. A Tool is a Switch record marked is_tool,
with its Ports (models.Tool); Switch.objects leaves it out, so it is never reserved, Cleaned up,
Inspected or Swept, and gets no Switch accounts.

A Tool port is taken while it carries a Link and free otherwise: whoever links first gets it
(api.links claims both ports under the SVLAN lock). The Link ends when the Switch at its other
end is Released, as every Link with an end on a Released Switch does.

An admin marks a record either way here (api.admin).
"""
import logging
from typing import List

from django.contrib.auth.models import User
from django.db import transaction

from .models import PendingCleanup, Port, Quarantine, Reservation, Switch, SwitchAccount
from .quarantine import lift

logger = logging.getLogger(__name__)


class ToolError(Exception):
    """A record can't be marked: the message says why, for the admin."""


def free_ports(tools) -> List[Port]:
    """The Ports of these Tools that carry no Link, which a New Link may take."""
    return list(Port.objects.filter(switch__in=tools, svlan__isnull=True).order_by('switch_id', 'id'))


def mark_tool(switch: Switch, admin: User) -> List[str]:
    """
    Marks a Switch record as a Tool, with its Ports and their Links. A Reservation of it ends
    without a Release, so its Links stay, and so does a Cleanup BLab still had to do: a Tool is
    never Cleaned up. Its open Quarantines are lifted, since nothing would Re-check it again.
    Refused while it has Switch accounts: Release it first, so BLab removes them. Returns what
    it did besides, a line each.
    """
    done = []
    with transaction.atomic():
        switch = Switch.everything.select_for_update().get(pk=switch.pk)
        if switch.is_tool:
            return done
        if SwitchAccount.objects.filter(switch=switch).exists():
            raise ToolError(f"{switch} has Switch accounts: Release it first, so BLab removes them.")
        for reservation in Reservation.objects.filter(switch=switch).select_related('user'):
            reservation.delete()
            done.append(f"its Reservation by {reservation.user.username} ended, its Links kept")
        if PendingCleanup.objects.filter(switch=switch).delete()[0]:
            done.append("its pending Cleanup was dropped")
        for quarantine in Quarantine.objects.select_for_update().filter(switch=switch, lifted_at__isnull=True):
            lift(quarantine, admin, f"marked as a Tool by {admin.username} (admin): never Inspected again")
            done.append("its Quarantine was lifted")
        switch.is_tool = True
        switch.save(update_fields=['is_tool'])
    logger.info("%s marked %s as a Tool%s", admin.username, switch, ''.join(f"; {line}" for line in done))
    return done


def mark_switch(tool: Switch, admin: User) -> None:
    """
    Marks a Tool back as a Switch. Refused while one of its ports carries a Link: a Switch's
    Links belong to its Reservation. It comes back as it is, so the Sweep Inspects it next.
    """
    with transaction.atomic():
        tool = Switch.everything.select_for_update().get(pk=tool.pk)
        if not tool.is_tool:
            return
        if Port.objects.filter(switch=tool, svlan__isnull=False).exists():
            raise ToolError(f"{tool} carries Links: disconnect them first.")
        tool.is_tool = False
        tool.save(update_fields=['is_tool'])
    logger.info("%s marked %s back as a Switch", admin.username, tool)
