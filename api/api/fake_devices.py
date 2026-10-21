"""
In-memory stand-in for the lab equipment, used when settings.BLAB_DEVICES == 'fake'.

Local development runs against a snapshot of the production database, so it knows
about real switches and backbones. This module makes sure nothing it does reaches them.
"""
import hashlib
import logging
import re
import shlex
from contextlib import contextmanager
from datetime import timedelta
from fnmatch import fnmatch

from django.conf import settings
from django.core.management.base import CommandError

from . import clean_state
from .backbone import APIRequestError
from .lab_switch import (FREE_SPACE_COMMAND, INIT_CONFIG_PATH, LLDP_COMMAND, LOGIN_LOG_COMMAND, REFUSED_ON_SLAVE,
                         RELEASE_COMMAND, SAVE_CONFIG,
                         SWITCH_ADMIN, UPTIME_COMMAND, VC_TOPOLOGY_COMMAND, WORKING_CONFIG_PATH, CommandResult,
                         LabSwitchError, LoginRefused, Session, is_image, port_order, uptime_parts)
from .port_capability import CAPABILITY_COMMAND

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
    disabled ones, as the device does. 'show interfaces alias' prints each port's link: up
    while it is enabled and its cable (a Port record) is in (FakeLabSwitches.unplug).
    'show interfaces capability' gives each port's speeds: DEFAULT_SPEEDS unless set in `speeds`.
    """
    error = APIRequestError

    def __init__(self):
        super().__init__()
        self.config = {}    # ip -> list of configuration lines
        self.disabled = {}  # ip -> set of ports whose admin state is disabled
        self.lldp_on = set()  # ips whose LLDP sends (lldp nearest-bridge chassis lldpdu tx-and-rx)
        self.speeds = {}    # ip -> {port: (speeds as the device prints them, copper)}

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
        if cmd == "show interfaces alias":
            return self.interfaces_alias(ip)
        if cmd == CAPABILITY_COMMAND:
            return interfaces_capability(set(self.disabled.get(ip, ())) | set(unis_cabled(ip)),
                                         self.speeds.get(ip, {}))
        if cmd == "show system":
            return (f"System:\n  Description:  Alcatel-Lucent Enterprise OS6900-X72 8.9.107.R02 GA,\n"
                    f"  Name:         {self.name(ip)},\n")
        if cmd.startswith("lldp nearest-bridge chassis lldpdu "):
            (self.lldp_on.discard if cmd.endswith(" disable") else self.lldp_on.add)(ip)
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

    @staticmethod
    def name(ip: str) -> str:
        """Its system name, as show system and its LLDP give it."""
        return f"backbone-{ip}"

    def link_up(self, ip: str, uni: str) -> bool:
        """A UNI's link is up while it is enabled and its cable to a lab switch port is in (cable_of)."""
        cable = cable_of(ip, uni)
        return uni not in self.disabled.get(ip, ()) and cable is not None and cable not in lab_switches.unplugged

    def interfaces_alias(self, ip: str) -> str:
        """'show interfaces alias', as the device prints it: each port's admin state and link."""
        ports = set(self.disabled.get(ip, ())) | set(unis_cabled(ip))
        rows = ''.join(f" {port:<9} {'disable' if port in self.disabled.get(ip, ()) else 'enable':<10}"
                       f"{'up' if self.link_up(ip, port) else 'down':<8}0          0          \"\"\n"
                       for port in sorted(ports, key=port_order))
        return (" Chas/\n Slot/     Admin     Link        WTR      WTS       Alias\n"
                " Port      Status   Status      (sec)    (msec)\n"
                "--------+----------+---------+----------+----------+-----------------------\n" + rows)

    def reset(self):
        super().reset()
        self.config.clear()
        self.disabled.clear()
        self.lldp_on.clear()
        self.speeds.clear()


DEFAULT_SPEEDS = ('10/100/1G', True)  # a fake port's speeds and whether it is copper, unless set


def set_speeds(speeds: dict, ip: str, label: str, *ports: str, copper: bool = False) -> None:
    """Gives ports of a fake device other speeds ('1G/10G/25G'): a cage unless copper."""
    speeds.setdefault(ip, {}).update({port: (label, copper) for port in ports})


