"""
A lab Switch, seen as what BLab does to it over SSH: set its banner, Clean it up, create and
remove Switch accounts, read its VC topology, read what an Inspection needs and its running
config, and read and load what a Snapshot holds (see CONTEXT.md).

LabSwitch runs its steps through a session, opened by a connect function picked in one
place (lab_switch()): the real switch over SSH, or the in-memory fake in fake_devices.

BLab logs in as `admin`, trying each password of settings.BLAB_SWITCH_ADMIN_PASSWORDS
(docs/adr/0004). Account commands refuse that name. Only secure_admin changes `admin`: at onboarding,
after each reload BLab starts (secure_admin_after_reload, #87), and when the Switch worker's Login
check finds one that only takes an older password (api.login_check, #92).
"""
import logging
import re
import shlex
import socket
import stat
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from collections import deque
from datetime import datetime, timedelta
from typing import Callable, ContextManager, Deque, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import paramiko
from django.conf import settings

from . import clean_state
from .port_capability import CAPABILITY_COMMAND, Capability, parse_capabilities

logger = logging.getLogger(__name__)

SWITCH_ADMIN = 'admin'
BANNER_PATH = 'switch/pre_banner.txt'
CONFIRM_PROMPT_WAIT = 10      # seconds a command that asks for confirmation may take to ask
CONFIRMED_COMMAND_WAIT = 20   # seconds the session stays open once it is answered
# The longest a command or a file transfer may take before BLab gives up on the Switch and closes
# the session (#64): a Switch that hard-resets or drops off the network mid-session otherwise
# leaves the Switch worker waiting for ever, holding its lock. show commands and `write memory`
# answer in seconds; this leaves room for slow models.
COMMAND_TIMEOUT = 120
# Copying a directory (Cleanup's `cp -r init/* working/`) copies the AOS images, hundreds of MB
# on flash, and prints nothing while it does. Not timed on a Switch yet: generous on purpose.
COPY_TIMEOUT = 600
# Saves the running config into working/, then makes certified/ a copy of working/ on every chassis
# (#99): a Switch that powers up again boots from certified/, not from working/, so without it a
# power cut brings back whatever certified/ last held. It copies the images too. On 10.69.144.132
# (OS6900, AOS 8.11.162.R01, 2026-11-03): no confirmation, no output, exit status 0, 43 s, and
# certified/ matched working/ once it answered, but show running-directory only said CERTIFIED
# a little later.
CERTIFY_COMMAND = 'write memory flash-synchro'
# md5sum reads a whole image too
COPY_COMMANDS = ('cp ', 'md5sum ', CERTIFY_COMMAND)
# Which directory the Switch runs from, and whether certified/ matches it (10.69.144.132, 2026-11-03):
#   CONFIGURATION STATUS
#     Running CMM              : MASTER-PRIMARY,
#     CMM Mode                 : VIRTUAL-CHASSIS MONO CMM,
#     Current CMM Slot         : CHASSIS-1 A,
#     Running configuration    : WORKING,
#     Certify/Restore Status   : CERTIFY NEEDED
#   SYNCHRONIZATION STATUS
#     Running Configuration    : NOT SYNCHRONIZED
# The first "Running configuration" is the directory the Switch runs from.
RUNNING_DIRECTORY_COMMAND = 'show running-directory'
RUNNING_FROM = re.compile(r'^\s*Running configuration\s*:\s*([A-Za-z-]+)', re.IGNORECASE | re.MULTILINE)
CERTIFY_STATUS = re.compile(r'^\s*Certify/Restore Status\s*:\s*([A-Za-z ]+?)\s*,?\s*$', re.IGNORECASE | re.MULTILINE)
CERTIFIED_STATUS = 'CERTIFIED'  # what show running-directory says once certified/ matches working/
CERTIFY_TIMEOUT = 600  # seconds the synchro may take to show as done, once the command answered
CERTIFY_POLL = 10      # seconds between two looks at it
# How AOS refuses a command before it is ready. `user` commands may be refused for a minute or
# more after show interfaces reads (10.69.144.132 into 8.10.86.R04, 2026-11-03, #49)
NOT_READY_ERROR = 'has not reached the ready state'
# How AOS refuses write memory flash-synchro for a while after a reload, once show interfaces and
# user commands answer (10.69.144.132, Cleanup reload, 2026-11-03, #106)
CMM_SYNCHRONIZATION_ERROR = 'not permitted during initialization and CMM synchronization'
# The synchro is tried again while AOS answers one of these: after CERTIFY_RETRY_FIRST s, doubling
# up to CERTIFY_RETRY_MAX, until CERTIFY_RETRY_TOTAL s have gone by (#106)
CERTIFY_NOT_YET = (NOT_READY_ERROR, CMM_SYNCHRONIZATION_ERROR)
CERTIFY_RETRY_FIRST = 10
CERTIFY_RETRY_MAX = 60
CERTIFY_RETRY_TOTAL = 300
# Copying an AOS image (100 to 250 MB) from the BLab server to the Switch over SFTP. Generous on purpose.
UPLOAD_TIMEOUT = 1800
# Seconds between SSH keepalives: an idle session that a NAT or firewall would drop stays open.
# A dead connection is only seen once TCP gives up; the timeouts above are what bound a step.
KEEPALIVE_INTERVAL = 15
# A working/ directory missing any of these would not boot cleanly, so Cleanup won't reload
ESSENTIAL_FILES = ('.img', 'pkg', 'vcboot.cfg')


def port_order(port: str):
    """A sort key that puts 1/1/2 before 1/1/10."""
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', port)]


def missing_for_boot(listing: str) -> List[str]:
    """What a boot needs (ESSENTIAL_FILES) that a directory's listing lacks."""
    return [name for name in ESSENTIAL_FILES if name not in listing]


# A regular file in `ls -l` (10.69.144.132, AOS 8.10.86.R04, 2026-10-20), its size in bytes then its
# name: "-rw-r--r--    1 admin    user     241594635 Jan  6 05:35 Yos.img". Directories start with d.
LS_FILE = re.compile(r'^-\S*\s+\d+\s+\S+\s+\S+\s+(\d+)\s+\S+\s+\d+\s+\S+\s+(.+?)\s*$', re.MULTILINE)


def file_sizes(listing: str) -> Dict[str, int]:
    """The regular files of one directory's `ls -l`, name -> size in bytes."""
    return {name: int(size) for size, name in LS_FILE.findall(listing or '')}


def is_image(name: str) -> bool:
    """An AOS image (Yos.img, Mos.img...)."""
    return name.endswith('.img')


LISTED_IMAGE = re.compile(r'(\S+\.img)\s*$', re.MULTILINE)  # an image's name ending a line of a listing
CUT_SHORT = 'a copy cut short? Not reloading into it'


# What an Inspection reads: only show commands, and the init config file (read, never written).
# show microcode only records the release the Switch runs (#48): it is never a reason.
RUNNING_CONFIG_COMMAND = 'show configuration snapshot'  # the running config: also what a member's Config shows live (#44)
INSPECTION_COMMANDS = ('show chassis', 'show interfaces', RUNNING_CONFIG_COMMAND, 'show user', 'show microcode')
INIT_CONFIG_PATH = clean_state.INIT_CONFIG_PATH
# The logins since the Switch last booted, as AOS logs them ("... SES AAA INFO: Login by admin from
# 10.69.144.180 through SSH Success ..."). swlog_chassis<n> (n: the chassis ID, 3 on a VC member that
# is chassis 3) is rotated at boot into swlog_chassis<n>.0 and so on, which can't be read as admin,
# so this only sees the logins since the last reload (checked on an OS6900, AOS 8.9.107.R02, and
# OS6870s). grep exits 1 when there is none.
LOGIN_LOG = '/flash/swlog_chassis?'
LOGIN_LOG_COMMAND = f"grep -h 'Login by' {LOGIN_LOG}"
LOGIN_LINE = re.compile(r'Login by (\S+?)(?: from (\S+))?(?: through (\S+))? Success')
# Local users that are not Switch accounts: BLab's own, and AOS's template for new users
BUILT_IN_USERS = frozenset({SWITCH_ADMIN, 'default'})
# What AOS 8 takes as a local user name (checked on an OS6900, AOS 8.9.107.R02): 1 to 63 ASCII
# letters, digits and ._@+-, case-sensitive. Anything else is refused before it reaches the CLI.
ACCOUNT_NAME = re.compile(r'[A-Za-z0-9._@+-]{1,63}')
# Passwords AOS takes for a local user (checked on 10.69.144.132, AOS 8.10.86.R04, 2026-10-12):
# 8 to 30 printable ASCII characters without spaces. '!' is refused, and a quote is taken but
# then doesn't log in. BLab's random ones and the Switch passwords users choose both fit.
ACCOUNT_PASSWORD = re.compile(r'''[\x21-\x7e]{8,30}''')
ACCOUNT_PASSWORD_REFUSED = frozenset('!"\'')
# How AOS refuses a password the account had among its last ones ("New password can not be the
# same as your current password or any of your previous 3 passwords"); BLab then makes it afresh
PASSWORD_IN_HISTORY = 'can not be the same as your current password'
# How AOS refuses to turn a read-only local user into a read-write one, or back, in one `user`
# command (10.69.144.132, AOS 8.11.162.R01, 2026-11-04): BLab then makes it afresh too (#91)
OTHER_PRIVILEGE = 'Inconsistent configuration between Read Only and Read/Write privileges'
# Every fallback that removes an account and makes it afresh
MADE_AFRESH_ON = (PASSWORD_IN_HISTORY, OTHER_PRIVILEGE)
# How a VC slave refuses a `user` command: the master holds the local users of the whole VC
REFUSED_ON_SLAVE = 'Command not allowed on slave'
# How AOS refuses a word its `user` command doesn't know (as 10.69.144.132, AOS 8.11, refuses
# `allow-su`): a release without SU (show user says nothing of it before 8.10) refuses `su` so
NO_SU = 'Invalid entry: "su"'
# Saves the running config, local users included, and has AOS copy it to every chassis of a VC
# (#60). Checked on 10.69.144.132 (AOS 8.10.86.R04, 2026-10-19): no confirmation, exit status 0,
# under a second; it rewrites working/vcboot.cfg from the running config.
SAVE_CONFIG = 'write memory'
# What init/ is built from at onboarding (install_golden_init): (what, whether an ls lists it, the copy command)
INIT_PARTS = (('AOS image', lambda names: any(n.endswith('.img') for n in names), 'cp {}/*.img init/'),
              ('pkg directory', lambda names: 'pkg' in names, 'cp -r {}/pkg init/'))
INIT_SOURCES = ('working', 'certified')
# Who is who in a VC (10.69.144.139, AOS 8.10, 2026-10-19):
#   Local Chassis: 2
#    Chas  Role         Status              Chas ID  Pri   Group  MAC-Address
#   -----+------------+-------------------+--------+-----+------+------------------
#    2     Master       Split-Topology      2        250   10     78:24:59:28:39:81
#    -     Unassigned   Not-Joined          1        -     -      -
VC_TOPOLOGY_COMMAND = 'show virtual-chassis topology'
VC_LOCAL_CHASSIS = re.compile(r'^\s*Local Chassis\s*:\s*(\d+)', re.IGNORECASE | re.MULTILINE)
VC_MEMBER = re.compile(r'^\s*(\d+)\s+([A-Za-z]+)\s+\S+\s+\S+\s+\S+\s+\S+\s+([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\s*$',
                       re.MULTILINE)
