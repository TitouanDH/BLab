"""
In-memory stand-in for the lab equipment, used when settings.BLAB_DEVICES == 'fake'.

Local development runs against a snapshot of the production database, so it knows
about real switches and backbones. This module makes sure nothing it does reaches them.
"""
import logging
from django.conf import settings
from django.core.management.base import CommandError

logger = logging.getLogger(__name__)


def devices_are_fake() -> bool:
    return settings.BLAB_DEVICES == 'fake'


def require_real_devices(command_name: str) -> None:
    """Management commands that only make sense against real equipment call this first."""
    if devices_are_fake():
        raise CommandError(
            f"{command_name} talks to real lab equipment, but BLAB_DEVICES=fake. "
            "Set BLAB_DEVICES=real to run it."
        )


class FakeBackbone:
    """
    Remembers the ethernet-service lines it is given, per backbone IP, and prints them
    back for 'show configuration snapshot vlan', which is what link verification reads.
    """

    def __init__(self):
        self.config = {}    # ip -> list of configuration lines
        self.commands = []  # (ip, cmd) in order received, for inspection in tests

    def cli(self, ip: str, cmd: str) -> str:
        self.commands.append((ip, cmd))
        logger.info("[fake backbone %s] %s", ip, cmd)
        lines = self.config.setdefault(ip, [])

        if cmd == "show configuration snapshot vlan":
            return "\n".join(lines)
        if cmd.startswith("no ethernet-service "):
            # Like the device, removing "ethernet-service sap 1001" also removes
            # everything configured under it.
            removed = cmd[len("no "):]
            self.config[ip] = [
                line for line in lines
                if line != removed and not line.startswith(removed + " ")
            ]
        elif cmd.startswith("ethernet-service ") and cmd not in lines:
            lines.append(cmd)
        return ""

    def reset(self):
        self.config.clear()
        self.commands.clear()


backbone = FakeBackbone()
