import os
import tempfile
from argparse import ArgumentParser
from datetime import timedelta
from io import StringIO
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.db import migrations, models
from django.db import connection, connections
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from requests.cookies import RequestsCookieJar
from rest_framework.test import APIClient

from . import backbone as backbone_module
from . import fake_devices, links
from .backbone import (APIRequestError, Backbone, Service, backbone, parse_disabled_ports, parse_service,
                       parse_services)
from .link_worker import WORKER_LOCK, LinkWorker, holds_worker_lock, made_by_blab, remove_orphan
from .lab_switch import BANNER_PATH, LabSwitch, LabSwitchError, banner_text, lab_switch
from .management.switch_ips import add_ip_arguments, ips_from
from .migration_safety import unsafe_operations
from .models import PendingCleanup, Port, Reservation, Switch, SwitchEvent, TopologyShare
from .reconcile import GhostLink, NotALink, Orphan, StatusDrift, Unreachable, Unreadable, reconcile, repair
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
        self.assertEqual(response.status_code, 202, response.data)
        LinkWorker().tear_down_requested()
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

    def test_connect_next_to_existing_svlans_then_disconnect_them(self):
        # The backbone shows 1001-1003 as one range; the third Link used to fail to verify
        pairs = [(self.port(0, f'1/1/{n}'), self.port(1, f'1/2/{n}')) for n in (1, 2, 3)]
        made = [links.connect(a, b) for a, b in pairs]
        self.assertEqual([link.svlan for link in made], [1001, 1002, 1003])

        for link in made:
            links.disconnect(link)
        self.assert_backbones_empty()

    def test_connect_reuses_an_svlan_left_alone_on_the_backbone(self):
        self.fake.cli(self.BB1, 'ethernet-service svlan 1001 admin-state enable')
        sent = len(self.fake.commands)
        link = links.connect(self.port(0, '1/1/1'), self.port(1, '1/1/2'))

        self.assertEqual(link.svlan, 1001)
        self.assertNotIn((self.BB1, 'ethernet-service svlan 1001 admin-state enable'), self.fake.commands[sent:])
        self.assertTrue(self.service(self.BB1, 1001).is_complete('blab_1001', ['1/1/1', '1/1/2']))

    def test_a_failed_connect_keeps_the_svlan_it_found_on_the_backbone(self):
        self.fake.cli(self.BB1, 'ethernet-service svlan 1001 admin-state enable')
        self.fake.fail_on('cvlan all')
        with self.assertRaises(links.BackboneFailure):
            links.connect(self.port(0, '1/1/1'), self.port(1, '1/1/2'))
        self.assertEqual(self.fake.config[self.BB1], ['ethernet-service svlan 1001 admin-state enable'])

    def test_connect_finishes_a_leftover_of_its_own_service(self):
        bb = Backbone(self.BB1, self.fake.cli)
        bb.configure_service(1001, 'blab_1001', ['1/1/1'])
        self.fake.cli(self.BB1, 'no ethernet-service sap 1001 uni port 1/1/1')
        link = links.connect(self.port(0, '1/1/1'), self.port(1, '1/1/2'))
        self.assertEqual(link.svlan, 1001)
        self.assertTrue(self.service(self.BB1, 1001).is_complete('blab_1001', ['1/1/1', '1/1/2']))

    def test_connect_skips_an_svlan_whose_service_belongs_to_someone_else(self):
        Backbone(self.BB1, self.fake.cli).configure_service(1001, 'bob_1001', ['1/3/9'])
        sent = len(self.fake.commands)
        a, b = self.port(0, '1/1/1'), self.port(1, '1/1/2')
        link = links.connect(a, b)

        self.assertEqual(link.svlan, 1002)
        self.assertFalse([cmd for _, cmd in self.fake.commands[sent:]
                          if ' 1001' in cmd and not cmd.startswith('show')])
        self.assertEqual(self.service(self.BB1, 1001).unis, ('1/3/9',))
        a.refresh_from_db()
        self.assertEqual(a.svlan, 1002)

    def test_connect_skips_an_svlan_taken_on_the_far_backbone(self):
        Backbone(self.BB2, self.fake.cli).configure_service(1001, 'bob_1001', ['1/3/9'])
        link = links.connect(self.port(0, '1/1/1', self.BB1), self.port(1, '1/1/7', self.BB2))
        self.assertEqual(link.svlan, 1002)
        self.assertIsNone(self.service(self.BB1, 1001))

    def test_connect_gives_up_after_a_few_svlans_taken_on_the_backbone(self):
        for svlan in range(1001, 1001 + links.SVLAN_TRIES):
            Backbone(self.BB1, self.fake.cli).configure_service(svlan, f'bob_{svlan}', ['1/3/9'])
        a, b = self.port(0, '1/1/1'), self.port(1, '1/1/2')
        with self.assertRaises(links.NoFreeSvlan):
            links.connect(a, b)
        a.refresh_from_db()
        self.assertIsNone(a.svlan)

    def test_connect_leaves_the_ports_unlinked_when_the_backbone_cant_be_read(self):
        self.fake.fail_on('show')
        a, b = self.port(0, '1/1/1'), self.port(1, '1/1/2')
        with self.assertRaises(links.BackboneFailure):
            links.connect(a, b)
        a.refresh_from_db()
        self.assertIsNone(a.svlan)
        self.assertFalse([cmd for _, cmd in self.fake.commands if not cmd.startswith('show')])

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
                self.fake.disabled.update({self.BB1: {'1/1/1'}, self.BB2: {'1/1/7'}})  # as unlinked UNIs are
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
        self.assertEqual(response.status_code, 202, response.data)
        LinkWorker().tear_down_requested()
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

    def test_a_link_is_disconnected_by_whoever_works_on_either_end(self):
        # Bob links his own Switch to one in Alice's shared Topology; Alice may remove that Link
        bobs_switch = Switch.objects.create(mngt_IP='10.0.0.4', model='OS6860', console='TODO', part_number='pn',
                                            hardware_revision='A', serial_number='sn4')
        Reservation.objects.create(switch=bobs_switch, user=self.bob)
        bobs_port = Port.objects.create(switch=bobs_switch, port_switch='1/1/1',
                                        backbone='10.0.0.100', port_backbone='1/1/4')
        a = self.ports[0]
        response = self.client_for(self.bob).post('/api/connect/', {'portA': a.id, 'portB': bobs_port.id}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

        response = self.client_for(self.alice).post('/api/disconnect/', {'portA': a.id, 'portB': bobs_port.id},
                                                    format='json')
        self.assertEqual(response.status_code, 202, response.data)
        LinkWorker().tear_down_requested()
        self.assertEqual(fake_devices.backbone.config['10.0.0.100'], [])

    def test_connecting_needs_access_to_both_ends(self):
        carols_switch = Switch.objects.create(mngt_IP='10.0.0.4', model='OS6860', console='TODO', part_number='pn',
                                              hardware_revision='A', serial_number='sn4')
        Reservation.objects.create(switch=carols_switch, user=User.objects.create_user('carol', password='pw'))
        carols_port = Port.objects.create(switch=carols_switch, port_switch='1/1/1',
                                          backbone='10.0.0.100', port_backbone='1/1/4')
        response = self.client_for(self.alice).post('/api/connect/', {'portA': self.ports[0].id,
                                                                      'portB': carols_port.id}, format='json')
        self.assertEqual(response.status_code, 403, response.data)


@override_settings(BLAB_DEVICES='fake')
class TopologyReadTest(TestCase):
    """GET topology/<owner_id>/: a user's Switches, their Ports and every Link with an end on them."""

    def setUp(self):
        fake_devices.backbone.reset()
        self.alice = User.objects.create_user('alice', password='pw')
        self.bob = User.objects.create_user('bob', password='pw')
        self.carol = User.objects.create_user('carol', password='pw')
        self.switches = make_switches(3)
        self.ports = [
            Port.objects.create(switch=switch, port_switch='1/1/1', backbone='10.0.0.100', port_backbone=f'1/1/{i}')
            for i, switch in enumerate(self.switches, 1)
        ]
        Reservation.objects.create(switch=self.switches[0], user=self.alice)
        Reservation.objects.create(switch=self.switches[1], user=self.alice)
        Reservation.objects.create(switch=self.switches[2], user=self.bob)
        TopologyShare.objects.create(owner=self.alice, target=self.bob)

    def read(self, user, owner):
        client = APIClient()
        client.force_authenticate(user)
        return client.get(f'/api/topology/{owner.id}/')

    def link(self, a, b, svlan):
        Port.objects.filter(id__in=[a.id, b.id]).update(svlan=svlan)

    def test_own_topology_holds_its_switches_ports_and_each_link_once(self):
        a, b, c = self.ports
        self.link(a, b, 1001)
        response = self.read(self.alice, self.alice)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['may_work'])
        self.assertEqual([(s['id'], s['in_topology']) for s in response.data['switches']],
                         [(self.switches[0].id, True), (self.switches[1].id, True)])
        self.assertEqual([p['id'] for p in response.data['ports']], [a.id, b.id])
        self.assertEqual(response.data['links'], [{'svlan': 1001, 'ports': [a.id, b.id], 'teardown_error': None}])

    def test_a_user_the_topology_is_shared_with_reads_and_works_on_it(self):
        response = self.read(self.bob, self.alice)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['may_work'])
        self.assertEqual(len(response.data['switches']), 2)

    def test_a_topology_not_shared_with_the_user_is_refused(self):
        self.assertEqual(self.read(self.carol, self.alice).status_code, 403)
        self.assertEqual(self.read(self.alice, self.bob).status_code, 403)  # shares go one way

    def test_an_unknown_owner_is_not_found(self):
        response = self.read(self.alice, User(id=999))
        self.assertEqual(response.status_code, 404)

    def test_a_link_to_a_switch_outside_the_topology_comes_with_its_far_end_only(self):
        a, b, c = self.ports
        Port.objects.create(switch=self.switches[2], port_switch='1/1/2', backbone='10.0.0.100', port_backbone='1/1/9')
        self.link(a, c, 1002)
        response = self.read(self.alice, self.alice)
        self.assertEqual([(s['id'], s['in_topology']) for s in response.data['switches']],
                         [(self.switches[0].id, True), (self.switches[1].id, True), (self.switches[2].id, False)])
        self.assertEqual([p['id'] for p in response.data['ports']], [a.id, b.id, c.id])
        self.assertEqual(response.data['links'], [{'svlan': 1002, 'ports': [a.id, c.id], 'teardown_error': None}])

    def test_an_svlan_held_by_other_than_two_ports_is_not_a_link(self):
        a, b, c = self.ports
        Port.objects.filter(id__in=[a.id, b.id, c.id]).update(svlan=1003)
        with self.assertLogs('api.links', 'WARNING'):
            response = self.read(self.alice, self.alice)
        self.assertEqual(response.data['links'], [])
        self.assertEqual([s['id'] for s in response.data['switches']], [self.switches[0].id, self.switches[1].id])

    def test_a_user_without_reservations_has_an_empty_topology(self):
        response = self.read(self.carol, self.carol)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data['switches'], response.data['ports'], response.data['links']), ([], [], []))


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

    def test_unbuild_takes_back_only_its_own_unis_while_others_remain(self):
        self.backbone.configure_service(1001, 'blab_1001', ['1/1/1', '1/1/2'])
        self.fake.cli(self.IP, 'ethernet-service sap 1001 uni port 1/3/9')
        self.backbone.unbuild_service(1001, 'blab_1001', ['1/1/1', '1/1/2'], keep_svlan=False)
        self.assertEqual(self.backbone.read_service(1001).unis, ('1/3/9',))
        self.assertEqual(self.backbone.read_service(1001).name, 'blab_1001')

    def test_unbuild_leaves_a_service_of_another_name_alone(self):
        self.backbone.configure_service(1001, 'bob_1001', ['1/1/1'])
        sent = len(self.fake.commands)
        self.backbone.unbuild_service(1001, 'blab_1001', ['1/1/1'], keep_svlan=False)
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

    def test_parse_svlans_shown_as_a_range(self):
        # As read off the backbone: AOS folds consecutive SVLANs into one line
        snapshot = """
ethernet-service svlan 1001-1003 admin-state enable
ethernet-service svlan 1005 admin-state enable
ethernet-service svlan 1010-1011 nni port 1/1/24
ethernet-service service-name "blab_1003" svlan 1003
ethernet-service sap 1003 service-name "blab_1003"
ethernet-service sap 1003 uni port 1/2/3
ethernet-service sap 1003 uni port 1/5/30
ethernet-service sap 1003 cvlan all
"""
        self.assertTrue(parse_service(snapshot, 1003).is_complete('blab_1003', ['1/2/3', '1/5/30']))
        self.assertEqual(parse_service(snapshot, 1002), Service(svlan=1002, svlan_configured=True))
        self.assertEqual(parse_service(snapshot, 1011), Service(svlan=1011, svlan_configured=True, nni=True))
        self.assertIsNone(parse_service(snapshot, 1004))
        self.assertIsNone(parse_service(snapshot, 100))

    def test_the_fake_shows_consecutive_svlans_as_a_range_like_the_device(self):
        for svlan in (1001, 1002, 1003, 1005):
            self.backbone.configure_service(svlan, f'blab_{svlan}', ['1/1/1'])
        snapshot = self.fake.cli(self.IP, 'show configuration snapshot vlan')
        self.assertIn('ethernet-service svlan 1001-1003 admin-state enable', snapshot.splitlines())
        self.assertIn('ethernet-service svlan 1005 admin-state enable', snapshot.splitlines())

    def test_remove_an_svlan_from_the_middle_of_a_range(self):
        for svlan in (1001, 1002, 1003):
            self.backbone.configure_service(svlan, f'blab_{svlan}', [f'1/1/{svlan - 1000}'])
        self.backbone.remove_service(1002)

        self.assertIn((self.IP, 'no ethernet-service svlan 1002'), self.fake.commands)
        self.assertIsNone(self.backbone.read_service(1002))
        self.assertTrue(self.backbone.read_service(1001).is_complete('blab_1001', ['1/1/1']))
        self.assertTrue(self.backbone.read_service(1003).is_complete('blab_1003', ['1/1/3']))


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
class SwitchBannerTest(TestCase):
    """Switch.changeBanner goes through the LabSwitch seam and reports failures."""

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
    """release() against fake backbones and lab switches: teardown, then a Cleanup asked of the Switch worker."""

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

    def assert_released(self, result):
        self.assertTrue(result.released)
        self.assertTrue(result.cleanup_requested)
        self.assertEqual(result.failures, [])
        self.assertFalse(Reservation.objects.filter(switch=self.switch).exists())
        self.assertEqual(self.backbone.config['10.0.0.100'], [])
        self.assertFalse(Port.objects.filter(svlan__isnull=False).exists())
        # The Cleanup is the Switch worker's: nothing reaches the switch within the Release
        self.assertEqual(self.switches.commands, [])
        self.assertEqual(PendingCleanup.objects.get().switch, self.switch)

    def test_a_release_asks_for_a_cleanup_naming_the_holder(self):
        self.assert_released(release(self.reservation, self.alice))
        self.assertEqual(PendingCleanup.objects.get().holder, self.alice)
        event = self.switch.events.get(kind=SwitchEvent.RELEASE)
        self.assertEqual((event.user, event.reasons), (self.alice, ['released by alice']))

    def test_expiry_asks_for_a_cleanup_too(self):
        self.assert_released(expire(self.reservation))
        self.assertEqual(PendingCleanup.objects.get().holder, self.alice)
        self.assertEqual(self.switch.events.get(kind=SwitchEvent.RELEASE).reasons, ['expired'])

    def test_a_reservation_that_already_ended_is_left_alone(self):
        stale = Reservation.objects.get(pk=self.reservation.pk)
        self.reservation.delete()
        Reservation.objects.create(switch=self.switch, user=self.bob)  # someone else's now
        with self.assertRaises(AlreadyReleased):
            expire(stale)
        self.assertNotEqual(self.backbone.config['10.0.0.100'], [])
        self.assertFalse(PendingCleanup.objects.exists())

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
        self.assertFalse(PendingCleanup.objects.exists())

    def test_a_user_the_topology_is_shared_with_may_release(self):
        TopologyShare.objects.create(owner=self.alice, target=self.bob)
        self.assertTrue(may_release(self.bob, self.reservation))
        self.assert_released(release(self.reservation, self.bob))
        # The Quarantine, if any, names the holder, not whoever released
        self.assertEqual(PendingCleanup.objects.get().holder, self.alice)

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

    def test_release_always_cleans_up(self):
        self.reserve()
        # An older UI asking for no Cleanup gets one anyway
        response = self.post_release(self.alice, cleanup=False)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn('Cleaning the Switch up', response.data['detail'])
        self.assertFalse(Reservation.objects.exists())
        self.assertTrue(PendingCleanup.objects.filter(switch=self.switch, holder=self.alice).exists())

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
        self.assertEqual(list(PendingCleanup.objects.values_list('switch', 'holder')), [(self.switch.id, self.alice.id)])

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
        self.assertFalse(PendingCleanup.objects.exists())


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