def interfaces_capability(ports, speeds: dict) -> str:
    """'show interfaces capability' as the device prints it (10.69.144.130), for those ports and any set in speeds."""
    rows = []
    for port in sorted(set(ports) | set(speeds), key=port_order):
        label, copper = speeds.get(port, DEFAULT_SPEEDS)
        crossover, autoneg, duplex = ('MDI/X/Auto', 'EN/DIS', 'Full/Half') if copper else ('-', 'DIS', 'Full')
        rows.append(f" {port:<7} CAP   {autoneg:>6}   Tx/Rx/Tx&Rx/DIS  {crossover:>10}  {label:>10}  {duplex} \n"
                    f" {port:<7} DEF   {autoneg[:2]:>6}               DIS  {'-':>10}  {'Auto':>10}  Auto\n")
    return (" Ch/Slot/Port   AutoNeg       Pause       Crossover     Speed    Duplex\n"
            "--------------+--------+----------------+-----------+----------+----------\n" + ''.join(rows))


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


def _ports():
    """
    The cables between a lab switch port and a UNI: each Port record, and those plugged in
    that BLab doesn't record yet (FakeLabSwitches.plug, a Contribution). None without a database.
    """
    plugged = [(ip, port, bb, uni) for (ip, port), (bb, uni) in lab_switches.plugged.items()]
    try:
        from .models import Port
        return list(Port.objects.values_list('switch__mngt_IP', 'port_switch', 'backbone', 'port_backbone')) + plugged
    except Exception:  # a test without a database
        return plugged


def cable_of(backbone_ip: str, uni: str):
    """(switch ip, port) at the other end of a UNI's cable, or None: a Port record is a cable."""
    return next(((ip, port) for ip, port, bb, bb_port in _ports() if (bb, bb_port) == (backbone_ip, uni)), None)


def unis_cabled(backbone_ip: str):
    return [bb_port for _, _, bb, bb_port in _ports() if bb == backbone_ip]


def uni_of(switch_ip: str, port: str):
    """(backbone ip, UNI) at the other end of a lab switch port's cable, or None."""
    return next(((bb, bb_port) for ip, p, bb, bb_port in _ports() if (ip, p) == (switch_ip, port)), None)


backbone = FakeBackbone()

# What a fake switch's init/vcboot.cfg holds when it isn't in the database (otherwise, the Golden
# init of its model, as prepare_switches writes it); a switch runs its init until told otherwise
INIT_CONFIG = """! Chassis:
system name "lab switch"
! VLAN:
vlan 1 admin-state enable
! IP:
ip interface "EMP-CHAS1" address 10.0.0.250 mask 255.255.255.0
"""
FAKE_PORTS = tuple(f'1/1/{n}' for n in range(1, 9))  # each fake switch's ports, links down unless cabled
AOS_RELEASE = '8.10.86.R04'  # what a fake switch runs unless told otherwise
IMAGE_SIZE = 241594635  # bytes of a fake switch's AOS image, as Yos.img on 10.69.144.132
CONFIG_SIZE = 667  # bytes of any other of its files, as init/vcboot.cfg on 10.69.144.132
DIRECTORIES = ('pkg',)  # what `ls -l` lists as a directory in init/, working/ and certified/
FREE_SPACE = 15112011776  # bytes free on /flash, as on 10.69.144.132
UPTIME = timedelta(days=13, hours=13, minutes=42, seconds=52)  # how long a fake switch is up until it reloads
# How AOS refuses some commands for a while once it answers after a reload (10.69.144.132, 2026-10-13)
NOT_READY_ERROR = 'ERROR: system has not reached the ready state, please try again later\n'


