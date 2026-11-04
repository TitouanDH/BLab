"""
Clean state (see CONTEXT.md, docs/adr/0006): once a Reservation ends, nothing of it stays on
the Switch. What a clean Switch holds is its Baseline (AOS's own files, and the lab managers'
extras an admin lists as BaselineExtra rows) and its Golden init; anything else is Unwanted.

This module only decides; it never talks to a Switch. The Inspection (api.inspection) reports
what it finds, and the Cleanup (api.switch_worker, through LabSwitch.restore_init_and_reload)
repairs it: it removes the Unwanted files and every local user but `admin` and the lab
managers' logins, and rewrites init/vcboot.cfg from the Golden init if a user changed it, and
init/vcsetup.cfg from the Golden VC setup if it isn't that.

BLAB_CLEAN_STATE_MODE (settings) is 'report' until the lab has been told: Cleanups then only
say what they would remove or rewrite, and Inspections list it as warnings. 'enforce' repairs,
and makes whatever is left a reason the Switch isn't clean.

The Baseline was built from the lab on 2026-10-12 (#38): 10.69.144.132 (OS6900-X48C6, AOS
8.10.86.R04) after its Cleanup, and a read-only listing of every reachable lab Switch (OS6900,
OS6860E, OS6860N, OS6870 and OS9900 families, AOS 8.9 to 8.11). It is one list for every
family: what one family's AOS writes is simply absent on the others. Only the directories in
SCANNED are compared, and only their own entries: AOS's own trees (switch/, system/, network/,
working/ and certified/, which every Cleanup replaces from init/...) are never looked into.
When in doubt whether AOS writes something, it is in the Baseline: a file left behind is
better than a Switch that doesn't boot.

AOS 6 Switches (OS6450 and kin, #108, docs/adr/0012) have no init/ of their own: AOS 6 boots
working/boot.cfg and the images beside it. BLab makes them an init/ that holds only its Golden
init, init/boot.cfg (the Golden init of AOS 6, its management included, as an AOS 6 Switch has
no EMP and no vcsetup.cfg), and its Cleanup copies that file into working/; the images stay as
they are, since BLab never installs a release on AOS 6. AOS 6's own files in /flash are part of
the Baseline (read on 10.69.144.148, an OS6450-48 on 6.7.2.28.R06, 2026-11-04).
"""
import ipaddress
import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Dict, Iterable, List, Optional, Sequence

from django.conf import settings

from . import management_network

FLASH = '/flash'
INIT_CONFIG_PATH = 'init/vcboot.cfg'
INIT_VC_SETUP_PATH = 'init/vcsetup.cfg'
AOS6_INIT_CONFIG_PATH = 'init/boot.cfg'  # an AOS 6 Switch's Golden init (#108)
# Model prefixes of the families that run AOS 6 (#108): the OS6450 is the lab's; the others are
# its AOS 6 kin, so that one is never taken for an AOS 8 Switch
AOS6_MODELS = ('OS6250', 'OS6350', 'OS6400', 'OS6450', 'OS6850', 'OS6855', 'OS9700', 'OS9800')


def runs_aos6(model: Optional[str]) -> bool:
    """Whether Switches of this model run AOS 6 (#108)."""
    return bool(model) and model.strip().upper().startswith(AOS6_MODELS)


def init_config_path(model: Optional[str]) -> str:
    """Where a Switch of this model keeps its Golden init: init/boot.cfg on AOS 6, init/vcboot.cfg on AOS 8."""
    return AOS6_INIT_CONFIG_PATH if runs_aos6(model) else INIT_CONFIG_PATH
# The directories under /flash whose entries are compared with the Baseline ('' is /flash itself)
SCANNED = ('', '.ssh', 'python', 'pmd', 'init')

