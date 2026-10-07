"""Inspection (see CONTEXT.md): reading a Switch to decide whether it is clean, against fake devices."""
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import paramiko
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import fake_devices
from .inspection import (config_differences, inspect, inspect_and_record, parse_chassis_ids,
                         parse_link_states)
from .lab_switch import LabSwitchError, LoginRefused, ssh
from .models import PermanentCable, Port, Reservation, Switch, SwitchEvent

README_CHASSIS = """OS6900-V72 show chassis
Local Chassis ID 1 (Master)
  Model Name:                    OS6900-V72,
  Module Type:                   0xa062302,
  Part Number:                   903985-90,
  Serial Number:                 AI46021761,
  MAC Address:                   dc:08:56:10:4d:b9
"""

VC_CHASSIS = README_CHASSIS + """
Remote Chassis ID 2 (Slave)
  Model Name:                    OS6900-V72,
  Serial Number:                 AI46021762,
"""

INTERFACES = """Chassis/Slot/Port 1/1/1    :
 Operational Status     : up,
 Port-Down/Violation Reason: None,
 Type                   : Ethernet,

Chassis/Slot/Port 1/1/2    :
 Operational Status     : down,
 Port-Down/Violation Reason: None,

Chassis/Slot/Port 1/1/49A  :
 Operational Status     : UP,
"""


class ParsingTest(SimpleTestCase):
    """The show outputs an Inspection reads. Anything they can't read is None, never a pass."""

    def test_one_chassis_from_the_readme_sample(self):
        self.assertEqual(parse_chassis_ids(README_CHASSIS), [1])

    def test_a_vc_lists_every_chassis(self):
        self.assertEqual(parse_chassis_ids(VC_CHASSIS), [1, 2])

    def test_chassis_output_without_any_chassis_is_unreadable(self):
        self.assertIsNone(parse_chassis_ids(''))
        self.assertIsNone(parse_chassis_ids('ERROR: Invalid entry: "chassis"'))

    def test_link_states_per_port(self):
        self.assertEqual(parse_link_states(INTERFACES), {'1/1/1': 'up', '1/1/2': 'down', '1/1/49A': 'up'})

    def test_interfaces_output_without_ports_is_unreadable(self):
        self.assertIsNone(parse_link_states(''))
        self.assertIsNone(parse_link_states('ERROR: Invalid entry'))

    def test_a_port_without_its_operational_status_makes_it_unreadable(self):
        self.assertIsNone(parse_link_states(INTERFACES + "\nChassis/Slot/Port 1/1/3 :\n Type : Ethernet,\n"))

    def test_config_differences_ignore_comments_blank_lines_and_indentation(self):
        init = "! Chassis:\nsystem name lab\n\n  vlan 10 admin-state enable\n"
        self.assertEqual(config_differences("! File: x\nsystem name lab\nvlan 10 admin-state enable", init), ([], []))

    def test_config_differences_list_added_and_removed_lines(self):
        init = "system name lab\nvlan 10 admin-state enable\n"
        running = "system name lab\nvlan 20 admin-state enable\n"
        self.assertEqual(config_differences(running, init),
                         (['vlan 20 admin-state enable'], ['vlan 10 admin-state enable']))


