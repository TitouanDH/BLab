"""
A lab Switch, seen as what BLab does to it over SSH: set its banner, Clean it up, create and
remove Switch accounts, read its VC topology, read what an Inspection needs, and read and load what a Snapshot holds
(see CONTEXT.md).

LabSwitch runs its steps through a session, opened by a connect function picked in one
place (lab_switch()): the real switch over SSH, or the in-memory fake in fake_devices.

BLab logs in as `admin`, trying each password of settings.BLAB_SWITCH_ADMIN_PASSWORDS
(docs/adr/0004). It never changes `admin`: account commands refuse that name.
"""
import logging
import re
import shlex
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, ContextManager, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import paramiko
from django.conf import settings

from . import clean_state

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
COPY_COMMANDS = ('cp ',)
# Seconds between SSH keepalives: an idle session that a NAT or firewall would drop stays open.
# A dead connection is only seen once TCP gives up; the timeouts above are what bound a step.
KEEPALIVE_INTERVAL = 15
# A working/ directory missing any of these would not boot cleanly, so Cleanup won't reload
ESSENTIAL_FILES = ('.img', 'pkg', 'vcboot.cfg')


def missing_for_boot(listing: str) -> List[str]:
    """What a boot needs (ESSENTIAL_FILES) that a directory's listing lacks."""
    return [name for name in ESSENTIAL_FILES if name not in listing]