class FakeLabSwitches(FailureInjection):
    """
    Lab switches as far as banner, Cleanup, prepare_switches, Switch accounts and Inspection
    go: the files written to each one, and the entries of its directories under /flash ('' is
    /flash itself; a new one holds only its Baseline); reloads are recorded; its local users
    and their passwords, and those allowed SU. For Inspections:
    how many chassis it has (more than one is a VC), the ports with a cable (link up), the
    config it runs, and whether it refuses BLab's login or can't be reached. `outputs`
    replaces a show command's output, to feed the parsers junk. For the Sweep: the logins
    since the last reload, BLab's own included. A reload starts a new login log, as on the
    device, and runs working/vcboot.cfg: the init config, unless something else was written
    there since working/ was last emptied (a Restore). For Snapshots: each one's AOS release.
    For VCs (#60): which switches are slaves of which master. A slave refuses `user` commands
    and `write memory`; the master's `write memory` copies its local users to its slaves.
    For the Switch worker: how long each one is up, which a reload sets back to nothing; one
    `slow_to_reload` takes the reload but goes on running as it was until come_up() (AOS 8.10
    "verifies and copies images" first, for minutes); one `not_ready` answers SSH but refuses
    every show command except show system, and the user commands, as AOS does for a while once up.
    Each AOS release keeps its local users in a table of its own (/flash/system/userTable<n>), so a
    reload into another release brings back the users that release last had (`older_users`), as on
    10.69.144.155 and .169 on 2026-10-19 (#74).
    Each file has a size in bytes (`ls -l`), IMAGE_SIZE for an image unless set in `sizes`; a copy
    keeps it, unless `cut_short` makes the next copy into a path stop at fewer bytes (#80).
    Each port runs at DEFAULT_SPEEDS unless set in `speeds` (show interfaces capability, #82).
    For AOS releases (#48): each image's release (`image_releases`, else the switch's), which a
    copy or a move takes along and show microcode <directory> lists; an image copied from the BLab
    server (put_file) is the release it says it is (FakeImageServer), with its md5; `free` bytes
    on /flash.
    """
    error = LabSwitchError
    INIT = ('Uos.img', 'pkg', 'vcboot.cfg')
    FLASH = ('init', 'working', 'certified', 'switch', 'system', 'python', 'pmd', '.ssh', 'command.log',
             'swlog_chassis1')

    def __init__(self):
        super().__init__()
        self.written = {}         # ip -> {path: text}
        self.directories = {}     # ip -> {directory: set of entries}
        self.reloads = []         # ip, for each reload started
        self.chassis = {}         # ip -> number of chassis (1 unless set)
        self.cabled = {}          # ip -> set of ports whose link is up
        # (ip, port) of the cables to a UNI that are out (a Missing cable): a port paired with a
        # UNI by a Port record is cabled to it, and its link is up while that UNI is enabled
        self.unplugged = set()
        self.plugged = {}  # (ip, port) -> (backbone ip, UNI): a cable to a UNI that no Port records yet
        self.present = set()  # ips that answer on SSH when onboarding scans a subnet (#55); others don't
        self.factory_admin = set()  # ips whose admin still has a factory password, not the lab's secret one
        self.running_config = {}  # ip -> config text (INIT_CONFIG unless set)
        self.outputs = {}         # ip -> {show command: output to print instead}
        self.refused = set()      # ips that refuse BLab's login
        self.unreachable = set()  # ips that don't answer
        self.users = {}           # ip -> {local user name: password}, besides admin
        self.password_history = {}  # ip -> {local user name: the passwords it had, latest last}
        self.su = {}              # ip -> local user names allowed SU (admin always is)
        self.older_users = {}     # ip -> local users of the release its next reload boots into
        self.logins = {}          # ip -> [(user, address)] since the last reload
        self.releases = {}        # ip -> the AOS release of its image (AOS_RELEASE unless set)
        self.vc_masters = {}      # ip of a VC slave -> ip of its master
        self.uptime = {}          # ip -> how long it has been up (UPTIME unless set or reloaded)
        self.slow_to_reload = set()  # ips that take a reload but only come up once come_up() is called
        self.not_ready = set()    # ips that refuse most commands: AOS hasn't reached the ready state
        self.sizes = {}           # ip -> {'directory/name': bytes}, for a file not of its usual size
        self.cut_short = {}       # ip -> {'directory/name': bytes} the next copy there stops at
        self.speeds = {}          # ip -> {port: (speeds as the device prints them, copper)}
        self.image_releases = {}  # ip -> {'directory/name': release}, for an image not of the switch's release
        self.md5s = {}            # ip -> {'directory/name': md5}, of an image copied from the BLab server
        self.free = {}            # ip -> bytes free on /flash (FREE_SPACE unless set)

    def size(self, ip: str, path: str) -> int:
        """A file's size in bytes: as set, or the usual one for its kind."""
        default = IMAGE_SIZE if is_image(path) else CONFIG_SIZE
        return self.sizes.get(ip, {}).get(path, default)

    def release_of(self, ip: str, path: str) -> str:
        """The AOS release of an image: as copied there, or the switch's."""
        return self.image_releases.get(ip, {}).get(path) or self.releases.get(ip, AOS_RELEASE)

    def move_along(self, ip: str, source: str, target: str, keep: bool) -> None:
        """A file's release and md5 go with it when it is copied (keep) or moved."""
        for table in (self.image_releases, self.md5s):
            entries = table.setdefault(ip, {})
            if source in entries:
                entries[target] = entries[source] if keep else entries.pop(source)
            else:
                entries.pop(target, None)

    def form_vc(self, master: str, *slaves: str) -> None:
        """Makes the switches one VC, `master` its master."""
        for ip in (master,) + slaves:
            self.vc_masters.pop(ip, None)
        self.vc_masters.update({slave: master for slave in slaves})

    def take_over(self, ip: str) -> None:
        """A VC slave becomes its VC's master (the master reloaded or failed)."""
        old = self.vc_masters[ip]
        self.form_vc(ip, *sorted(self.vc_members(old) - {ip}))

    def vc_members(self, ip: str) -> set:
        """Every switch of ip's VC, itself included."""
        master = self.vc_masters.get(ip, ip)
        return {master} | {slave for slave, its in self.vc_masters.items() if its == master}

    @staticmethod
    def mac(ip: str) -> str:
        return '2c:fa:a2:' + ':'.join(f'{int(n):02x}' for n in ip.split('.')[1:])

    def files(self, ip: str) -> dict:
        return self.directories.setdefault(ip, {
            '': set(self.FLASH), 'init': set(self.INIT), 'working': set(self.INIT), 'certified': set(self.INIT),
            'python': {'default_broker_vso.py'}, 'pmd': {'work'}, '.ssh': set()})

    def init_config(self, ip: str) -> str:
        """What init/vcboot.cfg holds: as written, or the Golden init of the switch's model."""
        if INIT_CONFIG_PATH in self.written.get(ip, {}):
            return self.written[ip][INIT_CONFIG_PATH]
        try:
            from .models import Switch
            model = Switch.objects.filter(mngt_IP=ip).values_list('model', flat=True).first()
        except Exception:  # a test without a database
            model = None
        return clean_state.golden_init_config(model) if model else INIT_CONFIG

    def cable(self, ip: str, *ports: str) -> None:
        """Plugs a cable into each port: its link comes up."""
        self.cabled.setdefault(ip, set()).update(ports)

    def unplug(self, ip: str, *ports: str) -> None:
        """Pulls the cable out of each port, its cable to a UNI included: its link goes down."""
        self.cabled.get(ip, set()).difference_update(ports)
        self.unplugged.update((ip, port) for port in ports)

    def secure_admin(self, ip: str) -> bool:
        """lab_switch.secure_admin: gives admin the lab's secret password if it has a factory one."""
        if ip not in self.factory_admin:
            return False
        self.commands += [(ip, 'user admin password ...'), (ip, SAVE_CONFIG)]
        self.factory_admin.discard(ip)
        return True

    def lldp(self, ip: str) -> str:
        """show lldp remote-system: the Backbone at the other end of each cable, while its UNI is enabled and its LLDP on."""
        sections = []
        for switch_ip, port, bb, uni in _ports():
            if switch_ip == ip and bb in backbone.lldp_on and backbone.link_up(bb, uni):
                sections.append(f"Remote LLDP nearest-bridge Agents on Local Port {port}:\n"
                                f"    Chassis 2c:fa:a2:22:b9:f0, Port {uni}:\n"
                                f"      System Name                 = {backbone.name(bb)},\n"
                                f"      Port Description            = Alcatel-Lucent OS10K XNI {uni},\n")
        return '\n'.join(sections)

    def ports(self, ip: str) -> set:
        """Its ports: FAKE_PORTS, and any cabled or paired with a UNI."""
        paired = {port for switch_ip, port, _, _ in _ports() if switch_ip == ip}
        return set(FAKE_PORTS) | self.cabled.get(ip, set()) | paired

    def plug(self, ip: str, port: str, backbone_ip: str, uni: str) -> None:
        """Plugs a new cable between a port and a UNI: its link is up while that UNI is enabled."""
        self.plugged[(ip, port)] = (backbone_ip, uni)

    def link_up(self, ip: str, port: str) -> bool:
        """Up when cabled, or when paired with a UNI that is enabled and the cable between them is in."""
        if port in self.cabled.get(ip, ()):
            return True
        uni = uni_of(ip, port)
        return uni is not None and (ip, port) not in self.unplugged and uni[1] not in backbone.disabled.get(uni[0], ())

    def log_in(self, ip: str, user: str, address: str) -> None:
        """Someone logs in to the switch: it lands in its login log."""
        self.logins.setdefault(ip, []).append((user, address))

    @contextmanager
    def connect(self, ip: str):
        if ip in self.unreachable:
            raise LabSwitchError(f"Cannot connect to {ip}: timed out")
        if ip in self.refused:
            raise LoginRefused(f"{ip} refused the login of admin: Authentication failed.")
        self.log_in(ip, SWITCH_ADMIN, settings.BLAB_SOURCE_ADDRESSES[0])
        yield FakeLabSwitchSession(self, ip)

    def come_up(self, ip: str) -> None:
        """The switch comes up again, as a reload makes it: it runs working/vcboot.cfg, with a new login log."""
        loaded = self.written.get(ip, {}).get(WORKING_CONFIG_PATH)
        if loaded is None:
            self.running_config.pop(ip, None)
        else:
            self.running_config[ip] = loaded
        self.logins.pop(ip, None)
        self.uptime[ip] = timedelta(0)
        if ip in self.older_users:
            self.users[ip] = self.older_users.pop(ip)

    def login_log(self, ip: str) -> str:
        """The login lines of the switch's log, as AOS writes them."""
        return ''.join(f"2008 Jan 12 05:28:{n % 60:02d}.469 OS6900 swlogd SES AAA INFO: Login by {user} from "
                       f"{address} through SSH Success [in LoginAaaSession::handleLoginResult()]\n"
                       for n, (user, address) in enumerate(self.logins.get(ip, [])))

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
            return '\n'.join(
                f"Chassis/Slot/Port {port}    :\n"
                f" Operational Status     : {'up' if self.link_up(ip, port) else 'down'},\n"
                f" Port-Down/Violation Reason: None,\n"
                for port in sorted(self.ports(ip), key=port_order))
        if cmd == LLDP_COMMAND:
            return self.lldp(ip)
        if cmd == CAPABILITY_COMMAND:
            return interfaces_capability(self.ports(ip), self.speeds.get(ip, {}))
        if cmd == 'show configuration snapshot':
            return self.running_config.get(ip) or self.init_config(ip)
        if cmd == RELEASE_COMMAND or cmd.startswith(RELEASE_COMMAND + ' '):
            directory = cmd[len(RELEASE_COMMAND):].strip() or 'working'
            images = sorted(name for name in self.files(ip).get(directory, ()) if is_image(name))
            return (f"   /flash/{directory}\n   Package           Release                 Size      Description\n"
                    "-----------------+-------------------------+---------+---------------------\n"
                    + ''.join(f"{name:<17} {self.release_of(ip, f'{directory}/{name}'):<25} "
                              f"{self.size(ip, f'{directory}/{name}')} Alcatel-Lucent OS\n" for name in images))
        if cmd == VC_TOPOLOGY_COMMAND:
            master = self.vc_masters.get(ip, ip)
            members = sorted(self.vc_members(ip), key=lambda m: (m != master, m))
            chassis = {member: n for n, member in enumerate(members, start=1)}
            rows = ''.join(f" {chassis[m]}     {'Slave ' if m in self.vc_masters else 'Master'}       Running"
                           f"             {chassis[m]}        100   65     {self.mac(m)}\n" for m in members)
            return (f"Local Chassis: {chassis[ip]}\n Chas  Role         Status              Chas ID  Pri   Group  "
                    "MAC-Address\n-----+------------+-------------------+--------+-----+------+------------------\n"
                    + rows)
        if cmd == UPTIME_COMMAND:
            days, hours, minutes, seconds = (n for n, _ in uptime_parts(self.uptime.get(ip, UPTIME)))
            return ("System:\n"
                    f"  Description:  Alcatel-Lucent Enterprise OS6860-48 {self.releases.get(ip, AOS_RELEASE)} GA,\n"
                    f"  Up Time:      {days} days {hours} hours {minutes} minutes and {seconds} seconds,\n"
                    "  Name:         OS6860-48,\n")
        if cmd == 'show user':
            names = ['admin', 'default (*)'] + list(self.users.get(ip, {}))
            su = {'admin'} | self.su.get(ip, set())
            return ''.join(f"User name = {name},\n  Read/Write for domains  = All ,\n  SSH/SSH-PKIX allowed    = YES,\n"
                           f"  SU allowed    = {'YES' if name in su else 'NO'},\n"
                           for name in names)
        return ''

    def reset(self):
        super().reset()
        self.written.clear()
        self.directories.clear()
        self.reloads.clear()
        self.chassis.clear()
        self.cabled.clear()
        self.unplugged.clear()
        self.plugged.clear()
        self.present.clear()
        self.factory_admin.clear()
        self.running_config.clear()
        self.outputs.clear()
        self.refused.clear()
        self.unreachable.clear()
        self.users.clear()
        self.password_history.clear()
        self.su.clear()
        self.older_users.clear()
        self.logins.clear()
        self.releases.clear()
        self.vc_masters.clear()
        self.uptime.clear()
        self.slow_to_reload.clear()
        self.not_ready.clear()
        self.sizes.clear()
        self.cut_short.clear()
        self.speeds.clear()
        self.image_releases.clear()
        self.md5s.clear()
        self.free.clear()