# Paths under /flash (fnmatch patterns, case-sensitive) that a clean Switch may hold
BASELINE = (
    # AOS's own directories
    'bootflash', 'certified', 'config-backup-recovery', 'extensions', 'foss', 'init', 'lost+found', 'network',
    'pmd', 'python', 'switch', 'swlog_archive', 'system', 'working', '.ssh',
    'afn', 'externalCPU', 'onie', 'webview', 'vc-dir',     # some families or releases only
    'CMMA', 'CMMB', 'ni[0-9]*', 'hostpmd', 'diags', 'tmp',  # OS9900 chassis
    # Files AOS writes there itself, and logs it rewrites on its own (never compared)
    'agcmm-memlog', 'fpga_name', 'hwinfo', 'loaded', 'wpa_supplicant.conf', 'poeswver', 'coreboot_ver',
    'cpu_model', 'currentRunning', 'tmpCurRunning', 'dmesg.txt', 'stty.out',
    'swlog*', 'NI*_consoleLog', '*.log', '*.log.*', '*_log', '*_log.*', 'tech_support*', '*.err',
    '.fuse_hidden*',  # files still open after their removal: they go when the Switch reboots
    # Licenses: once applied, AOS needs them, and they can't be fetched again
    '*lic*', '*LIC*',
    # AOS 6's own files in /flash (10.69.144.148, 2026-11-04): its boot parameters and stack slot,
    # crash dumps, and what it writes there itself
    'boot.params', 'boot.slot.cfg', 'Crash.pmd', 'installed', 'random-seed', '*.ktrace', 'libcurl_log_backup',
    'pre_banner.txt',
    # Inside the scanned directories
    'python/default_*', 'python/iec_config.py',
    'pmd/work',
    # The Golden init: an AOS image (one or more, the image of the family), pkg/, and the config;
    # on AOS 6, the config alone (#108)
    'init/*.img', 'init/pkg', 'init/vcboot.cfg', 'init/boot.cfg',
)
# What init/ must hold, as Baseline patterns, with how to name what's missing
INIT_ESSENTIALS = (('*.img', 'an AOS image (*.img)'), ('pkg', 'pkg/'), ('vcboot.cfg', 'vcboot.cfg'))
AOS6_INIT_ESSENTIALS = (('boot.cfg', 'boot.cfg'),)
# Local users that stay: BLab's own login, and AOS's template for new users
BUILT_IN_LOGINS = frozenset({'admin', 'default'})

# Lines of show user: each user's block starts with "User name = admin," ("default (*)," for the
# template); AOS 8.10 adds "SU allowed = YES," (older releases don't print it)
USER_BLOCK = re.compile(r'^\s*User name\s*=\s*(.*?)(?:\s*\(\*\))?,?\s*$', re.IGNORECASE | re.MULTILINE)
SU_ALLOWED = re.compile(r'^\s*SU allowed\s*=\s*(\w+)', re.IGNORECASE | re.MULTILINE)
SHOWN = 5  # paths or names quoted in one reason


def enforcing() -> bool:
    """Whether Cleanups repair the clean state and Inspections fail on it, or only report it."""
    return settings.BLAB_CLEAN_STATE_MODE == 'enforce'


# The static routes every lab Switch has, via its Management network subnet's gateway (the Golden init's,
# and those a contributed Switch is given at its console, #59)
GOLDEN_INIT_ROUTES = ('10.0.0.0/8', '10.69.0.0/16', '135.118.225.0/24')
LAB_GATEWAY = '10.69.144.129'  # the gateway of a Switch outside every subnet of the Management network
MANAGEMENT_INTERFACE = 'management'  # the IP interface of a Management port that is a front port (#81)


@dataclass(frozen=True)
class Management:
    """
    How a Switch reaches the Management network (#81): its address, its subnet's gateway and mask
    length, and its Management port when that is a front port ('': its EMP, set up at its console).
    """
    ip: str = ''
    gateway: str = LAB_GATEWAY
    prefixlen: int = 26
    port: str = ''


def management_of(ip: str, port: str = '') -> Management:
    """The Management of a Switch at this address: its subnet's gateway (the lab's when it is in none)."""
    try:
        subnet = management_network.subnet_of(ip)
    except ValueError:  # not an address (NO_MANAGEMENT_IP)
        subnet = None
    if subnet is None:
        return Management(ip=ip, port=port)
    return Management(ip=ip, gateway=subnet.gateway, prefixlen=subnet.network.prefixlen, port=port)


def golden_init_config(model: str, management: Management = Management()) -> str:
    """init/vcboot.cfg of a lab Switch of this model: names it after its model, turns on the LLDP TLVs
    populate_ports discovers with, and routes the lab through its subnet's gateway. On AOS 6,
    init/boot.cfg (aos6_golden_init_config)."""
    routes = '\n'.join(f'ip static-route {route} gateway {management.gateway} metric 1' for route in GOLDEN_INIT_ROUTES)
    if runs_aos6(model):
        return aos6_golden_init_config(model, management, routes)
    return f'''system name "{model}"
session prompt default "{model}"
session cli timeout 5555

health threshold memory 80

aaa authentication console "local"
aaa authentication ssh "local"

lldp nearest-bridge chassis tlv management port-description enable system-name enable system-description enable
lldp nearest-bridge chassis tlv management management-address enable
lldp nearest-bridge chassis tlv dot3 mac-phy enable

{routes}

auto-fabric admin-state disable
mvrp disable
command-log enable
'''


