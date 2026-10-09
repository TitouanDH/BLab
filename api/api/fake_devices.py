"""
In-memory stand-in for the lab equipment, used when settings.BLAB_DEVICES == 'fake'.

Local development runs against a snapshot of the production database, so it knows
about real switches and backbones. This module makes sure nothing it does reaches them.
"""
import logging
import re
import shlex
from contextlib import contextmanager
from fnmatch import fnmatch

from django.conf import settings
from django.core.management.base import CommandError

from .backbone import APIRequestError
from .lab_switch import INIT_CONFIG_PATH, CommandResult, LabSwitchError, LoginRefused, Session

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
    Ports are enabled until disabled; 'show configuration snapshot interface' prints the
    disabled ones, as the device does.
    """
    error = APIRequestError

    def __init__(self):
        super().__init__()
        self.config = {}    # ip -> list of configuration lines
        self.disabled = {}  # ip -> set of ports whose admin state is disabled

    def cli(self, ip: str, cmd: str) -> str:
        if self.injected_exit_status(ip, cmd) is not None:
            # The HTTPS CLI has no exit status: any failure is an error
            raise APIRequestError(f"Injected failure on {ip}: {cmd}")
        self.commands.append((ip, cmd))
        logger.info("[fake backbone %s] %s", ip, cmd)
        lines = self.config.setdefault(ip, [])

        if cmd == "show configuration snapshot vlan":
            return "\n".join(["! VLAN:"] + snapshot_lines(lines))
        if cmd == "show configuration snapshot interface":
            return "\n".join(["! Interface:"] + [f"interfaces port {port} admin-state disable"
                                                 for port in sorted(self.disabled.get(ip, ()))])
        words = cmd.split()
        if words[:1] == ["interfaces"] and words[2:3] == ["admin-state"]:
            disabled = self.disabled.setdefault(ip, set())
            if words[3] == "disable":
                disabled.add(words[1])
            else:
                disabled.discard(words[1])
        elif cmd.startswith("no ethernet-service "):
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
        self.disabled.clear()


def snapshot_lines(lines):
    """
    The lines as the device prints them: it folds consecutive SVLANs enabled the same way
    into one range, "ethernet-service svlan 1001-1003 admin-state enable", printed first.
    """
    svlan_line = re.compile(r"ethernet-service svlan (\d+) admin-state enable")
    svlans = sorted(int(m.group(1)) for m in map(svlan_line.fullmatch, lines) if m)
    ranges = []
    for svlan in svlans:
        if ranges and ranges[-1][1] == svlan - 1:
            ranges[-1][1] = svlan
        else:
            ranges.append([svlan, svlan])
    folded = []
    for first, last in ranges:
        svlan_range = str(first) if first == last else f"{first}-{last}"
        folded.append(f"ethernet-service svlan {svlan_range} admin-state enable")
    return folded + [line for line in lines if not svlan_line.fullmatch(line)]


backbone = FakeBackbone()

# What every fake switch's init/vcboot.cfg holds, and what it runs until told otherwise
INIT_CONFIG = """! Chassis:
system name "lab switch"
! VLAN:
vlan 1 admin-state enable
! IP:
ip interface "EMP-CHAS1" address 10.0.0.250 mask 255.255.255.0
"""
FAKE_PORTS = tuple(f'1/1/{n}' for n in range(1, 9))  # each fake switch's ports, links down unless cabled


class FakeLabSwitches(FailureInjection):
    """
    Lab switches as far as banner, Cleanup, prepare_switches, Switch accounts and Inspection
    go: the files written to each one, and the entries of its init/, working/ and certified/
    directories; reloads are recorded; its local users and their passwords. For Inspections:
    how many chassis it has (more than one is a VC), the ports with a cable (link up), the
    config it runs, and whether it refuses BLab's login or can't be reached. `outputs`
    replaces a show command's output, to feed the parsers junk.
    """
    error = LabSwitchError
    INIT = ('Uos.img', 'pkg', 'vcboot.cfg', 'vcsetup.cfg')

    def __init__(self):
        super().__init__()
        self.written = {}         # ip -> {path: text}
        self.directories = {}     # ip -> {directory: set of entries}
        self.reloads = []         # ip, for each reload started
        self.chassis = {}         # ip -> number of chassis (1 unless set)
        self.cabled = {}          # ip -> set of ports whose link is up
        self.running_config = {}  # ip -> config text (INIT_CONFIG unless set)
        self.outputs = {}         # ip -> {show command: output to print instead}
        self.refused = set()      # ips that refuse BLab's login
        self.unreachable = set()  # ips that don't answer
        self.users = {}           # ip -> {local user name: password}, besides admin

    def files(self, ip: str) -> dict:
        return self.directories.setdefault(ip, {'init': set(self.INIT), 'working': set(), 'certified': set()})

    def cable(self, ip: str, *ports: str) -> None:
        """Plugs a cable into each port: its link comes up."""
        self.cabled.setdefault(ip, set()).update(ports)

    @contextmanager
    def connect(self, ip: str):
        if ip in self.unreachable:
            raise LabSwitchError(f"Cannot connect to {ip}: timed out")
        if ip in self.refused:
            raise LoginRefused(f"{ip} refused the login of admin: Authentication failed.")
        yield FakeLabSwitchSession(self, ip)

    def show(self, ip: str, cmd: str) -> str:
        """What the switch prints for a show command, in the device's format."""
        if cmd in self.outputs.get(ip, {}):
            return self.outputs[ip][cmd]
        if cmd == 'show chassis':
            blocks = []
            for n in range(1, self.chassis.get(ip, 1) + 1):
                role = 'Local Chassis ID 1 (Master)' if n == 1 else f'Remote Chassis ID {n} (Slave)'
                blocks.append(f"{role}\n  Model Name:                    OS6860-48,\n"
                              f"  Serial Number:                 FAKE{n},\n")
            return '\n'.join(blocks)
        if cmd == 'show interfaces':
            cabled = self.cabled.get(ip, set())
            return '\n'.join(
                f"Chassis/Slot/Port {port}    :\n"
                f" Operational Status     : {'up' if port in cabled else 'down'},\n"
                f" Port-Down/Violation Reason: None,\n"
                for port in sorted(set(FAKE_PORTS) | cabled))
        if cmd == 'show configuration snapshot':
            return self.running_config.get(ip, INIT_CONFIG)
        if cmd == 'show user':
            names = ['admin', 'default (*)'] + list(self.users.get(ip, {}))
            return ''.join(f"User name = {name},\n  Read/Write for domains  = All ,\n  SSH allowed    = YES\n"
                           for name in names)
        return ''

    def reset(self):
        super().reset()
        self.written.clear()
        self.directories.clear()
        self.reloads.clear()
        self.chassis.clear()
        self.cabled.clear()
        self.running_config.clear()
        self.outputs.clear()
        self.refused.clear()
        self.unreachable.clear()
        self.users.clear()


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
        if cmd.startswith('show '):
            return CommandResult(0, self.switches.show(self.ip, cmd))
        if cmd.startswith(('user ', 'no user ')):
            return self._user(cmd)
        files = self.switches.files(self.ip)
        words = cmd.split()
        if words[:2] == ['rm', '-rf']:
            if words[2].endswith('/*'):
                if words[2][:-2] in files:
                    files[words[2][:-2]] = set()
            else:
                files.pop(words[2], None)
        elif words[:2] == ['mkdir', '-p']:
            files.setdefault(words[2], set())
        elif words[0] == 'cp':
            return self._copy(files, words[-2], words[-1].rstrip('/'))
        elif words[0] == 'ls':
            directory = words[1].rstrip('/')
            if directory not in files:
                return CommandResult(2, error=f"ls: {words[1]}: No such file or directory")
            return CommandResult(0, '\n'.join(sorted(files[directory])))
        return CommandResult(0)

    def _user(self, cmd: str) -> CommandResult:
        """'user <name> password "<pw>" read-write all' and 'no user <name>'; refusals as AOS prints them."""
        words = shlex.split(cmd)
        users = self.switches.users.setdefault(self.ip, {})
        if words[0] == 'no':
            if words[2] not in users:
                return CommandResult(0, 'ERROR: Unknown user\n')
            del users[words[2]]
            return CommandResult(0)
        name, password = words[1], words[3]
        if len(name) > 63:
            return CommandResult(0, 'ERROR: User name length should be between 1 and 63 characters\n')
        if len(password) < 8:
            return CommandResult(0, 'ERROR: Password must contain at least 8 characters\n')
        users[name] = password
        return CommandResult(0)

    @staticmethod
    def _copy(files: dict, source: str, target: str) -> CommandResult:
        """cp [-r] dir/pattern target/: fails, like the shell, when nothing matches."""
        directory, _, pattern = source.partition('/')
        copied = {name for name in files.get(directory, ()) if fnmatch(name, pattern)}
        if not copied or target not in files:
            return CommandResult(1, error=f"cp: cannot copy {source} to {target}/")
        files[target] |= copied
        return CommandResult(0)

    def run_confirmed(self, cmd: str) -> None:
        if self._accept(cmd) is None and cmd.startswith('reload'):
            self.switches.reloads.append(self.ip)

    def write_file(self, path: str, text: str) -> None:
        if self._accept(f"write {path}") is not None:
            raise LabSwitchError(f"Injected failure writing {path} on {self.ip}")
        self.switches.written.setdefault(self.ip, {})[path] = text
        directory, _, name = path.rpartition('/')
        if directory in self.switches.files(self.ip):
            self.switches.files(self.ip)[directory].add(name)

    def read_file(self, path: str) -> str:
        if self._accept(f"read {path}") is not None:
            raise LabSwitchError(f"Injected failure reading {path} on {self.ip}")
        directory, _, name = path.rpartition('/')
        if name not in self.switches.files(self.ip).get(directory, ()):
            raise LabSwitchError(f"{path}: No such file on {self.ip}")
        if path == INIT_CONFIG_PATH:
            return INIT_CONFIG
        return self.switches.written.get(self.ip, {}).get(path, '')


lab_switches = FakeLabSwitches()