# What a Snapshot holds of a Switch: the config it boots, and its VC setup if it has one
WORKING_CONFIG_PATH = 'working/vcboot.cfg'
WORKING_VC_SETUP_PATH = 'working/vcsetup.cfg'
# The release of the image the Switch boots from working/, in show microcode (10.69.144.132, AOS
# 8.10.86.R04, 2026-10-13): "Yos.img           8.10.86.R04               241594635 Alcatel-Lucent OS"
RELEASE_COMMAND = 'show microcode'
IMAGE_RELEASE = re.compile(r'^\s*\S+\.img\s+(\S+)', re.MULTILINE)
# From AOS 8.10R04 on it adds a column (10.69.144.132, 2026-10-21):
#   Package           Release                 Size      Description          Secure Boot
#   Yos.img           8.10.86.R04               241594635 Alcatel-Lucent OS     Yes
IMAGE_SECURE_BOOT = re.compile(r'^\s*\S+\.img\s+\S+\s+\d+\s+.*?\s(Yes|No)\s*$', re.IGNORECASE | re.MULTILINE)
# A release being installed is copied under this suffix, then renamed once whole (install_images)
NEW_IMAGE_SUFFIX = '.blab-new'
# "Chassis 1 /flash 15112011776 bytes free" (10.69.144.132, AOS 8.10.86.R04, 2026-10-21)
FREE_SPACE_COMMAND = 'freespace'
FREE_SPACE = re.compile(r'/flash\s+(\d+)\s+bytes free', re.IGNORECASE)
FREE_SPACE_MARGIN = 64 * 1024 * 1024  # bytes left free on /flash once a release's images are copied
MD5_LINE = re.compile(r'^([0-9a-fA-F]{32})\s', re.MULTILINE)
RELOAD_FROM_WORKING = 'reload from working no rollback-timeout'
# How long the Switch has been up, in show system (10.69.144.132, AOS 8.10.86.R04, 2026-10-19):
# "  Up Time:      2 days 23 hours 1 minutes and 55 seconds,". Units it leaves out count as 0.
UPTIME_COMMAND = 'show system'
LLDP_COMMAND = 'show lldp remote-system'  # its neighbours, as onboarding reads them (#55)
UPTIME_LINE = re.compile(r'^\s*Up\s*Time\s*:(.*)$', re.IGNORECASE | re.MULTILINE)
UPTIME_PART = re.compile(r'(\d+)\s*(d(?:ays?)?|h(?:ours?|rs?)?|m(?:inutes?|ins?)?|s(?:econds?|ecs?)?)\b', re.IGNORECASE)
UPTIME_UNITS = {'d': 86400, 'h': 3600, 'm': 60, 's': 1}  # by the unit's first letter

# AOS 6 (#108, docs/adr/0012), as 10.69.144.148 (OS6450-48, 6.7.2.28.R06) does it, 2026-11-04.
# Its SSH server takes no exec request (it takes one for SCP: "SCP: Invalid command received show"),
# so BLab drives its CLI as a person would (ShellSession). Its files go over SFTP, on a connection of
# their own: it opens one channel per connection ("Administratively prohibited" for a second one).
# SFTP starts in /flash, as on AOS 8, so paths are the same relative ones.
AOS6_WORKING_CONFIG_PATH = 'working/boot.cfg'
AOS6_INIT_CONFIG_PATH = clean_state.AOS6_INIT_CONFIG_PATH
# Makes certified/ a copy of working/: no confirmation; it copies only what differs, and says
# "CERTIFY process completed successfully" (boot.cfg alone: 2 s)
AOS6_CERTIFY_COMMAND = 'copy working certified'
AOS6_COPY_COMMANDS = ('cp ', AOS6_CERTIFY_COMMAND)
# AOS 6 says "reload working", not AOS 8's "reload from working"; it asks "Confirm Activate (Y/N) :". Its
# rollback-timeout is at most 15 minutes, and copy working certified doesn't cancel it (.148 rolled back
# to certified/ 15 minutes after such a reload, 2026-11-04): BLab never sets one.
AOS6_RELOAD = 'reload working no rollback-timeout'
# Gives a Switch standing alone in stack slot n slot 1 from its next boot, and reloads it now; it asks
# "Confirm set slot operation with reload (Y/N)?" (10.69.144.175, an OS6450, by hand on 2026-11-04, #113)
AOS6_SLOT_ONE = 'stack set slot {} saved-slot 1 reload'
# The SSH server's version string (paramiko's remote_version): "SSH-2.0-OpenSSH_5.0" on AOS 6 (.148);
# AOS 8 says "SSH-2.0-OpenSSH-FIPS(capable)" (10.69.144.132, .139 and .140, 2026-11-04)
AOS6_SSH_VERSION = re.compile(r'^SSH-2\.0-OpenSSH_[1-6]\.')
# AOS 6 takes 3 new SSH sessions a minute; a 4th is an attack, and for the next minute it takes one
# only, until 3 quiet minutes (Switch Management Guide 6.7.2, p. 2-18). Over it, it closes the
# connection before its banner. BLab keeps under it: at most AOS6_SESSIONS_A_MINUTE new connections
# to one AOS 6 Switch in any minute from one process, keeps its CLI session open between steps
# (AOS6_IDLE_CLOSE), and, refused all the same (another process), waits AOS6_REFUSED_WAIT and tries again.
AOS6_SESSIONS_A_MINUTE = 2
AOS6_MINUTE = 61.0
AOS6_REFUSED_WAIT = 65.0
AOS6_CONNECT_TRIES = 4
AOS6_IDLE_CLOSE = 45.0  # seconds an AOS 6 CLI session stays open unused before it is closed
# Its log names who logs in once its Golden init turns session accounting on (aaa accounting
# session "local"): "TUE JAN 02 00:28:45 2001            AAA    info AAA login,blabdq,SSH,,session=020,10.69.6.148".
# show log swlog appid aaa prints those lines alone, from both its log files (swlog1.log and swlog2.log
# are binary), in a second.
AOS6_LOGIN_LOG_COMMAND = 'show log swlog appid aaa'
AOS6_LOG_LINE = re.compile(r'^\s*\w{3}\s+(\w{3})\s+(\d+)\s+(\d+):(\d+):(\d+)\s+(\d{4})\s+AAA\s+\w+\s+AAA login,'
                           r'([^,]+),([^,]*),[^,]*,session=\d+,(\S+)\s*$', re.MULTILINE)
# "  Date & Time:  TUE JAN 02 2001  00:37:51 (UTC)" in show system: the Switch's clock, which its log uses
AOS6_CLOCK = re.compile(r'^\s*Date\s*&\s*Time\s*:\s*\w{3}\s+(\w{3})\s+(\d+)\s+(\d{4})\s+(\d+):(\d+):(\d+)',
                        re.IGNORECASE | re.MULTILINE)
MONTHS = ('JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC')


class LabSwitchError(Exception):
    """A step could not be done on a lab switch."""


class LoginRefused(LabSwitchError):
    """The switch answered, but refused BLab's credentials."""


@dataclass(frozen=True)
class CommandResult:
    status: int
    output: str = ''
    error: str = ''


class Session:
    """
    What LabSwitch needs from an open connection to one switch. `aos6` says it speaks AOS 6
    (#108): LabSwitch then uses AOS 6's commands and files, and list_dir.
    """
    aos6 = False

    def list_dir(self, path: str) -> Dict[str, Optional[int]]:
        """
        AOS 6 only: a directory's entries under /flash ('' for /flash itself), name -> size in
        bytes (None for a directory). Raises LabSwitchError if it can't be listed.
        """
        raise NotImplementedError

    def run(self, cmd: str) -> CommandResult:
        raise NotImplementedError

    def run_confirmed(self, cmd: str) -> None:
        """Starts a command that asks for confirmation, and answers yes."""
        raise NotImplementedError

    def write_file(self, path: str, text: str) -> None:
        raise NotImplementedError

    def read_file(self, path: str) -> str:
        raise NotImplementedError

    def put_file(self, local_path: str, path: str) -> None:
        """Copies a file of the BLab server to the switch."""
        raise NotImplementedError


@dataclass(frozen=True)
class ImageFile:
    """An AOS image on the BLab server, ready to be installed (api.aos_release.download)."""
    name: str   # as on the Switch: Yos.img
    path: str   # the file on the BLab server
    size: int   # bytes
    md5: str


@dataclass(frozen=True)
class Microcode:
    """The AOS image of one directory, as show microcode lists it."""
    release: str
    secure: Optional[bool]  # whether it is a Secure Boot image; None if the release doesn't say


def parse_microcode(output: str) -> Optional[Microcode]:
    """show microcode [<directory>], or None if it lists no image."""
    release = IMAGE_RELEASE.search(output or '')
    if not release:
        return None
    secure = IMAGE_SECURE_BOOT.search(output)
    return Microcode(release.group(1), secure.group(1).lower() == 'yes' if secure else None)


@dataclass(frozen=True)
class Login:
    """One successful login to a Switch, as its log records it (address None when it doesn't say)."""
    user: str
    address: Optional[str]
    through: Optional[str]


@dataclass(frozen=True)
class Readings:
    """
    What an Inspection read on a switch: each show command's result, init's config (None if
    unreadable), the entries of each directory of clean_state.SCANNED ([] if it doesn't
    exist, None if it couldn't be listed), and its VC setup.
    """
    outputs: Dict[str, CommandResult]
    init_config: Optional[str]
    listings: Dict[str, Optional[List[str]]]
    vc_setup: clean_state.VcSetupReading = clean_state.VcSetupReading()
    init_path: str = INIT_CONFIG_PATH  # where init_config was read from (init/boot.cfg on AOS 6)


@dataclass(frozen=True)
class SwitchConfig:
    """What a Snapshot holds of one Switch: working's config, its VC setup (None if it has none), its AOS release."""
    vcboot: str
    vcsetup: Optional[str]
    aos_release: str


@dataclass(frozen=True)
class VcMember:
    """One chassis of a VC, as show virtual-chassis topology lists it."""
    chassis: int
    role: str  # 'Master', 'Slave'...
    mac: str   # lower case

    @property
    def is_master(self) -> bool:
        return self.role.lower() == 'master'


@dataclass(frozen=True)
class VcTopology:
    """A Switch's view of its VC: which chassis it is, and the chassis it lists (itself alone when standalone)."""
    local: int
    members: Sequence[VcMember]

    @property
    def me(self) -> Optional[VcMember]:
        return next((m for m in self.members if m.chassis == self.local), None)

    @property
    def master(self) -> Optional[VcMember]:
        return next((m for m in self.members if m.is_master), None)


def parse_vc_topology(output: str) -> Optional[VcTopology]:
    """show virtual-chassis topology, or None if it doesn't say which chassis the Switch is (unreadable)."""
    local = VC_LOCAL_CHASSIS.search(output or '')
    if not local:
        return None
    members = [VcMember(int(chassis), role, mac.lower()) for chassis, role, mac in VC_MEMBER.findall(output)]
    return VcTopology(int(local.group(1)), members)


