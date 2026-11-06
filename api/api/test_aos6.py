"""
AOS 6 Switches (#108, docs/adr/0012): an OS6450 onboarded, Inspected, Cleaned up, given Switch accounts,
Swept, Snapshotted and Restored as any lab Switch, but for AOS releases. The outputs are 10.69.144.148's
(OS6450-48, 6.7.2.28.R06), read on 2026-11-04.
"""
import stat
from datetime import datetime, timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings

from . import clean_state, contribute_switch, fake_devices, lab_switch as lab_switch_module, replacement
from .clean_state import (AOS6_INIT_CONFIG_PATH, Management, front_panel_port, golden_init_config, golden_vc_setup,
                          init_config_path, management_of, runs_aos6)
from .contribute_switch import Refused
from .contribute import free_switch_ports
from .inspection import config_differences, inspect, parse_chassis_ids, parse_link_states, unwanted_cables
from .lab_switch import (AOS6_CERTIFY_COMMAND, AOS6_WORKING_CONFIG_PATH, LabSwitch, LabSwitchError, Login, ShellSession,
                         SwitchConfig, account_command, lab_switch, parse_aos6_clock)
from .models import Onboarding, Port, Reservation, Switch, SwitchContribution, SwitchEvent
from .onboarding import Onboarder, aos6_management_port, parse_neighbours, unsupported_release
from .port_capability import parse_capabilities
from .switch_worker import CERTIFIED, RELOAD_GRACE, SwitchWorker, request_cleanup

AOS6_IP = '10.0.0.3'
BB = '10.0.0.100'
CHASSIS = """
Chassis 2
  Model Name:                    OS6450-48,
  Description:                   Chassis,
  Part Number:                   903107-90,
  Hardware Revision:             04,
  Serial Number:                 N128005P,
"""
INTERFACES = """ Slot/Port  2/47 :
  Operational Status     : down, "",
  Type                   : Ethernet,
 Slot/Port  2/48 :
  Operational Status     : up, "",
  Last Time Link Changed : MON JAN 01 22:58:16 ,
"""
LLDP = """Remote LLDP Agents on Local Slot/Port 2/48:

    Chassis 94:24:e1:8f:d8:3f, Port 1047:
      Remote ID                   = 1,
      Port Description            = Alcatel-Lucent Enterprise OS2360-48 GNI 1/1/47,
      System Name                 = Blab-OoB-Backbone-LTM116,
"""
CAPABILITY = """   Slot/Port   AutoNeg     Flow   Crossover      Speed    Duplex
-----------+---------+--------+-----------+----------+----------
  2/1  CAP     EN/DIS   EN/DIS  MDI/X/Auto  10/100/1G  Full/Half
  2/1  DEF         EN       EN        Auto       Auto       Auto
  2/49 CAP         EN      DIS           -         1G       Full
"""
# Its running config as it came, its address on VLAN 1443, untagged on 2/48
RUNNING = """vlan 1 disable name "VLAN 1"
vlan 1443 enable name "VLAN 1443"
vlan 1443 port default 2/48
ip interface "management-temp-access" address 10.0.0.3 mask 255.255.255.248 vlan 1443 ifindex 1
"""
LOG = """Time Stamp               Application    Level   Log Message
------------------------+--------------+-------+--------------------------------
FRI JAN 19 22:39:09 2001            AAA    info AAA login,old,SSH,,session=001,10.1.1.1
TUE JAN 02 00:28:33 2001            AAA    info AAA logout,admin,SSH,cause=0000,session=019,10.69.6.148
TUE JAN 02 00:28:45 2001            AAA    info AAA login,blabdq,SSH,,session=020,10.69.6.148
TUE JAN 02 00:30:19 2001            AAA    info AAA login,admin,TELNET,,session=022,10.69.6.149
TUE JAN 02 00:30:08 2001            AAA    info AAA failure,blabsq,SSH,cause=0005,session=021,10.69.6.148
"""


