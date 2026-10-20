"""
The Management network (see CONTEXT.md): the lab's management subnets, each with its gateway,
from BLAB_MANAGEMENT_NETWORKS. Every Switch, Backbone and Management switch is reached through
it; onboard_switches scans it (#55), and a contributed Switch gets its address in it (#59).
"""
import ipaddress
from dataclasses import dataclass
from typing import List

from django.conf import settings


@dataclass(frozen=True)
class Subnet:
    network: ipaddress.IPv4Network
    gateway: str

    def hosts(self) -> List[str]:
        """Its addresses a device may have: every host but the gateway."""
        return [str(ip) for ip in self.network.hosts() if str(ip) != self.gateway]


def parse(text: str) -> List[Subnet]:
    """'10.69.144.128/26 via 10.69.144.129, ...'. Raises ValueError on anything else."""
    subnets = []
    for item in filter(None, (part.strip() for part in text.split(','))):
        words = item.split()
        if len(words) != 3 or words[1] != 'via':
            raise ValueError(f"BLAB_MANAGEMENT_NETWORKS: '{item}' is not '<subnet> via <gateway>'")
        network = ipaddress.IPv4Network(words[0])
        if ipaddress.IPv4Address(words[2]) not in network:
            raise ValueError(f"BLAB_MANAGEMENT_NETWORKS: gateway {words[2]} is not in {network}")
        subnets.append(Subnet(network, words[2]))
    return subnets


def subnets() -> List[Subnet]:
    return parse(settings.BLAB_MANAGEMENT_NETWORKS)


def subnet_of(ip: str):
    """The subnet ip is in, or None."""
    address = ipaddress.IPv4Address(ip)
    return next((s for s in subnets() if address in s.network), None)