@dataclass
class AccountsUpdate:
    """What update_accounts did: why each account that failed did, by name, and what else to know."""
    failed: Dict[str, str]
    # Refused because the Switch is a VC slave: the VC's master holds the accounts of every chassis
    on_slave: List[str]
    not_saved: Optional[str] = None  # why the config couldn't be saved after the changes, if it couldn't


@dataclass(frozen=True)
class Repair:
    """What a Cleanup repairs before restoring init (api.clean_state decides it): nothing by default."""
    users: Sequence[str] = ()          # local users to remove
    files: Sequence[str] = ()          # paths under /flash to remove
    init_config: Optional[str] = None  # written to init/vcboot.cfg, when a user changed it
    vc_setup: Optional[str] = None     # written to init/vcsetup.cfg, when a user changed it or it is missing


Connect = Callable[[str], ContextManager[Session]]  # ip -> open session


def images_cut_short(init_sizes: Dict[str, int], copies: Dict[str, Dict[str, int]]) -> List[Tuple[str, int, str]]:
    """
    The images of init/ (name -> size) that may be cut short (#80, #117): those of a size no copy
    of them has in `copies` ('working/' and 'certified/' -> their files' sizes). working/ boots
    its copy now, and certified/ holds what the last Cleanup copied, so a whole image in init/
    matches one of them. Each comes with its size and what the copies have, for a reason.
    """
    short = []
    for name, size in init_sizes.items():
        if is_image(name) and not any(listing.get(name) == size for listing in copies.values()):
            found = ', '.join(f"{listing[name]} in {where}" for where, listing in copies.items()
                              if name in listing) or 'no copy in working/ or certified/'
            short.append((name, size, found))
    return short


def banner_text(user_names: Iterable[str]) -> str:
    holders = ', '.join(user_names) or 'nobody'
    return f"""
***************** LAB RESERVATION SYSTEM ******************
This switch is reserved by : {holders}
If you access this switch without reservation, please contact admin

Release it in BLab when you are done: BLab cleans it up.
"""