class ParsingTest(SimpleTestCase):
    def test_show_chassis_names_the_stack_slot(self):
        self.assertEqual(parse_chassis_ids(CHASSIS), [2])
        self.assertEqual(parse_chassis_ids(CHASSIS + CHASSIS.replace('Chassis 2', 'Chassis 3')), [2, 3])

    def test_show_interfaces_with_slot_port(self):
        self.assertEqual(parse_link_states(INTERFACES), {'2/47': 'down', '2/48': 'up'})

    def test_lldp_neighbours_with_slot_port(self):
        self.assertEqual(parse_neighbours(LLDP), [('2/48', 'Blab-OoB-Backbone-LTM116', '1/1/47')])

    def test_port_capabilities_with_slot_port(self):
        found = parse_capabilities(CAPABILITY, aos6=True)
        self.assertEqual(sorted(found), ['2/1', '2/49'])
        self.assertEqual(found['2/1'].speeds, {10, 100, 1000})
        self.assertEqual(parse_capabilities(CAPABILITY), {}, 'AOS 8 names no front port slot/port (#113)')
        self.assertEqual(parse_capabilities(CAPABILITY.replace('  2/', '1/1/'), aos6=True), {})

    def test_aos6_is_a_supported_release(self):
        self.assertIsNone(unsupported_release(fake_devices.AOS6_SYSTEM))

    def test_the_management_port_from_the_running_config(self):
        self.assertEqual(aos6_management_port(RUNNING, '10.0.0.3'), '2/48')
        self.assertIsNone(aos6_management_port(RUNNING, '10.0.0.4'), 'no interface with that address')
        self.assertEqual(aos6_management_port('ip interface "EMP" address 10.0.0.3 mask 255.255.255.0\n', '10.0.0.3'),
                         '', 'on no VLAN: an EMP')
        self.assertIsNone(aos6_management_port(RUNNING.replace('vlan 1443 port default 2/48', ''), '10.0.0.3'))

    def test_the_clock_in_show_system(self):
        self.assertEqual(parse_aos6_clock(fake_devices.AOS6_SYSTEM), datetime(2001, 1, 2, 0, 37, 51))
        self.assertIsNone(parse_aos6_clock('System:\n  Up Time: 1 days,\n'))

    def test_the_config_as_aos6_prints_it_back_doesnt_differ(self):
        init = golden_init_config('OS6450-48', management_of('10.0.0.3', '2/48'))
        running = (init.replace('ip interface "management" address 10.0.0.3 mask 255.255.255.248 vlan 1443',
                                'ip interface "management" address 10.0.0.3 mask 255.255.255.248 vlan 1443 ifindex 1')
                   .replace('lldp chassis tlv dot3 mac-phy enable', 'lldp chassis tlv dot3  mac-phy enable')
                   + 'ip service all\nbridge mode 1x1 \nvlan 1 enable name "VLAN 1"\n! IPMS :\n')
        self.assertEqual(config_differences(running, init), ([], []))


class FrontPortTest(TestCase):
    """A front port is slot/port on AOS 6 only, chassis/slot/port on AOS 8 (#113)."""

    def test_by_the_model(self):
        aos6, aos8 = front_panel_port('OS6450-48'), front_panel_port('OS6860E-24')
        self.assertTrue(aos6.fullmatch('2/48'))
        self.assertFalse(aos6.fullmatch('1/1/48'))
        self.assertTrue(aos8.fullmatch('1/1/48') and aos8.fullmatch('1/1/49A'))
        self.assertFalse(aos8.fullmatch('2/48'))
        self.assertIs(front_panel_port(None), aos8, 'without a model: AOS 8, as every Switch but the OS6450s')

    def test_inspection_and_contribution_judge_ports_by_the_switch_model(self):
        aos8 = Switch.objects.create(mngt_IP='10.0.0.9', model='OS6860E-24', console='TODO')
        aos6 = Switch.objects.create(mngt_IP=AOS6_IP, model='OS6450-48', console='TODO')
        states = {'1/1/3': 'up', '2/3': 'up', '1/1/4': 'down', '2/4': 'down'}
        self.assertEqual(unwanted_cables(aos8, states), (['1/1/3'], ['2/3']))
        self.assertEqual(unwanted_cables(aos6, states), (['2/3'], ['1/1/3']))
        self.assertEqual(free_switch_ports(aos8, states), ['1/1/4'])
        self.assertEqual(free_switch_ports(aos6, states), ['2/4'])

    def test_onboarding_records_only_the_front_ports_of_the_switch_aos(self):
        aos8 = Switch.objects.create(mngt_IP='10.0.0.9', model='OS6860E-24', console='TODO')
        device = mock.Mock()
        device.lldp_neighbours.return_value = LLDP + LLDP.replace('Slot/Port 2/48', 'Port 1/1/3').replace('1/1/47', '1/1/46')
        with mock.patch('api.onboarding.lab_switch', return_value=device):
            Onboarder(say=lambda line: None).record_ports('10.0.0.9', {'Blab-OoB-Backbone-LTM116': BB},
                                                          {BB: ['1/1/46', '1/1/47']})
        self.assertEqual(list(Port.objects.filter(switch=aos8).values_list('port_switch', flat=True)), ['1/1/3'])


