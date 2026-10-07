import os
import tempfile
from argparse import ArgumentParser
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.db import migrations, models
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import fake_devices, links
from .backbone import APIRequestError, Backbone, Service, backbone, parse_service
from .lab_switch import BANNER_PATH, LabSwitch, LabSwitchError, banner_text, lab_switch
from .management.switch_ips import add_ip_arguments, ips_from
from .migration_safety import unsafe_operations
from .models import Port, Reservation, Switch, TopologyShare
from .release import AlreadyReleased, NotAllowed, expire, may_release, release


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

        with self.assertLogs('api.release', 'ERROR'):
            response = client.post('/api/release/', {'switch': self.switches[0].id}, format='json')
        self.assertEqual(response.status_code, 422, response.data)
        self.assertIn('still reserved', response.data['detail'])
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


@override_settings(BLAB_DEVICES='fake')
class ReleaseTest(TestCase):
    """release() against fake backbones and lab switches: teardown, Cleanup, banner, failures."""

    def setUp(self):
        self.backbone = fake_devices.backbone
        self.switches = fake_devices.lab_switches
        self.backbone.reset()
        self.switches.reset()
        self.alice = User.objects.create_user('alice', password='pw')
        self.bob = User.objects.create_user('bob', password='pw')
        self.switch, other = make_switches(2)
        self.ports = [
            Port.objects.create(switch=switch, port_switch='1/1/1', backbone='10.0.0.100', port_backbone=f'1/1/{i}')
            for i, switch in enumerate((self.switch, other), 1)
        ]
        self.reservation = Reservation.objects.create(switch=self.switch, user=self.alice)
        links.connect(*self.ports)

    def banner(self):
        return self.switches.written.get(self.switch.mngt_IP, {}).get(BANNER_PATH)

    def assert_released(self, result):
        self.assertTrue(result.released)
        self.assertFalse(Reservation.objects.filter(switch=self.switch).exists())
        self.assertEqual(self.backbone.config['10.0.0.100'], [])
        self.assertFalse(Port.objects.filter(svlan__isnull=False).exists())

    def test_user_release_without_cleanup(self):
        result = release(self.reservation, self.alice)
        self.assert_released(result)
        self.assertEqual(result.failures, [])
        self.assertFalse(result.cleaned_up)
        self.assertEqual(self.switches.reloads, [])
        self.assertIn('reserved by : nobody', self.banner())

    def test_user_release_with_cleanup(self):
        result = release(self.reservation, self.alice, cleanup=True)
        self.assert_released(result)
        self.assertEqual(result.failures, [])
        self.assertTrue(result.cleaned_up)
        self.assertEqual(self.switches.reloads, ['10.0.0.1'])
        self.assertIn('reserved by : nobody', self.banner())

    def test_the_banner_is_written_before_the_reload(self):
        release(self.reservation, self.alice, cleanup=True)
        sent = [cmd for ip, cmd in self.switches.commands]
        self.assertLess(sent.index(f'write {BANNER_PATH}'), sent.index('reload from working no rollback-timeout'))

    def test_a_failed_banner_does_not_stop_the_cleanup(self):
        self.switches.fail_on('write')
        with self.assertLogs('api.models', 'ERROR'):
            result = expire(self.reservation)
        self.assertTrue(result.cleaned_up)
        self.assertEqual(self.switches.reloads, ['10.0.0.1'])
        self.assertEqual(result.failures, ["The banner couldn't be updated."])

    def test_a_reservation_that_already_ended_is_left_alone(self):
        stale = Reservation.objects.get(pk=self.reservation.pk)
        self.reservation.delete()
        Reservation.objects.create(switch=self.switch, user=self.bob)  # someone else's now
        with self.assertRaises(AlreadyReleased):
            expire(stale)
        self.assertNotEqual(self.backbone.config['10.0.0.100'], [])
        self.assertEqual(self.switches.commands, [])

    def test_expiry_cleans_up_and_updates_the_banner(self):
        result = expire(self.reservation)
        self.assert_released(result)
        self.assertEqual(result.failures, [])
        self.assertEqual(self.switches.reloads, ['10.0.0.1'])
        self.assertIn('reserved by : nobody', self.banner())

    def test_a_link_that_cant_be_torn_down_keeps_the_reservation(self):
        self.backbone.fail_on('no ethernet-service sap')
        with self.assertLogs('api.release', 'ERROR'):
            result = expire(self.reservation)
        self.assertFalse(result.released)
        self.assertEqual(len(result.failures), 1)
        self.assertIn('failed to disconnect', result.failures[0])
        self.assertTrue(Reservation.objects.filter(switch=self.switch).exists())
        self.ports[0].refresh_from_db()
        self.assertEqual(self.ports[0].svlan, 1001)
        # Nothing is done to the switch while it is still reserved
        self.assertEqual(self.switches.commands, [])

    def test_a_failed_cleanup_is_reported_and_the_release_goes_on(self):
        self.switches.fail_on('ls working/')
        with self.assertLogs('api.models', 'ERROR'):
            result = release(self.reservation, self.alice, cleanup=True)
        self.assert_released(result)
        self.assertFalse(result.cleaned_up)
        self.assertEqual(len(result.failures), 1)
        self.assertIn('Cleanup', result.failures[0])
        self.assertEqual(self.switches.reloads, [])
        self.assertIn('reserved by : nobody', self.banner())

    def test_a_failed_banner_is_reported(self):
        self.switches.fail_on('write')
        with self.assertLogs('api.models', 'ERROR'):
            result = release(self.reservation, self.alice)
        self.assert_released(result)
        self.assertEqual(len(result.failures), 1)
        self.assertIn('banner', result.failures[0])

    def test_failed_cleanup_and_banner_are_both_reported(self):
        self.switches.fail_on('reload')
        self.switches.fail_on('write')
        with self.assertLogs('api.models', 'ERROR'):
            result = expire(self.reservation)
        self.assert_released(result)
        self.assertEqual(len(result.failures), 2)

    def test_a_user_the_topology_is_shared_with_may_release(self):
        TopologyShare.objects.create(owner=self.alice, target=self.bob)
        self.assertTrue(may_release(self.bob, self.reservation))
        self.assert_released(release(self.reservation, self.bob, cleanup=True))
        self.assertEqual(self.switches.reloads, ['10.0.0.1'])

    def test_anyone_else_may_not_release(self):
        self.assertFalse(may_release(self.bob, self.reservation))
        with self.assertRaises(NotAllowed):
            release(self.reservation, self.bob)
        self.assertTrue(Reservation.objects.filter(switch=self.switch).exists())
        self.assertNotEqual(self.backbone.config['10.0.0.100'], [])

    def test_reservation_delete_is_plain_django(self):
        self.reservation.delete()
        self.assertFalse(Reservation.objects.exists())
        # Deleting the row alone touches no equipment
        self.assertNotEqual(self.backbone.config['10.0.0.100'], [])
        self.assertEqual(self.switches.commands, [])


