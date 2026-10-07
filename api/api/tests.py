from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import migrations, models
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import fake_devices, links
from .backbone import APIRequestError, Backbone, Service, backbone, parse_service
from .lab_switch import BANNER_PATH, LabSwitch, LabSwitchError, banner_text, lab_switch
from .migration_safety import unsafe_operations
from .models import Port, Reservation, Switch, TopologyShare


@override_settings(BLAB_DEVICES='fake')
class LinkLifecycleWithFakeDevicesTest(TestCase):
    """Reserve two switches, link them, unlink them, release: all against the fake backbone."""

    def setUp(self):
        fake_devices.backbone.reset()
        self.user = User.objects.create_user('alice', password='pw')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

        self.ports = []
        for i in (1, 2):
            switch = Switch.objects.create(
                mngt_IP=f'10.0.0.{i}', model='OS6860', console='TODO',
                part_number='pn', hardware_revision='A', serial_number=f'sn{i}',
            )
            self.ports.append(Port.objects.create(
                switch=switch, port_switch='1/1/1',
                backbone='10.0.0.100', port_backbone=f'1/1/{i}',
            ))

    def reserve_both(self):
        for port in self.ports:
            response = self.client.post('/api/reserve/', {'switch': port.switch.id}, format='json')
            self.assertEqual(response.status_code, 201, response.data)

    def test_connect_then_disconnect(self):
        self.reserve_both()
        a, b = self.ports

        response = self.client.post('/api/connect/', {'portA': a.id, 'portB': b.id}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertIsNotNone(a.svlan)
        self.assertEqual(a.svlan, b.svlan)
        self.assertIn(f'ethernet-service sap {a.svlan} uni port 1/1/2',
                      fake_devices.backbone.config['10.0.0.100'])

        response = self.client.post('/api/disconnect/', {'portA': a.id, 'portB': b.id}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        a.refresh_from_db()
        self.assertIsNone(a.svlan)
        self.assertEqual(fake_devices.backbone.config['10.0.0.100'], [])

    def test_release_tears_down_link(self):
        self.reserve_both()
        a, b = self.ports
        self.client.post('/api/connect/', {'portA': a.id, 'portB': b.id}, format='json')

        response = self.client.post('/api/release/', {'switch': a.switch.id}, format='json')
        self.assertIn(response.status_code, (200, 204), response.data)
        self.assertFalse(Reservation.objects.filter(switch=a.switch).exists())
        self.assertEqual(fake_devices.backbone.config['10.0.0.100'], [])


def make_switches(n):
    return [
        Switch.objects.create(mngt_IP=f'10.0.0.{i}', model='OS6860', console='TODO', part_number='pn',
                              hardware_revision='A', serial_number=f'sn{i}')
        for i in range(1, n + 1)
    ]


@override_settings(BLAB_DEVICES='fake')
class LinkModuleTest(TestCase):
    """The Link module against fake backbones: one or two backbones, failures, lookups."""

    BB1, BB2 = '10.0.0.100', '10.0.0.200'

    def setUp(self):
        self.fake = fake_devices.backbone
        self.fake.reset()
        self.switches = make_switches(3)

    def port(self, switch, uni, backbone=BB1):
        return Port.objects.create(switch=self.switches[switch], port_switch='1/1/1',
                                   backbone=backbone, port_backbone=uni)

    def service(self, ip, svlan):
        return backbone(ip).read_service(svlan)

    def assert_backbones_empty(self):
        for ip, lines in self.fake.config.items():
            self.assertEqual(lines, [], ip)

    def test_connect_on_one_backbone(self):
        a, b = self.port(0, '1/1/1'), self.port(1, '1/1/2')
        link = links.connect(a, b)

        self.assertEqual(link.svlan, 1001)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual((a.svlan, b.svlan, a.status, b.status), (1001, 1001, 'UP', 'UP'))
        self.assertTrue(self.service(self.BB1, 1001).is_complete('blab_1001', ['1/1/1', '1/1/2']))

    def test_connect_across_two_backbones_puts_one_service_on_each(self):
        a, b = self.port(0, '1/1/1', self.BB1), self.port(1, '1/1/7', self.BB2)
        links.connect(a, b)

        self.assertTrue(self.service(self.BB1, 1001).is_complete('blab_1001', ['1/1/1']))
        self.assertTrue(self.service(self.BB2, 1001).is_complete('blab_1001', ['1/1/7']))

        links.disconnect(links.link_between(a, b))
        self.assert_backbones_empty()
        a.refresh_from_db()
        self.assertIsNone(a.svlan)

    def test_connect_takes_the_lowest_free_svlan(self):
        first = links.connect(self.port(0, '1/1/1'), self.port(1, '1/1/2'))
        second = links.connect(self.port(0, '1/1/3'), self.port(1, '1/1/4'))
        links.disconnect(first)
        third = links.connect(self.port(0, '1/1/5'), self.port(2, '1/1/6'))
        self.assertEqual((second.svlan, third.svlan), (1002, 1001))

    def test_a_failure_at_any_command_undoes_the_whole_link(self):
        a, b = self.port(0, '1/1/1', self.BB1), self.port(1, '1/1/7', self.BB2)
        links.connect(a, b)
        commands = len(self.fake.commands)
        links.disconnect(links.link_between(a, b))

        for n in range(commands):
            with self.subTest(failing_command=n):
                self.fake.reset()
                self.fake.fail_after(n)
                with self.assertRaises(links.BackboneFailure):
                    links.connect(a, b)
                self.assert_backbones_empty()
                self.assertFalse(Port.objects.filter(svlan__isnull=False).exists())

    def test_ports_already_linked_are_refused(self):
        a, b, c = self.port(0, '1/1/1'), self.port(1, '1/1/2'), self.port(2, '1/1/3')
        stale_a = Port.objects.get(id=a.id)
        links.connect(a, b)
        with self.assertRaises(links.PortsBusy):
            links.connect(stale_a, c)
        with self.assertRaises(links.SamePort):
            links.connect(c, c)

    def test_no_free_svlan(self):
        links.connect(self.port(0, '1/1/1'), self.port(1, '1/1/2'))
        with patch.object(links, 'SVLAN_RANGE', range(1001, 1002)):
            with self.assertRaisesMessage(links.NoFreeSvlan, '1001 to 1001'):
                links.connect(self.port(0, '1/1/3'), self.port(1, '1/1/4'))

    def test_a_failed_disconnect_keeps_the_link_and_can_be_retried(self):
        a, b = self.port(0, '1/1/1', self.BB1), self.port(1, '1/1/7', self.BB2)
        links.connect(a, b)
        self.fake.fail_on('no ethernet-service sap', ip=self.BB2)

        with self.assertRaises(links.BackboneFailure):
            links.disconnect(links.link_between(a, b))
        a.refresh_from_db()
        self.assertEqual(a.svlan, 1001)

        links.disconnect(links.link_between(a, b))
        self.assert_backbones_empty()

    def test_disconnect_keeps_a_trunk_bound_to_the_svlan_by_hand(self):
        a, b = self.port(0, '1/1/1', self.BB1), self.port(1, '1/1/7', self.BB2)
        links.connect(a, b)
        for ip in (self.BB1, self.BB2):
            self.fake.cli(ip, 'ethernet-service svlan 1001 nni port 1/1/24')

        links.disconnect(links.link_between(a, b))
        for ip in (self.BB1, self.BB2):
            self.assertEqual(self.fake.config[ip], ['ethernet-service svlan 1001 admin-state enable',
                                                   'ethernet-service svlan 1001 nni port 1/1/24'])

    def test_disconnect_a_link_built_by_the_old_code_across_backbones(self):
        # The old code put both UNIs on the first port's backbone, named after the user
        a, b = self.port(0, '1/1/1', self.BB1), self.port(1, '1/1/7', self.BB2)
        Backbone(self.BB1, self.fake.cli).configure_service(1001, 'alice_1001', ['1/1/1', '1/1/7'])
        Port.objects.filter(id__in=[a.id, b.id]).update(svlan=1001)
        a.refresh_from_db()
        b.refresh_from_db()

        links.disconnect(links.link_between(a, b))
        self.assert_backbones_empty()

    def test_an_unexpected_error_still_undoes_the_link(self):
        a, b = self.port(0, '1/1/1'), self.port(1, '1/1/2')
        with patch.object(Port, 'save', side_effect=RuntimeError('db down')):
            with self.assertRaises(RuntimeError):
                links.connect(a, b)
        self.assert_backbones_empty()
        self.assertFalse(Port.objects.filter(svlan__isnull=False).exists())

    def test_links_for_finds_links_whose_other_end_is_elsewhere(self):
        a, b = self.port(0, '1/1/1'), self.port(1, '1/1/2')
        c, d = self.port(1, '1/1/3'), self.port(2, '1/1/4')
        links.connect(a, b)
        links.connect(c, d)
        found = links.links_for([self.switches[0]])
        self.assertEqual([(l.svlan, {p.id for p in l.ports}) for l in found], [(1001, {a.id, b.id})])
        self.assertEqual(len(links.links_for(self.switches[1:])), 2)


@override_settings(BLAB_DEVICES='fake')
class LinkViewsTest(TestCase):
    """The HTTP views on top of the Link module."""

    def setUp(self):
        fake_devices.backbone.reset()
        self.alice = User.objects.create_user('alice', password='pw')
        self.bob = User.objects.create_user('bob', password='pw')
        self.switches = make_switches(3)
        self.ports = [
            Port.objects.create(switch=switch, port_switch='1/1/1', backbone='10.0.0.100', port_backbone=f'1/1/{i}')
            for i, switch in enumerate(self.switches, 1)
        ]
        for switch in self.switches:
            Reservation.objects.create(switch=switch, user=self.alice)
        TopologyShare.objects.create(owner=self.alice, target=self.bob)

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_shared_user_disconnects_a_link_named_after_its_creator(self):
        # A link created before the Link module: its service is named after whoever connected it
        a, b = self.ports[:2]
        for cmd in ('ethernet-service svlan 1001 admin-state enable',
                    'ethernet-service service-name alice_1001 svlan 1001',
                    'ethernet-service sap 1001 service-name alice_1001',
                    'ethernet-service sap 1001 uni port 1/1/1',
                    'ethernet-service sap 1001 uni port 1/1/2',
                    'ethernet-service sap 1001 cvlan all'):
            fake_devices.backbone.cli('10.0.0.100', cmd)
        Port.objects.filter(id__in=[a.id, b.id]).update(svlan=1001)

        response = self.client_for(self.bob).post('/api/disconnect/', {'portA': a.id, 'portB': b.id}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(fake_devices.backbone.config['10.0.0.100'], [])

    def test_connect_failure_is_reported_and_leaves_nothing(self):
        a, b = self.ports[:2]
        fake_devices.backbone.fail_on('cvlan all')
        response = self.client_for(self.alice).post('/api/connect/', {'portA': a.id, 'portB': b.id}, format='json')
        self.assertEqual(response.status_code, 422, response.data)
        self.assertEqual(fake_devices.backbone.config['10.0.0.100'], [])
        a.refresh_from_db()
        self.assertIsNone(a.svlan)

    def test_disconnecting_unlinked_ports_is_refused(self):
        a, b = self.ports[:2]
        response = self.client_for(self.alice).post('/api/disconnect/', {'portA': a.id, 'portB': b.id}, format='json')
        self.assertEqual(response.status_code, 400, response.data)

    def test_release_tears_down_every_link_of_the_switch(self):
        a, b, c = self.ports
        extra = Port.objects.create(switch=self.switches[0], port_switch='1/1/2',
                                    backbone='10.0.0.100', port_backbone='1/1/9')
        client = self.client_for(self.alice)
        for x, y in ((a, b), (extra, c)):
            response = client.post('/api/connect/', {'portA': x.id, 'portB': y.id}, format='json')
            self.assertEqual(response.status_code, 200, response.data)

        response = client.post('/api/release/', {'switch': self.switches[0].id}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(fake_devices.backbone.config['10.0.0.100'], [])
        self.assertFalse(Port.objects.filter(svlan__isnull=False).exists())

    def test_release_keeps_the_reservation_when_a_link_cant_be_removed(self):
        a, b, c = self.ports
        client = self.client_for(self.alice)
        client.post('/api/connect/', {'portA': a.id, 'portB': b.id}, format='json')
        fake_devices.backbone.fail_on('no ethernet-service sap')

        # assertLogs also keeps Django's 500 email handler out of it (it crashes on Python 3.14)
        with self.assertLogs('django.request', 'ERROR'):
            response = client.post('/api/release/', {'switch': self.switches[0].id}, format='json')
        self.assertEqual(response.status_code, 500, response.data)
        self.assertTrue(Reservation.objects.filter(switch=self.switches[0]).exists())
        a.refresh_from_db()
        self.assertEqual(a.svlan, 1001)

    def test_shared_topology_lists_each_link_once(self):
        a, b, c = self.ports
        self.client_for(self.alice).post('/api/connect/', {'portA': a.id, 'portB': b.id}, format='json')
        response = self.client_for(self.bob).get(f'/api/get_shared_topology/{self.alice.id}/')
        self.assertEqual(response.data, {'connections': [{'port1_id': a.id, 'port2_id': b.id, 'svlan': 1001}]})


class BackboneServicesTest(SimpleTestCase):
    """Service operations against the fake backbone, and parsing of the real snapshot format."""

    IP = '10.0.0.100'

    def setUp(self):
        self.fake = fake_devices.FakeBackbone()
        self.backbone = Backbone(self.IP, self.fake.cli)

    def test_configure_then_read_service(self):
        self.backbone.configure_service(1001, 'blab_1001', ['1/1/1', '1/1/2'])
        self.assertEqual(
            self.backbone.read_service(1001),
            Service(svlan=1001, svlan_configured=True, name='blab_1001', sap=True,
                    unis=('1/1/1', '1/1/2'), cvlan_all=True),
        )
        self.assertIsNone(self.backbone.read_service(1002))

    def test_remove_uses_the_name_found_on_the_device(self):
        self.backbone.configure_service(1001, 'bob_1001', ['1/1/1', '1/1/2'])
        self.backbone.remove_service(1001)
        self.assertEqual(self.fake.config[self.IP], [])
        self.assertIn((self.IP, 'no ethernet-service service-name bob_1001 svlan 1001'), self.fake.commands)

    def test_remove_is_idempotent_and_handles_half_built_services(self):
        self.fake.fail_on('cvlan all')
        with self.assertRaises(APIRequestError):
            self.backbone.configure_service(1001, 'blab_1001', ['1/1/1'])
        self.assertFalse(self.backbone.read_service(1001).is_complete('blab_1001', ['1/1/1']))

        self.backbone.remove_service(1001)
        self.assertEqual(self.fake.config[self.IP], [])
        sent = len(self.fake.commands)
        self.backbone.remove_service(1001)
        self.assertEqual(len(self.fake.commands), sent + 1)  # only the read

    def test_remove_leaves_other_svlans_alone(self):
        self.backbone.configure_service(1001, 'a', ['1/1/1'])
        self.backbone.configure_service(10010, 'b', ['1/1/2'])
        self.backbone.remove_service(1001)
        self.assertEqual(self.backbone.read_service(10010).unis, ('1/1/2',))

    def test_fail_after_lets_n_commands_through_then_fails_once(self):
        self.fake.fail_after(2)
        self.fake.cli(self.IP, 'ethernet-service svlan 1 admin-state enable')
        self.fake.cli(self.IP, 'ethernet-service svlan 2 admin-state enable')
        with self.assertRaises(APIRequestError):
            self.fake.cli(self.IP, 'ethernet-service svlan 3 admin-state enable')
        self.fake.cli(self.IP, 'ethernet-service svlan 4 admin-state enable')
        self.assertEqual(len(self.fake.config[self.IP]), 3)

    def test_fail_on_can_target_one_backbone(self):
        self.fake.fail_on('svlan', ip='10.0.0.200')
        self.fake.cli(self.IP, 'ethernet-service svlan 1 admin-state enable')
        with self.assertRaises(APIRequestError):
            self.fake.cli('10.0.0.200', 'ethernet-service svlan 1 admin-state enable')

    def test_parse_real_snapshot_with_quotes_and_port_ranges(self):
        snapshot = """
! VLAN SVLAN:
ethernet-service svlan 1001 admin-state enable
ethernet-service svlan 1002 admin-state enable
ethernet-service service-name "alice_1001" svlan 1001
ethernet-service sap 1001 service-name "alice_1001"
ethernet-service sap 1001 uni port 1/1/3-5
ethernet-service sap 1001 cvlan all
ethernet-service sap 10011 uni port 1/1/9
"""
        self.assertEqual(
            parse_service(snapshot, 1001),
            Service(svlan=1001, svlan_configured=True, name='alice_1001', sap=True,
                    unis=('1/1/3', '1/1/4', '1/1/5'), cvlan_all=True),
        )
        self.assertEqual(parse_service(snapshot, 1002), Service(svlan=1002, svlan_configured=True))
        self.assertIsNone(parse_service(snapshot, 101))


class LabSwitchTest(SimpleTestCase):
    """Banner and Cleanup against the fake lab switch, including its failure paths."""

    IP = '10.0.0.1'
    CLEANUP = [
        'rm -rf working/*',
        'cp -r init/* working/',
        'ls working/',
        'rm -rf certified/*',
        'cp -r init/* certified/',
        'reload from working no rollback-timeout',
    ]

    def setUp(self):
        self.fake = fake_devices.FakeLabSwitches()
        self.switch = LabSwitch(self.IP, self.fake.connect)

    def sent(self):
        return [cmd for ip, cmd in self.fake.commands if ip == self.IP]

    def test_set_banner_writes_the_banner_file(self):
        self.switch.set_banner(['alice', 'bob'])
        text = self.fake.written[self.IP][BANNER_PATH]
        self.assertIn('This switch is reserved by : alice, bob', text)
        self.assertEqual(text, banner_text(['alice', 'bob']))

    def test_banner_of_an_unreserved_switch_names_nobody(self):
        self.assertIn('This switch is reserved by : nobody', banner_text([]))

    def test_set_banner_failure_raises(self):
        self.fake.fail_on('write')
        with self.assertRaises(LabSwitchError):
            self.switch.set_banner(['alice'])
        self.assertNotIn(self.IP, self.fake.written)

    def test_cleanup_restores_init_and_reloads(self):
        self.fake.files(self.IP)['working'] = {'mine.cfg'}
        self.switch.restore_init_and_reload()
        self.assertEqual(self.sent(), self.CLEANUP)
        files = self.fake.files(self.IP)
        self.assertEqual(files['working'], files['init'])
        self.assertEqual(files['certified'], files['init'])
        self.assertEqual(self.fake.reloads, [self.IP])

    def test_cleanup_fails_at_any_command_before_the_reload_happens(self):
        for n in range(len(self.CLEANUP)):
            with self.subTest(failing_command=n):
                self.fake.reset()
                self.fake.fail_after(n)
                with self.assertRaises(LabSwitchError):
                    self.switch.restore_init_and_reload()
                self.assertEqual(self.fake.reloads, [])

    def test_cleanup_stops_when_init_cannot_be_copied(self):
        self.fake.fail_on('cp -r init/* working/', exit_status=1)
        with self.assertRaises(LabSwitchError):
            self.switch.restore_init_and_reload()
        self.assertEqual(self.sent(), self.CLEANUP[:2])
        self.assertEqual(self.fake.reloads, [])

    def test_cleanup_stops_when_working_cannot_be_listed(self):
        self.fake.fail_on('ls working/', exit_status=2)
        with self.assertRaises(LabSwitchError):
            self.switch.restore_init_and_reload()
        self.assertEqual(self.fake.reloads, [])

    def test_cleanup_refuses_to_reload_without_an_essential_file(self):
        for missing in ('Uos.img', 'pkg', 'vcboot.cfg'):
            with self.subTest(missing=missing):
                self.fake.reset()
                self.fake.files(self.IP)['init'].discard(missing)
                with self.assertRaises(LabSwitchError):
                    self.switch.restore_init_and_reload()
                self.assertEqual(self.fake.reloads, [])

    def test_cleanup_goes_on_when_only_a_side_step_fails(self):
        # Clearing working/ and refreshing certified/ are not essential to a clean reload
        for side_step in ('rm -rf working/*', 'rm -rf certified/*', 'cp -r init/* certified/'):
            with self.subTest(side_step=side_step):
                self.fake.reset()
                self.fake.fail_on(side_step, exit_status=1)
                self.switch.restore_init_and_reload()
                self.assertEqual(self.fake.reloads, [self.IP])

    def test_fail_on_can_target_one_switch(self):
        self.fake.fail_on('ls', ip='10.0.0.2')
        self.switch.restore_init_and_reload()
        with self.assertRaises(LabSwitchError):
            LabSwitch('10.0.0.2', self.fake.connect).restore_init_and_reload()


@override_settings(BLAB_DEVICES='fake')
class SwitchBannerAndCleanupTest(TestCase):
    """Switch.changeBanner and Switch.cleanup go through the LabSwitch seam and report failures."""

    def setUp(self):
        self.fake = fake_devices.lab_switches
        self.fake.reset()
        self.switch = make_switches(1)[0]
        self.alice = User.objects.create_user('alice', password='pw')

    def test_the_fake_is_picked_when_devices_are_fake(self):
        lab_switch(self.switch.mngt_IP).set_banner([])
        self.assertIn(BANNER_PATH, self.fake.written[self.switch.mngt_IP])

    def test_change_banner_names_whoever_holds_the_switch(self):
        Reservation.objects.create(switch=self.switch, user=self.alice)
        self.assertTrue(self.switch.changeBanner())
        self.assertIn('reserved by : alice', self.fake.written['10.0.0.1'][BANNER_PATH])

    def test_change_banner_reports_a_failure(self):
        self.fake.fail_on('write')
        with self.assertLogs('api.models', 'ERROR'):
            self.assertFalse(self.switch.changeBanner())

    def test_change_banner_skips_switches_without_a_management_ip(self):
        self.switch.mngt_IP = 'Not available'
        self.assertTrue(self.switch.changeBanner())
        self.assertEqual(self.fake.commands, [])

    def test_cleanup_reloads_an_unreserved_switch(self):
        self.assertTrue(self.switch.cleanup())
        self.assertEqual(self.fake.reloads, ['10.0.0.1'])

    def test_cleanup_leaves_a_reserved_switch_alone(self):
        Reservation.objects.create(switch=self.switch, user=self.alice)
        self.assertFalse(self.switch.cleanup())
        self.assertEqual(self.fake.commands, [])

    def test_cleanup_reports_a_failure(self):
        self.fake.fail_on('ls working/')
        with self.assertLogs('api.models', 'ERROR'):
            self.assertFalse(self.switch.cleanup())
        self.assertEqual(self.fake.reloads, [])

    def test_reserve_reports_a_banner_failure(self):
        self.fake.fail_on('write')
        client = APIClient()
        client.force_authenticate(self.alice)
        with self.assertLogs('api.models', 'ERROR'):
            response = client.post('/api/reserve/', {'switch': self.switch.id}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIn('failed to update the switch banner', response.data['detail'])
        self.assertTrue(Reservation.objects.filter(switch=self.switch).exists())

    def test_expired_reservation_is_released_and_cleaned_up(self):
        Reservation.objects.create(switch=self.switch, user=self.alice,
                                   end_date=timezone.now() - timedelta(hours=1))
        self.assertEqual(Reservation.cleanup_expired_reservations(), 1)
        self.assertEqual(self.fake.reloads, ['10.0.0.1'])

    def test_expired_reservation_is_released_even_if_cleanup_fails(self):
        Reservation.objects.create(switch=self.switch, user=self.alice,
                                   end_date=timezone.now() - timedelta(hours=1))
        self.fake.fail_on('reload')
        with self.assertLogs('api.models', 'ERROR'):
            self.assertEqual(Reservation.cleanup_expired_reservations(), 1)
        self.assertFalse(Reservation.objects.exists())


class MigrationSafetyForSharedDatabaseTest(SimpleTestCase):
    """Migrations not on main yet must keep main's code working (docs/adr/0002)."""

    def migration(self, *operations, **attrs):
        migration = migrations.Migration('0099_test', 'api')
        migration.operations = list(operations)
        for key, value in attrs.items():
            setattr(migration, key, value)
        return migration

    def test_additive_changes_are_safe(self):
        migration = self.migration(
            migrations.CreateModel('Thing', [('id', models.BigAutoField(primary_key=True))]),
            migrations.AddField('port', 'note', models.CharField(max_length=10, null=True)),
            migrations.AddField('port', 'flag', models.BooleanField(db_default=False)),
            migrations.AddIndex('port', models.Index(fields=['status'], name='port_status_idx')),
        )
        self.assertEqual(unsafe_operations(migration), [])

    def test_not_null_column_without_db_default_is_unsafe(self):
        migration = self.migration(
            migrations.AddField('port', 'flag', models.BooleanField(default=False)),
        )
        self.assertEqual(len(unsafe_operations(migration)), 1)

    def test_destructive_changes_are_unsafe(self):
        migration = self.migration(
            migrations.RemoveField('port', 'svlan'),
            migrations.RenameField('port', 'status', 'state'),
            migrations.DeleteModel('TopologyShare'),
        )
        self.assertEqual(len(unsafe_operations(migration)), 3)

    def test_reviewed_migration_can_opt_out(self):
        migration = self.migration(migrations.RemoveField('port', 'svlan'), shared_db_safe=True)
        self.assertEqual(unsafe_operations(migration), [])
