"""
What each port of a Switch or a Backbone can run at, from 'show interfaces capability' (#82): a
cable only comes up between two ports that share a speed.

The CAP row of each port gives its speeds in a column whose place depends on the device (an
OS6860N adds MACsec columns), so the field is found by its shape: '10/100/1G' (Mbit/s unless
it says G), '1G/10G/25G', '10G40G'. A cage (SFP, QSFP) holding an optic shows the optic's
speeds ('1G' on a 10G cage with a 1G optic). Copper ports have a crossover ('MDI/X/Auto');
cages show '-' there. Read on 10.69.144.130 (an OS6900 Backbone) and 10.69.144.152 (an
OS6860N-P24M), 2026-10-21.
"""
import re
from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional

CAPABILITY_COMMAND = 'show interfaces capability'
CAP_ROW = re.compile(r'^\s*(\d+/\d+/\d+[A-Za-z]?)\s+CAP\s+(.*)$', re.MULTILINE)
SPEED_FIELD = re.compile(r'(?:\d+(?:\.\d+)?[MG]?/?)+')
SPEED = re.compile(r'(\d+(?:\.\d+)?)([MG]?)')


@dataclass(frozen=True)
class Capability:
    speeds: FrozenSet[int]  # in Mbit/s
    copper: bool
    label: str  # the speeds as the device prints them, for the user

    def runs_with(self, other: 'Capability') -> bool:
        """Whether a cable between the two can come up: they share a speed."""
        return bool(self.speeds & other.speeds)


def parse_capabilities(output: str) -> Dict[str, Capability]:
    """Each port's Capability; a port whose CAP row has no speeds is left out (unknown)."""
    capabilities = {}
    for port, rest in CAP_ROW.findall(output or ''):
        field = next((word for word in rest.split() if SPEED_FIELD.fullmatch(word)), None)
        if field is None:
            continue
        speeds = frozenset(int(float(value) * (1000 if unit == 'G' else 1)) for value, unit in SPEED.findall(field))
        capabilities[port] = Capability(speeds, 'MDI' in rest, field)
    return capabilities


def compatible(a: Optional[Capability], b: Optional[Capability]) -> bool:
    """Whether two ports share a speed; a port BLab couldn't read never does."""
    return a is not None and b is not None and a.runs_with(b)