def aos6_golden_init_config(model: str, management: Management, routes: str) -> str:
    """
    init/boot.cfg of an AOS 6 Switch (#108): the Golden init in AOS 6's words, with session
    accounting so that its log names who logs in (the Sweep reads it, LabSwitch.logins_since_boot),
    and its Management port's config, which AOS 6 can only keep there. Booted on 10.69.144.148
    (OS6450-48, 6.7.2.28.R06, 2026-11-04): show configuration snapshot then said every line back
    (health threshold memory 80 is AOS 6's default, so it isn't written; the system name is unquoted as it prints it).
    """
    management_config = ''.join(f'{line}\n' for line in aos6_management_lines(management))
    return f'''system name {model}
session prompt default "{model}->"
session timeout cli 5555

aaa authentication default "local"
aaa authentication ssh "local"
aaa accounting session "local"

lldp chassis tlv management port-description enable system-name enable system-description enable
lldp chassis tlv management management-address enable
lldp chassis tlv dot3 mac-phy enable

{management_config}
{routes}

command-log enable
'''


def aos6_management_lines(management: Management) -> List[str]:
    """
    The config of an AOS 6 Switch's Management port when it is a front port (none for an EMP):
    untagged in the Management network's VLAN, with the Switch's address on it, in AOS 6's words.
    """
    if not management.port:
        return []
    vlan = management_network.MANAGEMENT_VLAN
    mask = str(ipaddress.IPv4Network(f'0.0.0.0/{management.prefixlen}').netmask)
    return [f'vlan {vlan} enable name "BLab management"', f'vlan {vlan} port default {management.port}',
            f'ip interface "{MANAGEMENT_INTERFACE}" address {management.ip} mask {mask} vlan {vlan}']


def config_lines(text: str) -> List[str]:
    """A config's lines as AOS reads them: trailing spaces, blank lines and '!' comments aside."""
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith('!')]


def init_differences(init_config: str, model: str, management: Management = Management()) -> List[str]:
    """How init/vcboot.cfg differs from the Golden init of the model, a line each ('+ ' added, '- ' missing)."""
    return golden_differences(init_config, golden_init_config(model, management))


def golden_differences(text: str, golden: str) -> List[str]:
    """How a config differs from what it should be, a line each ('+ ' added, '- ' missing)."""
    have, want = config_lines(text), config_lines(golden)
    if have == want:
        return []
    added = [f'+ {line}' for line in have if line not in want]
    missing = [f'- {line}' for line in want if line not in have]
    return added + missing or ['the same lines in another order']


