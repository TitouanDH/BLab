"""
In-memory stand-in for the lab equipment, used when settings.BLAB_DEVICES == 'fake'.

Local development runs against a snapshot of the production database, so it knows
about real switches and backbones. This module makes sure nothing it does reaches them.
"""
import logging
from contextlib import contextmanager

from django.conf import settings
from django.core.management.base import CommandError

from .backbone import APIRequestError
from .lab_switch import CommandResult, LabSwitchError, Session

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


class FailureInjection:
    """
    Lets tests make a fake device fail like a real one would, with fail_after() and fail_on().
    A failure raises `error`, or, if an exit status is given, makes the command return it.
    """
    error = Exception

    def __init__(self):
        self.commands = []  # (ip, cmd) in order received, for inspection in tests
        self.failures = []  # [should_fail(ip, cmd), remaining times, exit status or None]

    def fail_after(self, n: int, times: int = 1):
        """Lets the next n commands through, then fails the following `times` ones."""
        countdown = [n]

        def should_fail(ip, cmd):
            countdown[0] -= 1
            return countdown[0] < 0
        self.failures.append([should_fail, times, None])

    def fail_on(self, text: str, ip: str = None, times: int = 1, exit_status: int = None):
        """Fails the next `times` commands containing text (on that device, if ip is given)."""
        self.failures.append([lambda i, cmd: text in cmd and ip in (None, i), times, exit_status])

    def injected_exit_status(self, ip: str, cmd: str):
        """Raises an injected failure, or returns an injected exit status (None if there is none)."""
        # Every armed failure sees every command, so countdowns stay independent
        fired = [f for f in self.failures if f[1] > 0 and f[0](ip, cmd)]
        if not fired:
            return None
        fired[0][1] -= 1
        logger.info("[fake %s] failing on purpose: %s", ip, cmd)
        if fired[0][2] is None:
            raise self.error(f"Injected failure on {ip}: {cmd}")
        return fired[0][2]

    def reset(self):
        self.commands.clear()
        self.failures.clear()


class FakeBackbone(FailureInjection):
    """
    Remembers the ethernet-service lines it is given, per backbone IP, and prints them
    back for 'show configuration snapshot vlan', which is what link verification reads.
    """
    error = APIRequestError

    def __init__(self):
        super().__init__()
        self.config = {}    # ip -> list of configuration lines

    def cli(self, ip: str, cmd: str) -> str:
        if self.injected_exit_status(ip, cmd) is not None:
            # The HTTPS CLI has no exit status: any failure is an error
            raise APIRequestError(f"Injected failure on {ip}: {cmd}")
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
        super().reset()
        self.config.clear()


backbone = FakeBackbone()


class FakeLabSwitches(FailureInjection):
    """
    Lab switches as far as banner and Cleanup go: the files written to each one, and the
    entries of its init/, working/ and certified/ directories. Reloads are recorded.
    """
    error = LabSwitchError
    INIT = ('Uos.img', 'pkg', 'vcboot.cfg', 'vcsetup.cfg')

    def __init__(self):
        super().__init__()
        self.written = {}      # ip -> {path: text}
        self.directories = {}  # ip -> {directory: set of entries}
        self.reloads = []      # ip, for each reload started

    def files(self, ip: str) -> dict:
        return self.directories.setdefault(ip, {'init': set(self.INIT), 'working': set(), 'certified': set()})

    @contextmanager
    def connect(self, ip: str):
        yield FakeLabSwitchSession(self, ip)

    def reset(self):
        super().reset()
        self.written.clear()
        self.directories.clear()
        self.reloads.clear()


class FakeLabSwitchSession(Session):
    def __init__(self, switches: FakeLabSwitches, ip: str):
        self.switches = switches
        self.ip = ip

    def _accept(self, cmd: str):
        """Records a command that reached the switch (one made to raise never did)."""
        status = self.switches.injected_exit_status(self.ip, cmd)
        self.switches.commands.append((self.ip, cmd))
        logger.info("[fake switch %s] %s", self.ip, cmd)
        return status

    def run(self, cmd: str) -> CommandResult:
        status = self._accept(cmd)
        if status is not None:
            return CommandResult(status, error=f"injected exit status {status}")
        files = self.switches.files(self.ip)
        words = cmd.split()
        if words[:2] == ['rm', '-rf'] and words[2].endswith('/*'):
            files[words[2][:-2]] = set()
        elif words[:3] == ['cp', '-r', 'init/*']:
            files[words[3].rstrip('/')] = set(files['init'])
        elif words[0] == 'ls':
            return CommandResult(0, '\n'.join(sorted(files.get(words[1].rstrip('/'), ()))))
        return CommandResult(0)

    def run_confirmed(self, cmd: str) -> None:
        if self._accept(cmd) is None and cmd.startswith('reload'):
            self.switches.reloads.append(self.ip)

    def write_file(self, path: str, text: str) -> None:
        if self._accept(f"write {path}") is not None:
            raise LabSwitchError(f"Injected failure writing {path} on {self.ip}")
        self.switches.written.setdefault(self.ip, {})[path] = text


lab_switches = FakeLabSwitches()