class ParseServicesTest(SimpleTestCase):
    """Reading every Service, the disabled ports, and what the parser doesn't understand."""

    def test_every_service_and_the_lines_not_understood(self):
        snapshot = """
ethernet-service svlan 1001-1002 admin-state enable
ethernet-service svlan 1099 nni port 1/1/24
ethernet-service service-name "alice_1001" svlan 1001
ethernet-service sap 1001 service-name "alice_1001"
ethernet-service sap 1001 uni port 1/1/1-2
ethernet-service sap 1001 cvlan all
ethernet-service sap 1001 something-new
ethernet-service uni-profile default
"""
        services, unreadable = parse_services(snapshot)
        self.assertEqual(sorted(services), [1001, 1002, 1099])
        self.assertTrue(services[1001].carries(['1/1/1', '1/1/2']))
        self.assertEqual(services[1002], Service(svlan=1002, svlan_configured=True))
        self.assertTrue(services[1099].nni)
        self.assertEqual(unreadable, ['ethernet-service sap 1001 something-new',
                                      'ethernet-service uni-profile default'])

    def test_disabled_ports_as_the_device_prints_them(self):
        snapshot = """
! Interface:
interfaces port 1/5/20 alias "BLAB_MANAGEMENT_INTERFACE"
interfaces port 1/1/2 admin-state disable
interfaces port 1/1/4-5 admin-state disable
interfaces 1/1/9 admin-state disable
"""
        self.assertEqual(parse_disabled_ports(snapshot), {'1/1/2', '1/1/4', '1/1/5', '1/1/9'})

    def test_carries_ignores_the_name_but_not_the_unis(self):
        service = Service(1001, svlan_configured=True, name='alice_1001', sap=True,
                          unis=('1/1/1', '1/1/2'), cvlan_all=True)
        self.assertTrue(service.carries(['1/1/2', '1/1/1']))
        self.assertFalse(service.carries(['1/1/1']))
        self.assertFalse(Service(1001, svlan_configured=True, sap=True, unis=('1/1/1',),
                                 cvlan_all=True).carries(['1/1/1']))