# The VC setup (#94, #107, #109). AOS reads a Switch's VC setup from working/vcsetup.cfg at boot. A
# Cleanup empties working/, and without that file AOS makes its last ports auto-VFL ports (VC links
# that form a VC on their own), which carry no Link, and writes them into working/vcsetup.cfg itself:
# 1/1/25 to 1/1/28A on the OS6900-T24C2s, 1/1/50A to 54A on 132, the last two on the OS6870s. So the
# clean state includes init/vcsetup.cfg, which the Cleanup copies to working/: a standalone Switch,
# chassis 1, in static VF-link mode, where AOS picks no auto-VFL port at all. In auto mode, even with
# no auto-vf-link-port line in the file, an OS6860N-P24Z still made 1/1/25-26 auto-VFL (#107). On 132
# (OS6900-X48C6, AOS 8.11.162.R01, 2026-11-04), booting with the two static lines from a working/
# whose auto-VFL ports were 1/1/50A-54A left none, and AOS kept the file as written. Members may still
# set up a VC during a Reservation; the Cleanup puts it back. A chassis (OS99xx: its CMM ports are
# listed, .171), the OS2260 Management switches (no virtual-chassis command, .133) and a Switch
# without a model have no Golden VC setup, and are left as they are.
# What makes a VC setup not clean is what it does, not its wording (#109): auto-VFL ports at run time
# (show virtual-chassis auto-vf-link-port), or a VC config other than AOS's standalone defaults in
# init/vcsetup.cfg (another chassis-id, a priority, a VFL, auto-VFL ports). The forms AOS writes for a
# standalone Switch (VC_SETUP_DEFAULTS, read on every lab Switch on 2026-11-04, AOS 8.8 to 8.11) are
# fine in it, as is their absence: a missing file is clean as long as no port is auto-VFL. The
# Cleanup still writes the Golden VC setup whenever the file isn't word for word that.
# This is the one place that decides a Switch's VC setup. A Switch without an EMP keeps its
# management there (#81): its Management port, a front port, untagged in the Management network's
# VLAN, with its address on that VLAN. AOS takes these lines from vcsetup.cfg at boot (132, AOS
# 8.11.162.R01, 2026-11-04: show vlan, show ip interface and the routes as written). A write memory
# moves them into working/vcboot.cfg, which is fine: init/ is what the Cleanup copies back.
AUTO_VFL_COMMAND = 'show virtual-chassis auto-vf-link-port'
# " Chassis/Slot/Port  Chassis/VFLink ID  VFLink member status" then a row per port, such as
# " 1/1/27A            Unassigned         Unassigned" (132 prints nothing at all once it has none)
AUTO_VFL_PORT = re.compile(r'^\s*(\d+/\d+/\d+[A-Za-z]?)\s', re.MULTILINE)
# Ports that are only stacking (VFL) ports by hardware: AOS lists them as auto-VFL ports whatever the VC
# setup says, no Link can use them, so they aren't auto-VFL ports to fix (#114). 10.69.144.177
# (OS6560-P48Z16, 8.10.102.R01, 2026-11-04): booted with the Golden VC setup, AOS wrote
# auto-vf-link-port 1/1/53 and 1/1/54 back into working/vcsetup.cfg itself. Add a model once seen.
DEDICATED_VFL_PORTS = {'OS6560-P48Z16': ('1/1/53', '1/1/54')}
NO_VC_SETUP = ('OS99', 'OS22')  # model prefixes whose VC setup BLab leaves as it is
GOLDEN_VC_SETUP = '''virtual-chassis chassis-id 1 configured-chassis-id 1
virtual-chassis vf-link-mode static
'''
# The VC lines AOS writes for a standalone Switch, chassis 1 (chassis-group: a number AOS picks)
VC_SETUP_DEFAULTS = re.compile(r'virtual-chassis (?:chassis-id 1 configured-chassis-id 1|vf-link-mode (?:auto|static)'
                               r'|chassis-id 1 chassis-group \d+)')


def golden_vc_setup(model: Optional[str], management: Management = Management()) -> Optional[str]:
    """init/vcsetup.cfg of a lab Switch of this model, or None if BLab leaves its VC setup as it is.
    With a front port as its Management port, its management too."""
    if not model or model.upper().startswith(NO_VC_SETUP) or runs_aos6(model):
        return None  # AOS 6 has no VC setup: its management is in its Golden init (#108)
    if not management.port:
        return GOLDEN_VC_SETUP
    return GOLDEN_VC_SETUP + ''.join(f'{line}\n' for line in management_lines(management))


def management_lines(management: Management) -> List[str]:
    """The config of a Management port that is a front port: untagged in the Management network's VLAN,
    with the Switch's address on it (the Golden VC setup's, and what a Contribution pastes at the console)."""
    vlan = management_network.MANAGEMENT_VLAN
    mask = str(ipaddress.IPv4Network(f'0.0.0.0/{management.prefixlen}').netmask)
    return [f'vlan {vlan} admin-state enable', f'vlan {vlan} name "BLab management"',
            f'vlan {vlan} members port {management.port} untagged',
            f'ip interface "{MANAGEMENT_INTERFACE}" address {management.ip} mask {mask} vlan {vlan}']


def parse_auto_vfl_ports(output: str) -> List[str]:
    """The ports show virtual-chassis auto-vf-link-port lists."""
    return AUTO_VFL_PORT.findall(output or '')


@dataclass(frozen=True)
class VcSetupReading:
    """
    What was read of a Switch's VC setup: init/vcsetup.cfg (None if it isn't there or couldn't be
    read: the listing of init/ tells which), and its auto-VFL ports (None if unreadable).
    """
    init_vc_setup: Optional[str] = None
    auto_vfl_ports: Optional[List[str]] = field(default_factory=list)


