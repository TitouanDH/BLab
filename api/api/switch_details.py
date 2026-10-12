"""
Switch details (#36): a Switch's management IP, console and hardware (part number, hardware
revision, serial number) are for the users who may work on it, and for admins. Everyone
else gets the Switch without them, so nobody can SSH to a Switch they don't hold: the server
leaves them out, the UI doesn't just hide them.

Who may work on a Switch is topology.workable_switches; this module only applies it.
"""
import re
from typing import Dict, Iterable

from django.contrib.auth.models import User

from . import topology
from .models import NO_MANAGEMENT_IP, Switch

DETAIL_FIELDS = ('mngt_IP', 'console', 'part_number', 'hardware_revision', 'serial_number')


def label(switch: Switch) -> str:
    """How BLab names a Switch to someone who may not see its details: its model and id."""
    return f"{switch.model} #{switch.id}"


class Viewer:
    """What one user may see of the Switches: their details, or only model and id. One query."""

    def __init__(self, user: User):
        self.sees_all = bool(user.is_staff)
        self.workable = set() if self.sees_all else topology.workable_switches(user)
        self._hidden_ips = None

    def sees_details(self, switch_id: int) -> bool:
        return self.sees_all or switch_id in self.workable

    def redact_switch(self, data: Dict) -> Dict:
        """A serialized Switch (with "id") as this user may see it: without its details unless they may."""
        if self.sees_details(data['id']):
            return data
        return {key: value for key, value in data.items() if key not in DETAIL_FIELDS}

    def redact(self, text):
        """Text BLab wrote (a reason, a Sweep problem) with the management IP of every Switch this
        user may not see replaced by the Switch's label. Anything but a string comes back as is."""
        if not isinstance(text, str) or self.sees_all:
            return text
        for ip, name in self.hidden_ips().items():
            text = ip.sub(name, text)
        return text

    def redact_all(self, texts: Iterable) -> list:
        return [self.redact(text) for text in texts]

    def redact_reason(self, unavailable):
        """Why a Switch can't be reserved ({'state', 'reason'}, as api.quarantine.unavailable gives it), redacted."""
        return unavailable and {**unavailable, 'reason': self.redact(unavailable['reason'])}

    def hidden_ips(self) -> Dict:
        """{pattern matching a hidden Switch's management IP: its label}, looked up once."""
        if self._hidden_ips is None:
            self._hidden_ips = {
                re.compile(r'(?<![\d.])' + re.escape(switch.mngt_IP) + r'(?!\.?\d)'): label(switch)
                for switch in Switch.objects.exclude(id__in=self.workable).only('id', 'model', 'mngt_IP')
                if switch.mngt_IP and switch.mngt_IP != NO_MANAGEMENT_IP
            }
        return self._hidden_ips