class GoldenInitTest(SimpleTestCase):
    def test_an_aos6_model(self):
        for model in ('OS6450-48', 'OS6450-P24', 'os6350-24', 'OS6850E-24X', 'OS6855-14'):
            self.assertTrue(runs_aos6(model), model)
        for model in ('OS6860E-24', 'OS6900-X48C6', 'OS6465-P12', 'OS6865-P16X', '', None):
            self.assertFalse(runs_aos6(model), model)
        self.assertEqual(init_config_path('OS6450-48'), 'init/boot.cfg')
        self.assertEqual(init_config_path('OS6860E-24'), 'init/vcboot.cfg')

    def test_the_golden_init_of_aos6_carries_its_management(self):
        config = golden_init_config('OS6450-48', Management('10.69.144.148', '10.69.144.129', 26, '2/48'))
        lines = clean_state.config_lines(config)
        for line in ('system name OS6450-48', 'session prompt default "OS6450-48->"', 'aaa accounting session "local"',
                     'aaa authentication ssh "local"', 'vlan 1443 enable name "BLab management"',
                     'vlan 1443 port default 2/48',
                     'ip interface "management" address 10.69.144.148 mask 255.255.255.192 vlan 1443',
                     'ip static-route 10.69.0.0/16 gateway 10.69.144.129 metric 1',
                     'lldp chassis tlv management port-description enable system-name enable system-description enable'):
            self.assertIn(line, lines)
        self.assertFalse(any('nearest-bridge' in line or 'auto-fabric' in line or 'mvrp' in line for line in lines),
                         'none of AOS 8 words')
        self.assertIsNone(golden_vc_setup('OS6450-48', management_of('10.0.0.3', '2/48')), 'no VC setup on AOS 6')

    def test_through_an_emp_it_has_no_management_lines(self):
        self.assertNotIn('vlan 1443', golden_init_config('OS6450-48', Management('10.0.0.3')))

    def test_a_clean_aos6_flash_is_its_baseline(self):
        listings = {'': list(fake_devices.AOS6_FLASH) + ['Crash.pmd', 'installed', 'random-seed', 'l1stack_split_1.ktrace',
                                                         'libcurl_log_backup', 'cloud_connection.txt.1.err', 'lic.txt'],
                    '.ssh': [], 'python': [], 'pmd': [], 'init': ['boot.cfg']}
        found = clean_state.find('OS6450-48', listings, {'admin': None, 'default': None},
                                 golden_init_config('OS6450-48'), clean_state.Extras())
        self.assertEqual(clean_state.describe(found), [])
        listings['init'] = []
        self.assertEqual(clean_state.find('OS6450-48', listings, {'admin': None}, None, clean_state.Extras()).init_lacks,
                         ['boot.cfg'])

    def test_account_commands_in_aos6_words(self):
        self.assertEqual(account_command('alice', "it's-0k!x", aos6=True)[0],
                         'user alice password "it\'s-0k!x" read-write all')
        self.assertEqual(account_command('ro', 'Abcd-1234', read_only=True, aos6=True),
                         ('user ro password "Abcd-1234" read-only all', 'user ro password ... read-only all'))
        self.assertIn(' su enable', account_command('alice', 'Abcd-1234')[0], 'AOS 8 as it was')


class Channel:
    """An interactive shell as paramiko gives it, AOS 6's CLI behind it: answers each command from a script."""

    def __init__(self, answers, prompt='-> ', banner='Welcome to the Alcatel-Lucent Enterprise OmniSwitch 6450\r\n'):
        self.answers, self.prompt = answers, prompt
        self.pending = (banner + '\r\n' + prompt).encode()
        self.sent, self.closed = [], False

    def recv_ready(self):
        return bool(self.pending)

    def recv(self, n):
        data, self.pending = self.pending[:n], self.pending[n:]
        return data

    def send(self, text):
        self.sent.append(text)
        if text == 'y\n':
            self.closed = True  # the reload drops the session
            return
        command = text.rstrip('\n')
        answer = self.answers.get(command, '')
        if answer is None:
            return  # never answers
        self.pending += (f'{command}\r\n{answer}' + ('' if answer.endswith('(Y/N) : ') else self.prompt)).encode()


class Client:
    def __init__(self, channel):
        self.channel = channel
        self.transport = mock.Mock(remote_version='SSH-2.0-OpenSSH_5.0')
        self.transport.is_active.return_value = True
        self.closed = False

    def invoke_shell(self, width, height):
        return self.channel

    def get_transport(self):
        return self.transport

    def close(self):
        self.closed = True