@override_settings(BLAB_DEVICES='fake')
class InspectionTest(TestCase):
    IP = '10.0.0.1'

    def setUp(self):
        self.fake = fake_devices.lab_switches
        self.fake.reset()
        self.switch = Switch.objects.create(mngt_IP=self.IP, model='OS6860', console='TODO', part_number='pn',
                                            hardware_revision='A', serial_number='sn1')
        Port.objects.create(switch=self.switch, port_switch='1/1/1', backbone='10.0.0.100', port_backbone='1/2/1')
        self.alice = User.objects.create_user('alice', password='pw')

    def test_a_clean_switch(self):
        result = inspect(self.switch)
        self.assertTrue(result.clean)
        self.assertEqual(result.reasons, [])
        self.assertEqual(result.warnings, [])

    def test_it_only_reads(self):
        self.fake.cable(self.IP, '1/1/5')
        self.fake.chassis[self.IP] = 2
        inspect(self.switch)
        sent = [cmd for ip, cmd in self.fake.commands]
        self.assertTrue(sent)
        for cmd in sent:
            self.assertTrue(cmd.startswith('show ') or cmd.startswith('read '), cmd)
        self.assertEqual(self.fake.written, {})
        self.assertEqual(self.fake.reloads, [])

    def test_a_vc_is_not_clean(self):
        self.fake.chassis[self.IP] = 2
        result = inspect(self.switch)
        self.assertFalse(result.clean)
        self.assertIn('in a VC: chassis 1, 2', result.reasons)

    def test_an_unwanted_cable(self):
        self.fake.cable(self.IP, '1/1/5', '1/1/6')
        result = inspect(self.switch)
        self.assertFalse(result.clean)
        self.assertEqual(result.reasons, ['Unwanted cable: 1/1/5, 1/1/6'])

    def test_ports_paired_with_a_uni_are_not_unwanted(self):
        self.fake.cable(self.IP, '1/1/1')
        self.assertTrue(inspect(self.switch).clean)

    def test_permanently_cabled_ports_are_not_unwanted(self):
        PermanentCable.objects.create(switch=self.switch, port='1/1/5', note='to the traffic generator')
        self.fake.cable(self.IP, '1/1/5')
        self.assertTrue(inspect(self.switch).clean)

    def test_a_reserved_switch_may_have_cables(self):
        Reservation.objects.create(switch=self.switch, user=self.alice)
        self.fake.cable(self.IP, '1/1/5')
        self.assertTrue(inspect(self.switch).clean)

    def test_login_refused(self):
        self.fake.refused.add(self.IP)
        result = inspect(self.switch)
        self.assertFalse(result.clean)
        self.assertEqual(result.reasons, ['login refused'])

    def test_unreachable(self):
        self.fake.unreachable.add(self.IP)
        result = inspect(self.switch)
        self.assertFalse(result.clean)
        self.assertEqual(len(result.reasons), 1)
        self.assertTrue(result.reasons[0].startswith('unreachable'), result.reasons)

    def test_config_that_differs_from_init_is_only_a_warning(self):
        self.fake.running_config[self.IP] = fake_devices.INIT_CONFIG + 'vlan 999 admin-state enable\n'
        result = inspect(self.switch)
        self.assertTrue(result.clean)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn('config differs from init', result.warnings[0])
        self.assertIn('vlan 999 admin-state enable', result.warnings[0])

    def test_a_failing_show_command_is_unreadable_not_a_pass(self):
        for cmd in ('show chassis', 'show interfaces', 'show configuration snapshot'):
            with self.subTest(cmd=cmd):
                self.fake.reset()
                self.fake.fail_on(cmd, exit_status=1)
                result = inspect(self.switch)
                self.assertFalse(result.clean)
                self.assertIn(f'unreadable: {cmd}', result.reasons)

    def test_output_it_cannot_parse_is_unreadable(self):
        self.fake.outputs[self.IP] = {'show chassis': 'something else', 'show interfaces': 'something else'}
        result = inspect(self.switch)
        self.assertFalse(result.clean)
        self.assertEqual(result.reasons, ['unreadable: show chassis', 'unreadable: show interfaces'])

    def test_the_management_port_is_not_unwanted(self):
        self.fake.outputs[self.IP] = {'show interfaces': (
            "Chassis/Slot/Port EMP :\n Operational Status : up,\n"
            "Chassis/Slot/Port 1/1/1 :\n Operational Status : up,\n")}
        self.assertTrue(inspect(self.switch).clean)

    def test_an_up_port_with_a_name_it_does_not_know_is_unreadable(self):
        self.fake.outputs[self.IP] = {'show interfaces': "Chassis/Slot/Port 1-1-7 :\n Operational Status : up,\n"}
        result = inspect(self.switch)
        self.assertFalse(result.clean)
        self.assertEqual(result.reasons, ['unreadable: show interfaces port 1-1-7'])

    def test_a_missing_init_config_is_unreadable(self):
        self.fake.files(self.IP)['init'].discard('vcboot.cfg')
        result = inspect(self.switch)
        self.assertFalse(result.clean)
        self.assertIn('unreadable: init/vcboot.cfg', result.reasons)

    def test_a_switch_without_management_ip(self):
        self.switch.mngt_IP = 'Not available'
        result = inspect(self.switch)
        self.assertFalse(result.clean)
        self.assertEqual(self.fake.commands, [])

    def test_inspect_and_record_keeps_a_history(self):
        inspect_and_record(self.switch)
        self.fake.cable(self.IP, '1/1/5')
        inspect_and_record(self.switch)
        events = list(SwitchEvent.objects.filter(switch=self.switch))
        self.assertEqual([e.kind for e in events], ['inspection', 'inspection'])
        latest, first = events  # newest first
        self.assertFalse(latest.ok)
        self.assertEqual(latest.reasons, ['Unwanted cable: 1/1/5'])
        self.assertTrue(first.ok)
        self.assertEqual(self.switch.last_inspection(), latest)


