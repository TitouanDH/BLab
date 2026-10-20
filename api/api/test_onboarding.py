"""Bulk onboarding (#55): onboard_switches scans the Management network and onboards what BLab doesn't record."""
from datetime import datetime, timedelta, timezone as tz
from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from . import fake_devices, management_network
from .clean_state import golden_init_config
from .lab_switch import INIT_CONFIG_PATH
from .models import LldpWindow, Onboarding, Port, Switch, SwitchEvent
from .onboarding import LEFT_OUT, ONBOARDING, Onboarder, parse_chassis, parse_neighbours

BB = '10.0.0.100'
OS2260_CHASSIS = "Local Chassis ID 1 (Master)\n  Model Name:                    OS2260-P10,\n  Serial Number: X1,\n"

# show lldp remote-system on a lab Switch, as populate_ports parses it
LLDP = """Remote LLDP nearest-bridge Agents on Local Port 1/1/49:
    Chassis 2c:fa:a2:22:b9:f0, Port 1012:
      Remote ID                   = 1,
      System Name                 = OS10K-ROW1,
      Port Description            = Alcatel-Lucent OS10K XNI 1/2/12,
Remote LLDP nearest-bridge Agents on Local Port 1/1/50:
    Chassis 2c:fa:a2:22:b9:f1, Port 1013:
      System Name                 = some-server,
      Port Description            = eth0,
"""


class ParsingTest(SimpleTestCase):
    def test_a_switch_record_from_show_chassis(self):
        output = ("Local Chassis ID 1 (Master)\n  Model Name:                    OS6860E-24,\n"
                  "  Part Number:                   903624-90,\n  Hardware Revision:             05,\n"
                  "  Serial Number:                 SN0001,\n")
        self.assertEqual(parse_chassis(output), {'model': 'OS6860E-24', 'part_number': '903624-90',
                                                 'hardware_revision': '05', 'serial_number': 'SN0001'})
        self.assertIsNone(parse_chassis('ERROR: nothing'))

    def test_lldp_neighbours(self):
        self.assertEqual(parse_neighbours(LLDP), [('1/1/49', 'OS10K-ROW1', '1/2/12')])

    def test_the_management_network_setting(self):
        [subnet] = management_network.parse('10.69.144.128/26 via 10.69.144.129')
        self.assertEqual((str(subnet.network), subnet.gateway, len(subnet.hosts())), ('10.69.144.128/26', '10.69.144.129', 61))
        self.assertEqual(len(management_network.parse('10.0.0.0/29 via 10.0.0.1, 10.0.1.0/29 via 10.0.1.1')), 2)
        for wrong in ('10.0.0.0/29', '10.0.0.0/29 via 10.9.9.9', 'nonsense via 10.0.0.1'):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                management_network.parse(wrong)


class Clock:
    def __init__(self):
        self.at = datetime(2026, 10, 20, 9, 0, tzinfo=tz.utc)

    def __call__(self):
        return self.at

    def sleep(self, seconds):
        self.at += timedelta(seconds=seconds or 1)