@override_settings(BLAB_DEVICES='fake')
class ReleaseViewAndExpiryCommandTest(TestCase):
    """The release view and the expiry command only translate to and from release()."""

    def setUp(self):
        fake_devices.backbone.reset()
        fake_devices.lab_switches.reset()
        self.alice = User.objects.create_user('alice', password='pw')
        self.bob = User.objects.create_user('bob', password='pw')
        self.switch, self.other = make_switches(2)

    def reserve(self, switch=None, user=None, **fields):
        return Reservation.objects.create(switch=switch or self.switch, user=user or self.alice, **fields)

    def post_release(self, user, **data):
        client = APIClient()
        client.force_authenticate(user)
        return client.post('/api/release/', {'switch': self.switch.id, **data}, format='json')

    def run_expiry(self):
        out = StringIO()
        call_command('cleanup_expired_reservations', '--once', stdout=out)
        return out.getvalue()

    def test_release_with_cleanup(self):
        self.reserve()
        response = self.post_release(self.alice, cleanup=True)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn('Cleanup', response.data['detail'])
        self.assertEqual(fake_devices.lab_switches.reloads, ['10.0.0.1'])
        self.assertFalse(Reservation.objects.exists())

    def test_release_reports_a_failed_cleanup(self):
        self.reserve()
        fake_devices.lab_switches.fail_on('reload')
        with self.assertLogs('api.models', 'ERROR'):
            response = self.post_release(self.alice, cleanup=True)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn('Cleanup failed', response.data['detail'])
        self.assertFalse(Reservation.objects.exists())

    def test_a_user_the_topology_is_shared_with_releases(self):
        self.reserve()
        TopologyShare.objects.create(owner=self.alice, target=self.bob)
        response = self.post_release(self.bob)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(Reservation.objects.exists())

    def test_anyone_else_is_forbidden(self):
        self.reserve()
        response = self.post_release(self.bob)
        self.assertEqual(response.status_code, 403, response.data)
        self.assertTrue(Reservation.objects.exists())

    def test_releasing_an_unreserved_switch_is_refused(self):
        response = self.post_release(self.alice)
        self.assertEqual(response.status_code, 400, response.data)

    def test_expiry_releases_expired_reservations_only(self):
        self.reserve(end_date=timezone.now() - timedelta(hours=1))
        self.reserve(switch=self.other, user=self.bob, end_date=timezone.now() + timedelta(hours=1))
        self.run_expiry()
        self.assertEqual(list(Reservation.objects.values_list('switch', flat=True)), [self.other.id])
        self.assertEqual(fake_devices.lab_switches.reloads, ['10.0.0.1'])
        self.assertIn('reserved by : nobody', fake_devices.lab_switches.written['10.0.0.1'][BANNER_PATH])

    def test_expiry_logs_a_stuck_switch_on_every_cycle(self):
        self.reserve(end_date=timezone.now() - timedelta(hours=1))
        links.connect(
            Port.objects.create(switch=self.switch, port_switch='1/1/1', backbone='10.0.0.100', port_backbone='1/1/1'),
            Port.objects.create(switch=self.other, port_switch='1/1/1', backbone='10.0.0.100', port_backbone='1/1/2'),
        )
        fake_devices.backbone.fail_on('no ethernet-service sap', times=2)
        for _ in range(2):
            with self.assertLogs('api.release', 'ERROR') as logs:
                self.run_expiry()
            self.assertTrue(any('10.0.0.1' in line for line in logs.output), logs.output)
            self.assertTrue(Reservation.objects.exists())
        self.assertEqual(fake_devices.lab_switches.reloads, [])

    def test_expiry_reports_a_failed_cleanup_but_releases(self):
        self.reserve(end_date=timezone.now() - timedelta(hours=1))
        fake_devices.lab_switches.fail_on('reload')
        with self.assertLogs('api.release', 'WARNING'):
            self.run_expiry()
        self.assertFalse(Reservation.objects.exists())