class LabSwitch:
    """One lab switch, reached at its management IP. Every failure raises LabSwitchError."""

    def __init__(self, ip: str, connect: Connect):
        self.ip = ip
        self._connect = connect

    def set_banner(self, user_names: Iterable[str]) -> None:
        """Shows who holds the switch to whoever logs in."""
        with self._connect(self.ip) as session:
            session.write_file(BANNER_PATH, banner_text(user_names))

    def restore_init_and_reload(self, repair: Repair = Repair()) -> List[str]:
        """
        Cleanup: removes the local users and the files in `repair`, rewrites init's config if
        it is given, and init's VC setup, then replaces working/ with init/, checks it can boot, refreshes
        certified/ as a backup, and reloads from working/. Nothing is reloaded if an
        essential step fails: rewriting init's config is one, and so is an init/ without
        what a boot needs or with an image that isn't the size of its copy in working/ or
        certified/ (checked before working/ is touched), and a working/ whose files aren't
        the size of init's once copied (#80). So another release put in init/ by hand needs
        a copy in certified/ as well. Returns what it couldn't remove, a line each.
        """
        warnings = []
        with self._connect(self.ip) as session:
            def step(cmd: str, essential: bool) -> CommandResult:
                result = session.run(cmd)
                if result.status != 0:
                    # AOS may print why on stdout alone (#102)
                    said = result.error.strip() or result.output.strip()[:200]
                    message = f"'{cmd}' returned {result.status} on {self.ip}: {said}"
                    if essential:
                        raise LabSwitchError(message)
                    logger.warning(message)
                return result

            for name in repair.users:
                try:
                    check_account_name(name)
                    self._user_command(session, f'no user {name}', already_done='Unknown user')
                    logger.info("Local user %s removed from %s", name, self.ip)
                except (ValueError, LabSwitchError) as e:
                    warnings.append(f"Couldn't remove the local user {name}: {e}")
            if session.aos6:
                self._restore_init_aos6(session, repair, warnings, step)
                return warnings
            for path, text, golden in ((INIT_CONFIG_PATH, repair.init_config, 'Golden init'),
                                       (clean_state.INIT_VC_SETUP_PATH, repair.vc_setup, 'Golden VC setup')):
                if text is None:
                    continue
                try:
                    session.write_file(path, text)
                except LabSwitchError:
                    raise
                except Exception as e:  # SFTP refused...
                    raise LabSwitchError(f"cannot rewrite {path} on {self.ip}: {e}") from e
                logger.info("%s rewritten from the %s on %s", path, golden, self.ip)
            for path in repair.files:
                if not clean_state.removable(path):
                    warnings.append(f"Didn't remove /flash/{path}: not a path a Cleanup removes")
                    continue
                result = session.run(f'rm -rf -- {shlex.quote(f"{clean_state.FLASH}/{path}")}')
                if result.status != 0:
                    warnings.append(f"Couldn't remove /flash/{path}: {result.error.strip() or result.status}")
                else:
                    logger.info("/flash/%s removed from %s", path, self.ip)

            init = step('ls -l init/', essential=True).output
            missing = missing_for_boot(init)
            if missing:
                raise LabSwitchError(f"init/ on {self.ip} lacks {', '.join(missing)}: {init.strip()}")
            init_sizes = file_sizes(init)
            unread = set(LISTED_IMAGE.findall(init)) - set(init_sizes)
            if unread or not any(is_image(name) for name in init_sizes):
                raise LabSwitchError(f"cannot read the size of the image in init/ on {self.ip}: {init.strip()}")
            # An image in init/ is whole if a copy of it is: working/ boots it now, certified/
            # holds what the last Cleanup copied. A copy cut short (#80) matches neither, and
            # nor does another release put in init/ by hand: it must go in certified/ too.
            copies = {'working/': file_sizes(step('ls -l working/', essential=True).output),
                      'certified/': file_sizes(step('ls -l certified/', essential=False).output)}
            for name, size, found in images_cut_short(init_sizes, copies):
                raise LabSwitchError(f"init/{name} on {self.ip} is {size} bytes ({found}): {CUT_SHORT}")
            step('rm -rf working/*', essential=False)
            step('cp -r init/* working/', essential=True)
            contents = step('ls -l working/', essential=True).output
            missing = missing_for_boot(contents)
            if missing:
                raise LabSwitchError(f"working/ on {self.ip} lacks {', '.join(missing)}: {contents.strip()}")
            copied = file_sizes(contents)
            for name, size in init_sizes.items():
                if copied.get(name) != size:
                    got = 'missing' if name not in copied else f"{copied[name]} bytes"
                    raise LabSwitchError(f"working/{name} on {self.ip} is {got} after the copy from init/, "
                                         f"where it is {size} bytes: {CUT_SHORT}")
            step('rm -rf certified/*', essential=False)
            step('cp -r init/* certified/', essential=False)
            session.run_confirmed(RELOAD_FROM_WORKING)
            logger.info("Cleanup reload started on %s", self.ip)
        return warnings

    def _restore_init_aos6(self, session: Session, repair: Repair, warnings: List[str],
                           step: Callable[[str, bool], CommandResult]) -> None:
        """
        The Cleanup of an AOS 6 Switch (#108), once its local users are removed: rewrites
        init/boot.cfg if it is given, removes the Unwanted files, puts back from certified/ any
        image of working/ that isn't the size of certified's (a member's, or a copy cut short:
        certified/ holds the lab's release, which BLab never changes on AOS 6), copies init/boot.cfg
        into working/, checks it, and reloads from working/. certify() makes certified/ match once
        the Switch is back. Nothing is reloaded if an essential step fails.
        """
        if repair.init_config is not None:
            self._write(session, AOS6_INIT_CONFIG_PATH, repair.init_config)
            logger.info("%s rewritten from the Golden init on %s", AOS6_INIT_CONFIG_PATH, self.ip)
        listings: Dict[str, Dict[str, Optional[int]]] = {}

        def listing(directory: str) -> Dict[str, Optional[int]]:
            if directory not in listings:
                listings[directory] = session.list_dir(directory)
            return listings[directory]

        for path in repair.files:
            if not clean_state.removable(path) or ' ' in path:
                warnings.append(f"Didn't remove /flash/{path}: not a path a Cleanup removes")
                continue
            directory, _, name = path.rpartition('/')
            try:
                is_directory = name in listing(directory) and listing(directory)[name] is None
            except LabSwitchError as e:
                warnings.append(f"Couldn't remove /flash/{path}: {e}")
                continue
            result = session.run(f"rm {'-r ' if is_directory else ''}{clean_state.FLASH}/{path}")
            if result.status != 0:
                warnings.append(f"Couldn't remove /flash/{path}: {result.error.strip() or result.status}")
            else:
                logger.info("/flash/%s removed from %s", path, self.ip)
        init = listing('init')
        init_name = AOS6_INIT_CONFIG_PATH.split('/')[-1]
        if init.get(init_name) is None:
            raise LabSwitchError(f"init/ on {self.ip} lacks {init_name}: {', '.join(sorted(init)) or 'empty'}")
        working = listing('working')
        try:
            certified = listing('certified')
        except LabSwitchError as e:
            logger.warning("Cannot list certified/ on %s: %s", self.ip, e)
            certified = {}
        for name, size in sorted(certified.items()):
            if is_image(name) and size is not None and working.get(name) != size:
                step(f'cp {clean_state.FLASH}/certified/{name} {clean_state.FLASH}/working/{name}', True)
                copied = session.list_dir('working').get(name)
                if copied != size:
                    raise LabSwitchError(f"working/{name} on {self.ip} is {copied} bytes after the copy from "
                                         f"certified/, where it is {size} bytes: {CUT_SHORT}")
                warnings.append(f"working/{name} put back from certified/ (it was "
                                f"{'missing' if name not in working else f'{working[name]} bytes'})")
        listings.pop('working')
        if not any(is_image(name) for name in listing('working')):
            raise LabSwitchError(f"working/ on {self.ip} has no AOS image: {', '.join(sorted(listing('working')))}")
        step(f'cp {clean_state.FLASH}/{AOS6_INIT_CONFIG_PATH} {clean_state.FLASH}/{AOS6_WORKING_CONFIG_PATH}', True)
        copied = session.list_dir('working').get(init_name)
        if copied != init[init_name]:
            raise LabSwitchError(f"{AOS6_WORKING_CONFIG_PATH} on {self.ip} is {copied} bytes after the copy from "
                                 f"init/, where it is {init[init_name]} bytes: {CUT_SHORT}")
        session.run_confirmed(AOS6_RELOAD)
        logger.info("Cleanup reload started on %s", self.ip)

    def certify(self) -> None:
        """
        Makes certified/ what working/ holds, on every chassis of a VC, with CERTIFY_COMMAND, and
        waits until show running-directory says it is done (#99). While AOS isn't ready for it
        after a reload, tries again for CERTIFY_RETRY_TOTAL s (#106). A Switch that runs from
        certified/ (working/ didn't boot) is left as it is. Raises LabSwitchError.
        """
        with self._connect(self.ip) as session:
            give_up = time.monotonic() + CERTIFY_RETRY_TOTAL
            pause = CERTIFY_RETRY_FIRST
            while True:
                try:
                    self._start_certify(session)
                    break
                except LabSwitchError as error:
                    # Not ready yet after a reload: AOS takes it a few minutes later (#106)
                    if not any(why in str(error) for why in CERTIFY_NOT_YET) or time.monotonic() >= give_up:
                        raise
                    logger.info("%s; trying again in %s s", error, pause)
                    time.sleep(pause)
                    pause = min(pause * 2, CERTIFY_RETRY_MAX)
            deadline = time.monotonic() + CERTIFY_TIMEOUT
            while True:
                _, status = self._running_directory(session)
                if status == CERTIFIED_STATUS:
                    break
                if time.monotonic() >= deadline:
                    raise LabSwitchError(f"certified/ on {self.ip} is still '{status}' {CERTIFY_TIMEOUT} s "
                                         f"after '{CERTIFY_COMMAND}'")
                time.sleep(CERTIFY_POLL)
        logger.info("certified/ now matches working/ on %s", self.ip)

    def _start_certify(self, session: Session) -> None:
        """Checks it runs from working/, then sends CERTIFY_COMMAND. Raises LabSwitchError if refused."""
        running, _ = self._running_directory(session)
        if running != 'WORKING':
            raise LabSwitchError(f"{self.ip} runs from {running.lower()}/, not working/: certified/ left as it is")
        command = AOS6_CERTIFY_COMMAND if session.aos6 else CERTIFY_COMMAND
        result = session.run(command)
        # AOS answers some refusals with exit status 0 and an ERROR line (docs/adr/0004)
        refused = [line.strip() for line in result.output.splitlines() if line.strip().startswith('ERROR')]
        if result.status != 0 or refused:
            why = refused[0] if refused else result.error.strip() or f'exit status {result.status}'
            raise LabSwitchError(f"'{command}' failed on {self.ip}: {why}")

    def _running_directory(self, session: Session) -> Tuple[str, str]:
        """Which directory it runs from and its Certify/Restore Status, upper case, from show running-directory."""
        result = session.run(RUNNING_DIRECTORY_COMMAND)
        running = RUNNING_FROM.search(result.output or '') if result.status == 0 else None
        status = CERTIFY_STATUS.search(result.output or '') if result.status == 0 else None
        if running is None or status is None:
            raise LabSwitchError(f"cannot read {RUNNING_DIRECTORY_COMMAND} on {self.ip}: "
                                 f"{result.error.strip() or result.output.strip()[:80] or result.status}")
        return running.group(1).upper(), status.group(1).upper()

    def read_config(self) -> SwitchConfig:
        """
        Reads what a Snapshot holds of the Switch: working/vcboot.cfg, working/vcsetup.cfg if
        there is one, and the AOS release in working/. Only reads.
        """
        with self._connect(self.ip) as session:
            if session.aos6:  # its config alone: AOS 6 has no VC setup (#108)
                return SwitchConfig(self._read(session, AOS6_WORKING_CONFIG_PATH), None, self._release(session))
            listing = session.run('ls working/')
            if listing.status != 0:
                raise LabSwitchError(f"cannot list working/ on {self.ip}: {listing.error.strip() or listing.status}")
            vcboot = self._read(session, WORKING_CONFIG_PATH)
            has_setup = WORKING_VC_SETUP_PATH.split('/')[-1] in listing.output.split()
            vcsetup = self._read(session, WORKING_VC_SETUP_PATH) if has_setup else None
            return SwitchConfig(vcboot, vcsetup, self._release(session))

    def aos_release(self) -> str:
        """The AOS release of the image in working/, which it boots. Only reads."""
        with self._connect(self.ip) as session:
            return self._release(session)

    def init_images_cut_short(self) -> List[str]:
        """
        The images of init/ that may be cut short, as the Cleanup's check (restore_init_and_reload)
        finds them, each as a reason: none on AOS 6, whose init/ holds no image (#108). Only reads.
        Raises LabSwitchError.
        """
        with self._connect(self.ip) as session:
            if session.aos6:
                return []
            listings = {}
            for directory in ('init', 'working', 'certified'):
                result = session.run(f'ls -l {directory}/')
                if result.status != 0 and directory != 'certified':
                    raise LabSwitchError(f"cannot list {directory}/ on {self.ip}: "
                                         f"{result.error.strip() or result.output.strip()[:80] or result.status}")
                listings[f'{directory}/'] = file_sizes(result.output) if result.status == 0 else {}
        init = listings.pop('init/')
        return [f"init/{name} on {self.ip} is {size} bytes ({found})"
                for name, size, found in images_cut_short(init, listings)]

    def microcode(self, directory: str) -> Microcode:
        """The AOS image in a directory (init, working...), with show microcode. Only reads. Raises LabSwitchError."""
        with self._connect(self.ip) as session:
            result = session.run(f'{RELEASE_COMMAND} {directory}')
        found = parse_microcode(result.output) if result.status == 0 else None
        if found is None:
            raise LabSwitchError(f"cannot read the AOS release in {directory}/ on {self.ip}: "
                                 f"{result.error.strip() or result.output.strip()[:80] or result.status}")
        return found

    def install_images(self, images: Sequence[ImageFile], directories: Sequence[str]) -> None:
        """
        Puts AOS images (a release's, api.aos_release) into each directory, in place of the
        images of the same name, and doesn't reload. Each is copied over SFTP into the first
        directory under NEW_IMAGE_SUFFIX and checked (size and md5), copied from there into the
        others and checked (size), and only once every copy is whole are they renamed into
        place, the first directory last. If a step fails before, the copies are removed and the
        directories are as they were. Raises LabSwitchError.
        """
        first = directories[0]
        new = [(directory, image) for image in images for directory in directories]
        with self._connect(self.ip) as session:
            if session.aos6:  # the lab runs AOS 6's last release on them (Titouan, 2026-11-04)
                raise LabSwitchError(f"{self.ip} runs AOS 6: BLab doesn't install AOS releases on AOS 6")
            def run(cmd: str) -> CommandResult:
                result = session.run(cmd)
                if result.status != 0:
                    raise LabSwitchError(f"'{cmd}' returned {result.status} on {self.ip}: "
                                         f"{(result.error or result.output).strip()}")
                return result

            def size_of(directory: str, name: str) -> Optional[int]:
                return file_sizes(run(f'ls -l {directory}/').output).get(name)

            def remove_copies() -> None:
                for directory, image in new:
                    session.run(f'rm -f {directory}/{image.name}{NEW_IMAGE_SUFFIX}')

            free = FREE_SPACE.search(run(FREE_SPACE_COMMAND).output)
            needed = sum(image.size for image in images) * len(directories) + FREE_SPACE_MARGIN
            if free is None:
                raise LabSwitchError(f"cannot read the free space on {self.ip}")
            if int(free.group(1)) < needed:
                raise LabSwitchError(f"/flash on {self.ip} has {int(free.group(1)) // 2**20} MB free, "
                                     f"{needed // 2**20} MB needed")
            try:
                remove_copies()  # left by an install cut short
                for image in images:
                    path = f'{first}/{image.name}{NEW_IMAGE_SUFFIX}'
                    with self._file_errors('copy to', path):
                        session.put_file(image.path, path)
                    if size_of(first, image.name + NEW_IMAGE_SUFFIX) != image.size:
                        raise LabSwitchError(f"{path} on {self.ip} isn't {image.size} bytes once copied")
                    md5 = MD5_LINE.search(run(f'md5sum {path}').output)
                    if md5 is None or md5.group(1).lower() != image.md5:
                        raise LabSwitchError(f"{path} on {self.ip} doesn't match the image's md5 once copied")
                    for directory in directories[1:]:
                        run(f'cp {path} {directory}/')
                        if size_of(directory, image.name + NEW_IMAGE_SUFFIX) != image.size:
                            raise LabSwitchError(f"{directory}/{image.name}{NEW_IMAGE_SUFFIX} on {self.ip} "
                                                 f"isn't {image.size} bytes once copied: {CUT_SHORT}")
            except Exception:
                remove_copies()
                raise
            # The first directory last: a Cleanup checks init/'s images against certified/'s (#80)
            for directory, image in sorted(new, key=lambda pair: pair[0] == first):
                run(f'mv -f {directory}/{image.name}{NEW_IMAGE_SUFFIX} {directory}/{image.name}')
            logger.info("%s installed in %s on %s", ', '.join(i.name for i in images), ', '.join(directories), self.ip)

    def load_config_and_reload(self, config: SwitchConfig) -> None:
        """
        Restore: writes a Snapshot's config (and VC setup, if it has one) into working/ and
        reloads from it. init/ and certified/ are left as they are, so the next Cleanup brings
        the Switch back to its Golden init. Nothing is written if working/ lacks what a boot needs.
        """
        with self._connect(self.ip) as session:
            if session.aos6:
                self._load_config_aos6(session, config)
                return
            contents = session.run('ls working/')
            missing = missing_for_boot(contents.output)
            if contents.status != 0 or missing:
                raise LabSwitchError(f"working/ on {self.ip} lacks {', '.join(missing) or 'a listing'}: "
                                     f"{(contents.output or contents.error).strip()}")
            self._write(session, WORKING_CONFIG_PATH, config.vcboot)
            if config.vcsetup is not None:
                self._write(session, WORKING_VC_SETUP_PATH, config.vcsetup)
            else:  # the Switch had none: it boots as it did then
                removed = session.run(f'rm -f {WORKING_VC_SETUP_PATH}')
                if removed.status != 0:
                    raise LabSwitchError(f"cannot remove {WORKING_VC_SETUP_PATH} on {self.ip}: "
                                         f"{removed.error.strip() or removed.status}")
            session.run_confirmed(RELOAD_FROM_WORKING)
            logger.info("Restore reload started on %s", self.ip)

    def _load_config_aos6(self, session: Session, config: SwitchConfig) -> None:
        """A Restore on AOS 6 (#108): the Snapshot's config as working/boot.cfg, then a reload from working/."""
        if config.vcsetup is not None:
            raise LabSwitchError(f"{self.ip} runs AOS 6, which has no VC setup: this Snapshot is an AOS 8 Switch's")
        working = session.list_dir('working')
        if not any(is_image(name) for name in working):
            raise LabSwitchError(f"working/ on {self.ip} lacks an AOS image: {', '.join(sorted(working))}")
        self._write(session, AOS6_WORKING_CONFIG_PATH, config.vcboot)
        session.run_confirmed(AOS6_RELOAD)
        logger.info("Restore reload started on %s", self.ip)

    def reload(self) -> None:
        """Reloads from working/, as it is: a member's AOS install (api.aos_install) has put its release there."""
        with self._connect(self.ip) as session:
            session.run_confirmed(AOS6_RELOAD if session.aos6 else RELOAD_FROM_WORKING)
            logger.info("Reload from working/ started on %s", self.ip)

    def give_slot_one(self, slot: int, management_port: str) -> None:
        """
        AOS 6 (#113): gives a Switch standing alone in stack slot `slot` slot 1, and reloads it, so
        that its ports are 1/x. Its Management port (`management_port`, slot/port; '' through an
        EMP) is renamed 1/<port> in working/boot.cfg first, or the reload would cut it off.
        Nothing is written, and it doesn't reload, if working/boot.cfg doesn't name that port.
        Raises LabSwitchError.
        """
        with self._connect(self.ip) as session:
            if not session.aos6:
                raise LabSwitchError(f"{self.ip} doesn't run AOS 6: its slot isn't BLab's to change")
            if management_port:
                config = self._read(session, AOS6_WORKING_CONFIG_PATH)
                number = management_port.rpartition('/')[2]
                name = re.compile(rf'(?<![\w/]){slot}/{re.escape(number)}(?![\w/])')
                if not name.search(config):
                    raise LabSwitchError(f"{AOS6_WORKING_CONFIG_PATH} on {self.ip} doesn't name its Management port "
                                         f"{slot}/{number}: not giving it slot 1, the reload would cut it off")
                self._write(session, AOS6_WORKING_CONFIG_PATH, name.sub(f'1/{number}', config))
            session.run_confirmed(AOS6_SLOT_ONE.format(slot))
            logger.info("%s given stack slot 1 (it was %s): reloading", self.ip, slot)

    def _read(self, session: Session, path: str) -> str:
        with self._file_errors('read', path):
            return session.read_file(path)

    def _write(self, session: Session, path: str, text: str) -> None:
        with self._file_errors('write', path):
            session.write_file(path, text)

    @contextmanager
    def _file_errors(self, what: str, path: str):
        """Makes any failure to read or write a file (SFTP refused, no such file...) a LabSwitchError."""
        try:
            yield
        except LabSwitchError:
            raise
        except Exception as e:
            raise LabSwitchError(f"cannot {what} {path} on {self.ip}: {e}") from e

    def _release(self, session: Session) -> str:
        result = session.run(RELEASE_COMMAND)
        match = IMAGE_RELEASE.search(result.output or '') if result.status == 0 else None
        if not match:
            raise LabSwitchError(f"cannot read the AOS release on {self.ip}: "
                                 f"{result.error.strip() or result.output.strip()[:80] or result.status}")
        return match.group(1)

    def uptime(self) -> Optional[timedelta]:
        """
        How long the Switch has been up (show system), or None if it doesn't say (a refusal,
        a line it doesn't understand). Only reads. Raises LabSwitchError if it can't log in.
        """
        with self._connect(self.ip) as session:
            result = session.run(UPTIME_COMMAND)
        uptime = parse_uptime(result.output) if result.status == 0 else None
        if uptime is None:
            logger.warning("Cannot read the uptime of %s: %s", self.ip,
                           result.error.strip() or result.output.strip()[:80] or result.status)
        return uptime

    def identify(self) -> Tuple[str, str]:
        """What it says it is: show system and show chassis. Only reads. Raises LabSwitchError."""
        with self._connect(self.ip) as session:
            system, chassis = session.run(UPTIME_COMMAND), session.run('show chassis')
        if system.status != 0 or chassis.status != 0:
            raise LabSwitchError(f"cannot read show system and show chassis on {self.ip}")
        return system.output, chassis.output

    def lldp_neighbours(self) -> str:
        """show lldp remote-system. Only reads. Raises LabSwitchError."""
        with self._connect(self.ip) as session:
            result = session.run(LLDP_COMMAND)
        if result.status != 0:
            raise LabSwitchError(f"cannot read {LLDP_COMMAND} on {self.ip}: {result.error.strip() or result.status}")
        return result.output

    def port_capabilities(self) -> Dict[str, Capability]:
        """What each port can run at (show interfaces capability). Only reads. Raises LabSwitchError."""
        with self._connect(self.ip) as session:
            result = session.run(CAPABILITY_COMMAND)
        capabilities = parse_capabilities(result.output, session.aos6) if result.status == 0 else {}
        if not capabilities:
            raise LabSwitchError(f"cannot read {CAPABILITY_COMMAND} on {self.ip}: "
                                 f"{result.error.strip() or result.output.strip()[:80] or result.status}")
        return capabilities

    def install_golden_init(self, config: str, vc_setup: Optional[str] = None) -> None:
        """
        Builds a fresh init/ for a Switch being onboarded (#55; as prepare_switches does): the
        AOS image and pkg/ from working/, or certified/ for what working/ lacks, and the Golden
        init as init/vcboot.cfg. A Cleanup then copies it to working/. `vc_setup`, when given, is
        written as init/vcsetup.cfg: a Switch without an EMP keeps its management there (#81), and
        must have it whether or not Cleanups repair the VC setup yet. Raises LabSwitchError.
        """
        with self._connect(self.ip) as session:
            if session.aos6:  # init/ holds the Golden init alone: AOS 6 boots working/'s images (#108)
                if 'init' in session.list_dir(''):
                    session.run(f'rm -r {clean_state.FLASH}/init')
                result = session.run(f'mkdir {clean_state.FLASH}/init')
                if result.status != 0:
                    raise LabSwitchError(f"cannot create init/ on {self.ip}: {result.error.strip()}")
                self._write(session, AOS6_INIT_CONFIG_PATH, config)
                return
            listings = {}
            for source in INIT_SOURCES:
                result = session.run(f'ls {source}/')
                listings[source] = result.output.split() if result.status == 0 else []
            for name, has, _ in INIT_PARTS:
                if not any(has(listing) for listing in listings.values()):
                    raise LabSwitchError(f"no {name} in working/ or certified/ on {self.ip}")
            session.run('rm -rf init')
            result = session.run('mkdir -p init')
            if result.status != 0:
                raise LabSwitchError(f"cannot create init/ on {self.ip}: {result.error.strip()}")
            for name, has, copy in INIT_PARTS:
                # From the first that has it only: certified/ may hold an older release than working/
                source = next(s for s in INIT_SOURCES if has(listings[s]))
                if session.run(copy.format(source)).status != 0:
                    raise LabSwitchError(f"cannot copy the {name} from {source}/ into init/ on {self.ip}")
                if name == 'AOS image':  # a copy cut short, as a full /flash does, would go unseen (#117)
                    sources = file_sizes(session.run(f'ls -l {source}/').output)
                    copied = file_sizes(session.run('ls -l init/').output)
                    for image, size in sources.items():
                        if is_image(image) and copied.get(image) != size:
                            got = 'missing' if image not in copied else f"{copied[image]} bytes"
                            raise LabSwitchError(f"init/{image} on {self.ip} is {got} after the copy from "
                                                 f"{source}/, where it is {size} bytes: {CUT_SHORT}")
            session.write_file(INIT_CONFIG_PATH, config)
            if vc_setup is not None:
                session.write_file(clean_state.INIT_VC_SETUP_PATH, vc_setup)

    def show(self, cmd: str) -> CommandResult:
        """Runs one of the show commands an Inspection reads, and only those."""
        if cmd not in INSPECTION_COMMANDS:
            raise ValueError(f"{cmd!r} is not a command an Inspection reads")
        with self._connect(self.ip) as session:
            return session.run(cmd)

    def running_config(self) -> str:
        """The running config, as show configuration snapshot prints it (#44). Only reads. Raises LabSwitchError."""
        result = self.show(RUNNING_CONFIG_COMMAND)
        if result.status != 0:
            raise LabSwitchError(f"cannot read {RUNNING_CONFIG_COMMAND} on {self.ip}: "
                                 f"{result.error.strip() or result.output.strip()[:80] or result.status}")
        return result.output

    def read_for_inspection(self) -> Readings:
        """
        Reads what an Inspection needs, and only reads: the show commands, the init config
        file, and the entries of the directories a clean state is judged on. Raises
        LoginRefused if BLab's credentials are refused.
        """
        with self._connect(self.ip) as session:
            outputs = {cmd: session.run(cmd) for cmd in INSPECTION_COMMANDS}
            init_path = AOS6_INIT_CONFIG_PATH if session.aos6 else INIT_CONFIG_PATH
            try:
                init_config = session.read_file(init_path)
            except Exception as e:  # SFTP refused, no such file...: the Inspection reports it unreadable
                logger.warning("Cannot read %s on %s: %s", init_path, self.ip, e)
                init_config = None
            if session.aos6:  # no VC setup on AOS 6 (#108)
                return Readings(outputs, init_config, self._entries_aos6(session), init_path=init_path)
            listings = {directory: self._entries(session, directory) for directory in clean_state.SCANNED}
            return Readings(outputs, init_config, listings, self._vc_setup(session, listings.get('init')))

    def _entries_aos6(self, session: Session) -> Dict[str, Optional[List[str]]]:
        """The entries of each scanned directory on AOS 6, over SFTP: [] if it doesn't exist, None if unreadable."""
        try:
            root = session.list_dir('')
        except LabSwitchError as e:
            logger.warning("Cannot list /flash on %s: %s", self.ip, e)
            return {directory: None for directory in clean_state.SCANNED}
        listings: Dict[str, Optional[List[str]]] = {}
        for directory in clean_state.SCANNED:
            if not directory:
                listings[directory] = sorted(root)
            elif directory not in root:
                listings[directory] = []
            else:
                try:
                    listings[directory] = sorted(session.list_dir(directory))
                except LabSwitchError as e:
                    logger.warning("Cannot list /flash/%s on %s: %s", directory, self.ip, e)
                    listings[directory] = None
        return listings

    def _vc_setup(self, session: Session, init: Optional[List[str]]) -> clean_state.VcSetupReading:
        """What an Inspection reads of the VC setup: init/vcsetup.cfg if init/ lists it, and the auto-VFL ports."""
        init_vc_setup = None
        if init and 'vcsetup.cfg' in init:
            try:
                init_vc_setup = session.read_file(clean_state.INIT_VC_SETUP_PATH)
            except Exception as e:  # SFTP refused...: the Inspection reports it unreadable
                logger.warning("Cannot read %s on %s: %s", clean_state.INIT_VC_SETUP_PATH, self.ip, e)
        ports = session.run(clean_state.AUTO_VFL_COMMAND)
        return clean_state.VcSetupReading(
            init_vc_setup, clean_state.parse_auto_vfl_ports(ports.output) if ports.status == 0 else None)

    def _entries(self, session: Session, directory: str) -> Optional[List[str]]:
        """A directory's entries under /flash: [] if it doesn't exist, None if it can't be listed."""
        path = f'{clean_state.FLASH}/{directory}'.rstrip('/')
        result = session.run(f'ls -a {path}')
        if result.status != 0:
            if 'No such file or directory' in result.error:
                return []
            logger.warning("Cannot list %s on %s: %s", path, self.ip, result.error.strip() or result.status)
            return None
        return [name for name in result.output.splitlines() if name.strip() and name not in ('.', '..')]

    def logins_since_boot(self) -> List[Login]:
        """
        The successful logins the Switch logged since it last booted, oldest first. Only reads.
        Raises LabSwitchError if the log can't be read.
        """
        with self._connect(self.ip) as session:
            if session.aos6:
                return self._logins_since_boot_aos6(session)
            result = session.run(LOGIN_LOG_COMMAND)
        # AOS answers some refusals with exit status 0 and an ERROR line (docs/adr/0004)
        refused = [line.strip() for line in result.output.splitlines() if line.strip().startswith('ERROR')]
        if refused or result.status not in (0, 1) or (result.status == 1 and result.output.strip()):
            why = refused[0] if refused else result.error.strip() or f'exit status {result.status}'
            raise LabSwitchError(f"cannot read {LOGIN_LOG} on {self.ip}: {why}")
        return [Login(*match.groups()) for match in map(LOGIN_LINE.search, result.output.splitlines()) if match]

    def _logins_since_boot_aos6(self, session: Session) -> List[Login]:
        """
        The logins of an AOS 6 Switch since it booted (#108): the AAA login lines of its log
        (AOS6_LOGIN_LOG_COMMAND) stamped between its boot and now, by its own clock (show system's
        Date & Time, less its uptime). Its log outlives reboots, and a clock set back would date older
        lines later, so only that window counts.
        """
        system = session.run(UPTIME_COMMAND)
        clock = parse_aos6_clock(system.output) if system.status == 0 else None
        uptime = parse_uptime(system.output) if system.status == 0 else None
        if clock is None or uptime is None:
            raise LabSwitchError(f"cannot read the clock and uptime of {self.ip} in {UPTIME_COMMAND}")
        result = session.run(AOS6_LOGIN_LOG_COMMAND)
        if result.status != 0:
            raise LabSwitchError(f"cannot read {AOS6_LOGIN_LOG_COMMAND} on {self.ip}: "
                                 f"{result.error.strip() or result.status}")
        booted, now = clock - uptime - timedelta(minutes=1), clock + timedelta(minutes=1)
        found = []
        for month, day, hour, minute, second, year, user, through, address in AOS6_LOG_LINE.findall(result.output):
            at = aos6_time(month, day, year, hour, minute, second)
            if at is not None and booted <= at <= now:
                found.append((at, Login(user, address, through or None)))
        return [login for _, login in sorted(found, key=lambda pair: pair[0])]

    def update_accounts(self, create: Dict[str, str], remove: Iterable[str], su: bool = True,
                        read_only: Iterable[str] = ()) -> AccountsUpdate:
        """
        In one session, removes the Switch accounts named in `remove` (one already gone is
        fine) and creates those in `create` (name -> password): local users with full
        privileges, SU allowed unless `su` is False, but those named in `read_only`, which can
        only look, without SU (account_command, #95, #91; an existing one gets the new password
        and privilege). Then, if any changed, saves the config once (SAVE_CONFIG), so that
        every chassis of a VC holds them and a new master
        still has them after a takeover (#60). A VC slave refuses them all: they are listed
        apart (on_slave), not as failures. Raises LabSwitchError if it can't log in at all.
        """
        update = AccountsUpdate(failed={}, on_slave=[])
        changed = False
        read_only = set(read_only)

        def refused(name: str, e: Exception) -> None:
            if REFUSED_ON_SLAVE in str(e):
                update.on_slave.append(name)
            else:
                update.failed[name] = str(e)

        with self._connect(self.ip) as session:
            for name in remove:
                try:
                    check_account_name(name)
                    self._user_command(session, f'no user {name}', already_done='Unknown user')
                    changed = True
                    logger.info("Switch account %s removed from %s", name, self.ip)
                except (ValueError, LabSwitchError) as e:
                    refused(name, e)
            for name, password in create.items():
                try:
                    check_account_name(name)
                    if not ACCOUNT_PASSWORD.fullmatch(password) or ACCOUNT_PASSWORD_REFUSED & set(password):
                        raise ValueError("not a password AOS takes")
                    command, shown = account_command(name, password, su, read_only=name in read_only,
                                                     aos6=session.aos6)
                    try:
                        self._create_account(session, command, shown)
                    except LabSwitchError as e:
                        if not any(why in str(e) for why in MADE_AFRESH_ON):
                            raise
                        # It had this password lately (the user chose an older one again), or it
                        # has the other privilege (#91): made afresh
                        self._user_command(session, f'no user {name}', already_done='Unknown user')
                        self._create_account(session, command, shown)
                    changed = True
                    logger.info("Switch account %s created on %s", name, self.ip)
                except (ValueError, LabSwitchError) as e:
                    refused(name, e)
            if changed and not session.aos6:  # AOS 6 keeps its users at once, in its own table (#108)
                try:
                    self._user_command(session, SAVE_CONFIG)
                except LabSwitchError as e:
                    update.not_saved = str(e)
        return update

    def _create_account(self, session: Session, command: str, shown: str) -> None:
        """
        Runs a Switch account's `user` command (account_command). A release that has no SU
        (before 8.10: show user doesn't say) refuses its `su` word: there it is run without.
        Raises LabSwitchError.
        """
        try:
            self._user_command(session, command, shown=shown)
        except LabSwitchError as e:
            if NO_SU not in str(e):
                raise
            self._user_command(session, command.rsplit(' su ', 1)[0], shown=shown.rsplit(' su ', 1)[0])

    def vc_topology(self) -> VcTopology:
        """The Switch's view of its VC (itself alone when standalone). Only reads. Raises LabSwitchError if unreadable."""
        with self._connect(self.ip) as session:
            result = session.run(VC_TOPOLOGY_COMMAND)
        topology = parse_vc_topology(result.output) if result.status == 0 else None
        if topology is None:
            raise LabSwitchError(f"cannot read the VC topology of {self.ip}: "
                                 f"{result.error.strip() or result.output.strip()[:80] or result.status}")
        return topology

    def _user_command(self, session: Session, cmd: str, shown: Optional[str] = None,
                      already_done: Optional[str] = None) -> None:
        """
        Runs a `user` command, or SAVE_CONFIG. AOS answers exit status 0 even when it refuses
        one, with an "ERROR: ..." line in the output, so that line is a failure too, unless it
        says `already_done`. `shown` replaces the command in messages (not to log a password).
        """
        result = session.run(cmd)
        errors = [line.strip() for line in (result.output or '').splitlines() if line.strip().startswith('ERROR')]
        if result.status == 0 and not errors:
            return
        if already_done and errors and all(already_done in e for e in errors):
            return
        why = '; '.join(errors) or result.error.strip() or f'exit status {result.status}'
        raise LabSwitchError(f"'{shown or cmd}' failed on {self.ip}: {why}")