@override_settings(BLAB_DEVICES='fake', BLAB_MANAGEMENT_NETWORKS='10.0.0.0/29 via 10.0.0.1')
class OnboardTest(TestCase):
    def setUp(self):
        self.fake = fake_devices.lab_switches
        self.fake.reset()
        self.bb = fake_devices.backbone
        self.bb.reset()
        # Recorded already, with a Port to the Backbone: .2. New: .3, and .4 with admin's factory password.
        # Not Switches: .5, a Management switch; .6, which refuses BLab's login. .1 is the gateway.
        self.known = Switch.objects.create(mngt_IP='10.0.0.2', model='OS6860', console='TODO', part_number='pn',
                                           hardware_revision='A', serial_number='sn2')
        for uni in ('1/2/1', '1/2/2', '1/2/3'):
            self.bb.cli(BB, f'interfaces {uni} admin-state disable')
        Port.objects.create(switch=self.known, port_switch='1/1/1', backbone=BB, port_backbone='1/2/1')
        self.fake.present.update({'10.0.0.1', '10.0.0.2', '10.0.0.3', '10.0.0.4', '10.0.0.5', '10.0.0.6'})
        self.fake.factory_admin.add('10.0.0.4')
        self.fake.outputs['10.0.0.5'] = {'show chassis': OS2260_CHASSIS}
        self.fake.refused.add('10.0.0.6')
        self.fake.plug('10.0.0.3', '1/1/5', BB, '1/2/2')
        self.clock = Clock()
        self.bb.commands.clear()

    def run_it(self, apply=True, **kwargs):
        onboarder = Onboarder(now=self.clock, sleep=self.clock.sleep, say=lambda line: None)
        return {o.ip: (o.step, o.detail) for o in onboarder.run(['10.0.0.0/29'], kwargs.get('ips', []), apply)}

    def test_a_dry_run_says_what_it_would_do_and_writes_nothing(self):
        outcomes = self.run_it(apply=False)
        self.assertEqual(outcomes, {
            '10.0.0.3': ('would onboard', 'OS6860-48'),
            '10.0.0.4': ('would onboard', 'OS6860-48'),
            '10.0.0.5': ('left out', 'a Management switch (OS2260-P10)'),
            '10.0.0.6': ('left out', "refuses BLab's admin login: not a lab Switch, or another password"),
        })
        self.assertEqual(Switch.objects.count(), 1)
        self.assertEqual((self.fake.written, self.fake.reloads), ({}, []))
        self.assertIn('10.0.0.4', self.fake.factory_admin)
        self.assertFalse(any(not cmd.startswith('show') for _, cmd in self.bb.commands))

    def test_it_onboards_them_from_record_to_in_service(self):
        outcomes = self.run_it()
        self.assertEqual(outcomes['10.0.0.3'], (Onboarding.DONE, 'onboarded: clean, in service'))
        self.assertEqual(outcomes['10.0.0.4'][0], Onboarding.DONE)
        self.assertNotIn('10.0.0.4', self.fake.factory_admin, "admin got the lab's secret password")
        new = Switch.objects.get(mngt_IP='10.0.0.3')
        self.assertEqual((new.model, new.serial_number, new.out_of_service), ('OS6860-48', 'FAKE1', False))
        self.assertEqual(self.fake.written['10.0.0.3'][INIT_CONFIG_PATH], golden_init_config('OS6860-48'))
        self.assertEqual(sorted(self.fake.reloads), ['10.0.0.3', '10.0.0.4'])
        self.assertEqual(list(Port.objects.filter(switch=new).values_list('port_switch', 'backbone', 'port_backbone')),
                         [('1/1/5', BB, '1/2/2')])
        self.assertEqual(self.bb.disabled[BB], {'1/2/1', '1/2/2', '1/2/3'}, 'the free ports are disabled again')
        self.assertEqual(self.bb.lldp_on, set(), "the Backbone's LLDP is off again")
        self.assertEqual(list(new.events.order_by('id').values_list('kind', flat=True)),
                         [SwitchEvent.OUT_OF_SERVICE, SwitchEvent.CLEANUP, SwitchEvent.INSPECTION,
                          SwitchEvent.BACK_IN_SERVICE])

    def test_running_it_again_goes_on_from_where_it_stopped(self):
        self.run_it()
        reloads = list(self.fake.reloads)
        outcomes = self.run_it()
        self.assertEqual(outcomes['10.0.0.3'], (Onboarding.DONE, 'onboarded already'))
        self.assertEqual(self.fake.reloads, reloads)

    def test_one_that_isnt_clean_stays_out_of_service_until_a_run_finds_it_clean(self):
        self.fake.cable('10.0.0.3', '1/1/7')  # to something that isn't a Backbone
        outcomes = self.run_it()
        self.assertEqual(outcomes['10.0.0.3'], (Onboarding.CABLED, 'not clean, so still Out of service: Unwanted cable: 1/1/7'))
        self.assertEqual(Switch.objects.get(mngt_IP='10.0.0.3').out_of_service_reason, ONBOARDING)
        self.fake.unplug('10.0.0.3', '1/1/7')
        self.assertEqual(self.run_it()['10.0.0.3'][0], Onboarding.DONE)
        self.assertFalse(Switch.objects.get(mngt_IP='10.0.0.3').out_of_service)

    def test_a_vc_is_left_out_before_anything_is_written(self):
        self.fake.chassis['10.0.0.3'] = 2
        outcomes = self.run_it()
        self.assertEqual(outcomes['10.0.0.3'], (LEFT_OUT, 'a VC (chassis 1, 2): onboard each chassis once it stands alone'))
        self.assertNotIn('10.0.0.3', self.fake.written)
        self.assertFalse(Switch.objects.filter(mngt_IP='10.0.0.3').exists())

    def test_an_lldp_window_a_killed_run_left_open_is_closed_first(self):
        self.bb.cli(BB, 'interfaces 1/2/3 admin-state enable')
        self.bb.lldp_on.add(BB)
        LldpWindow.objects.create(backbone=BB, ports=['1/2/3'])
        self.run_it()
        self.assertIn('1/2/3', self.bb.disabled[BB])
        self.assertEqual((self.bb.lldp_on, LldpWindow.objects.count()), (set(), 0))

    def test_a_window_it_cant_close_is_kept_for_the_next_run(self):
        self.bb.fail_on('interfaces 1/2/3 admin-state disable')
        self.run_it()
        self.assertEqual(list(LldpWindow.objects.values_list('backbone', 'ports')), [(BB, ['1/2/3'])])
        self.run_it()
        self.assertFalse(LldpWindow.objects.exists())
        self.assertIn('1/2/3', self.bb.disabled[BB])

    def test_a_switch_an_admin_deleted_meanwhile_is_onboarded_afresh(self):
        self.fake.slow_to_reload.add('10.0.0.3')
        self.run_it()
        Switch.objects.get(mngt_IP='10.0.0.3').delete()
        self.fake.slow_to_reload.discard('10.0.0.3')
        self.fake.come_up('10.0.0.3')
        self.assertEqual(self.run_it()['10.0.0.3'][0], Onboarding.DONE)
        self.assertTrue(Switch.objects.filter(mngt_IP='10.0.0.3').exists())

    def test_a_backbone_that_fails_is_left_out_of_the_window_and_the_rest_goes_on(self):
        self.bb.fail_on('show system', ip=BB)
        outcomes = self.run_it()
        self.assertEqual(outcomes['10.0.0.3'], (Onboarding.DONE, 'onboarded: clean, in service'))
        self.assertFalse(Port.objects.filter(switch__mngt_IP='10.0.0.3').exists())

    def test_the_admin_cant_clean_up_a_switch_being_onboarded(self):
        from django.contrib.auth.models import User
        from .admin_actions import Refused, clean_up_now
        self.fake.cable('10.0.0.3', '1/1/7')
        self.run_it()
        admin = User.objects.create_superuser('root', 'root@example.com', 'pw')
        with self.assertRaisesRegex(Refused, 'being onboarded'):
            clean_up_now(Switch.objects.get(mngt_IP='10.0.0.3'), admin)

    def test_init_takes_the_image_from_working_only_when_it_has_one(self):
        self.run_it()
        copies = [cmd for ip, cmd in self.fake.commands if ip == '10.0.0.3' and cmd.startswith('cp') and cmd.endswith(' init/')]
        self.assertEqual(copies, ['cp working/*.img init/', 'cp -r working/pkg init/'])

    def test_one_that_never_comes_back_waits_for_the_next_run(self):
        self.fake.slow_to_reload.add('10.0.0.3')
        outcomes = self.run_it()
        step, detail = outcomes['10.0.0.3']
        self.assertEqual(step, Onboarding.RELOADING)
        self.assertIn('not back 0:20:00 after its reload', detail)
        self.assertEqual(outcomes['10.0.0.4'][0], Onboarding.DONE)
        self.fake.come_up('10.0.0.3')
        self.assertEqual(self.run_it()['10.0.0.3'][0], Onboarding.DONE)

    def test_a_trunk_is_never_enabled_for_the_lldp_window(self):
        self.bb.config.setdefault(BB, []).append('vlan 1441 members port 1/2/3 tagged')
        self.run_it()
        self.assertNotIn((BB, 'interfaces 1/2/3 admin-state enable'), self.bb.commands)
        self.assertIn((BB, 'interfaces 1/2/2 admin-state enable'), self.bb.commands)

    def test_an_address_asked_by_name_says_why_it_is_left_out(self):
        outcomes = self.run_it(ips=['10.0.0.2', '10.0.0.7'])
        self.assertEqual(outcomes['10.0.0.2'], ('left out', 'a Switch BLab records already'))
        self.assertEqual(outcomes['10.0.0.7'], ('left out', "doesn't answer on SSH"))

    def test_the_command_is_a_dry_run_without_apply(self):
        out = StringIO()
        call_command('onboard_switches', subnet=['10.0.0.0/29'], stdout=out)
        self.assertIn('Dry run', out.getvalue())
        self.assertIn('would onboard', out.getvalue())
        self.assertEqual(Switch.objects.count(), 1)