def find_vc_setup(model: Optional[str], init: Optional[List[str]], reading: VcSetupReading, found: 'Findings',
                  management: Management = Management()) -> None:
    """Adds where the VC setup strays from the Golden VC setup to `found`. `init`: the entries of init/."""
    golden = golden_vc_setup(model, management)
    if golden is None:
        return
    if reading.auto_vfl_ports is None:
        found.unreadable.append(AUTO_VFL_COMMAND)
    else:
        dedicated = DEDICATED_VFL_PORTS.get((model or '').upper(), ())
        found.auto_vfl_ports = [port for port in reading.auto_vfl_ports if port not in dedicated]
    if init is None:
        return  # init/ unreadable: already said
    present = 'vcsetup.cfg' in init
    if present and reading.init_vc_setup is None:
        found.unreadable.append(INIT_VC_SETUP_PATH)
        return
    found.vc_setup_changes = vc_setup_differences(reading.init_vc_setup or '', golden)
    found.vc_setup_rewritten = bool(golden_differences(reading.init_vc_setup or '', golden))


def vc_setup_differences(text: str, golden: str) -> List[str]:
    """
    How a VC setup differs from the Golden VC setup in what it does, a line each ('+ ' added, '- '
    missing): AOS's standalone defaults may be there or not, in any order; anything else must be as golden.
    """
    have, want = config_lines(text), config_lines(golden)
    added = [f'+ {line}' for line in have if line not in want and not VC_SETUP_DEFAULTS.fullmatch(line)]
    missing = [f'- {line}' for line in want if line not in have and not VC_SETUP_DEFAULTS.fullmatch(line)]
    return added + missing


@dataclass(frozen=True)
class Extras:
    """The lab managers' extras (BaselineExtra rows): file patterns under /flash, and logins."""
    files: Sequence[str] = ()
    logins: Sequence[str] = ()


def extras() -> Extras:
    """The extras an admin listed, from the database."""
    from .models import BaselineExtra  # models imports lab_switch, which imports this module
    rows = list(BaselineExtra.objects.values_list('kind', 'value'))
    return Extras(files=tuple(v.strip().strip('/') for k, v in rows if k == BaselineExtra.FILE),
                  logins=tuple(v.strip() for k, v in rows if k == BaselineExtra.LOGIN))


def in_baseline(path: str, extra_files: Iterable[str] = ()) -> bool:
    """
    Whether a path under /flash ('python/x.py') belongs to the Baseline. A pattern matches in
    its own directory only: '*.log' is a log in /flash, never python/x.log.
    """
    directory, _, name = path.rpartition('/')
    for pattern in (*BASELINE, *extra_files):
        pattern_directory, _, pattern_name = pattern.rpartition('/')
        if pattern_directory == directory and fnmatchcase(name, pattern_name):
            return True
    return False


def removable(path: str) -> bool:
    """
    Whether a Cleanup may remove this path under /flash: an entry of a scanned directory,
    outside the Baseline (the extras aside: those are checked by whoever lists them).
    """
    parts = path.split('/')
    if not path or path.startswith('/') or any(part in ('', '.', '..') for part in parts):
        return False
    if any(c in path for c in '*?[]\n\'"\\'):
        return False
    return '/'.join(parts[:-1]) in SCANNED and not in_baseline(path)


def parse_users(output: str) -> Optional[Dict[str, Optional[bool]]]:
    """
    Each local user in show user, with whether SU is allowed for it (None when AOS doesn't
    say, before 8.10), or None if it lists none (unreadable: admin is always there).
    """
    blocks = list(USER_BLOCK.finditer(output or ''))
    users = {}
    for block, following in zip(blocks, blocks[1:] + [None]):
        name = block.group(1)
        if not name:
            continue
        su = SU_ALLOWED.search(output, block.end(), following.start() if following else len(output))
        users[name] = None if su is None else su.group(1).upper() == 'YES'
    return users or None


@dataclass
class Findings:
    """Where a Switch that isn't reserved strays from its clean state."""
    files: List[str] = field(default_factory=list)     # Unwanted files, paths under /flash
    logins: List[str] = field(default_factory=list)    # local users other than admin and the extras
    su: List[str] = field(default_factory=list)        # users other than admin and the extras with SU allowed
    init_changes: List[str] = field(default_factory=list)  # how init/vcboot.cfg differs from the Golden init
    init_lacks: List[str] = field(default_factory=list)    # what init/ should hold and doesn't
    vc_setup_changes: List[str] = field(default_factory=list)  # how init/vcsetup.cfg differs from the Golden VC setup
    vc_setup_rewritten: bool = False  # whether a Cleanup rewrites init/vcsetup.cfg: it isn't the Golden VC setup word for word
    auto_vfl_ports: List[str] = field(default_factory=list)    # its auto-VFL ports, which carry no Link
    unreadable: List[str] = field(default_factory=list)    # what couldn't be read