@override_settings(BLAB_DEVICES='fake')
class ReconcileTest(TestCase):
    """Drift between the database and the fake backbones, and what repair does about it."""

    BB1, BB2 = '10.0.0.100', '10.0.0.200'

    def setUp(self):
        self.fake = fake_devices.backbone
        self.fake.reset()
        self.switches = make_switches(2)
        self.a = self.port(0, '1/1/1')
        self.b = self.port(1, '1/1/2')

    def port(self, switch, uni, backbone=BB1):
        port = Port.objects.create(switch=self.switches[switch], port_switch=uni,
                                   backbone=backbone, port_backbone=uni)
        self.fake.cli(backbone, f'interfaces {uni} admin-state disable')  # as an unlinked UNI is
        return port

    def drifts(self, kind=None):
        return [d for d in reconcile() if kind is None or isinstance(d, kind)]

    def record_link(self, svlan, *ports):
        Port.objects.filter(id__in=[p.id for p in ports]).update(svlan=svlan, status='UP')
        for p in ports:
            self.fake.cli(p.backbone, f'interfaces {p.port_backbone} admin-state enable')

    def test_links_made_by_blab_leave_no_drift(self):
        c, d = self.port(0, '1/1/7', self.BB1), self.port(1, '1/1/8', self.BB2)
        links.connect(self.a, self.b)
        link = links.connect(c, d)
        self.assertEqual(self.drifts(), [])

        links.disconnect(link)
        self.assertEqual(self.drifts(), [])

    def test_a_link_built_by_the_old_code_with_a_user_name_is_no_drift(self):
        Backbone(self.BB1, self.fake.cli).configure_service(1001, 'jkabali_1001', ['1/1/1', '1/1/2'])
        self.record_link(1001, self.a, self.b)
        self.assertEqual(self.drifts(), [])

    def test_orphans_are_reported_but_a_trunk_svlan_is_not(self):
        self.fake.cli(self.BB1, 'ethernet-service svlan 1003 admin-state enable')
        Backbone(self.BB1, self.fake.cli).configure_service(1005, 'bob_1005', ['1/3/1'])
        self.fake.cli(self.BB1, 'ethernet-service svlan 1010 nni port 1/1/24')

        orphans = self.drifts(Orphan)
        self.assertEqual([o.service.svlan for o in orphans], [1003, 1005])
        self.assertIn('SVLAN only, no Service', str(orphans[0]))
        self.assertIn('bob_1005', str(orphans[1]))

    def test_a_ghost_link_is_reported_and_repair_builds_it_again(self):
        self.record_link(1001, self.a, self.b)
        [ghost] = self.drifts()
        self.assertIsInstance(ghost, GhostLink)
        self.assertIn('no Service on the backbone', str(ghost))

        outcomes = repair([ghost])
        self.assertEqual(len(outcomes), 1)
        self.assertTrue(outcomes[0].startswith('Restored'), outcomes)
        self.assertTrue(backbone(self.BB1).read_service(1001).is_complete('blab_1001', ['1/1/1', '1/1/2']))
        self.assertEqual(self.drifts(), [])

    def test_repair_completes_a_half_built_service_under_its_own_name(self):
        Backbone(self.BB1, self.fake.cli).configure_service(1001, 'alice_1001', ['1/1/1', '1/1/2'])
        self.fake.cli(self.BB1, 'no ethernet-service sap 1001 cvlan all')
        self.record_link(1001, self.a, self.b)
        self.assertIn('incomplete Service', str(self.drifts(GhostLink)[0]))

        sent = len(self.fake.commands)
        repair(self.drifts())
        configured = [c for _, c in self.fake.commands[sent:] if c.startswith('ethernet-service')]
        self.assertEqual(configured, ['ethernet-service sap 1001 cvlan all'])
        self.assertEqual(self.drifts(), [])

    def test_a_link_with_a_disabled_uni_is_a_ghost_and_repair_enables_it(self):
        links.connect(self.a, self.b)
        self.fake.cli(self.BB1, 'interfaces 1/1/2 admin-state disable')
        drifts = self.drifts()
        self.assertEqual({type(d) for d in drifts}, {GhostLink, StatusDrift})
        self.assertIn('UNIs disabled: 1/1/2', str(self.drifts(GhostLink)[0]))

        repair(drifts)
        self.assertEqual(self.drifts(), [])
        self.b.refresh_from_db()
        self.assertEqual(self.b.status, 'UP')

    def test_repair_never_touches_a_service_carrying_other_unis(self):
        Backbone(self.BB1, self.fake.cli).configure_service(1001, 'bob_1001', ['1/1/1', '1/3/9'])
        self.record_link(1001, self.a, self.b)
        sent = len(self.fake.commands)

        [outcome] = repair(self.drifts())
        self.assertIn('other UNIs on SVLAN 1001: 1/3/9', outcome)
        self.assertEqual([c for _, c in self.fake.commands[sent:] if not c.startswith('show')], [])

    def test_status_drift_is_reported_and_repair_records_the_real_state(self):
        Port.objects.filter(id=self.a.id).update(status='UP')
        [drift] = self.drifts()
        self.assertEqual((drift.port.id, drift.actual), (self.a.id, 'DOWN'))

        repair([drift])
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, 'DOWN')

    def test_an_svlan_held_by_three_ports_is_reported_and_never_built(self):
        c = self.port(0, '1/1/3')
        links.connect(self.a, self.b)
        Port.objects.filter(id=c.id).update(svlan=1001)
        self.assertEqual([d.link.svlan for d in self.drifts(NotALink)], [1001])

        repair(self.drifts())
        self.assertEqual(backbone(self.BB1).read_service(1001).unis, ('1/1/1', '1/1/2'))

    def test_a_ghost_link_across_two_backbones_is_built_where_it_is_missing(self):
        c, d = self.port(0, '1/1/7', self.BB1), self.port(1, '1/1/8', self.BB2)
        links.connect(c, d)
        backbone(self.BB2).remove_service(1001)
        [ghost] = self.drifts()
        self.assertEqual(ghost.backbone, self.BB2)

        sent = len(self.fake.commands)
        repair([ghost])
        self.assertFalse([cmd for ip, cmd in self.fake.commands[sent:]
                          if ip == self.BB1 and cmd.startswith('ethernet-service')])
        self.assertEqual(self.drifts(), [])

    def test_a_failed_repair_still_records_the_real_uni_states(self):
        Backbone(self.BB1, self.fake.cli).configure_service(1001, 'bob_1001', ['1/1/1', '1/3/9'])
        self.record_link(1001, self.a, self.b)
        Port.objects.filter(id=self.a.id).update(status='DOWN')

        repair(self.drifts())
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, 'UP')

    def test_lines_not_understood_are_reported(self):
        self.fake.config[self.BB1] = ['ethernet-service sap 1001 something-new']
        self.assertEqual([d.line for d in self.drifts(Unreadable)], ['ethernet-service sap 1001 something-new'])

    def test_an_unreachable_backbone_is_reported_and_the_others_still_read(self):
        self.port(0, '1/1/7', self.BB2)
        self.fake.fail_on('show', ip=self.BB1)
        self.fake.cli(self.BB2, 'ethernet-service svlan 1003 admin-state enable')
        self.assertEqual({type(d) for d in self.drifts()}, {Unreachable, Orphan})

    def test_reconcile_only_reads(self):
        self.fake.cli(self.BB1, 'ethernet-service svlan 1003 admin-state enable')
        self.record_link(1001, self.a, self.b)
        Port.objects.filter(id=self.a.id).update(status='DOWN')
        sent = len(self.fake.commands)
        self.assertTrue(self.drifts())
        self.assertTrue(all(c.startswith('show') for _, c in self.fake.commands[sent:]))

    def test_audit_links_command_reports_and_repairs_on_request(self):
        self.record_link(1001, self.a, self.b)
        out = StringIO()
        call_command('audit_links', stdout=out)
        self.assertIn('ghost Link SVLAN 1001', out.getvalue())
        self.assertIsNone(backbone(self.BB1).read_service(1001))

        call_command('audit_links', '--repair', stdout=StringIO())
        out = StringIO()
        call_command('audit_links', stdout=out)
        self.assertIn('No drift', out.getvalue())