class ShellSessionTest(SimpleTestCase):
    def session(self, answers, **kwargs):
        sftp = mock.Mock()
        files = mock.Mock()
        files.open_sftp.return_value = sftp
        session = ShellSession(Client(Channel(answers, **kwargs)), lambda: files)
        return session, sftp

    def test_it_learns_the_prompt_and_strips_the_echo(self):
        session, _ = self.session({'show microcode': 'KFbase.img        6.7.2.28.R06    18035036 Base\n'},
                                  prompt='OS6450-48-> ')
        self.assertEqual(session.prompt, 'OS6450-48-> ')
        result = session.run('show microcode')
        self.assertEqual((result.status, result.output), (0, 'KFbase.img        6.7.2.28.R06    18035036 Base\n'))

    def test_an_error_line_is_a_failure(self):
        session, _ = self.session({'ls -a /flash': '        ^\r\nERROR: Invalid entry: "/flash"\r\n'})
        result = session.run('ls -a /flash')
        self.assertEqual((result.status, result.error), (1, 'ERROR: Invalid entry: "/flash"'))

    def test_a_confirmed_reload_ends_the_session(self):
        session, _ = self.session({'reload working no rollback-timeout': 'Confirm Activate (Y/N) : '})
        session.run_confirmed('reload working no rollback-timeout')
        self.assertEqual(session.client.channel.sent[-1], 'y\n')
        self.assertFalse(session.usable, 'never used again')

    def test_giving_slot_1_ends_the_session_too(self):
        command = 'stack set slot 2 saved-slot 1 reload'
        session, _ = self.session({command: 'Confirm set slot operation with reload (Y/N)?'})
        session.run_confirmed(command)
        self.assertEqual(session.client.channel.sent[-1], 'y\n')
        self.assertFalse(session.usable, 'it reloads')

    def test_a_refused_confirmation_raises(self):
        session, _ = self.session({'reload working rollback-timeout 20':
                                   'ERROR: Activate Timeout value (min) must be: 0 <= value <= 15\r\n'})
        with mock.patch.object(lab_switch_module, 'CONFIRM_PROMPT_WAIT', 1), \
                self.assertRaisesRegex(LabSwitchError, 'Activate Timeout value'):
            session.run_confirmed('reload working rollback-timeout 20')

    def test_a_command_never_answered_times_out_and_spoils_the_session(self):
        session, _ = self.session({'show interfaces': None})
        with mock.patch.object(lab_switch_module, 'COMMAND_TIMEOUT', 1), \
                self.assertRaisesRegex(LabSwitchError, "'show interfaces' timed out"):
            session.run('show interfaces')
        self.assertFalse(session.usable)

    def test_files_go_over_sftp_on_a_second_connection(self):
        session, sftp = self.session({})
        sftp.listdir_attr.return_value = [mock.Mock(filename='boot.cfg', st_mode=stat.S_IFREG, st_size=752),
                                          mock.Mock(filename='sub', st_mode=stat.S_IFDIR, st_size=2048)]
        self.assertEqual(session.list_dir('init'), {'boot.cfg': 752, 'sub': None})
        sftp.listdir_attr.assert_called_with('init')
        with self.assertRaisesRegex(LabSwitchError, "doesn't copy AOS images"):
            session.put_file('/tmp/Yos.img', 'working/Yos.img')


class RateTest(SimpleTestCase):
    def test_at_most_two_new_sessions_a_minute(self):
        now = [1000.0]
        slept = []

        def sleep(seconds):
            slept.append(round(seconds))
            now[0] += seconds
        with mock.patch.object(lab_switch_module.time, 'monotonic', lambda: now[0]), \
                mock.patch.object(lab_switch_module.time, 'sleep', sleep), \
                mock.patch.dict(lab_switch_module._aos6_connections, clear=True):
            for _ in range(3):
                lab_switch_module._wait_for_aos6_slot('10.9.9.9')
        self.assertEqual(slept, [61], 'the third waits for the first to be a minute old')

    def test_a_refusal_before_the_banner_is_tried_again(self):
        good = mock.Mock()
        good.get_transport.return_value = mock.Mock(remote_version='SSH-2.0-OpenSSH_5.0')
        refusing = mock.Mock()
        refusing.connect.side_effect = lab_switch_module.paramiko.SSHException('Error reading SSH protocol banner')
        with mock.patch.object(lab_switch_module.paramiko, 'SSHClient', side_effect=[refusing, good]), \
                mock.patch.object(lab_switch_module, '_wait_for_aos6_slot'), \
                mock.patch.object(lab_switch_module.time, 'sleep') as sleep, \
                mock.patch.object(lab_switch_module, '_aos6_ips', {'10.9.9.9'}):
            self.assertIs(lab_switch_module._connect_once('10.9.9.9', 'admin', 'pw', 5), good)
        sleep.assert_called_once_with(lab_switch_module.AOS6_REFUSED_WAIT)


