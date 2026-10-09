"""
A lab Switch, seen as what BLab does to it over SSH: set its banner, Clean it up, create and
remove Switch accounts, and read what an Inspection needs (see CONTEXT.md).

LabSwitch runs its steps through a session, opened by a connect function picked in one
place (lab_switch()): the real switch over SSH, or the in-memory fake in fake_devices.

BLab logs in as `admin`, trying each password of settings.BLAB_SWITCH_ADMIN_PASSWORDS
(docs/adr/0004). It never changes `admin`: account commands refuse that name.
"""
import logging
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, ContextManager, Dict, Iterable, Iterator, List, Optional

import paramiko
from django.conf import settings

logger = logging.getLogger(__name__)

SWITCH_ADMIN = 'admin'
BANNER_PATH = 'switch/pre_banner.txt'
# A working/ directory missing any of these would not boot cleanly, so Cleanup won't reload
ESSENTIAL_FILES = ('.img', 'pkg', 'vcboot.cfg')
# What an Inspection reads: only show commands, and the init config file (read, never written)
INSPECTION_COMMANDS = ('show chassis', 'show interfaces', 'show configuration snapshot', 'show user')
INIT_CONFIG_PATH = 'init/vcboot.cfg'
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
# Passwords BLab generates: no quote, space or '!' (AOS refuses '!'), so they pass the CLI as is
ACCOUNT_PASSWORD = re.compile(r'[A-Za-z0-9._@%=+-]{8,64}')


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
    """What an Inspection read on a switch: each show command's result, and init's config (None if unreadable)."""
    outputs: Dict[str, CommandResult]
    init_config: Optional[str]


Connect = Callable[[str], ContextManager[Session]]  # ip -> open session


def banner_text(user_names: Iterable[str]) -> str:
    holders = ', '.join(user_names) or 'nobody'
    return f"""
***************** LAB RESERVATION SYSTEM ******************
This switch is reserved by : {holders}
If you access this switch without reservation, please contact admin

To cleanup the switch:
cp init/vc* working
reload from working no rollback-timeout
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

    def restore_init_and_reload(self) -> None:
        """
        Cleanup: replaces working/ with init/, checks it can boot, refreshes certified/ as a
        backup, and reloads from working/. Nothing is reloaded if an essential step fails.
        """
        with self._connect(self.ip) as session:
            def step(cmd: str, essential: bool) -> CommandResult:
                result = session.run(cmd)
                if result.status != 0:
                    message = f"'{cmd}' returned {result.status} on {self.ip}: {result.error.strip()}"
                    if essential:
                        raise LabSwitchError(message)
                    logger.warning(message)
                return result

            step('rm -rf working/*', essential=False)
            step('cp -r init/* working/', essential=True)
            contents = step('ls working/', essential=True).output
            missing = [name for name in ESSENTIAL_FILES if name not in contents]
            if missing:
                raise LabSwitchError(f"working/ on {self.ip} lacks {', '.join(missing)}: {contents.strip()}")
            step('rm -rf certified/*', essential=False)
            step('cp -r init/* certified/', essential=False)
            session.run_confirmed('reload from working no rollback-timeout')
            logger.info("Cleanup reload started on %s", self.ip)

    def show(self, cmd: str) -> CommandResult:
        """Runs one of the show commands an Inspection reads, and only those."""
        if cmd not in INSPECTION_COMMANDS:
            raise ValueError(f"{cmd!r} is not a command an Inspection reads")
        with self._connect(self.ip) as session:
            return session.run(cmd)

    def read_for_inspection(self) -> Readings:
        """
        Reads what an Inspection needs, and only reads: the show commands, and the init
        config file. Raises LoginRefused if BLab's credentials are refused.
        """
        with self._connect(self.ip) as session:
            outputs = {cmd: session.run(cmd) for cmd in INSPECTION_COMMANDS}
            try:
                init_config = session.read_file(INIT_CONFIG_PATH)
            except Exception as e:  # SFTP refused, no such file...: the Inspection reports it unreadable
                logger.warning("Cannot read %s on %s: %s", INIT_CONFIG_PATH, self.ip, e)
                init_config = None
            return Readings(outputs, init_config)

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

    def update_accounts(self, create: Dict[str, str], remove: Iterable[str]) -> Dict[str, str]:
        """
        In one session, removes the Switch accounts named in `remove` (one already gone is
        fine) and creates those in `create` (name -> password): local users with full
        privileges (an existing one gets the new password). Returns why each one that failed
        did, by name. Raises LabSwitchError if it can't log in at all.
        """
        failed = {}
        with self._connect(self.ip) as session:
            for name in remove:
                try:
                    check_account_name(name)
                    self._user_command(session, f'no user {name}', already_done='Unknown user')
                    logger.info("Switch account %s removed from %s", name, self.ip)
                except (ValueError, LabSwitchError) as e:
                    failed[name] = str(e)
            for name, password in create.items():
                try:
                    check_account_name(name)
                    if not ACCOUNT_PASSWORD.fullmatch(password):
                        raise ValueError("not a password BLab would generate")
                    self._user_command(session, f'user {name} password "{password}" read-write all',
                                       shown=f'user {name} password ... read-write all')
                    logger.info("Switch account %s created on %s", name, self.ip)
                except (ValueError, LabSwitchError) as e:
                    failed[name] = str(e)
        return failed

    def _user_command(self, session: Session, cmd: str, shown: Optional[str] = None,
                      already_done: Optional[str] = None) -> None:
        """
        Runs a `user` command. AOS answers exit status 0 even when it refuses one, with an
        "ERROR: ..." line in the output, so that line is a failure too, unless it says
        `already_done`. `shown` replaces the command in messages (not to log a password).
        """
        result = session.run(cmd)
        errors = [line.strip() for line in (result.output or '').splitlines() if line.strip().startswith('ERROR')]
        if result.status == 0 and not errors:
            return
        if already_done and errors and all(already_done in e for e in errors):
            return
        why = '; '.join(errors) or result.error.strip() or f'exit status {result.status}'
        raise LabSwitchError(f"'{shown or cmd}' failed on {self.ip}: {why}")


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
    def __init__(self, client: paramiko.SSHClient):
        self.client = client

    def run(self, cmd: str) -> CommandResult:
        _, stdout, stderr = self.client.exec_command(cmd)
        output = stdout.read().decode('utf-8', 'replace')
        error = stderr.read().decode('utf-8', 'replace')
        return CommandResult(stdout.channel.recv_exit_status(), output, error)

    def run_confirmed(self, cmd: str) -> None:
        stdin, _, _ = self.client.exec_command(cmd, get_pty=True)
        time.sleep(1)  # wait for the prompt
        stdin.write('y\n')
        stdin.flush()

    def write_file(self, path: str, text: str) -> None:
        with self.client.open_sftp() as sftp:
            with sftp.file(path, 'w') as file:
                file.write(text)

    def read_file(self, path: str) -> str:
        with self.client.open_sftp() as sftp:
            with sftp.file(path, 'r') as file:
                return file.read().decode('utf-8', 'replace')


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