class FakeLabSwitchSession(Session):
    def __init__(self, switches: FakeLabSwitches, ip: str):
        self.switches = switches
        self.ip = ip

    def _accept(self, cmd: str):
        """Records a command that reached the switch (one made to raise never did)."""
        status = self.switches.injected_exit_status(self.ip, cmd)
        self.switches.commands.append((self.ip, cmd))
        # Never a Switch account's password in the log, as the real switch's messages don't show it
        logger.info("[fake switch %s] %s", self.ip, re.sub(r'^(user \S+ password) .* (read-write all)$', r'\1 ... \2', cmd))
        return status

    def run(self, cmd: str) -> CommandResult:
        status = self._accept(cmd)
        if status is not None:
            return CommandResult(status, error=f"injected exit status {status}")
        if self.ip in self.switches.not_ready and cmd != UPTIME_COMMAND and cmd.startswith(
                ('show ', 'user ', 'no user ', SAVE_CONFIG)):
            return CommandResult(0, NOT_READY_ERROR)
        if cmd.startswith('show '):
            return CommandResult(0, self.switches.show(self.ip, cmd))
        if cmd == LOGIN_LOG_COMMAND:
            log = self.switches.login_log(self.ip)
            return CommandResult(0 if log else 1, log)  # grep: 1 when nothing matches
        if cmd.startswith(('user ', 'no user ')) or cmd == SAVE_CONFIG:
            if self.ip in self.switches.vc_masters:
                return CommandResult(0, f'ERROR: {REFUSED_ON_SLAVE} \n\n')
            if cmd == SAVE_CONFIG:
                return self._save()
            return self._user(cmd)
        files = self.switches.files(self.ip)
        words = cmd.split()
        if cmd == FREE_SPACE_COMMAND:
            return CommandResult(0, f"Chassis 1 /flash {self.switches.free.get(self.ip, FREE_SPACE)} bytes free\n")
        if words[0] == 'md5sum':
            directory, _, name = words[1].rpartition('/')
            if name not in files.get(directory, ()):
                return CommandResult(1, error=f"md5sum: {words[1]}: No such file or directory")
            md5 = self.switches.md5s.get(self.ip, {}).get(words[1]) or hashlib.md5(words[1].encode()).hexdigest()
            return CommandResult(0, f"{md5}  {words[1]}\n")
        if words[:2] == ['mv', '-f']:
            return self._move(files, words[2], words[3])
        if words[:3] == ['rm', '-rf', '--']:
            return self._remove(files, shlex.split(cmd)[3])
        if words[:2] == ['ls', '-a']:
            directory = self._under_flash(words[2])
            if directory not in files:
                return CommandResult(2, error=f"ls: cannot access '{words[2]}': No such file or directory")
            return CommandResult(0, '\n'.join(['.', '..'] + sorted(files[directory])))
        if words[:2] == ['rm', '-f']:
            directory, _, name = words[2].rpartition('/')
            files.get(directory, set()).discard(name)
            self.switches.written.get(self.ip, {}).pop(words[2], None)
            return CommandResult(0)
        if words[:2] == ['rm', '-rf']:
            if words[2].endswith('/*'):
                if words[2][:-2] in files:
                    files[words[2][:-2]] = set()
                written = self.switches.written.get(self.ip, {})
                for path in [path for path in written if path.startswith(words[2][:-1])]:
                    del written[path]
            else:
                files.pop(words[2], None)
        elif words[:2] == ['mkdir', '-p']:
            files.setdefault(words[2], set())
        elif words[0] == 'cp':
            return self._copy(files, words[-2], words[-1].rstrip('/'))
        elif words[:2] == ['ls', '-l']:
            directory = words[2].rstrip('/')
            if directory not in files:
                return CommandResult(1, error=f"ls: {words[2]}: No such file or directory")
            return CommandResult(0, self._long_listing(files, directory))
        elif words[0] == 'ls':
            directory = words[1].rstrip('/')
            if directory not in files:
                return CommandResult(2, error=f"ls: {words[1]}: No such file or directory")
            return CommandResult(0, '\n'.join(sorted(files[directory])))
        return CommandResult(0)

    @staticmethod
    def _under_flash(path: str) -> str:
        """A path as a key of files(): relative to /flash, '' for /flash itself."""
        path = path.rstrip('/')
        if path == '/flash':
            return ''
        return path[len('/flash/'):] if path.startswith('/flash/') else path

    def _remove(self, files: dict, path: str) -> CommandResult:
        """rm -rf -- /flash/<path>: the entry goes from its directory, and so does what it held."""
        relative = self._under_flash(path)
        directory, _, name = relative.rpartition('/')
        if directory in files:
            files[directory].discard(name)
        for key in [key for key in files if key == relative or key.startswith(relative + '/')]:
            del files[key]
        return CommandResult(0)

    def _user(self, cmd: str) -> CommandResult:
        """
        'user <name> password <pw> read-write all' and 'no user <name>'; refusals as AOS prints them,
        including its password history (on 10.69.144.132: the current one and the 3 before it).
        """
        words = shlex.split(cmd)
        users = self.switches.users.setdefault(self.ip, {})
        if words[0] == 'no':
            if words[2] not in users:
                return CommandResult(0, 'ERROR: Unknown user\n')
            del users[words[2]]
            self.switches.su.get(self.ip, set()).discard(words[2])
            self.switches.password_history.get(self.ip, {}).pop(words[2], None)
            return CommandResult(0)
        name, password = words[1], words[3]
        if len(name) > 63:
            return CommandResult(0, 'ERROR: User name length should be between 1 and 63 characters\n')
        if len(password) < 8:
            return CommandResult(0, 'ERROR: Password must contain at least 8 characters\n')
        if len(password) > 30:
            return CommandResult(0, 'ERROR: The max password length is 30.\n')
        history = self.switches.password_history.setdefault(self.ip, {}).setdefault(name, [])
        if password in history[-4:]:
            return CommandResult(0, 'ERROR: New password can not be the same as your current password '
                                    'or any of your previous 3 passwords\n')
        history.append(password)
        users[name] = password
        return CommandResult(0)

    def _save(self) -> CommandResult:
        """write memory: the local users reach every chassis of its VC, as AOS synchronises them."""
        users = self.switches.users.get(self.ip, {})
        history = self.switches.password_history.get(self.ip, {})
        for slave in self.switches.vc_members(self.ip) - {self.ip}:
            self.switches.users[slave] = dict(users)
            self.switches.password_history[slave] = {name: list(had) for name, had in history.items()}
        return CommandResult(0, '\n')

    def _long_listing(self, files: dict, directory: str) -> str:
        """ls -l, as busybox prints it on AOS (10.69.144.132, AOS 8.10.86.R04, 2026-10-20)."""
        lines = ['total 235944']
        for name in sorted(files[directory]):
            if name in DIRECTORIES or f'{directory}/{name}' in files:
                lines.append(f"drwxr-xr-x    5 admin    user          4096 Jan 15 04:43 {name}")
            else:
                size = self.switches.size(self.ip, f'{directory}/{name}')
                lines.append(f"-rw-r--r--    1 admin    user     {size:>9} Jan  6 05:35 {name}")
        return '\n'.join(lines) + '\n'

    def _copy(self, files: dict, source: str, target: str) -> CommandResult:
        """cp [-r] dir/pattern target/: fails, like the shell, when nothing matches. Sizes go along."""
        directory, _, pattern = source.partition('/')
        copied = {name for name in files.get(directory, ()) if fnmatch(name, pattern)}
        if not copied or target not in files:
            return CommandResult(1, error=f"cp: cannot copy {source} to {target}/")
        files[target] |= copied
        sizes = self.switches.sizes.setdefault(self.ip, {})
        cut_short = self.switches.cut_short.get(self.ip, {})
        for name in copied:
            path = f'{target}/{name}'
            sizes[path] = cut_short.pop(path, self.switches.size(self.ip, f'{directory}/{name}'))
            self.switches.move_along(self.ip, f'{directory}/{name}', path, keep=True)
        return CommandResult(0)

    def _move(self, files: dict, source: str, target: str) -> CommandResult:
        """mv -f dir/name dir/name: the file takes its new name, with its size, release and md5."""
        directory, _, name = source.rpartition('/')
        target_directory, _, target_name = target.rpartition('/')
        if name not in files.get(directory, ()) or target_directory not in files:
            return CommandResult(1, error=f"mv: can't rename '{source}': No such file or directory")
        files[directory].discard(name)
        files[target_directory].add(target_name)
        sizes = self.switches.sizes.setdefault(self.ip, {})
        sizes[target] = self.switches.size(self.ip, source)
        sizes.pop(source, None)
        self.switches.move_along(self.ip, source, target, keep=False)
        return CommandResult(0)

    def put_file(self, local_path: str, path: str) -> None:
        """A file of the BLab server copied to the switch: an image is the release it says it is (FakeImageServer)."""
        if self._accept(f"put {path}") is not None:
            raise LabSwitchError(f"Injected failure copying {path} to {self.ip}")
        with open(local_path, 'rb') as file:
            content = file.read()
        directory, _, name = path.rpartition('/')
        if directory not in self.switches.files(self.ip):
            raise LabSwitchError(f"{path}: No such directory on {self.ip}")
        self.switches.files(self.ip)[directory].add(name)
        self.switches.sizes.setdefault(self.ip, {})[path] = self.switches.cut_short.get(self.ip, {}).pop(
            path, len(content))
        self.switches.md5s.setdefault(self.ip, {})[path] = hashlib.md5(content).hexdigest()
        release = FakeImageServer.release_in(content)
        if release:
            self.switches.image_releases.setdefault(self.ip, {})[path] = release

    def run_confirmed(self, cmd: str) -> None:
        if self._accept(cmd) is None and cmd.startswith('reload'):
            self.switches.reloads.append(self.ip)
            if self.ip not in self.switches.slow_to_reload:
                self.switches.come_up(self.ip)

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
            return self.switches.init_config(self.ip)
        if path == WORKING_CONFIG_PATH and path not in self.switches.written.get(self.ip, {}):
            return self.switches.init_config(self.ip)  # what a Cleanup copied from init/
        return self.switches.written.get(self.ip, {}).get(path, '')


