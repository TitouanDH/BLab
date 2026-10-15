"""
Clean state (see CONTEXT.md, docs/adr/0006): once a Reservation ends, nothing of it stays on
the Switch. What a clean Switch holds is its Baseline (AOS's own files, and the lab managers'
extras an admin lists as BaselineExtra rows) and its Golden init; anything else is Unwanted.

This module only decides; it never talks to a Switch. The Inspection (api.inspection) reports
what it finds, and the Cleanup (api.switch_worker, through LabSwitch.restore_init_and_reload)
repairs it: it removes the Unwanted files and every local user but `admin` and the lab
managers' logins, and rewrites init/vcboot.cfg from the Golden init if a user changed it.

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
"""
import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Dict, Iterable, List, Optional, Sequence

from django.conf import settings

FLASH = '/flash'
INIT_CONFIG_PATH = 'init/vcboot.cfg'
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
    # Inside the scanned directories
    'python/default_*', 'python/iec_config.py',
    'pmd/work',
    # The Golden init: an AOS image (one or more, the image of the family), pkg/, and the config
    'init/*.img', 'init/pkg', 'init/vcboot.cfg',
)
# What init/ must hold, as Baseline patterns, with how to name what's missing
INIT_ESSENTIALS = (('*.img', 'an AOS image (*.img)'), ('pkg', 'pkg/'), ('vcboot.cfg', 'vcboot.cfg'))
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


def golden_init_config(model: str) -> str:
    """init/vcboot.cfg of a lab Switch of this model: names it after its model, and turns on the LLDP TLVs
    populate_ports discovers with."""
    return f'''system name "{model}"
session prompt default "{model}"
session cli timeout 5555

health threshold memory 80

aaa authentication console "local"
aaa authentication ssh "local"

lldp nearest-bridge chassis tlv management port-description enable system-name enable system-description enable
lldp nearest-bridge chassis tlv management management-address enable
lldp nearest-bridge chassis tlv dot3 mac-phy enable

ip static-route 10.0.0.0/8 gateway 10.69.144.129 metric 1
ip static-route 10.69.0.0/16 gateway 10.69.144.129 metric 1
ip static-route 135.118.225.0/24 gateway 10.69.144.129 metric 1

auto-fabric admin-state disable
mvrp disable
command-log enable
'''


def config_lines(text: str) -> List[str]:
    """A config's lines as AOS reads them: trailing spaces, blank lines and '!' comments aside."""
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith('!')]


def init_differences(init_config: str, model: str) -> List[str]:
    """How init/vcboot.cfg differs from the Golden init of the model, a line each ('+ ' added, '- ' missing)."""
    have, want = config_lines(init_config), config_lines(golden_init_config(model))
    if have == want:
        return []
    added = [f'+ {line}' for line in have if line not in want]
    missing = [f'- {line}' for line in want if line not in have]
    return added + missing or ['the same lines in another order']


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
    unreadable: List[str] = field(default_factory=list)    # what couldn't be read


def find(model: str, listings: Dict[str, Optional[List[str]]], users: Optional[Dict[str, Optional[bool]]],
         init_config: Optional[str], found_extras: Extras) -> Findings:
    """
    Compares what was read on a Switch with its clean state. `listings`: each scanned directory's
    entries ([] when it doesn't exist, None when it couldn't be listed); `users` from parse_users;
    `init_config` None when unreadable.
    """
    found = Findings()
    for directory in SCANNED:
        entries = listings.get(directory)
        if entries is None:
            found.unreadable.append(f'ls {FLASH}/{directory}'.rstrip('/'))
            continue
        for name in sorted(entries):
            path = f'{directory}/{name}' if directory else name
            if not in_baseline(path, found_extras.files):
                found.files.append(path)
    init = listings.get('init')
    if init is not None:
        found.init_lacks = [what for pattern, what in INIT_ESSENTIALS
                            if not any(fnmatchcase(name, pattern) for name in init)]
    if users is None:
        found.unreadable.append('show user')
    else:
        found.logins = [name for name in users
                        if name.lower() not in BUILT_IN_LOGINS and name not in found_extras.logins]
        found.su = [name for name in found.logins if users[name]]
    # Without a model there is no Golden init to compare with (nor to rewrite init from)
    if init_config is not None and model:
        found.init_changes = init_differences(init_config, model)
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
# What a Cleanup repairs: when only these survive one, BLab failed, not the last holder
REPAIRED_BY_CLEANUP = (UNWANTED_FILE, LOGIN_LEFT, INIT_CHANGED)


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
    lines.extend(f'unreadable: {what}' for what in found.unreadable)
    return lines