# What an Inspection reads: only show commands, and the init config file (read, never written)
INSPECTION_COMMANDS = ('show chassis', 'show interfaces', 'show configuration snapshot', 'show user')
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
# How a VC slave refuses a `user` command: the master holds the local users of the whole VC
REFUSED_ON_SLAVE = 'Command not allowed on slave'
# Saves the running config, local users included, and has AOS copy it to every chassis of a VC
# (#60). Checked on 10.69.144.132 (AOS 8.10.86.R04, 2026-10-19): no confirmation, exit status 0,
# under a second; it rewrites working/vcboot.cfg from the running config.
SAVE_CONFIG = 'write memory'
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
RELOAD_FROM_WORKING = 'reload from working no rollback-timeout'
# How long the Switch has been up, in show system (10.69.144.132, AOS 8.10.86.R04, 2026-10-19):
# "  Up Time:      2 days 23 hours 1 minutes and 55 seconds,". Units it leaves out count as 0.
UPTIME_COMMAND = 'show system'
UPTIME_LINE = re.compile(r'^\s*Up\s*Time\s*:(.*)$', re.IGNORECASE | re.MULTILINE)
UPTIME_PART = re.compile(r'(\d+)\s*(d(?:ays?)?|h(?:ours?|rs?)?|m(?:inutes?|ins?)?|s(?:econds?|ecs?)?)\b', re.IGNORECASE)
UPTIME_UNITS = {'d': 86400, 'h': 3600, 'm': 60, 's': 1}  # by the unit's first letter


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
    """What LabSwitch needs from an open connection to one switch."""

    def run(self, cmd: str) -> CommandResult:
        raise NotImplementedError

    def run_confirmed(self, cmd: str) -> None:
        """Starts a command that asks for confirmation, and answers yes."""
        raise NotImplementedError

    def write_file(self, path: str, text: str) -> None:
        raise NotImplementedError

    def read_file(self, path: str) -> str:
        raise NotImplementedError


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
    unreadable), and the entries of each directory of clean_state.SCANNED ([] if it doesn't
    exist, None if it couldn't be listed).
    """
    outputs: Dict[str, CommandResult]
    init_config: Optional[str]
    listings: Dict[str, Optional[List[str]]]


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


Connect = Callable[[str], ContextManager[Session]]  # ip -> open session


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
        it is given, then replaces working/ with init/, checks it can boot, refreshes
        certified/ as a backup, and reloads from working/. Nothing is reloaded if an
        essential step fails: rewriting init's config is one, and so is an init/ without
        what a boot needs (checked before working/ is touched). Returns what it couldn't
        remove, a line each.
        """
        warnings = []
        with self._connect(self.ip) as session:
            def step(cmd: str, essential: bool) -> CommandResult:
                result = session.run(cmd)
                if result.status != 0:
                    message = f"'{cmd}' returned {result.status} on {self.ip}: {result.error.strip()}"
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
            if repair.init_config is not None:
                try:
                    session.write_file(INIT_CONFIG_PATH, repair.init_config)
                except LabSwitchError:
                    raise
                except Exception as e:  # SFTP refused...
                    raise LabSwitchError(f"cannot rewrite {INIT_CONFIG_PATH} on {self.ip}: {e}") from e
                logger.info("%s rewritten from the Golden init on %s", INIT_CONFIG_PATH, self.ip)
            for path in repair.files:
                if not clean_state.removable(path):
                    warnings.append(f"Didn't remove /flash/{path}: not a path a Cleanup removes")
                    continue
                result = session.run(f'rm -rf -- {shlex.quote(f"{clean_state.FLASH}/{path}")}')
                if result.status != 0:
                    warnings.append(f"Couldn't remove /flash/{path}: {result.error.strip() or result.status}")
                else:
                    logger.info("/flash/%s removed from %s", path, self.ip)

            init = step('ls init/', essential=True).output
            missing = missing_for_boot(init)
            if missing:
                raise LabSwitchError(f"init/ on {self.ip} lacks {', '.join(missing)}: {init.strip()}")
            step('rm -rf working/*', essential=False)
            step('cp -r init/* working/', essential=True)
            contents = step('ls working/', essential=True).output
            missing = missing_for_boot(contents)
            if missing:
                raise LabSwitchError(f"working/ on {self.ip} lacks {', '.join(missing)}: {contents.strip()}")
            step('rm -rf certified/*', essential=False)
            step('cp -r init/* certified/', essential=False)
            session.run_confirmed(RELOAD_FROM_WORKING)
            logger.info("Cleanup reload started on %s", self.ip)
        return warnings

    def read_config(self) -> SwitchConfig:
        """
        Reads what a Snapshot holds of the Switch: working/vcboot.cfg, working/vcsetup.cfg if
        there is one, and the AOS release in working/. Only reads.
        """
        with self._connect(self.ip) as session:
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

    def load_config_and_reload(self, config: SwitchConfig) -> None:
        """
        Restore: writes a Snapshot's config (and VC setup, if it has one) into working/ and
        reloads from it. init/ and certified/ are left as they are, so the next Cleanup brings
        the Switch back to its Golden init. Nothing is written if working/ lacks what a boot needs.
        """
        with self._connect(self.ip) as session:
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

    def show(self, cmd: str) -> CommandResult:
        """Runs one of the show commands an Inspection reads, and only those."""
        if cmd not in INSPECTION_COMMANDS:
            raise ValueError(f"{cmd!r} is not a command an Inspection reads")
        with self._connect(self.ip) as session:
            return session.run(cmd)

    def read_for_inspection(self) -> Readings:
        """
        Reads what an Inspection needs, and only reads: the show commands, the init config
        file, and the entries of the directories a clean state is judged on. Raises
        LoginRefused if BLab's credentials are refused.
        """
        with self._connect(self.ip) as session:
            outputs = {cmd: session.run(cmd) for cmd in INSPECTION_COMMANDS}
            try:
                init_config = session.read_file(INIT_CONFIG_PATH)
            except Exception as e:  # SFTP refused, no such file...: the Inspection reports it unreadable
                logger.warning("Cannot read %s on %s: %s", INIT_CONFIG_PATH, self.ip, e)
                init_config = None
            listings = {directory: self._entries(session, directory) for directory in clean_state.SCANNED}
            return Readings(outputs, init_config, listings)

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
            result = session.run(LOGIN_LOG_COMMAND)
        # AOS answers some refusals with exit status 0 and an ERROR line (docs/adr/0004)
        refused = [line.strip() for line in result.output.splitlines() if line.strip().startswith('ERROR')]
        if refused or result.status not in (0, 1) or (result.status == 1 and result.output.strip()):
            why = refused[0] if refused else result.error.strip() or f'exit status {result.status}'
            raise LabSwitchError(f"cannot read {LOGIN_LOG} on {self.ip}: {why}")
        return [Login(*match.groups()) for match in map(LOGIN_LINE.search, result.output.splitlines()) if match]

    def update_accounts(self, create: Dict[str, str], remove: Iterable[str]) -> AccountsUpdate:
        """
        In one session, removes the Switch accounts named in `remove` (one already gone is
        fine) and creates those in `create` (name -> password): local users with full
        privileges (an existing one gets the new password). Then, if any changed, saves the
        config once (SAVE_CONFIG), so that every chassis of a VC holds them and a new master
        still has them after a takeover (#60). A VC slave refuses them all: they are listed
        apart (on_slave), not as failures. Raises LabSwitchError if it can't log in at all.
        """
        update = AccountsUpdate(failed={}, on_slave=[])
        changed = False

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
                    # The command goes through a shell on the Switch: quoted, every character reaches AOS as is
                    command = f'user {name} password {shlex.quote(password)} read-write all'
                    shown = f'user {name} password ... read-write all'
                    try:
                        self._user_command(session, command, shown=shown)
                    except LabSwitchError as e:
                        if PASSWORD_IN_HISTORY not in str(e):
                            raise
                        # It had this password lately (the user chose an older one again): made afresh
                        self._user_command(session, f'no user {name}', already_done='Unknown user')
                        self._user_command(session, command, shown=shown)
                    changed = True
                    logger.info("Switch account %s created on %s", name, self.ip)
                except (ValueError, LabSwitchError) as e:
                    refused(name, e)
            if changed:
                try:
                    self._user_command(session, SAVE_CONFIG)
                except LabSwitchError as e:
                    update.not_saved = str(e)
        return update

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


def check_account_name(name: str) -> None:
    """Raises ValueError unless name can be a Switch account: never `admin`, nor AOS's template."""
    if not ACCOUNT_NAME.fullmatch(name or '') or name.lower() in BUILT_IN_USERS:
        raise ValueError(f"{name!r} can't be a Switch account")


_password_that_worked: Dict[str, str] = {}  # ip -> the admin password the Switch last took
_password_lock = threading.Lock()


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
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(ip, port=22, username=username, password=candidate, timeout=timeout,
                           look_for_keys=False, allow_agent=False)
            client.get_transport().set_keepalive(KEEPALIVE_INTERVAL)
        except paramiko.AuthenticationException as e:
            client.close()
            refused = e
            continue
        except Exception:
            client.close()
            raise
        if password is None:
            with _password_lock:
                _password_that_worked[ip] = candidate
        return client
    raise refused or paramiko.AuthenticationException('no password to try')


class SshSession(Session):
    """
    A session over SSH. Every command and file transfer is bounded (COMMAND_TIMEOUT, COPY_TIMEOUT
    for copies): past it, the connection is closed, which ends whatever it waits on, and the step
    fails with LabSwitchError, as does every later step of the session.
    """

    def __init__(self, client: paramiko.SSHClient):
        self.client = client
        self._timed_out: Optional[str] = None  # why the session was closed, once a step took too long

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

    def _sftp(self) -> paramiko.SFTPClient:
        """An SFTP session whose reads and writes time out like commands."""
        sftp = self.client.open_sftp()
        sftp.get_channel().settimeout(COMMAND_TIMEOUT)
        return sftp

    @contextmanager
    def _within(self, what: str, timeout: float):
        """
        Closes the connection if the block takes over timeout seconds, whatever it waits on
        (opening a channel, reading, the exit status), and makes that, or a read timing out,
        a LabSwitchError.
        """
        if self._timed_out:
            raise LabSwitchError(self._timed_out)
        transport = self.client.get_transport()  # closed alone: SSHClient.close isn't safe across threads
        expired = threading.Event()

        def expire():
            expired.set()
            if transport is not None:
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


def ssh(username: str = SWITCH_ADMIN, password: Optional[str] = None) -> Connect:
    """
    Connects to real switches with these credentials (admin and its passwords by default).
    Any error becomes LabSwitchError.
    """
    @contextmanager
    def connect(ip: str) -> Iterator[Session]:
        try:
            client = ssh_connect(ip, username, password)
        except paramiko.AuthenticationException as e:
            raise LoginRefused(f"{ip} refused the login of {username}: {e}") from e
        except Exception as e:
            raise LabSwitchError(f"Cannot connect to {ip}: {e}") from e
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
    return LabSwitch(ip, ssh())