class FakeImageServer:
    """
    ALE's image server (api.aos_release.HttpImageServer) as far as BLab uses it: each release's
    images per family, and its imgsha256sum, under Release/, and under Release/secure/ for the
    families in `secure` (Secure Boot variants). An image is a short text naming its release,
    which a fake switch reads when the image is copied to it. `unreachable` makes every request
    fail; `damaged` image names come with other bytes than their sha256 says. `fetched` lists the
    URLs of the images downloaded.
    """
    PATH = re.compile(r'/OS_(\d+(?:_\d+)+)_(R\d+)/(\w+)/Release(/secure)?/([^/]+)$')
    SECURE = ('yukon', 'nandi', 'vindhya', 'aravalli', 'whitney')  # as on 10.46.4.37, OS_8_11_162_R01

    def __init__(self):
        self.reset()

    def reset(self):
        self.unreachable = False
        self.damaged = set()
        self.secure = set(self.SECURE)
        self.fetched = []

    @staticmethod
    def release_in(content: bytes):
        """The release a fake image names, or None if it isn't one."""
        match = re.match(rb'AOS (\S+) ', content)
        return match.group(1).decode() if match else None

    def _found(self, url: str):
        """(name, the family's images, bytes of the image) for a URL under a release's family, or None."""
        from .aos_release import IMAGES
        match = self.PATH.search(url)
        if not match:
            return None
        numbers, rel, family, secure, name = match.groups()
        if family not in IMAGES or (secure and family not in self.secure):
            return None
        release = '.'.join(numbers.split('_')) + '.' + rel
        return name, IMAGES[family], f"AOS {release} {family}{' secure' if secure else ''} {name}\n".encode()

    def text(self, url: str):
        from .aos_release import CHECKSUMS, ImageError
        if self.unreachable:
            raise ImageError("cannot reach the image server: timed out")
        found = self._found(url)
        if found is None or found[0] != CHECKSUMS:
            return None
        base = url[:-len(CHECKSUMS)]
        return ''.join(f"{name}: {hashlib.sha256(self._found(base + name)[2]).hexdigest()}\n" for name in found[1])

    def fetch(self, url: str, path: str) -> None:
        from .aos_release import ImageError
        if self.unreachable:
            raise ImageError(f"cannot download {url}: timed out")
        found = self._found(url)
        if found is None or found[0] not in found[1]:
            raise ImageError(f"{url} answered 404")
        self.fetched.append(url)
        with open(path, 'wb') as file:
            file.write(found[2] + (b'damaged' if found[0] in self.damaged else b''))


image_server = FakeImageServer()
lab_switches = FakeLabSwitches()
