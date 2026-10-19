"""
Switch details (#75): every user sees every Switch's management IP, console and hardware, and
BLab's texts name Switches by their IP as written. #36 kept them for the users who may work on
a Switch, while anyone could log in as admin/switch; the admin password is secret now (ADR 0004).
"""
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from unittest import mock

from django.utils import timezone
from rest_framework.test import APIClient

from . import fake_devices
from .models import Port, Quarantine, Reservation, Sweep, SwitchEvent, TopologyShare
from .tests import make_switches


@override_settings(BLAB_DEVICES='fake')
class SwitchDetailsTest(TestCase):

    def setUp(self):
        fake_devices.backbone.reset()
        fake_devices.lab_switches.reset()
        self.holder = User.objects.create_user('alice', email='alice@example.com', password='pw')
        self.shared = User.objects.create_user('bob', email='bob@example.com', password='pw')
        self.stranger = User.objects.create_user('carol', email='carol@example.com', password='pw')
        self.admin = User.objects.create_user('dave', email='dave@example.com', password='pw', is_staff=True)
        # alice holds the first (shared with bob); carol the second; the third is free
        self.held, self.carols, self.free = make_switches(3)
        end_date = timezone.now() + timezone.timedelta(days=3)
        Reservation.objects.create(switch=self.held, user=self.holder, end_date=end_date)
        Reservation.objects.create(switch=self.carols, user=self.stranger, end_date=end_date)
        TopologyShare.objects.create(owner=self.holder, target=self.shared)

    def get(self, user, url):
        client = APIClient()
        client.force_authenticate(user)
        response = client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    DETAILS = {'mngt_IP', 'console', 'part_number', 'hardware_revision', 'serial_number'}

    def assert_every_detail(self, switch):
        self.assertLessEqual(self.DETAILS, set(switch), switch)

    # list_switch

    def list_switch(self, user):
        return {s['id']: s for s in self.get(user, '/api/list_switch/')['switchs']}

    def test_list_switch_gives_every_switch_s_details_to_everyone(self):
        for user in (self.holder, self.shared, self.stranger, self.admin):
            with self.subTest(user=user.username):
                rows = self.list_switch(user)
                for switch in (self.held, self.carols, self.free):
                    self.assert_every_detail(rows[switch.id])
        held = self.list_switch(self.stranger)[self.held.id]
        self.assertEqual({field: held[field] for field in self.DETAILS},
                         {'mngt_IP': '10.0.0.1', 'console': 'TODO', 'part_number': 'pn', 'hardware_revision': 'A',
                          'serial_number': 'sn1'})

    def test_why_a_switch_cant_be_reserved_keeps_its_ip(self):
        Quarantine.objects.create(switch=self.free, reasons=[f'cannot log in to {self.free.mngt_IP}'])
        self.assertIn(self.free.mngt_IP, self.list_switch(self.holder)[self.free.id]['unavailable']['reason'])

    # lab_status

    def lab_status(self, user):
        return self.get(user, '/api/lab_status/')

    def test_lab_status_shows_every_switch_s_ip_to_everyone(self):
        for user in (self.holder, self.stranger):
            with self.subTest(user=user.username):
                rows = {s['id']: s for s in self.lab_status(user)['switches']}
                self.assertEqual({rows[s.id]['mngt_IP'] for s in (self.held, self.carols, self.free)},
                                 {self.held.mngt_IP, self.carols.mngt_IP, self.free.mngt_IP})

    def test_lab_status_texts_keep_the_ip(self):
        ip = self.free.mngt_IP
        SwitchEvent.objects.create(switch=self.free, kind=SwitchEvent.INSPECTION, ok=False,
                                   reasons=[f'cannot log in to {ip}'], warnings=[f'{ip} slow'])
        Quarantine.objects.create(switch=self.free, reasons=[f'{ip}: unwanted cable on 1/1/5'])
        Sweep.objects.create(under_way=None, finished_at=timezone.now(), summary='1 problem(s).',
                             problems=[f'{ip} is in Quarantine (for an admin): unwanted cable'])
        data = self.lab_status(self.stranger)
        row = next(s for s in data['switches'] if s['id'] == self.free.id)
        texts = [*row['inspection']['reasons'], *row['inspection']['warnings'], *row['quarantine']['reasons'],
                 *row['history'][0]['reasons'], *data['sweep']['problems']]
        self.assertTrue(texts)
        for text in texts:
            self.assertIn(ip, text)

    # topology

    def topology(self, user, owner):
        return {s['id']: s for s in self.get(user, f'/api/topology/{owner.id}/')['switches']}

    def link(self, a, b, svlan=100):
        Port.objects.create(switch=a, port_switch='1/1/1', backbone='10.0.0.100', port_backbone='1/1/10', svlan=svlan)
        Port.objects.create(switch=b, port_switch='1/1/1', backbone='10.0.0.100', port_backbone='1/1/11', svlan=svlan)

    def test_topology_gives_a_far_switch_s_details_too(self):
        self.link(self.held, self.carols)  # a Link from alice's Switch to carol's
        rows = self.topology(self.holder, self.holder)
        self.assert_every_detail(rows[self.held.id])
        self.assert_every_detail(rows[self.carols.id])

    def test_a_stranger_cannot_read_the_holder_s_topology_at_all(self):
        client = APIClient()
        client.force_authenticate(self.stranger)
        self.assertEqual(client.get(f'/api/topology/{self.holder.id}/').status_code, 403)

    def test_a_ghost_reason_keeps_the_far_switch_s_ip(self):
        self.link(self.held, self.carols)
        Port.objects.filter(switch=self.held).update(
            ghost_svlan=100, ghost_seen_at=timezone.now(),
            ghost_reason=f'The backbone port facing 1/1/1 on OS6860 ({self.carols.mngt_IP}) is disabled.')
        links = self.get(self.holder, f'/api/topology/{self.holder.id}/')['links']
        self.assertEqual(len(links), 1)
        self.assertIn(self.carols.mngt_IP, links[0]['ghost_reason'])

    # reserve

    def test_a_refused_reservation_names_the_quarantined_switch_by_its_ip(self):
        Quarantine.objects.create(switch=self.free, holder=self.shared,
                                  reasons=[f'{self.free.mngt_IP}: unwanted cable'])
        client = APIClient()
        client.force_authenticate(self.shared)
        other = make_switches(4)[3]  # a free Switch bob may try to reserve (10.0.0.4)
        response = client.post('/api/reserve/', {'switch': other.id,
                                                 'end_date': (timezone.now() + timezone.timedelta(days=2)).isoformat()},
                               format='json')
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn(self.free.mngt_IP, response.data['detail'])

    # recheck: anyone may ask, and gets the reasons as written

    def recheck(self, user, switch):
        event = SwitchEvent(switch=switch, kind=SwitchEvent.INSPECTION, ok=False,
                            reasons=[f'unreachable: Cannot connect to {switch.mngt_IP}'])
        client = APIClient()
        client.force_authenticate(user)
        with mock.patch('api.views.quarantine.recheck', return_value=event):
            response = client.post('/api/recheck/', {'switch': switch.id}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_a_recheck_names_the_switch_by_its_ip_for_everyone(self):
        for user in (self.holder, self.stranger, self.admin):
            with self.subTest(user=user.username):
                data = self.recheck(user, self.held)
                for text in (data['detail'], *data['reasons']):
                    self.assertIn(self.held.mngt_IP, text)
