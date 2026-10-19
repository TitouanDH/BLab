"""
Switch details (#36): a Switch's management IP, console and hardware go only to the users who
may work on it (its holder, and whoever the holder shares their Topology with) and to admins.
Everyone else gets the Switch by model and id, and BLab's texts name it by its label.
"""
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from unittest import mock

from django.utils import timezone
from rest_framework.test import APIClient

from . import fake_devices
from .models import Port, Quarantine, Reservation, Sweep, SwitchEvent, TopologyShare
from .switch_details import DETAIL_FIELDS, Viewer
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
        # alice holds the first; carol the second; the third is free
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

    def assert_details(self, switch, shown):
        if shown:  # the IP at least; list_switch and the Topology give the others too
            self.assertIn('mngt_IP', switch)
        else:
            for field in DETAIL_FIELDS:
                self.assertNotIn(field, switch)
        self.assertIn('model', switch)
        self.assertIn('id', switch)

    def assert_seen_by(self, rows, expected):
        """rows: {switch id: serialized Switch}; expected: {switch: whether its details are shown}."""
        for switch, shown in expected.items():
            self.assert_details(rows[switch.id], shown)

    # list_switch

    def list_switch(self, user):
        return {s['id']: s for s in self.get(user, '/api/list_switch/')['switchs']}

    def test_list_switch_gives_all_the_details_to_whoever_may_see_them(self):
        held = self.list_switch(self.holder)[self.held.id]
        self.assertEqual({field: held[field] for field in DETAIL_FIELDS},
                         {'mngt_IP': '10.0.0.1', 'console': 'TODO', 'part_number': 'pn', 'hardware_revision': 'A',
                          'serial_number': 'sn1'})

    def test_list_switch_shows_details_to_the_holder_only_for_her_switch(self):
        self.assert_seen_by(self.list_switch(self.holder), {self.held: True, self.carols: False, self.free: False})

    def test_list_switch_shows_details_to_a_user_the_topology_is_shared_with(self):
        self.assert_seen_by(self.list_switch(self.shared), {self.held: True, self.carols: False, self.free: False})

    def test_list_switch_hides_details_from_a_stranger(self):
        self.assert_seen_by(self.list_switch(self.stranger), {self.held: False, self.carols: True, self.free: False})

    def test_list_switch_shows_every_switch_s_details_to_an_admin(self):
        self.assert_seen_by(self.list_switch(self.admin), {self.held: True, self.carols: True, self.free: True})

    def test_a_hidden_ip_is_left_out_of_why_a_switch_cant_be_reserved(self):
        Quarantine.objects.create(switch=self.free, reasons=[f'cannot log in to {self.free.mngt_IP}'])
        unavailable = self.list_switch(self.holder)[self.free.id]['unavailable']
        self.assertNotIn(self.free.mngt_IP, unavailable['reason'])
        self.assertIn(f'OS6860 #{self.free.id}', unavailable['reason'])
        self.assertIn(self.free.mngt_IP, self.list_switch(self.admin)[self.free.id]['unavailable']['reason'])

    # lab_status

    def lab_status(self, user):
        return self.get(user, '/api/lab_status/')

    def test_lab_status_shows_details_by_the_same_rule(self):
        for user, expected in [(self.holder, {self.held: True, self.carols: False, self.free: False}),
                               (self.shared, {self.held: True, self.carols: False, self.free: False}),
                               (self.stranger, {self.held: False, self.carols: True, self.free: False}),
                               (self.admin, {self.held: True, self.carols: True, self.free: True})]:
            with self.subTest(user=user.username):
                self.assert_seen_by({s['id']: s for s in self.lab_status(user)['switches']}, expected)

    def test_lab_status_texts_name_a_hidden_switch_by_its_label(self):
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
            self.assertNotIn(ip, text)
            self.assertIn(f'OS6860 #{self.free.id}', text)
        admin_row = next(s for s in self.lab_status(self.admin)['switches'] if s['id'] == self.free.id)
        self.assertIn(ip, admin_row['quarantine']['reasons'][0])

    def test_a_redacted_ip_does_not_eat_a_longer_one(self):
        # 10.0.0.1 is hidden from carol; 10.0.0.12 would be another Switch, and stays as it is
        self.assertEqual(Viewer(self.stranger).redact('10.0.0.1 and 10.0.0.12, then 10.0.0.1.'),
                         f'OS6860 #{self.held.id} and 10.0.0.12, then OS6860 #{self.held.id}.')

    # topology

    def topology(self, user, owner):
        return {s['id']: s for s in self.get(user, f'/api/topology/{owner.id}/')['switches']}

    def link(self, a, b, svlan=100):
        Port.objects.create(switch=a, port_switch='1/1/1', backbone='10.0.0.100', port_backbone='1/1/10', svlan=svlan)
        Port.objects.create(switch=b, port_switch='1/1/1', backbone='10.0.0.100', port_backbone='1/1/11', svlan=svlan)

    def test_topology_shows_own_switches_details_and_hides_a_far_switch_s(self):
        self.link(self.held, self.carols)  # a Link from alice's Switch to carol's
        for user in (self.holder, self.shared):
            with self.subTest(user=user.username):
                self.assert_seen_by(self.topology(user, self.holder), {self.held: True, self.carols: False})
        self.assert_seen_by(self.topology(self.stranger, self.stranger), {self.carols: True, self.held: False})

    def test_a_stranger_cannot_read_the_holder_s_topology_at_all(self):
        client = APIClient()
        client.force_authenticate(self.stranger)
        self.assertEqual(client.get(f'/api/topology/{self.holder.id}/').status_code, 403)

    def test_an_admin_sees_the_far_switch_s_details_too(self):
        self.link(self.held, self.carols)
        TopologyShare.objects.create(owner=self.holder, target=self.admin)
        self.assert_seen_by(self.topology(self.admin, self.holder), {self.held: True, self.carols: True})

    def test_a_ghost_reason_names_a_hidden_far_switch_by_its_label(self):
        self.link(self.held, self.carols)
        Port.objects.filter(switch=self.held).update(
            ghost_svlan=100, ghost_seen_at=timezone.now(),
            ghost_reason=f'The backbone port facing 1/1/1 on OS6860 ({self.carols.mngt_IP}) is disabled.')
        links = self.get(self.holder, f'/api/topology/{self.holder.id}/')['links']
        self.assertEqual(len(links), 1)
        self.assertNotIn(self.carols.mngt_IP, links[0]['ghost_reason'])
        self.assertIn(f'OS6860 #{self.carols.id}', links[0]['ghost_reason'])

    # reserve

    def test_a_refused_reservation_names_the_quarantined_switch_by_its_label(self):
        Quarantine.objects.create(switch=self.free, holder=self.shared,
                                  reasons=[f'{self.free.mngt_IP}: unwanted cable'])
        client = APIClient()
        client.force_authenticate(self.shared)
        other = make_switches(4)[3]  # a free Switch bob may try to reserve (10.0.0.4)
        response = client.post('/api/reserve/', {'switch': other.id,
                                                 'end_date': (timezone.now() + timezone.timedelta(days=2)).isoformat()},
                               format='json')
        self.assertEqual(response.status_code, 403, response.data)
        self.assertNotIn(self.free.mngt_IP, response.data['detail'])
        self.assertIn(f'OS6860 #{self.free.id}', response.data['detail'])

    # recheck: anyone may ask, so its reasons are redacted too

    def recheck(self, user, switch):
        event = SwitchEvent(switch=switch, kind=SwitchEvent.INSPECTION, ok=False,
                            reasons=[f'unreachable: Cannot connect to {switch.mngt_IP}'])
        client = APIClient()
        client.force_authenticate(user)
        with mock.patch('api.views.quarantine.recheck', return_value=event):
            response = client.post('/api/recheck/', {'switch': switch.id}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_a_recheck_names_a_switch_by_its_label_unless_the_caller_may_see_its_ip(self):
        for user, shown in [(self.holder, True), (self.shared, True), (self.stranger, False), (self.admin, True)]:
            with self.subTest(user=user.username):
                data = self.recheck(user, self.held)
                for text in (data['detail'], *data['reasons']):
                    self.assertEqual(self.held.mngt_IP in text, shown, text)
                    self.assertEqual(f'OS6860 #{self.held.id}' in text, not shown, text)
