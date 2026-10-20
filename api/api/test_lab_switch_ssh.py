"""The SSH layer under LabSwitch: a Switch that stops answering mid-session fails the step, bounded in time (#64)."""
import socket
import threading
from unittest.mock import patch

import paramiko
from django.test import SimpleTestCase

from . import lab_switch as lab_switch_module
from .lab_switch import LabSwitch, LabSwitchError, Repair, ssh


class Stream:
    """stdin, stdout and stderr of one command, and its channel. A hanging one answers once the connection is closed."""

    def __init__(self, client, output=b'', hangs=False, read_error=None):
        self.client = client
        self.output = output
        self.hangs = hangs
        self.read_error = read_error
        self.channel = self

    def read(self):
        if self.read_error:
            raise self.read_error
        if self.hangs:
            self.client.closed.wait()
            return b''
        return self.output

    def recv_exit_status(self):
        return -1 if self.client.closed.is_set() else 0


class Transport:
    def __init__(self, client):
        self.client = client

    def close(self):
        self.client.closed.set()


class Client:
    """A paramiko.SSHClient to a Switch that answers `answers` (command -> output) and hangs on anything else."""

    def __init__(self, answers=None, sftp_hangs=False, read_error=None):
        self.answers = answers or {}
        self.sftp_hangs = sftp_hangs
        self.read_error = read_error
        self.closed = threading.Event()
        self.commands = []
        self.timeouts = []  # each command's

    def exec_command(self, cmd, timeout=None, get_pty=False):
        if self.closed.is_set():
            raise paramiko.SSHException('SSH session not active')
        self.commands.append(cmd)
        self.timeouts.append(timeout)
        stream = Stream(self, self.answers.get(cmd, b''), hangs=cmd not in self.answers, read_error=self.read_error)
        return stream, stream, Stream(self)

    def open_sftp(self):
        if self.sftp_hangs:
            self.closed.wait()
        raise paramiko.SSHException('Channel closed.')

    def get_transport(self):
        return Transport(self)

    def close(self):
        self.closed.set()


class SshTimeoutTest(SimpleTestCase):
    def switch(self, client, timeout=0.2):
        for patcher in (patch.object(lab_switch_module, 'ssh_connect', return_value=client),
                        patch.object(lab_switch_module, 'COMMAND_TIMEOUT', timeout),
                        patch.object(lab_switch_module, 'COPY_TIMEOUT', timeout)):
            patcher.start()
            self.addCleanup(patcher.stop)
        return LabSwitch('10.0.0.1', ssh())

    def test_a_command_the_switch_never_finishes_is_a_lab_switch_error(self):
        client = Client()
        with self.assertRaisesMessage(LabSwitchError, "'show chassis' timed out after 0.2 s"):
            self.switch(client).show('show chassis')
        self.assertTrue(client.closed.is_set())

    def test_a_read_that_times_out_is_a_lab_switch_error(self):
        client = Client(read_error=socket.timeout())
        with self.assertRaisesMessage(LabSwitchError, "'show system' timed out"):
            self.switch(client, timeout=5).uptime()

    def test_a_file_transfer_the_switch_never_finishes_is_a_lab_switch_error(self):
        client = Client(sftp_hangs=True)
        with self.assertRaisesMessage(LabSwitchError, 'writing switch/pre_banner.txt timed out after 0.2 s'):
            self.switch(client).set_banner(['someone'])

    def test_after_a_timeout_the_session_fails_with_it(self):
        # An Inspection reports an unreadable init config and carries on: its next step says why
        answers = {cmd: b'' for cmd in lab_switch_module.INSPECTION_COMMANDS}
        client = Client(answers, sftp_hangs=True)
        with self.assertRaisesMessage(LabSwitchError, 'reading init/vcboot.cfg timed out after 0.2 s'):
            self.switch(client).read_for_inspection()

    def test_a_cleanup_whose_copy_hangs_fails_before_the_reload(self):
        listing = (b'-rw-r--r--    1 admin    user     241594635 Jan  6 05:35 Yos.img\n'
                   b'drwxr-xr-x    5 admin    user          4096 Jan 15 04:43 pkg\n'
                   b'-rw-r--r--    1 admin    user           667 Jan 15 05:28 vcboot.cfg\n')
        client = Client({'ls -l init/': listing, 'ls -l working/': listing, 'ls -l certified/': listing,
                         'rm -rf working/*': b''})
        with self.assertRaisesMessage(LabSwitchError, "'cp -r init/* working/' timed out"):
            self.switch(client).restore_init_and_reload(Repair())
        self.assertNotIn(lab_switch_module.RELOAD_FROM_WORKING, client.commands)

    def test_an_answering_switch_is_not_held_up(self):
        client = Client({'show system': b'  Up Time:      2 days 1 minutes,\n'})
        self.assertEqual(self.switch(client, timeout=5).uptime().days, 2)
        self.assertEqual(client.timeouts, [5])

    def test_copies_get_longer_than_other_commands(self):
        client = Client({'cp -r init/* working/': b'', 'rm -rf working/*': b''})
        session = lab_switch_module.SshSession(client)
        with patch.object(lab_switch_module, 'COMMAND_TIMEOUT', 3), patch.object(lab_switch_module, 'COPY_TIMEOUT', 7):
            session.run('cp -r init/* working/')
            session.run('rm -rf working/*')
        self.assertEqual(client.timeouts, [7, 3])


class KeepaliveTest(SimpleTestCase):
    def test_every_connection_sends_keepalives(self):
        intervals = []

        class KeptAlive:
            def set_keepalive(self, interval):
                intervals.append(interval)

        class SSHClient:
            def set_missing_host_key_policy(self, policy):
                pass

            def connect(self, *args, **kwargs):
                pass

            def get_transport(self):
                return KeptAlive()

        with patch.object(lab_switch_module.paramiko, 'SSHClient', SSHClient):
            lab_switch_module.ssh_connect('10.0.0.1', 'tester', 'x')
        self.assertEqual(intervals, [lab_switch_module.KEEPALIVE_INTERVAL])