class ContributedModelTest(TestCase):
    """A Contribution names its model: an AOS 6 one is known before BLab connects (#129)."""
    IP = '10.9.9.7'

    def setUp(self):
        patcher = mock.patch.object(lab_switch_module, '_aos6_ips', set())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = User.objects.create_user('contributor')

    def contribute(self, model, state=SwitchContribution.ONBOARDING):
        SwitchContribution.objects.create(user=self.user, model=model, mngt_IP=self.IP, state=state)

    def test_an_os6450_under_contribution_is_aos6(self):
        self.contribute('OS6450-P48')
        lab_switch_module._learn_aos6_from_record(self.IP)
        self.assertIn(self.IP, lab_switch_module._aos6_ips)

    def test_an_aos8_model_or_a_finished_contribution_says_nothing(self):
        self.contribute('OS6860E-24')
        lab_switch_module._learn_aos6_from_record(self.IP)
        SwitchContribution.objects.all().delete()
        self.contribute('OS6450-P48', SwitchContribution.DONE)
        lab_switch_module._learn_aos6_from_record(self.IP)
        self.assertNotIn(self.IP, lab_switch_module._aos6_ips)


@override_settings(BLAB_DEVICES='fake', BLAB_MANAGEMENT_NETWORKS='10.0.0.0/29 via 10.0.0.1')
class Aos6SwitchTestCase(TestCase):
    def setUp(self):
        self.fake = fake_devices.lab_switches
        self.fake.reset()
        self.bb = fake_devices.backbone
        self.bb.reset()
        self.fake.runs_aos6(AOS6_IP)

    def record(self, **fields):
        return Switch.objects.create(mngt_IP=AOS6_IP, model='OS6450-48', console='TODO', part_number='903107-90',
                                     hardware_revision='04', serial_number='N1', management_port='2/8', **fields)


class Clock:
    def __init__(self):
        from django.utils import timezone
        self.at = timezone.now()

    def __call__(self):
        return self.at

    def sleep(self, seconds):
        self.at += timedelta(seconds=seconds or 1)


