"""Contribute a cable (#58): BLab proposes a cable, checks it once plugged, and records the Port."""
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from . import fake_devices
from .models import PendingCleanup, PermanentCable, Port, Reservation
from .tests import make_switches

BB, BB2 = '10.0.0.100', '10.0.0.200'
URL = '/api/contribute/cable/'


@override_settings(BLAB_DEVICES='fake')
class ContributeCableTest(TestCase):
    IP = '10.0.0.1'

    def setUp(self):
        self.switches_fake = fake_devices.lab_switches
        self.switches_fake.reset()
        self.bb = fake_devices.backbone
        self.bb.reset()
        self.alice = User.objects.create_user('alice', email='alice@example.com', password='pw')
        self.bob = User.objects.create_user('bob', email='bob@example.com', password='pw')
        self.switch, self.other = make_switches(2)
        # At rest every UNI without a Link is disabled, and so is every unused Backbone port
        for uni in ('1/2/1', '1/2/2', '1/2/3'):
            self.bb.cli(BB, f'interfaces {uni} admin-state disable')
        Port.objects.create(switch=self.switch, port_switch='1/1/1', backbone=BB, port_backbone='1/2/1')
        self.client = APIClient()
        self.client.force_authenticate(self.alice)

    def proposal(self, **params):
        response = self.client.get(URL, {'switch': self.switch.id, **params})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['proposal']

    def plugged(self, switch_port='1/1/2', backbone_port='1/2/2', backbone=BB):
        return self.client.post(URL, {'switch': self.switch.id, 'switch_port': switch_port, 'backbone': backbone,
                                      'backbone_port': backbone_port}, format='json')

    def test_it_lists_the_switches_i_may_add_a_cable_to(self):
        Reservation.objects.create(switch=self.other, user=self.bob)
        ids = [s['id'] for s in self.client.get(URL).data['switches']]
        self.assertEqual(ids, [self.switch.id])
        self.client.force_authenticate(self.bob)
        self.assertEqual(len(self.client.get(URL).data['switches']), 2)

    def test_not_while_it_is_cleaned_up(self):
        PendingCleanup.objects.create(switch=self.switch)
        self.assertEqual(self.client.get(URL, {'switch': self.switch.id}).status_code, 403)

    def test_not_on_a_switch_someone_else_holds(self):
        Reservation.objects.create(switch=self.switch, user=self.bob)
        response = self.plugged()
        self.assertEqual(response.status_code, 403)
        self.assertIn('reserved by bob', response.data['detail'])

    def test_it_proposes_the_backbone_it_has_ports_to_and_the_lowest_free_ports(self):
        PermanentCable.objects.create(switch=self.switch, port='1/1/2')
        self.assertEqual(self.proposal(), {'switch_port': '1/1/3', 'backbone': BB, 'backbone_port': '1/2/2',
                                           'backbones': [BB], 'problem': None})

    def test_a_port_with_its_link_up_is_never_proposed(self):
        self.switches_fake.cable(self.IP, '1/1/2')
        self.assertEqual(self.proposal()['switch_port'], '1/1/3')

    def test_without_ports_the_user_picks_among_several_backbones(self):
        Port.objects.create(switch=self.other, port_switch='1/1/1', backbone=BB2, port_backbone='1/1/1')
        self.bb.cli(BB2, 'interfaces 1/1/1 admin-state disable')
        self.bb.cli(BB2, 'interfaces 1/1/2 admin-state disable')
        Port.objects.filter(switch=self.switch).update(switch=self.other, port_switch='1/1/5')  # BB stays known
        proposal = self.proposal()
        self.assertEqual((proposal['backbone'], proposal['backbone_port'], proposal['backbones']), (None, None, [BB, BB2]))
        self.assertEqual(proposal['problem'], 'Pick the Backbone you will plug the cable into.')
        self.assertEqual(self.proposal(backbone=BB2)['backbone_port'], '1/1/2')

    def test_a_cable_plugged_as_proposed_becomes_a_port(self):
        self.switches_fake.plug(self.IP, '1/1/2', BB, '1/2/2')
        response = self.plugged()
        self.assertEqual(response.status_code, 201, response.data)
        port = Port.objects.get(switch=self.switch, port_switch='1/1/2')
        self.assertEqual((port.backbone, port.port_backbone, port.status, port.svlan), (BB, '1/2/2', 'DOWN', None))
        self.assertIn('1/2/2', self.bb.disabled[BB], 'the Backbone port is disabled again, as a UNI without a Link')
        sent = [cmd for _, cmd in self.bb.commands if not cmd.startswith('show')]
        self.assertEqual(sent[-2:], ['interfaces 1/2/2 admin-state enable', 'interfaces 1/2/2 admin-state disable'])

    def test_no_cable_says_so_and_records_nothing(self):
        response = self.plugged()
        self.assertEqual(response.status_code, 400)
        self.assertIn('No link between 1/1/2', response.data['detail'])
        self.assertFalse(Port.objects.filter(port_switch='1/1/2').exists())
        self.assertIn('1/2/2', self.bb.disabled[BB])

    def test_a_cable_in_another_switch_port_says_which(self):
        self.switches_fake.plug(self.IP, '1/1/4', BB, '1/2/2')
        response = self.plugged()
        self.assertEqual(response.status_code, 400)
        self.assertIn('The link came up on 1/1/4, not on 1/1/2', response.data['detail'])

    def test_a_cable_in_another_backbone_port_is_refused(self):
        self.switches_fake.plug(self.IP, '1/1/2', BB, '1/2/3')
        response = self.plugged()
        self.assertEqual(response.status_code, 400)
        self.assertIn('No link between 1/1/2', response.data['detail'])
        self.assertEqual(self.bb.disabled[BB], {'1/2/1', '1/2/2', '1/2/3'})

    def test_a_switch_port_already_up_is_cabled_to_something_else(self):
        self.switches_fake.cable(self.IP, '1/1/2')
        response = self.plugged()
        self.assertEqual(response.status_code, 400)
        self.assertIn('cabled to something else', response.data['detail'])

    def test_never_on_a_port_that_is_already_a_port(self):
        for switch_port, backbone_port in (('1/1/1', '1/2/2'), ('1/1/2', '1/2/1')):
            with self.subTest(switch_port=switch_port):
                response = self.plugged(switch_port, backbone_port)
                self.assertEqual(response.status_code, 400)
                self.assertIn('already', response.data['detail'])

    def test_an_enabled_backbone_port_is_in_use(self):
        self.bb.cli(BB, 'interfaces 1/2/3 admin-state enable')
        response = self.plugged(backbone_port='1/2/3')
        self.assertEqual(response.status_code, 400)
        self.assertIn('is not free', response.data['detail'])

    def test_a_disabled_trunk_is_never_proposed_nor_enabled(self):
        # A spare trunk an admin disabled: a tagged VLAN member, as on 10.69.144.130
        self.bb.config.setdefault(BB, []).append('vlan 1441 members port 1/2/2 tagged')
        self.assertEqual(self.proposal()['backbone_port'], '1/2/3')
        self.switches_fake.plug(self.IP, '1/1/2', BB, '1/2/2')
        response = self.plugged()
        self.assertEqual(response.status_code, 400)
        self.assertIn('is not free', response.data['detail'])
        self.assertNotIn(('10.0.0.100', 'interfaces 1/2/2 admin-state enable'), self.bb.commands)

    def test_a_port_on_a_module_without_unis_is_never_proposed(self):
        self.bb.cli(BB, 'interfaces 1/2/2 admin-state enable')
        self.bb.cli(BB, 'interfaces 1/2/3 admin-state enable')
        self.bb.cli(BB, 'interfaces 1/5/1 admin-state disable')  # an uplink module, say
        proposal = self.proposal()
        self.assertEqual((proposal['backbone_port'], proposal['problem']),
                         (None, f'Backbone {BB} has no free port. Pick another Backbone.'))

    def test_a_backbone_port_it_cant_disable_again_is_for_an_admin(self):
        self.switches_fake.plug(self.IP, '1/1/2', BB, '1/2/2')
        self.bb.fail_on('interfaces 1/2/2 admin-state disable', times=3)
        response = self.plugged()
        self.assertEqual(response.status_code, 400)
        self.assertIn("couldn't disable it again. Tell an admin", response.data['detail'])
        self.assertFalse(Port.objects.filter(port_switch='1/1/2').exists())

    def test_a_backbone_port_disabled_on_the_second_try_is_fine(self):
        self.switches_fake.plug(self.IP, '1/1/2', BB, '1/2/2')
        self.bb.fail_on('interfaces 1/2/2 admin-state disable')
        self.assertEqual(self.plugged().status_code, 201)
        self.assertIn('1/2/2', self.bb.disabled[BB])

    def test_the_management_port_or_a_made_up_port_is_refused(self):
        for switch_port in ('EMP', 'not-a-port'):
            with self.subTest(switch_port=switch_port):
                response = self.plugged(switch_port=switch_port)
                self.assertEqual(response.status_code, 400)
                self.assertIn('not a front port', response.data['detail'])

    def test_a_switch_that_isnt_an_id_is_a_bad_request(self):
        self.assertEqual(self.client.get(URL, {'switch': 'abc'}).status_code, 400)

    def test_an_unknown_backbone_is_refused(self):
        response = self.client.get(URL, {'switch': self.switch.id, 'backbone': '10.9.9.9'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('not a Backbone BLab knows', response.data['detail'])

    def test_a_backbone_that_fails_leaves_its_port_disabled(self):
        self.switches_fake.plug(self.IP, '1/1/2', BB, '1/2/2')
        self.bb.fail_on('interfaces 1/2/2 admin-state enable', ip=BB)
        response = self.plugged()
        self.assertEqual(response.status_code, 400)
        self.assertIn("couldn't drive Backbone", response.data['detail'])
        self.assertIn('1/2/2', self.bb.disabled[BB])
        self.assertFalse(Port.objects.filter(port_switch='1/1/2').exists())

    def test_the_holder_may_add_a_cable_to_their_switch(self):
        Reservation.objects.create(switch=self.switch, user=self.alice)
        self.switches_fake.plug(self.IP, '1/1/2', BB, '1/2/2')
        self.assertEqual(self.plugged().status_code, 201)
