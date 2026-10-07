"""
Contract test: the real SSH LabSwitch adapter against a standalone test switch.

It changes the banner and runs a full Cleanup, so the switch reboots. Use a dedicated
switch that is not in the lab, not on the backbone and not in the inventory.

It never runs with the normal suite (Django only discovers test*.py). Run it by name:

    python manage.py test api.contract_lab_switch

It is skipped unless BLAB_TEST_SWITCH, BLAB_TEST_SWITCH_USER and BLAB_TEST_SWITCH_PASSWORD
are set, in the environment or in a git-ignored .env.test at the repository root (KEY=value
lines). BLAB_TEST_SWITCH_REBOOT_TIMEOUT (seconds, default 900) bounds the wait for the reboot.
"""
import os
import time
import unittest
from pathlib import Path

from django.test import SimpleTestCase

from .lab_switch import BANNER_PATH, LabSwitch, banner_text, ssh, ssh_connect

REPO_ROOT = Path(__file__).resolve().parents[2]
INVENTORY = REPO_ROOT / 'api' / 'switch_ips.txt'
VARIABLES = ('BLAB_TEST_SWITCH', 'BLAB_TEST_SWITCH_USER', 'BLAB_TEST_SWITCH_PASSWORD')
MARKER = 'working/blab_contract_marker.txt'


def load_env_test(path: Path = REPO_ROOT / '.env.test') -> None:
    """Reads KEY=value lines into the environment, without overriding what is already set."""
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_test()
CONFIGURED = all(os.environ.get(name) for name in VARIABLES)


@unittest.skipUnless(CONFIGURED, f"set {', '.join(VARIABLES)} (or .env.test) to run the LabSwitch contract")
class LabSwitchContractTest(SimpleTestCase):
    """Banner, then a full Cleanup, on the real test switch."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ip = os.environ['BLAB_TEST_SWITCH']
        cls.user = os.environ['BLAB_TEST_SWITCH_USER']
        cls.password = os.environ['BLAB_TEST_SWITCH_PASSWORD']
        cls.reboot_timeout = float(os.environ.get('BLAB_TEST_SWITCH_REBOOT_TIMEOUT', 900))
        if INVENTORY.exists() and cls.ip in INVENTORY.read_text().split():
            raise unittest.SkipTest(f"{cls.ip} is in the lab inventory; the contract needs a standalone switch")
        cls.switch = LabSwitch(cls.ip, ssh(cls.user, cls.password))

    def read_file(self, path: str) -> str:
        client = ssh_connect(self.ip, self.user, self.password)
        try:
            with client.open_sftp() as sftp, sftp.file(path, 'r') as file:
                return file.read().decode('utf-8')
        finally:
            client.close()

    def write_file(self, path: str, text: str) -> None:
        with ssh(self.user, self.password)(self.ip) as session:
            session.write_file(path, text)

    def reachable(self) -> bool:
        try:
            ssh_connect(self.ip, self.user, self.password).close()
            return True
        except Exception:
            return False

    def wait_until(self, reachable: bool, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.reachable() == reachable:
                return True
            time.sleep(10)
        return False

    def test_1_set_banner(self):
        names = ['blab-contract', f'run-{int(time.time())}']
        self.switch.set_banner(names)
        self.assertEqual(self.read_file(BANNER_PATH), banner_text(names))

    def test_2_cleanup_restores_init_and_reboots(self):
        # A file only in working/ must be gone once the switch is back from init/
        self.write_file(MARKER, 'left by the BLab contract test\n')

        self.switch.restore_init_and_reload()

        self.assertTrue(self.wait_until(False, 180), "the switch never went down for its reload")
        self.assertTrue(self.wait_until(True, self.reboot_timeout), "the switch did not come back")
        with self.assertRaises(IOError):
            self.read_file(MARKER)