def find(model: str, listings: Dict[str, Optional[List[str]]], users: Optional[Dict[str, Optional[bool]]],
         init_config: Optional[str], found_extras: Extras, vc_setup: VcSetupReading = VcSetupReading(),
         management: Management = Management()) -> Findings:
    """
    Compares what was read on a Switch with its clean state. `listings`: each scanned directory's
    entries ([] when it doesn't exist, None when it couldn't be listed); `users` from parse_users;
    `init_config` None when unreadable; `vc_setup` what was read of its VC setup; `management` how it
    reaches the Management network (management_of).
    """
    found = Findings()
    has_golden_vc_setup = golden_vc_setup(model) is not None
    for directory in SCANNED:
        entries = listings.get(directory)
        if entries is None:
            found.unreadable.append(f'ls {FLASH}/{directory}'.rstrip('/'))
            continue
        for name in sorted(entries):
            path = f'{directory}/{name}' if directory else name
            if path == INIT_VC_SETUP_PATH and has_golden_vc_setup:
                continue  # compared below, not Unwanted
            if not in_baseline(path, found_extras.files):
                found.files.append(path)
    init = listings.get('init')
    if init is not None:
        essentials = AOS6_INIT_ESSENTIALS if runs_aos6(model) else INIT_ESSENTIALS
        found.init_lacks = [what for pattern, what in essentials
                            if not any(fnmatchcase(name, pattern) for name in init)]
    if users is None:
        found.unreadable.append('show user')
    else:
        found.logins = [name for name in users
                        if name.lower() not in BUILT_IN_LOGINS and name not in found_extras.logins]
        found.su = [name for name in found.logins if users[name]]
    # Without a model there is no Golden init to compare with (nor to rewrite init from)
    if init_config is not None and model:
        found.init_changes = init_differences(init_config, model, management)
    find_vc_setup(model, init, vc_setup, found, management)
    return found


def shown(items: List[str], separator: str = ', ') -> str:
    """A list quoted in a reason: the first few, and how many more."""
    more = len(items) - SHOWN
    return separator.join(items[:SHOWN]) + (f'{separator.strip()} and {more} more' if more > 0 else '')


UNWANTED_FILE = 'Unwanted file: '
LOGIN_LEFT = "local users BLab didn't create: "
INIT_CHANGED = 'init differs from its Golden init: '
INIT_LACKS = 'init lacks '
SU_ALLOWED_FOR = 'SU allowed for: '
VC_SETUP_CHANGED = 'init/vcsetup.cfg differs from its Golden VC setup: '
AUTO_VFL = 'auto-VFL ports, where no Link comes up: '
# What a Cleanup repairs: when only these survive one, BLab failed, not the last holder
REPAIRED_BY_CLEANUP = (UNWANTED_FILE, LOGIN_LEFT, INIT_CHANGED, VC_SETUP_CHANGED, AUTO_VFL)


def describe(found: Findings) -> List[str]:
    """Each finding as a line, as an Inspection reports it."""
    lines = []
    if found.files:
        lines.append(UNWANTED_FILE + shown(found.files))
    if found.logins:
        lines.append(LOGIN_LEFT + shown(found.logins))
    if found.su:
        lines.append(SU_ALLOWED_FOR + shown(found.su))
    if found.init_changes:
        lines.append(INIT_CHANGED + f'{len(found.init_changes)} line(s): ' + shown(found.init_changes, '; '))
    if found.init_lacks:
        lines.append(INIT_LACKS + ', '.join(found.init_lacks))
    if found.vc_setup_changes:
        lines.append(VC_SETUP_CHANGED + f'{len(found.vc_setup_changes)} line(s): '
                     + shown(found.vc_setup_changes, '; '))
    if found.auto_vfl_ports:
        lines.append(AUTO_VFL + shown(found.auto_vfl_ports))
    lines.extend(f'unreadable: {what}' for what in found.unreadable)
    return lines