class SshLoginRefusedTest(SimpleTestCase):
    def test_an_authentication_failure_is_login_refused(self):
        with patch('api.lab_switch.ssh_connect', side_effect=paramiko.AuthenticationException('no')):
            with self.assertRaises(LoginRefused):
                with ssh()('10.0.0.1'):
                    pass

    def test_any_other_connection_failure_is_not(self):
        with patch('api.lab_switch.ssh_connect', side_effect=OSError('timed out')):
            with self.assertRaises(LabSwitchError) as caught:
                with ssh()('10.0.0.1'):
                    pass
        self.assertNotIsInstance(caught.exception, LoginRefused)


@override_settings(BLAB_DEVICES='fake')
class InspectCommandTest(TestCase):
    def setUp(self):
        self.fake = fake_devices.lab_switches
        self.fake.reset()
        self.switches = [
            Switch.objects.create(mngt_IP=f'10.0.0.{i}', model='OS6860', console='TODO', part_number='pn',
                                  hardware_revision='A', serial_number=f'sn{i}')
            for i in (1, 2)
        ]

    def test_inspects_every_switch_and_prints_a_report(self):
        self.fake.cable('10.0.0.2', '1/1/7')
        out = StringIO()
        call_command('inspect_switches', stdout=out)
        report = out.getvalue()
        self.assertIn('10.0.0.1', report)
        self.assertIn('Unwanted cable: 1/1/7', report)
        self.assertIn('1 clean, 1 not clean', report)
        self.assertEqual(SwitchEvent.objects.count(), 2)

    def test_can_be_limited_to_some_switches(self):
        call_command('inspect_switches', ips='10.0.0.2', stdout=StringIO())
        self.assertEqual([e.switch.mngt_IP for e in SwitchEvent.objects.all()], ['10.0.0.2'])


@override_settings(BLAB_DEVICES='fake')
class LabStatusViewTest(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user('alice', password='pw')
        self.bob = User.objects.create_user('bob', password='pw')
        self.switch = Switch.objects.create(mngt_IP='10.0.0.1', model='OS6860', console='TODO', part_number='pn',
                                            hardware_revision='A', serial_number='sn1')
        self.other = Switch.objects.create(mngt_IP='10.0.0.2', model='OS6900', console='TODO', part_number='pn',
                                           hardware_revision='A', serial_number='sn2')
        self.end = timezone.now() + timedelta(days=3)
        Reservation.objects.create(switch=self.switch, user=self.alice, end_date=self.end)
        for minutes, ok in ((10, True), (5, False)):
            SwitchEvent.objects.create(switch=self.switch, kind=SwitchEvent.INSPECTION, ok=ok,
                                       at=timezone.now() - timedelta(minutes=minutes),
                                       reasons=[] if ok else ['in a VC: chassis 1, 2'],
                                       warnings=['config differs from init: 1 line(s) added, 0 removed'])
        self.client = APIClient()

    def test_needs_a_login(self):
        self.assertIn(self.client.get('/api/lab_status/').status_code, (401, 403))

    def test_every_user_sees_every_switch_with_holder_and_inspection(self):
        self.client.force_authenticate(self.bob)
        response = self.client.get('/api/lab_status/')
        self.assertEqual(response.status_code, 200, response.data)
        rows = {row['mngt_IP']: row for row in response.data['switches']}
        self.assertEqual(set(rows), {'10.0.0.1', '10.0.0.2'})

        row = rows['10.0.0.1']
        self.assertEqual(row['holder'], 'alice')
        self.assertIsNotNone(row['end_date'])
        self.assertFalse(row['inspection']['ok'])
        self.assertEqual(row['inspection']['reasons'], ['in a VC: chassis 1, 2'])
        self.assertEqual([e['ok'] for e in row['history']], [False, True])

        self.assertIsNone(rows['10.0.0.2']['holder'])
        self.assertIsNone(rows['10.0.0.2']['inspection'])
        self.assertEqual(rows['10.0.0.2']['history'], [])