@override_settings(BLAB_DEVICES='fake')
class ReserveTest(TestCase):
    """A Switch has at most one Reservation at a time."""

    def setUp(self):
        fake_devices.lab_switches.reset()
        self.alice = User.objects.create_user('alice', password='pw')
        self.bob = User.objects.create_user('bob', password='pw')
        self.switch = make_switches(1)[0]

    def post_reserve(self, user, switch_id=None):
        client = APIClient()
        client.force_authenticate(user)
        return client.post('/api/reserve/', {'switch': switch_id or self.switch.id}, format='json')

    def test_reserve_then_nobody_else_can(self):
        self.assertEqual(self.post_reserve(self.alice).status_code, 201)
        self.assertEqual(self.post_reserve(self.alice).status_code, 400)
        self.assertEqual(self.post_reserve(self.bob).status_code, 400)
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertIn('reserved by : alice', fake_devices.lab_switches.written['10.0.0.1'][BANNER_PATH])

    def test_reserving_an_unknown_switch_is_404(self):
        self.assertEqual(self.post_reserve(self.alice, switch_id=999).status_code, 404)


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


@override_settings(BLAB_DEVICES='real')
class PrepareSwitchesTest(TestCase):
    """prepare_switches builds init/ itself, then reloads through LabSwitch's Cleanup."""

    IP = '10.0.0.1'
    LOGGER = 'api.management.commands.prepare_switches'
    INIT_BUILD = [
        'rm swlog*', 'rm vcboot.cfg*', 'rm ovng*',
        'ls working/', 'ls certified/', 'rm -rf init', 'mkdir -p init',
        'cp working/*.img init/', 'cp -r working/pkg init/', 'ls init/', 'write init/vcboot.cfg',
    ]

    def setUp(self):
        self.fake = fake_devices.FakeLabSwitches()
        make_switches(1)
        self.new_switch(self.IP)

    def new_switch(self, ip, working=('Uos.img', 'pkg', 'vcboot.cfg', 'vcsetup.cfg'), certified=()):
        """A switch as delivered: firmware in working/ (and maybe certified/), no init/."""
        self.fake.directories[ip] = {'working': set(working), 'certified': set(certified)}

    def prepare(self, *args, ips=IP):
        out = StringIO()
        with patch('api.management.commands.prepare_switches.ssh', return_value=self.fake.connect) as ssh:
            call_command('prepare_switches', '--ips', ips, *args, stdout=out)
        ssh.assert_called_with('admin', 'switch')
        return out.getvalue()

    def sent(self, ip=IP):
        return [cmd for i, cmd in self.fake.commands if i == ip]

    def test_builds_init_from_working_and_writes_the_config(self):
        out = self.prepare()
        self.assertEqual(self.sent(), self.INIT_BUILD)
        self.assertEqual(self.fake.files(self.IP)['init'], {'Uos.img', 'pkg', 'vcboot.cfg'})
        config = self.fake.written[self.IP]['init/vcboot.cfg']
        self.assertIn('system name "OS6860"', config)
        self.assertIn('lldp nearest-bridge chassis tlv management port-description enable', config)
        self.assertEqual(self.fake.reloads, [])
        self.assertIn('Successfully prepared: 1', out)

    def test_reload_runs_the_same_cleanup_as_a_release(self):
        self.prepare('--reload')
        self.assertEqual(self.sent(), self.INIT_BUILD + LabSwitchTest.CLEANUP)
        files = self.fake.files(self.IP)
        self.assertEqual(files['working'], {'Uos.img', 'pkg', 'vcboot.cfg'})
        self.assertEqual(files['certified'], files['init'])
        self.assertEqual(self.fake.reloads, [self.IP])

    def test_a_switch_not_in_the_inventory_is_named_after_its_ip(self):
        self.new_switch('10.9.9.9')
        self.prepare(ips='10.9.9.9')
        self.assertIn('system name "OS6900-10_9_9_9"', self.fake.written['10.9.9.9']['init/vcboot.cfg'])

    def test_init_takes_from_certified_what_working_lacks(self):
        self.new_switch(self.IP, working=('pkg',), certified=('Uos.img', 'pkg'))
        self.prepare()
        self.assertIn('cp certified/*.img init/', self.sent())
        self.assertEqual(self.fake.files(self.IP)['init'], {'Uos.img', 'pkg', 'vcboot.cfg'})

    def test_init_falls_back_to_certified_when_a_copy_from_working_fails(self):
        self.new_switch(self.IP, certified=('Uos.img', 'pkg'))
        self.fake.fail_on('cp working/*.img', exit_status=1)
        self.prepare()
        self.assertIn('cp certified/*.img init/', self.sent())
        self.assertEqual(self.fake.files(self.IP)['init'], {'Uos.img', 'pkg', 'vcboot.cfg'})

    def test_no_image_anywhere_fails_before_touching_init(self):
        self.new_switch(self.IP, working=('pkg',))
        with self.assertLogs(self.LOGGER, 'ERROR'):
            out = self.prepare('--reload')
        self.assertIn('Failed: 1', out)
        self.assertNotIn('rm -rf init', self.sent())
        self.assertEqual(self.fake.reloads, [])

    def test_a_failure_at_any_command_never_reloads(self):
        for n in range(len(self.INIT_BUILD) + len(LabSwitchTest.CLEANUP)):
            with self.subTest(failing_command=n):
                self.fake.reset()
                self.new_switch(self.IP)
                self.fake.fail_after(n)
                with self.assertLogs(self.LOGGER, 'ERROR'):
                    out = self.prepare('--reload')
                self.assertIn('Failed: 1', out)
                self.assertEqual(self.fake.reloads, [])

    def test_old_logs_and_configs_left_behind_are_not_a_failure(self):
        self.fake.fail_on('rm swlog*', exit_status=1)
        out = self.prepare('--reload')
        self.assertIn('Successfully prepared: 1', out)
        self.assertEqual(self.fake.reloads, [self.IP])

    def test_without_a_config_in_init_the_cleanup_refuses_to_reload(self):
        self.new_switch(self.IP, working=('Uos.img', 'pkg'))
        with self.assertLogs(self.LOGGER, 'ERROR'):
            out = self.prepare('--skip-config', '--reload')
        self.assertIn('Failed: 1', out)
        self.assertEqual(self.fake.reloads, [])

    def test_skip_init_keeps_the_existing_init(self):
        self.fake.files(self.IP)['init'] = {'Old.img', 'pkg', 'vcboot.cfg'}
        self.prepare('--skip-cleanup', '--skip-init', '--skip-config', '--reload')
        self.assertEqual(self.sent(), LabSwitchTest.CLEANUP)
        self.assertEqual(self.fake.files(self.IP)['working'], {'Old.img', 'pkg', 'vcboot.cfg'})

    def test_one_failed_switch_does_not_stop_the_others(self):
        self.new_switch('10.0.0.2')
        self.fake.fail_on('ls working/', ip=self.IP)
        with self.assertLogs(self.LOGGER, 'ERROR'):
            out = self.prepare('--reload', ips=f'{self.IP}, 10.0.0.2')
        self.assertIn('Successfully prepared: 1', out)
        self.assertIn('Failed: 1', out)
        self.assertEqual(self.fake.reloads, ['10.0.0.2'])

    @override_settings(BLAB_DEVICES='fake')
    def test_refuses_to_run_on_fake_devices(self):
        with self.assertRaises(CommandError):
            call_command('prepare_switches', '--ips', self.IP, stdout=StringIO())


class SwitchIpsArgumentsTest(SimpleTestCase):
    """--ips and --file, shared by populate_switches and prepare_switches."""

    def parse(self, *args):
        parser = ArgumentParser()
        add_ip_arguments(parser)
        return ips_from(vars(parser.parse_args(args)))

    def test_ips_are_split_on_commas_and_trimmed(self):
        self.assertEqual(self.parse('--ips', '10.0.0.1, 10.0.0.2'), ['10.0.0.1', '10.0.0.2'])

    def test_file_skips_blank_lines_and_comments(self):
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as file:
            file.write('# lab\n10.0.0.1\n\n  10.0.0.2  \n# 10.0.0.3\n')
        self.addCleanup(os.remove, file.name)
        self.assertEqual(self.parse('--file', file.name), ['10.0.0.1', '10.0.0.2'])

    def test_a_missing_file_or_no_ips_is_an_error(self):
        for args in (('--file', 'no/such/file.txt'), ()):
            with self.subTest(args=args), self.assertRaises(CommandError):
                self.parse(*args)