class OnboardingTest(Aos6SwitchTestCase):
    def test_it_is_onboarded_into_service_with_its_management_in_its_golden_init(self):
        self.fake.present.add(AOS6_IP)
        self.fake.factory_admin.add(AOS6_IP)
        self.fake.running_config[AOS6_IP] = RUNNING.replace('2/48', '2/8')
        self.bb.cli(BB, 'interfaces 1/2/2 admin-state disable')
        Port.objects.create(switch=Switch.objects.create(mngt_IP='10.0.0.2', model='OS6860', console='TODO'),
                            port_switch='1/1/1', backbone=BB, port_backbone='1/2/1')
        self.fake.plug(AOS6_IP, '2/5', BB, '1/2/2')
        clock = Clock()
        onboarder = Onboarder(now=clock, sleep=clock.sleep, say=lambda line: None)
        [outcome] = onboarder.run([], [AOS6_IP], apply=True)
        self.assertEqual((outcome.step, outcome.detail), (Onboarding.DONE, 'onboarded: clean, in service'))
        switch = Switch.objects.get(mngt_IP=AOS6_IP)
        # It came in stack slot 2, as the lab's first OS6450: given slot 1 first (#113)
        self.assertEqual((switch.model, switch.management_port, switch.out_of_service), ('OS6450-48', '1/8', False))
        self.assertEqual(self.fake.slot_of(AOS6_IP), 1)
        self.assertNotIn(AOS6_IP, self.fake.factory_admin, "admin got the lab's secret password")
        golden = golden_init_config('OS6450-48', management_of(AOS6_IP, '1/8'))
        self.assertEqual(self.fake.written[AOS6_IP][AOS6_INIT_CONFIG_PATH], golden)
        self.assertEqual(self.fake.running_config[AOS6_IP], golden, 'it came up on its Golden init')
        self.assertIn('vlan 1443 port default 1/8', golden)
        self.assertEqual(list(Port.objects.filter(switch=switch).values_list('port_switch', 'port_backbone')),
                         [('1/5', '1/2/2')])
        commands = [cmd for ip, cmd in self.fake.commands if ip == AOS6_IP]
        self.assertIn('mkdir /flash/init', commands)
        self.assertLess(commands.index('stack set slot 2 saved-slot 1 reload'), commands.index('mkdir /flash/init'),
                        'slot 1 before its Golden init')
        self.assertEqual(self.fake.reloads, [AOS6_IP, AOS6_IP], 'into slot 1, then into its Golden init')
        self.assertIn('cp /flash/init/boot.cfg /flash/working/boot.cfg', commands)
        self.assertIn(AOS6_CERTIFY_COMMAND, commands, 'certified/ once found clean')
        self.assertFalse(any(cmd.startswith(('ls ', 'rm -rf', 'cp -r')) for cmd in commands), "none of AOS 8's")

    def test_one_in_slot_1_already_isnt_renumbered(self):
        self.fake.present.add(AOS6_IP)
        self.fake.slots[AOS6_IP] = 1
        self.fake.running_config[AOS6_IP] = RUNNING.replace('2/48', '1/8')
        clock = Clock()
        [outcome] = Onboarder(now=clock, sleep=clock.sleep, say=lambda line: None).run([], [AOS6_IP], apply=True)
        self.assertEqual(outcome.step, Onboarding.DONE)
        self.assertEqual(Switch.objects.get(mngt_IP=AOS6_IP).management_port, '1/8')
        self.assertFalse(any(cmd.startswith('stack') for _, cmd in self.fake.commands))
        self.assertEqual(self.fake.reloads, [AOS6_IP], 'into its Golden init only')

    def test_the_switch_worker_goes_on_once_it_is_back_in_slot_1(self):
        self.fake.present.add(AOS6_IP)
        self.fake.running_config[AOS6_IP] = RUNNING.replace('2/48', '2/8')
        self.fake.slow_to_reload.add(AOS6_IP)
        clock = Clock()
        onboarder = Onboarder(now=clock, sleep=clock.sleep, say=lambda line: None)
        onboarder.advance([AOS6_IP])
        row = Onboarding.objects.get(mngt_IP=AOS6_IP)
        self.assertEqual(row.step, Onboarding.RECORDED)
        self.assertIn('slot 1', row.detail)
        self.assertEqual(Switch.objects.get(mngt_IP=AOS6_IP).management_port, '1/8')
        clock.sleep(60)
        onboarder.advance([AOS6_IP])  # still up on its old uptime: not back yet
        self.assertEqual(Onboarding.objects.get(mngt_IP=AOS6_IP).step, Onboarding.RECORDED)
        self.assertEqual(self.fake.reloads, [AOS6_IP])
        self.assertNotIn(AOS6_INIT_CONFIG_PATH, self.fake.written.get(AOS6_IP, {}))
        self.fake.slow_to_reload.discard(AOS6_IP)
        self.fake.come_up(AOS6_IP)
        clock.sleep(60)
        onboarder.advance([AOS6_IP])  # back in slot 1: on to its Golden init
        self.assertEqual(Onboarding.objects.get(mngt_IP=AOS6_IP).step, Onboarding.RELOADING)
        self.assertEqual(self.fake.slot_of(AOS6_IP), 1)
        self.assertIn('vlan 1443 port default 1/8', self.fake.written[AOS6_IP][AOS6_INIT_CONFIG_PATH])

    def test_slot_1_renames_its_management_port_in_boot_cfg_before_the_reload(self):
        self.fake.running_config[AOS6_IP] = RUNNING
        lab_switch(AOS6_IP).give_slot_one(2, '2/48')
        self.assertIn('vlan 1443 port default 1/48\n', self.fake.written[AOS6_IP][AOS6_WORKING_CONFIG_PATH])
        self.assertEqual(self.fake.running_config[AOS6_IP], RUNNING.replace('2/48', '1/48'), 'it came up on it')
        self.assertEqual((self.fake.slot_of(AOS6_IP), self.fake.reloads), (1, [AOS6_IP]))

    def test_slot_1_isnt_given_when_boot_cfg_doesnt_name_its_management_port(self):
        self.fake.running_config[AOS6_IP] = RUNNING.replace('2/48', '2/47')
        with self.assertRaisesRegex(LabSwitchError, "doesn't name its Management port 2/48"):
            lab_switch(AOS6_IP).give_slot_one(2, '2/48')
        self.assertEqual((self.fake.slot_of(AOS6_IP), self.fake.reloads), (2, []))

    def test_one_whose_management_cant_be_found_isnt_written_to(self):
        self.fake.present.add(AOS6_IP)
        self.fake.running_config[AOS6_IP] = 'vlan 1 enable\n'
        clock = Clock()
        onboarder = Onboarder(now=clock, sleep=clock.sleep, say=lambda line: None)
        onboarder.run([], [AOS6_IP], apply=True)
        self.assertIn("doesn't say how it reaches the Management network", onboarder.failed[AOS6_IP])
        self.assertFalse(Switch.objects.filter(mngt_IP=AOS6_IP).exists())
        self.assertEqual((self.fake.written.get(AOS6_IP), self.fake.reloads), (None, []))