def parse_uptime(output: str) -> Optional[timedelta]:
    """The Up Time in show system, or None if it has none it can read."""
    line = UPTIME_LINE.search(output or '')
    parts = UPTIME_PART.findall(line.group(1)) if line else []
    if not parts:
        return None
    return timedelta(seconds=sum(int(n) * UPTIME_UNITS[unit[0].lower()] for n, unit in parts))


def uptime_parts(duration: timedelta) -> List[Tuple[int, str]]:
    """A duration in the units show system gives it in: [(days, 'day'), (hours, 'hour')...]."""
    seconds = int(duration.total_seconds())
    return [(seconds // 86400, 'day'), (seconds // 3600 % 24, 'hour'), (seconds // 60 % 60, 'minute'),
            (seconds % 60, 'second')]


def parse_aos6_clock(output: str) -> Optional[datetime]:
    """The Switch's clock in AOS 6's show system (Date & Time), or None if it doesn't say."""
    match = AOS6_CLOCK.search(output or '')
    return aos6_time(*match.groups()) if match else None


def aos6_time(month: str, day: str, year: str, hour: str, minute: str, second: str) -> Optional[datetime]:
    """A time as AOS 6 prints it ("JAN", "02", "2001", "00", "37", "51"), or None if its month isn't one."""
    if month.upper() not in MONTHS:
        return None
    return datetime(int(year), MONTHS.index(month.upper()) + 1, int(day), int(hour), int(minute), int(second))


def quoted_password(password: str, aos6: bool = False) -> str:
    """
    A password as a `user` command takes it. On AOS 8 the command goes through a shell: quoted,
    every character reaches AOS as is. AOS 6's CLI takes double quotes as quotes and a single quote
    as part of the password (10.69.144.148, 2026-11-04); ACCOUNT_PASSWORD_REFUSED keeps both out.
    """
    return f'"{password}"' if aos6 else shlex.quote(password)


def account_command(name: str, password: str, su: bool = True, read_only: bool = False,
                    aos6: bool = False) -> Tuple[str, str]:
    """
    The `user` command that makes a Switch account, or gives it a new password, and how it is
    shown in messages (without the password). Read-write on every family; SU allowed unless
    `su` is False (#95, Titouan 2026-11-03: a member may need the root shell). A read-only
    account (#91) is read-only on every family, without SU. AOS keeps SU as it was when a
    command doesn't say, so it always says (10.69.144.132, AOS 8.11, 2026-11-03). It refuses
    the other privilege for an existing account (OTHER_PRIVILEGE): update_accounts then makes
    it afresh. AOS 6 has no root shell, and refuses the word (#108).
    """
    if read_only:
        rights = 'read-only all' if aos6 else 'read-only all su disable'
    else:
        rights = 'read-write all' if aos6 else f"read-write all su {'enable' if su else 'disable'}"
    return (f'user {name} password {quoted_password(password, aos6)} {rights}',
            f'user {name} password ... {rights}')


def check_account_name(name: str) -> None:
    """Raises ValueError unless name can be a Switch account: never `admin`, nor AOS's template."""
    if not ACCOUNT_NAME.fullmatch(name or '') or name.lower() in BUILT_IN_USERS:
        raise ValueError(f"{name!r} can't be a Switch account")


AFTER_RELOAD_ADMIN_SECURED = ("admin took only an older password after the reload: given the lab's secret one "
                              "again, and saved")
_password_that_worked: Dict[str, str] = {}  # ip -> the admin password the Switch last took
_password_lock = threading.Lock()


def secure_admin(ip: str) -> bool:
    """
    Gives a Switch that only takes an older admin password (one of BLAB_SWITCH_ADMIN_PASSWORDS
    but the first) the lab's secret one, the first, and saves it. Every lab Switch must have it
    (Titouan, 2026-10-20). A Switch that doesn't has just been onboarded, or has just reloaded into
    a release whose own user table has an older admin (#87, secure_admin_after_reload; or a reload
    BLab didn't start, api.login_check, #92): the only times BLab changes admin (docs/adr/0004).
    Returns whether it changed it. Raises LabSwitchError.
    """
    from . import fake_devices  # fake_devices needs LabSwitchError from here
    if fake_devices.devices_are_fake():
        return fake_devices.lab_switches.secure_admin(ip)
    secret = settings.BLAB_SWITCH_ADMIN_PASSWORDS[0]
    try:
        ssh_connect(ip, password=secret).close()
        with _password_lock:  # so took_older_admin_password no longer says otherwise
            _password_that_worked[ip] = secret
        return False
    except paramiko.AuthenticationException:
        pass
    except Exception as e:
        raise LabSwitchError(f"Cannot connect to {ip}: {e}") from e
    device = LabSwitch(ip, ssh())
    with device._connect(ip) as session:
        device._user_command(session, f'user {SWITCH_ADMIN} password {quoted_password(secret, session.aos6)}',
                             shown=f'user {SWITCH_ADMIN} password ...')
        if not session.aos6:  # AOS 6 keeps its users at once (#108)
            device._user_command(session, SAVE_CONFIG)
    try:  # it must take the secret one now, or BLab would believe it does
        ssh_connect(ip, password=secret).close()
    except Exception as e:
        raise LabSwitchError(f"{ip} doesn't take the lab's secret admin password after it was set: {e}") from e
    with _password_lock:
        _password_that_worked[ip] = secret
    logger.info("The admin password of %s is now the lab's secret one", ip)
    return True


def secure_admin_after_reload(ip: str) -> List[str]:
    """
    Once a Switch is back from a reload BLab started (a Cleanup, a Restore, a release install),
    gives admin the lab's secret password again if only an older one works, before the Inspection.
    Each AOS release keeps its own user table, so a reload into another release can bring back an
    older admin password: on 10.69.144.132, the first Cleanup into 8.11.162.R01 did (#87; Titouan
    agreed to the fix, 2026-10-21). Never fails: returns what to note in the history of the action.
    """
    try:
        if secure_admin(ip):
            return [AFTER_RELOAD_ADMIN_SECURED]
    except LabSwitchError as e:
        logger.warning("Couldn't check the admin password of %s after its reload: %s", ip, e)
        return [f"couldn't make sure admin has the lab's secret password after the reload: {e}"]
    return []


def took_older_admin_password(ip: str) -> bool:
    """
    Whether the last time this process logged in to the Switch as admin, trying each password,
    only an older one than the lab's secret one worked (api.login_check repairs it, #92).
    """
    with _password_lock:
        known = _password_that_worked.get(ip)
    return known is not None and known != settings.BLAB_SWITCH_ADMIN_PASSWORDS[0]


def admin_passwords(ip: str) -> List[str]:
    """The admin passwords to try on a Switch, the one it last took first."""
    candidates = list(settings.BLAB_SWITCH_ADMIN_PASSWORDS)
    with _password_lock:
        known = _password_that_worked.get(ip)
    if known in candidates:
        candidates.remove(known)
        candidates.insert(0, known)
    return candidates


def ssh_connect(ip: str, username: str = SWITCH_ADMIN, password: Optional[str] = None,
                timeout: float = 5) -> paramiko.SSHClient:
    """
    The one way into a switch over SSH. Without a password, logs in as admin trying each
    password of BLAB_SWITCH_ADMIN_PASSWORDS. The caller closes the client. Raises
    paramiko.AuthenticationException if every password is refused.
    """
    candidates = [password] if password is not None else admin_passwords(ip)
    refused = None
    for candidate in candidates:
        try:
            client = _connect_once(ip, username, candidate, timeout)
        except paramiko.AuthenticationException as e:
            refused = e
            continue
        if password is None:
            with _password_lock:
                _password_that_worked[ip] = candidate
        return client
    raise refused or paramiko.AuthenticationException('no password to try')


_aos6_ips = set()  # the addresses whose SSH server said it is AOS 6's, learned at each connection
_aos6_connections: Dict[str, Deque[float]] = {}  # ip -> when this process last connected to it (monotonic)
_aos6_lock = threading.Lock()


def speaks_aos6(client: paramiko.SSHClient) -> bool:
    """Whether the switch at the other end of this connection runs AOS 6, by its SSH server's version."""
    version = getattr(client.get_transport(), 'remote_version', None)
    return isinstance(version, str) and bool(AOS6_SSH_VERSION.match(version))


def _wait_for_aos6_slot(ip: str) -> None:
    """Waits until this process has opened fewer than AOS6_SESSIONS_A_MINUTE connections to an AOS 6 Switch in the last minute."""
    while True:
        with _aos6_lock:
            opened = _aos6_connections.setdefault(ip, deque())
            now = time.monotonic()
            while opened and now - opened[0] >= AOS6_MINUTE:
                opened.popleft()
            if len(opened) < AOS6_SESSIONS_A_MINUTE:
                opened.append(now)
                return
            wait = AOS6_MINUTE - (now - opened[0])
        logger.info("Waiting %.0f s to open another SSH session to %s (AOS 6 takes 3 a minute)", wait, ip)
        time.sleep(max(wait, 0.5))


def _connect_once(ip: str, username: str, password: str, timeout: float) -> paramiko.SSHClient:
    """
    One login. To a known AOS 6 Switch, it keeps under its rate (_wait_for_aos6_slot), and
    when the Switch closes the connection before its banner all the same (another process
    connected meanwhile), waits AOS6_REFUSED_WAIT and tries again, AOS6_CONNECT_TRIES times in all.
    An address not known yet is tried once, as an AOS 8 Switch still booting would otherwise hold
    its caller a minute: lab_switch() knows the recorded AOS 6 Switches by their model. Raises
    paramiko.AuthenticationException if the password is refused.
    """
    tries = AOS6_CONNECT_TRIES if ip in _aos6_ips else 1
    for attempt in range(1, tries + 1):
        if ip in _aos6_ips:
            _wait_for_aos6_slot(ip)
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(ip, port=22, username=username, password=password, timeout=timeout,
                           banner_timeout=max(timeout, 15), look_for_keys=False, allow_agent=False)
            client.get_transport().set_keepalive(KEEPALIVE_INTERVAL)
        except paramiko.AuthenticationException:
            _learn_aos6(ip, client)  # a refused password still counts towards its rate
            client.close()
            raise
        except paramiko.SSHException as e:
            client.close()
            if attempt == tries:
                raise
            logger.warning("%s closed the SSH connection before its banner (%s): over AOS 6's rate? Trying again "
                           "in %.0f s", ip, e, AOS6_REFUSED_WAIT)
            time.sleep(AOS6_REFUSED_WAIT)
            continue
        except Exception:
            client.close()
            raise
        _learn_aos6(ip, client)
        return client
    raise AssertionError('unreachable')


def _learn_aos6(ip: str, client: paramiko.SSHClient) -> None:
    """Remembers an address whose SSH server is AOS 6's; its first connection counts towards its rate too."""
    if speaks_aos6(client):
        with _aos6_lock:
            if ip not in _aos6_ips:
                _aos6_ips.add(ip)
                _aos6_connections.setdefault(ip, deque()).append(time.monotonic())


class _Bounded:
    """
    What bounds each step of a session over SSH: past its timeout, the connections are closed,
    which ends whatever the step waits on, and it fails with LabSwitchError, as does every later
    step of the session.
    """
    _timed_out: Optional[str] = None  # why the session was closed, once a step took too long

    def _transports(self) -> List[paramiko.Transport]:
        raise NotImplementedError

    @contextmanager
    def _within(self, what: str, timeout: float):
        """
        Closes the connections if the block takes over timeout seconds, whatever it waits on
        (opening a channel, reading, the exit status), and makes that, or a read timing out,
        a LabSwitchError.
        """
        if self._timed_out:
            raise LabSwitchError(self._timed_out)
        transports = self._transports()  # closed alone: SSHClient.close isn't safe across threads
        expired = threading.Event()

        def expire():
            expired.set()
            for transport in transports:
                transport.close()
        watchdog = threading.Timer(timeout, expire)
        watchdog.daemon = True
        watchdog.start()
        try:
            yield
        except Exception as e:
            if expired.is_set() or isinstance(e, socket.timeout):
                raise self._time_out(what, timeout) from e
            raise
        finally:
            watchdog.cancel()
        if expired.is_set():
            raise self._time_out(what, timeout)

    def _time_out(self, what: str, timeout: float) -> LabSwitchError:
        """The error a step that took too long fails with; every later step of the session fails with it too."""
        self._timed_out = f"{what} timed out after {timeout:g} s"
        return LabSwitchError(self._timed_out)


class SshSession(_Bounded, Session):
    """
    A session over SSH, to an AOS 8 Switch. Every command and file transfer is bounded (COMMAND_TIMEOUT,
    COPY_TIMEOUT for copies): past it, the connection is closed, which ends whatever it waits on, and the
    step fails with LabSwitchError, as does every later step of the session.
    """

    def __init__(self, client: paramiko.SSHClient):
        self.client = client

    def _transports(self) -> List[paramiko.Transport]:
        transport = self.client.get_transport()
        return [transport] if transport is not None else []

    def run(self, cmd: str) -> CommandResult:
        timeout = COPY_TIMEOUT if cmd.startswith(COPY_COMMANDS) else COMMAND_TIMEOUT
        with self._within(f"'{cmd}'", timeout):
            _, stdout, stderr = self.client.exec_command(cmd, timeout=timeout)
            output = stdout.read().decode('utf-8', 'replace')
            error = stderr.read().decode('utf-8', 'replace')
            return CommandResult(stdout.channel.recv_exit_status(), output, error)

    def run_confirmed(self, cmd: str) -> None:
        """
        Answers yes once the command asks ("Confirm Activate (Y/N) :"), then keeps the session
        open while it works. Closing it at once lets AOS 8.10 drop the command: on
        10.69.144.132 (8.10.86.R04, 2026-10-12) a reload answered and closed within a second
        never happened, while AOS first "verifies and copies images before reloading".
        """
        with self._within(f"'{cmd}'", COMMAND_TIMEOUT):
            stdin, stdout, _ = self.client.exec_command(cmd, get_pty=True, timeout=COMMAND_TIMEOUT)
            channel = stdout.channel
            self._read_until(channel, lambda text: '(Y/N)' in text.upper(), CONFIRM_PROMPT_WAIT)
            stdin.write('y\n')
            stdin.flush()
            # Until the switch closes the session going down, or long enough for the command to be under way
            self._read_until(channel, lambda text: False, CONFIRMED_COMMAND_WAIT)

    @staticmethod
    def _read_until(channel, done: Callable[[str], bool], timeout: float) -> str:
        """What the channel prints, until done(it) says so, the command exits, or timeout seconds pass."""
        text = ''
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not channel.exit_status_ready() and not channel.closed:
            if channel.recv_ready():
                text += channel.recv(4096).decode('utf-8', 'replace')
                if done(text):
                    break
            else:
                time.sleep(0.2)
        return text

    def write_file(self, path: str, text: str) -> None:
        with self._within(f'writing {path}', COMMAND_TIMEOUT):
            with self._sftp() as sftp:
                with sftp.file(path, 'w') as file:
                    file.write(text)

    def read_file(self, path: str) -> str:
        with self._within(f'reading {path}', COMMAND_TIMEOUT):
            with self._sftp() as sftp:
                with sftp.file(path, 'r') as file:
                    return file.read().decode('utf-8', 'replace')

    def put_file(self, local_path: str, path: str) -> None:
        with self._within(f'copying {path}', UPLOAD_TIMEOUT):
            with self._sftp(UPLOAD_TIMEOUT) as sftp:
                sftp.put(local_path, path)

    def _sftp(self, timeout: float = COMMAND_TIMEOUT) -> paramiko.SFTPClient:
        """An SFTP session whose reads and writes time out like commands."""
        sftp = self.client.open_sftp()
        sftp.get_channel().settimeout(timeout)
        return sftp


# What ShellSession reads once a command is sent: AOS 6 echoes it, prints its output, then its
# prompt again ("-> ", or the Golden init's "<model>-> "). A line of ERROR is how it refuses one.
SHELL_WIDTH = 511       # columns: no command BLab sends wraps
SHELL_QUIET = 1.5       # seconds of silence that end the banner at login
SHELL_LOGIN_WAIT = 20   # seconds the banner and first prompt may take
CONFIRM_ANSWERED_WAIT = 10  # seconds AOS 6 may take to answer a confirmed command or to drop the session
MORE_PROMPT = re.compile(r'(?:--More--|More\?)[^\n]*$', re.IGNORECASE)


class ShellSession(_Bounded, Session):
    """
    A session to an AOS 6 Switch (#108): its CLI, driven through one interactive shell, as AOS 6
    takes no exec request. Each command is sent, then read until the prompt the session learned
    at login comes back. Files go over SFTP on a second connection, opened when first needed
    (AOS 6 opens one channel per connection). Bounded as SshSession is; a session that timed
    out, or whose Switch went down, is never used again (`usable`).
    """
    aos6 = True

    def __init__(self, client: paramiko.SSHClient, open_file_client: Callable[[], paramiko.SSHClient]):
        self.client = client
        self._open_file_client = open_file_client
        self._file_client: Optional[paramiko.SSHClient] = None
        self._sftp_client: Optional[paramiko.SFTPClient] = None
        self.reloaded = False  # a reload was confirmed: the session ends with it
        with self._within('opening the CLI', SHELL_LOGIN_WAIT + 5):
            self.channel = client.invoke_shell(width=SHELL_WIDTH, height=0)
            self.prompt = self._learn_prompt()

    def _transports(self) -> List[paramiko.Transport]:
        clients = [self.client] + ([self._file_client] if self._file_client is not None else [])
        return [t for t in (c.get_transport() for c in clients) if t is not None]

    @property
    def usable(self) -> bool:
        """Whether the session may serve another step: it didn't time out, its Switch didn't reload, it is still open."""
        transport = self.client.get_transport()
        return (not self._timed_out and not self.reloaded and transport is not None and transport.is_active()
                and not self.channel.closed)

    def close(self) -> None:
        if self._sftp_client is not None:
            self._sftp_client.close()
        for client in (self._file_client, self.client):
            if client is not None:
                client.close()

    def _read(self, done: Callable[[str], bool], timeout: float) -> str:
        """What the shell prints (\\r dropped), until done(it) says so, the channel closes, or timeout seconds pass."""
        text = ''
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self.channel.closed:
            if self.channel.recv_ready():
                text += self.channel.recv(65536).decode('utf-8', 'replace').replace('\r', '')
                if MORE_PROMPT.search(text[-80:]):
                    self.channel.send(' ')
                    text = MORE_PROMPT.sub('', text)
                if done(text):
                    break
            else:
                time.sleep(0.05)
        return text

    def _learn_prompt(self) -> str:
        """The prompt, as the last line of what the Switch prints once logged in and quiet."""
        text, quiet_since, deadline = '', time.monotonic(), time.monotonic() + SHELL_LOGIN_WAIT
        while time.monotonic() < deadline:
            if self.channel.recv_ready():
                text += self.channel.recv(65536).decode('utf-8', 'replace').replace('\r', '')
                quiet_since = time.monotonic()
            elif text.strip() and time.monotonic() - quiet_since >= SHELL_QUIET:
                break
            else:
                time.sleep(0.05)
        prompt = text.rsplit('\n', 1)[-1]
        if not prompt.strip():
            raise LabSwitchError(f"no CLI prompt from the Switch after its login: {text.strip()[-80:]!r}")
        return prompt

    def _prompted(self, text: str) -> bool:
        return text.endswith('\n' + self.prompt) or text == self.prompt

    def run(self, cmd: str) -> CommandResult:
        timeout = COPY_TIMEOUT if cmd.startswith(AOS6_COPY_COMMANDS) else COMMAND_TIMEOUT
        with self._within(f"'{cmd}'", timeout):
            if self.channel.closed:
                raise LabSwitchError(f"the CLI session closed before '{cmd}'")
            self.channel.send(cmd + '\n')
            text = self._read(self._prompted, timeout + 1)
            if not self._prompted(text):
                if self.channel.closed:
                    raise LabSwitchError(f"the CLI session closed during '{cmd}': {text.strip()[-80:]!r}")
                raise socket.timeout()
        lines = text[:-len(self.prompt)].split('\n')
        output = '\n'.join(lines[1:])  # its first line is the command, echoed
        errors = [line.strip() for line in lines[1:] if line.strip().startswith('ERROR')]
        return CommandResult(1 if errors else 0, output, '\n'.join(errors))

    def run_confirmed(self, cmd: str) -> None:
        """
        Answers yes once the command asks ("Confirm Activate (Y/N) : "), then waits for it to be
        under way: the Switch drops the session going down, or prints its prompt again. A refusal
        instead of the question (an ERROR line) raises LabSwitchError.
        """
        with self._within(f"'{cmd}'", COMMAND_TIMEOUT):
            self.channel.send(cmd + '\n')
            text = self._read(lambda t: '(Y/N)' in t.upper() or self._prompted(t), CONFIRM_PROMPT_WAIT)
            if '(Y/N)' not in text.upper():
                errors = [line.strip() for line in text.split('\n') if line.strip().startswith('ERROR')]
                raise LabSwitchError(f"'{cmd}' wasn't confirmed: {'; '.join(errors) or text.strip()[-80:]!r}")
            self.channel.send('y\n')
            if cmd.startswith('reload') or cmd.endswith(' reload'):
                self.reloaded = True
            self._read(self._prompted, CONFIRM_ANSWERED_WAIT)

    def list_dir(self, path: str) -> Dict[str, Optional[int]]:
        with self._within(f'listing /flash/{path}', COMMAND_TIMEOUT):
            try:
                entries = self._sftp().listdir_attr(path or '.')
            except LabSwitchError:
                raise
            except Exception as e:
                raise LabSwitchError(f"cannot list /flash/{path}: {e}") from e
        return {entry.filename: (None if stat.S_ISDIR(entry.st_mode or 0) else entry.st_size)
                for entry in entries if entry.filename not in ('.', '..')}

    def write_file(self, path: str, text: str) -> None:
        with self._within(f'writing {path}', COMMAND_TIMEOUT):
            with self._sftp().file(path, 'w') as file:
                file.write(text)

    def read_file(self, path: str) -> str:
        with self._within(f'reading {path}', COMMAND_TIMEOUT):
            with self._sftp().file(path, 'r') as file:
                return file.read().decode('utf-8', 'replace')

    def put_file(self, local_path: str, path: str) -> None:
        raise LabSwitchError("BLab doesn't copy AOS images to AOS 6 Switches")

    def _sftp(self) -> paramiko.SFTPClient:
        """The SFTP session of this session's second connection, opened at its first use."""
        if self._sftp_client is None:
            self._file_client = self._open_file_client()
            self._sftp_client = self._file_client.open_sftp()
            self._sftp_client.get_channel().settimeout(COMMAND_TIMEOUT)
        return self._sftp_client


# AOS 6 CLI sessions kept open between steps (#108): (ip, username, password) -> (session, when it was
# last given back). One step at a time uses one; a step that finds none opens one.
_idle_shells: Dict[tuple, Tuple[ShellSession, float]] = {}
_shell_locks: Dict[tuple, threading.Lock] = {}
_shells_lock = threading.Lock()
_reaper: Optional[threading.Thread] = None


def _shell_lock(key: tuple) -> threading.Lock:
    with _shells_lock:
        return _shell_locks.setdefault(key, threading.Lock())


def _reap_idle_shells() -> None:
    """Closes the AOS 6 sessions unused for AOS6_IDLE_CLOSE seconds: a Switch takes 8 SSH sessions at most."""
    while True:
        time.sleep(AOS6_IDLE_CLOSE / 3)
        now = time.monotonic()
        with _shells_lock:
            stale = [key for key, (_, since) in _idle_shells.items() if now - since >= AOS6_IDLE_CLOSE]
            closing = [_idle_shells.pop(key)[0] for key in stale]
        for session in closing:
            session.close()


def _keep_shell(key: tuple, session: ShellSession) -> None:
    global _reaper
    with _shells_lock:
        kept = _idle_shells.get(key)
        if kept is not None and kept[0] is not session:
            # Two steps met the Switch at once and each opened one: one is enough of its 8
            kept[0].close()
        _idle_shells[key] = (session, time.monotonic())
        if _reaper is None:
            _reaper = threading.Thread(target=_reap_idle_shells, name='aos6-session-reaper', daemon=True)
            _reaper.start()


def _take_shell(key: tuple) -> Optional[ShellSession]:
    with _shells_lock:
        kept = _idle_shells.pop(key, None)
    if kept is None:
        return None
    if kept[0].usable:
        return kept[0]
    kept[0].close()
    return None


def ssh(username: str = SWITCH_ADMIN, password: Optional[str] = None) -> Connect:
    """
    Connects to real switches with these credentials (admin and its passwords by default).
    Any error becomes LabSwitchError. An AOS 6 Switch gets a ShellSession, kept open for the
    next step (#108); an AOS 8 one an SshSession, closed after each step.
    """
    def errors(ip: str):
        @contextmanager
        def translated():
            try:
                yield
            except LabSwitchError:
                raise
            except paramiko.AuthenticationException as e:
                raise LoginRefused(f"{ip} refused the login of {username}: {e}") from e
            except Exception as e:
                raise LabSwitchError(f"Cannot connect to {ip}: {e}") from e
        return translated()

    @contextmanager
    def shell(ip: str, key: tuple, client: Optional[paramiko.SSHClient]) -> Iterator[Session]:
        """An AOS 6 Switch's kept session, or a new one on `client` (or a new connection); kept again unless spoiled."""
        with _shell_lock(key):
            session = None if client is not None else _take_shell(key)
            if session is None:
                with errors(ip):
                    if client is None:
                        client = ssh_connect(ip, username, password)
                    try:
                        session = ShellSession(client, lambda: ssh_connect(ip, username, password))
                    except Exception:
                        client.close()
                        raise
            try:
                yield session
            except LabSwitchError:
                session.close()
                raise
            except Exception as e:
                session.close()
                raise LabSwitchError(f"SSH error on {ip}: {e}") from e
            if session.usable:
                _keep_shell(key, session)
            else:
                session.close()

    @contextmanager
    def connect(ip: str) -> Iterator[Session]:
        key = (ip, username, password)
        if ip in _aos6_ips:
            with shell(ip, key, None) as session:
                yield session
            return
        with errors(ip):
            client = ssh_connect(ip, username, password)
        if speaks_aos6(client):  # first met: a ShellSession from now on
            with shell(ip, key, client) as session:
                yield session
            return
        try:
            yield SshSession(client)
        except LabSwitchError:
            raise
        except Exception as e:
            raise LabSwitchError(f"SSH error on {ip}: {e}") from e
        finally:
            client.close()
    return connect


def lab_switch(ip: str) -> LabSwitch:
    """The one place that decides whether lab switch steps reach real devices."""
    from . import fake_devices  # fake_devices needs LabSwitchError from here
    if fake_devices.devices_are_fake():
        return LabSwitch(ip, fake_devices.lab_switches.connect)
    if ip not in _aos6_ips:
        _learn_aos6_from_record(ip)
    return LabSwitch(ip, ssh())


def _learn_aos6_from_record(ip: str) -> None:
    """A Switch the database records with an AOS 6 model is known as AOS 6 before BLab first connects (#108)."""
    from .models import Switch  # models imports this module
    try:
        model = Switch.everything.filter(mngt_IP=ip).values_list('model', flat=True).first()
    except Exception as e:  # no database (a script): the connection tells
        logger.debug("Couldn't look up the model of %s: %s", ip, e)
        return
    if clean_state.runs_aos6(model):
        with _aos6_lock:
            _aos6_ips.add(ip)
