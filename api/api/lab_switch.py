"""
A lab Switch, seen as what BLab does to it over SSH: set its banner, Clean it up, and read
what an Inspection needs (see CONTEXT.md).

LabSwitch runs its steps through a session, opened by a connect function picked in one
place (lab_switch()): the real switch over SSH, or the in-memory fake in fake_devices.
"""
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, ContextManager, Dict, Iterable, Iterator, Optional

import paramiko

from .backbone import SWITCH_PASSWORD, SWITCH_USERNAME

logger = logging.getLogger(__name__)

BANNER_PATH = 'switch/pre_banner.txt'
# A working/ directory missing any of these would not boot cleanly, so Cleanup won't reload
ESSENTIAL_FILES = ('.img', 'pkg', 'vcboot.cfg')
# What an Inspection reads: only show commands, and the init config file (read, never written)
INSPECTION_COMMANDS = ('show chassis', 'show interfaces', 'show configuration snapshot')
INIT_CONFIG_PATH = 'init/vcboot.cfg'


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


def ssh_connect(ip: str, username: str = SWITCH_USERNAME, password: str = SWITCH_PASSWORD,
                timeout: float = 5) -> paramiko.SSHClient:
    """The one way into a switch over SSH. The caller closes the client."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(ip, port=22, username=username, password=password, timeout=timeout)
    except Exception:
        client.close()
        raise
    return client


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


def ssh(username: str = SWITCH_USERNAME, password: str = SWITCH_PASSWORD) -> Connect:
    """Connects to real switches with these credentials. Any error becomes LabSwitchError."""
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