class InspectionTest(Aos6SwitchTestCase):
    def test_a_clean_aos6_switch(self):
        switch = self.record()
        self.fake.cable(AOS6_IP, '2/8')  # its Management port
        self.fake.running_config[AOS6_IP] = golden_init_config('OS6450-48', switch.management())
        result = inspect(switch)
        self.assertEqual((result.reasons, result.warnings), ([], []))
        self.assertEqual(result.aos_release, fake_devices.AOS6_RELEASE)

    @override_settings(BLAB_CLEAN_STATE_MODE='enforce')
    def test_what_strays_from_its_clean_state(self):
        switch = self.record()
        self.fake.cable(AOS6_IP, '2/3')
        self.fake.users[AOS6_IP] = {'someone': 'Abcd-1234'}
        self.fake.files(AOS6_IP)[''].add('notes.txt')
        self.fake.written[AOS6_IP] = {AOS6_INIT_CONFIG_PATH: 'system name "mine"\n'}
        reasons = inspect(switch).reasons
        self.assertIn('Unwanted cable: 2/3', reasons)
        self.assertIn('Unwanted file: notes.txt', reasons)
        self.assertIn("local users BLab didn't create: someone", reasons)
        self.assertTrue(any(reason.startswith('init differs from its Golden init') for reason in reasons))


@override_settings(BLAB_CLEAN_STATE_MODE='enforce')
class CleanupTest(Aos6SwitchTestCase):
    def test_a_cleanup_brings_back_its_golden_init_and_certifies(self):
        switch = self.record()
        self.fake.cable(AOS6_IP, '2/8')
        alice = User.objects.create_user('alice', email='alice@example.com')
        Reservation.objects.create(switch=switch, user=alice)
        self.fake.users[AOS6_IP] = {'leftover': 'Abcd-1234'}
        self.fake.files(AOS6_IP)[''].add('notes.txt')
        self.fake.written[AOS6_IP] = {AOS6_INIT_CONFIG_PATH: 'system name "mine"\n',
                                      AOS6_WORKING_CONFIG_PATH: 'vlan 10 enable\n'}
        self.fake.sizes[AOS6_IP] = {'working/KFos.img': 12}  # a member's image
        Reservation.objects.filter(switch=switch).delete()
        clock = Clock()
        worker = SwitchWorker(now=clock)
        request_cleanup(switch, alice)
        worker.work()
        clock.at += RELOAD_GRACE
        worker.work()
        cleanup = switch.events.filter(kind=SwitchEvent.CLEANUP).first()
        self.assertTrue(cleanup.ok, cleanup.reasons)
        self.assertIn(CERTIFIED, cleanup.reasons)
        golden = golden_init_config('OS6450-48', switch.management())
        self.assertEqual(self.fake.written[AOS6_IP][AOS6_INIT_CONFIG_PATH], golden)
        self.assertEqual(self.fake.running_config[AOS6_IP], golden)
        self.assertNotIn('leftover', self.fake.users[AOS6_IP])
        self.assertNotIn('notes.txt', self.fake.files(AOS6_IP)[''])
        self.assertTrue(any('working/KFos.img put back from certified/' in w for w in cleanup.warnings), cleanup.warnings)
        commands = [cmd for ip, cmd in self.fake.commands if ip == AOS6_IP]
        self.assertIn('rm /flash/notes.txt', commands)
        self.assertIn('cp /flash/certified/KFos.img /flash/working/KFos.img', commands)
        self.assertIn('reload working no rollback-timeout', commands, "AOS 6's words, not AOS 8's reload from working")
        self.assertEqual(self.fake.reloads, [AOS6_IP])
        self.assertTrue(switch.events.filter(kind=SwitchEvent.INSPECTION, ok=True).exists())

    def test_without_its_golden_init_in_init_nothing_reloads(self):
        self.record()
        self.fake.files(AOS6_IP)['init'] = set()
        with self.assertRaisesRegex(LabSwitchError, 'init/ on 10.0.0.3 lacks boot.cfg'):
            lab_switch(AOS6_IP).restore_init_and_reload()
        self.assertEqual(self.fake.reloads, [])


class AccountsAndSweepTest(Aos6SwitchTestCase):
    def test_switch_accounts_in_aos6_words(self):
        device = lab_switch(AOS6_IP)
        update = device.update_accounts({'alice': 'Abcd-1234', 'ro': 'Efgh-5678'}, [], read_only=['ro'])
        self.assertEqual((update.failed, update.on_slave), ({}, []))
        self.assertEqual(self.fake.users[AOS6_IP], {'alice': 'Abcd-1234', 'ro': 'Efgh-5678'})
        commands = [cmd for ip, cmd in self.fake.commands if ip == AOS6_IP]
        self.assertIn('user alice password "Abcd-1234" read-write all', commands)
        self.assertIn('user ro password "Efgh-5678" read-only all', commands)
        self.assertNotIn('write memory', commands, 'AOS 6 keeps its users at once')
        self.assertEqual(device.update_accounts({}, ['alice', 'nobody']).failed, {})
        self.assertNotIn('alice', self.fake.users[AOS6_IP])

    def test_logins_since_it_booted_by_its_own_clock(self):
        self.fake.outputs[AOS6_IP] = {'show log swlog appid aaa': LOG}
        self.fake.uptime[AOS6_IP] = timedelta(minutes=10)  # booted at 00:27:51 by its clock
        self.assertEqual(lab_switch(AOS6_IP).logins_since_boot(),
                         [Login('blabdq', '10.69.6.148', 'SSH'), Login('admin', '10.69.6.149', 'TELNET')])

    def test_the_fake_log_is_read_back(self):
        self.fake.log_in(AOS6_IP, 'bob', '10.1.2.3')
        logins = lab_switch(AOS6_IP).logins_since_boot()
        self.assertEqual(logins[-2:], [Login('bob', '10.1.2.3', 'SSH'), Login('admin', '10.69.144.180', 'SSH')],
                         "then BLab's own, reading it")