@override_settings(BLAB_DEVICES='fake')
class LinkWorkerTest(TestCase):
    """Asynchronous disconnect, Orphan cleanup and UNI states, through the link worker and the fake."""

    BB = '10.0.0.100'

    def setUp(self):
        self.fake = fake_devices.backbone
        self.fake.reset()
        self.alice = User.objects.create_user('alice', password='pw')
        self.client = APIClient()
        self.client.force_authenticate(self.alice)
        self.switches = make_switches(2)
        for switch in self.switches:
            Reservation.objects.create(switch=switch, user=self.alice)
        self.a = self.port(0, '1/1/1')
        self.b = self.port(1, '1/1/2')
        self.now = 1000.0
        self.worker = LinkWorker(clock=lambda: self.now)

    def port(self, switch, uni):
        self.fake.cli(self.BB, f'interfaces {uni} admin-state disable')  # as an unlinked UNI is
        return Port.objects.create(switch=self.switches[switch], port_switch=uni, backbone=self.BB, port_backbone=uni)

    def disconnect(self):
        response = self.client.post('/api/disconnect/', {'portA': self.a.id, 'portB': self.b.id}, format='json')
        self.assertEqual(response.status_code, 202, response.data)

    def topology_links(self):
        return self.client.get(f'/api/topology/{self.alice.id}/').data['links']

    def test_a_disconnect_returns_at_once_and_the_worker_tears_the_link_down(self):
        links.connect(self.a, self.b)
        self.disconnect()
        self.assertTrue(self.fake.config[self.BB], 'nothing is torn down by the request itself')
        self.assertEqual(self.topology_links(), [])

        self.assertEqual(len(self.worker.tear_down_requested()), 1)
        self.assertEqual(self.fake.config[self.BB], [])
        self.a.refresh_from_db()
        self.assertEqual((self.a.svlan, self.a.teardown_requested_at, self.a.teardown_error), (None, None, None))

    def test_a_teardown_that_keeps_failing_shows_again_with_its_reason_and_is_retried(self):
        links.connect(self.a, self.b)
        self.disconnect()
        self.fake.fail_on('no ethernet-service', times=2)
        [outcome] = self.worker.tear_down_requested()
        self.assertIn('trying again in 10s', outcome)
        self.assertEqual(self.topology_links(), [], 'one failure is not shown yet')

        self.now += 5
        self.assertEqual(self.worker.tear_down_requested(), [])  # not due yet
        self.now += 6
        self.assertIn('trying again in 30s', self.worker.tear_down_requested()[0])
        [shown] = self.topology_links()
        self.assertIn('Injected failure', shown['teardown_error'])

        self.now += 31
        self.assertTrue(self.worker.tear_down_requested()[0].startswith('Tore down'))
        self.assertEqual(self.topology_links(), [])

    def test_asking_again_after_a_failure_tries_again_at_once(self):
        links.connect(self.a, self.b)
        self.disconnect()
        self.fake.fail_on('no ethernet-service', times=2)
        self.worker.tear_down_requested()
        self.now += 10
        self.worker.tear_down_requested()
        self.assertTrue(self.topology_links()[0]['teardown_error'])

        self.now += 0.1
        self.disconnect()
        self.assertEqual(self.topology_links(), [], 'hidden again while being disconnected')
        self.assertTrue(self.worker.tear_down_requested()[0].startswith('Tore down'))

    def test_ports_being_disconnected_cannot_be_connected_yet(self):
        links.connect(self.a, self.b)
        self.disconnect()
        response = self.client.post('/api/connect/', {'portA': self.a.id, 'portB': self.b.id}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('still being disconnected', response.data['detail'])

    def test_a_release_tears_down_a_link_being_disconnected_at_once(self):
        links.connect(self.a, self.b)
        self.disconnect()
        response = self.client.post('/api/release/', {'switch': self.switches[0].id}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.b.refresh_from_db()
        self.assertEqual((self.b.svlan, self.b.teardown_requested_at), (None, None))
        self.assertEqual(self.worker.tear_down_requested(), [])

    def test_blabs_own_orphans_are_removed_once_seen_twice(self):
        self.fake.cli(self.BB, 'ethernet-service svlan 1003 admin-state enable')
        Backbone(self.BB, self.fake.cli).configure_service(1005, 'alice_1005', ['1/3/1'])
        Backbone(self.BB, self.fake.cli).configure_service(1006, 'blab_1006', ['1/3/2'])

        self.assertEqual(self.worker.reconcile(), [])
        self.assertEqual(len(self.fake.config[self.BB]), 11)
        outcomes = self.worker.reconcile()
        self.assertEqual(len(outcomes), 3, outcomes)
        self.assertEqual(self.fake.config[self.BB], [])

    def test_an_orphan_not_named_by_blab_is_left_alone(self):
        Backbone(self.BB, self.fake.cli).configure_service(1005, 'lab_trunk_test', ['1/3/1'])
        Backbone(self.BB, self.fake.cli).configure_service(1006, 'mallory_1006', ['1/3/2'])  # no such user
        self.worker.reconcile()
        self.assertEqual(self.worker.reconcile(), [])
        self.assertEqual(sorted(backbone(self.BB).read_services()[0]), [1005, 1006])

    def test_an_orphan_recorded_or_changed_in_between_is_left_alone(self):
        self.fake.cli(self.BB, 'ethernet-service svlan 1001 admin-state enable')
        self.fake.cli(self.BB, 'ethernet-service svlan 1002 admin-state enable')
        self.worker.reconcile()
        self.assertEqual(len(self.worker.suspects), 2)
        orphans = {o.service.svlan: o for o in reconcile() if isinstance(o, Orphan)}

        Port.objects.filter(id__in=[self.a.id, self.b.id]).update(svlan=1001)
        self.assertIn('recorded again', remove_orphan(orphans[1001]))
        self.fake.cli(self.BB, 'ethernet-service service-name blab_1002 svlan 1002')
        self.assertIn('changed since', remove_orphan(orphans[1002]))
        self.assertEqual(sorted(backbone(self.BB).read_services()[0]), [1001, 1002])

    def test_uni_states_are_recorded_once_seen_twice(self):
        Port.objects.filter(id=self.a.id).update(status='UP')
        self.assertEqual(self.worker.reconcile(), [])
        [outcome] = self.worker.reconcile()
        self.assertIn('as DOWN', outcome)
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, 'DOWN')

    def test_ghost_links_are_left_to_an_administrator(self):
        Port.objects.filter(id__in=[self.a.id, self.b.id]).update(svlan=1001, status='DOWN')
        self.worker.reconcile()
        with self.assertLogs('api.link_worker', 'WARNING') as logs:
            self.worker.reconcile()
        self.assertIn('ghost Link', '\n'.join(logs.output))
        self.assertIsNone(backbone(self.BB).read_service(1001))

    def test_a_backbone_failing_while_removing_an_orphan_does_not_stop_the_rest(self):
        self.fake.cli(self.BB, 'ethernet-service svlan 1003 admin-state enable')
        Port.objects.filter(id=self.a.id).update(status='UP')
        self.worker.reconcile()
        self.fake.fail_on('no ethernet-service svlan 1003')
        outcomes = self.worker.reconcile()
        self.assertEqual(len(outcomes), 2, outcomes)
        self.assertTrue(any(o.startswith('Could not act') for o in outcomes))
        self.a.refresh_from_db()
        self.assertEqual(self.a.status, 'DOWN')

    def test_blab_names(self):
        users = {'alice'}
        self.assertTrue(made_by_blab(Service(1003, svlan_configured=True), users))
        self.assertTrue(made_by_blab(Service(1003, name='blab_1003'), users))
        self.assertTrue(made_by_blab(Service(1003, name='alice_1003'), users))
        self.assertFalse(made_by_blab(Service(1003, name='alice_1004'), users))
        self.assertFalse(made_by_blab(Service(1003, name='bob_1003'), users))
        self.assertFalse(made_by_blab(Service(1003, name='trunk'), users))

    def test_a_request_left_behind_by_older_code_is_ignored(self):
        # Production's code before this change unlinks without knowing about teardown requests
        links.connect(self.a, self.b)
        self.disconnect()
        Port.objects.filter(id__in=[self.a.id, self.b.id]).update(svlan=None)
        self.fake.reset()

        relinked = links.connect(self.a, self.b)  # not refused as "still being disconnected"
        Port.objects.filter(id__in=[self.a.id, self.b.id]).update(svlan=relinked.svlan + 4)  # and relinks elsewhere
        self.assertEqual(self.worker.tear_down_requested(), [])
        self.assertEqual([link['svlan'] for link in self.topology_links()], [relinked.svlan + 4])

    def test_a_teardown_finding_its_link_released_leaves_the_new_link_on_that_svlan(self):
        links.connect(self.a, self.b)
        self.disconnect()
        [requested] = links.requested_teardowns()
        response = self.client.post('/api/release/', {'switch': self.switches[0].id}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        c, d = self.port(1, '1/1/3'), self.port(1, '1/1/4')
        self.assertEqual(links.connect(c, d).svlan, requested.svlan)

        links.disconnect(requested)  # what the worker was about to do
        self.assertTrue(backbone(self.BB).read_service(requested.svlan).is_complete(
            f'blab_{requested.svlan}', ['1/1/3', '1/1/4']))
        c.refresh_from_db()
        self.assertEqual(c.svlan, requested.svlan)

    @skipUnless(connection.vendor == 'postgresql', 'the lock is a Postgres advisory lock')
    def test_only_one_session_is_the_worker(self):
        self.assertTrue(holds_worker_lock())
        other = connections.create_connection('default')
        try:
            with other.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", [WORKER_LOCK])
                self.assertFalse(cursor.fetchone()[0])
        finally:
            other.close()

    def test_without_postgres_this_process_is_the_worker(self):
        self.assertTrue(holds_worker_lock())

    def test_the_command_runs_once(self):
        links.connect(self.a, self.b)
        self.disconnect()
        out = StringIO()
        call_command('link_worker', '--once', stdout=out)
        self.assertIn('Tore down SVLAN 1001', out.getvalue())


class HttpsCliTest(SimpleTestCase):
    """The HTTPS CLI transport against answers recorded from 10.69.144.130."""

    IP = '10.0.0.100'
    LOGIN = {'domain': 'auth (login)', 'diag': 200, 'error': '', 'output': '', 'data': []}
    EXPIRED = {'domain': 'cli', 'diag': 401, 'output': '', 'error': 'no such session - expired?', 'data': []}
    VLAN = '! VLAN:\nethernet-service svlan 1001 admin-state enable'

    class Answer:
        def __init__(self, result, cookie='wv_sess=new; path=/'):
            self.status_code = 200
            self.headers = {'Set-Cookie': cookie}
            self.result = result

        def json(self):
            return {'result': self.result}

        def raise_for_status(self):
            pass

    def cli_answering(self, *results):
        answers = iter(self.Answer(r) for r in results)
        session = type('Session', (), {})()
        session.calls = []
        session.cookies = RequestsCookieJar()

        def get(url, **kwargs):
            session.calls.append(url)
            return next(answers)
        session.get = get
        backbone_module.COOKIE_CACHE[self.IP] = 'old'
        self.addCleanup(backbone_module.COOKIE_CACHE.pop, self.IP, None)
        patcher = patch.object(backbone_module, '_session', return_value=session)
        patcher.start()
        self.addCleanup(patcher.stop)
        return session

    def cli(self, result):
        return {'domain': 'cli', 'diag': 200, 'output': result, 'error': '', 'data': []}

    def test_an_expired_session_logs_in_again_instead_of_answering_nothing(self):
        session = self.cli_answering(self.EXPIRED, self.LOGIN, self.cli(self.VLAN))
        self.assertEqual(backbone_module.https_cli(self.IP, 'show configuration snapshot vlan'), self.VLAN)
        self.assertIn('domain=auth', session.calls[1])

    def test_must_login_first_logs_in_again(self):
        self.cli_answering({'domain': 'cli', 'diag': 401, 'output': '', 'error': 'You must login first', 'data': []},
                           self.LOGIN, self.cli(self.VLAN))
        self.assertEqual(backbone_module.https_cli(self.IP, 'show configuration snapshot vlan'), self.VLAN)

    def test_a_command_the_device_refuses_is_a_failure(self):
        self.cli_answering({'domain': 'cli', 'diag': 400, 'output': '', 'error': 'ERROR: Invalid entity', 'data': []})
        with self.assertRaisesRegex(APIRequestError, 'Invalid entity'):
            backbone_module.https_cli(self.IP, 'ethernet-service svlan 1001 admin-state enable')

    def test_a_session_refused_again_after_logging_in_is_a_failure_not_an_empty_answer(self):
        session = self.cli_answering(self.EXPIRED, self.LOGIN, self.EXPIRED)
        with self.assertRaisesRegex(APIRequestError, 'after logging in again: no such session'):
            backbone_module.https_cli(self.IP, 'show configuration snapshot vlan')
        self.assertEqual(len(session.calls), 3)  # logged in once only

    def test_a_refused_admin_state_change_already_made_is_no_failure(self):
        answers = {'interfaces 1/1/1 admin-state enable': APIRequestError('already enabled'),
                   'interfaces 1/1/2 admin-state enable': APIRequestError('refused'),
                   'show configuration snapshot interface': '! Interface:\ninterfaces port 1/1/2 admin-state disable'}

        def cli(ip, cmd):
            if isinstance(answers[cmd], Exception):
                raise answers[cmd]
            return answers[cmd]
        bb = Backbone(self.IP, cli)
        bb.set_uni_admin_state('1/1/1', True)  # enabled already: fine
        with self.assertRaisesRegex(APIRequestError, 'refused'):
            bb.set_uni_admin_state('1/1/2', True)  # still disabled: a real failure

    def test_a_snapshot_without_its_section_is_a_failure(self):
        bb = Backbone(self.IP, lambda ip, cmd: '')
        with self.assertRaisesRegex(APIRequestError, "without its '! VLAN:' section"):
            bb.read_service(1001)
        with self.assertRaises(APIRequestError):
            bb.disabled_unis()


class UserEndpointsTest(TestCase):
    """Users are exposed by id and username only, never as whole rows."""

    def setUp(self):
        self.alice = User.objects.create_user('alice', email='alice@example.com', password='pw')
        self.bob = User.objects.create_user('bob', email='bob@example.com', password='pw', is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(self.alice)

    def test_list_user_returns_only_id_and_username(self):
        response = self.client.get('/api/list_user/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sorted(response.data['users'], key=lambda u: u['id']),
                         [{'id': self.alice.id, 'username': 'alice'}, {'id': self.bob.id, 'username': 'bob'}])

    def test_list_user_by_id_returns_only_id_and_username(self):
        response = self.client.get(f'/api/list_user/{self.bob.id}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'id': self.bob.id, 'username': 'bob'})

    def test_login_returns_only_id_and_username_for_the_user(self):
        response = APIClient().post('/api/login/', {'username': 'bob', 'password': 'pw'}, format='json')
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data['user'], {'id': self.bob.id, 'username': 'bob'})
        self.assertTrue(response.data['is_staff'])

    def test_signup_returns_only_id_and_username_and_sets_the_password(self):
        response = APIClient().post('/api/signup/', {'username': 'carol', 'password': 'Secret123'}, format='json')
        self.assertEqual(response.status_code, 201)
        carol = User.objects.get(username='carol')
        self.assertEqual(response.data['user'], {'id': carol.id, 'username': 'carol'})
        self.assertTrue(carol.check_password('Secret123'))

    def test_signup_cannot_make_an_admin(self):
        response = APIClient().post('/api/signup/', {'username': 'mallory', 'password': 'Secret123',
                                                     'is_staff': True, 'is_superuser': True}, format='json')
        self.assertEqual(response.status_code, 201)
        mallory = User.objects.get(username='mallory')
        self.assertFalse(mallory.is_staff)
        self.assertFalse(mallory.is_superuser)

    def test_signup_without_a_password_is_rejected(self):
        response = APIClient().post('/api/signup/', {'username': 'dave'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(username='dave').exists())