class SnapshotTest(Aos6SwitchTestCase):
    def test_a_snapshot_holds_boot_cfg_and_a_restore_loads_it(self):
        self.record()
        self.fake.written[AOS6_IP] = {AOS6_WORKING_CONFIG_PATH: 'vlan 10 enable name "mine"\n'}
        device = lab_switch(AOS6_IP)
        config = device.read_config()
        self.assertEqual(config, SwitchConfig('vlan 10 enable name "mine"\n', None, fake_devices.AOS6_RELEASE))
        self.fake.written[AOS6_IP][AOS6_WORKING_CONFIG_PATH] = 'changed since\n'
        device.load_config_and_reload(config)
        self.assertEqual(self.fake.running_config[AOS6_IP], 'vlan 10 enable name "mine"\n')
        self.assertEqual(self.fake.reloads, [AOS6_IP])

    def test_an_aos8_snapshot_is_refused(self):
        with self.assertRaisesRegex(LabSwitchError, 'no VC setup'):
            lab_switch(AOS6_IP).load_config_and_reload(SwitchConfig('x', 'virtual-chassis chassis-id 1\n', '8.10.86.R04'))
        self.assertEqual(self.fake.reloads, [])

    def test_no_release_is_installed_on_aos6(self):
        image = lab_switch_module.ImageFile('Yos.img', '/nowhere', 1, 'md5')
        with self.assertRaisesRegex(LabSwitchError, "doesn't install AOS releases on AOS 6"):
            lab_switch(AOS6_IP).install_images([image], ['working'])

    def test_no_replacement_across_or_within_aos6(self):
        entry = mock.Mock(model='OS6450-48')
        aos8 = Switch.objects.create(mngt_IP='10.0.0.5', model='OS6860', console='TODO')
        aos6 = self.record()
        for switch in (aos8, aos6):
            Port.objects.create(switch=switch, port_switch='1/1/1', backbone=BB, port_backbone=f'1/2/{switch.id}')
        self.assertEqual(replacement.candidates(entry, 1, [aos8, aos6]), [])
        self.assertEqual([c['id'] for c in replacement.candidates(mock.Mock(model='OS6860'), 1, [aos8, aos6])], [aos8.id])


@override_settings(BLAB_DEVICES='fake', BLAB_MANAGEMENT_NETWORKS='10.0.0.0/29 via 10.0.0.1')
class ContributionTest(TestCase):
    def setUp(self):
        fake_devices.lab_switches.reset()
        self.alice = User.objects.create_user('alice', email='alice@example.com')

    def test_an_aos6_switch_without_an_emp_gets_aos6_commands(self):
        contribution = contribute_switch.start(self.alice, 'OS6450-48', False, Clock()(), management_port='2/48')
        steps = contribute_switch.workflow(contribution)
        commands = steps[0]['blocks'][0]['commands']
        self.assertIn('vlan 1443 port default 2/48', commands)
        self.assertIn(f'ip interface "management" address {contribution.mngt_IP} mask 255.255.255.248 vlan 1443', commands)
        self.assertIn('ip static-route 10.0.0.0/8 gateway 10.0.0.1 metric 1', commands)
        self.assertEqual(commands[-1], 'write memory')
        self.assertFalse(any('virtual-chassis' in c or 'members port' in c for c in commands), "none of AOS 8's")
        self.assertIn('init/boot.cfg', steps[0]['note'])

    def test_an_aos6_switch_with_an_emp_or_an_aos8_port_is_refused(self):
        with self.assertRaisesRegex(Refused, 'without an EMP only'):
            contribute_switch.start(self.alice, 'OS6450-48', True, Clock()())
        with self.assertRaisesRegex(Refused, 'slot/port, e.g. 2/48'):
            contribute_switch.start(self.alice, 'OS6450-48', False, Clock()(), management_port='1/1/48')
        with self.assertRaisesRegex(Refused, 'e.g. 1/1/48'):
            contribute_switch.start(self.alice, 'OS6860E-24', False, Clock()(), management_port='2/48')
