"""
The Topology layout (#29): where each Switch is drawn on a user's Topology, saved on the
server so everyone viewing a shared Topology sees the same picture. Read with the Topology
(GET topology/<owner_id>/, "layout"), written and forgotten (Re-arrange) through topology/<owner_id>/layout/
by whoever may work on the Topology.
"""
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from . import fake_devices
from .models import Reservation, Switch, SwitchPosition, TopologyShare
from .tests import make_switches


@override_settings(BLAB_DEVICES='fake')
class TopologyLayoutTest(TestCase):

    def setUp(self):
        fake_devices.backbone.reset()
        self.alice = User.objects.create_user('alice', email='alice@example.com', password='pw')
        self.bob = User.objects.create_user('bob', email='bob@example.com', password='pw')
        self.carol = User.objects.create_user('carol', email='carol@example.com', password='pw')
        self.switches = make_switches(3)
        Reservation.objects.create(switch=self.switches[0], user=self.alice)
        Reservation.objects.create(switch=self.switches[1], user=self.alice)
        Reservation.objects.create(switch=self.switches[2], user=self.bob)
        TopologyShare.objects.create(owner=self.alice, target=self.bob)

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def layout(self, user, owner):
        return self.client_for(user).get(f'/api/topology/{owner.id}/').data['layout']

    def save(self, user, owner, positions):
        return self.client_for(user).put(f'/api/topology/{owner.id}/layout/', {'positions': positions}, format='json')

    def forget(self, user, owner):
        return self.client_for(user).delete(f'/api/topology/{owner.id}/layout/')

    def test_a_topology_without_a_saved_layout_reads_an_empty_one(self):
        self.assertEqual(self.layout(self.alice, self.alice), {})

    def test_saved_positions_are_read_with_the_topology_by_everyone_viewing_it(self):
        a, b, _ = self.switches
        response = self.save(self.alice, self.alice, {str(a.id): {'x': 10, 'y': -20.5}, str(b.id): {'x': 300, 'y': 40}})
        self.assertEqual(response.status_code, 200, response.data)
        expected = {str(a.id): {'x': 10.0, 'y': -20.5}, str(b.id): {'x': 300.0, 'y': 40.0}}
        self.assertEqual(self.layout(self.alice, self.alice), expected)
        self.assertEqual(self.layout(self.bob, self.alice), expected)  # shared with bob: the same picture

    def test_a_user_the_topology_is_shared_with_may_arrange_it(self):
        a = self.switches[0]
        self.assertEqual(self.save(self.bob, self.alice, {str(a.id): {'x': 1, 'y': 2}}).status_code, 200)
        self.assertEqual(self.layout(self.alice, self.alice), {str(a.id): {'x': 1.0, 'y': 2.0}})

    def test_saving_again_moves_switches_and_keeps_the_others(self):
        a, b, _ = self.switches
        self.save(self.alice, self.alice, {str(a.id): {'x': 1, 'y': 1}, str(b.id): {'x': 2, 'y': 2}})
        self.save(self.bob, self.alice, {str(a.id): {'x': 5, 'y': 6}})
        self.assertEqual(self.layout(self.alice, self.alice),
                         {str(a.id): {'x': 5.0, 'y': 6.0}, str(b.id): {'x': 2.0, 'y': 2.0}})
        self.assertEqual(SwitchPosition.objects.count(), 2)

    def test_a_layout_belongs_to_its_topology_owner(self):
        a, _, c = self.switches
        self.save(self.alice, self.alice, {str(a.id): {'x': 1, 'y': 1}})
        self.save(self.bob, self.bob, {str(c.id): {'x': 7, 'y': 7}})
        self.assertEqual(self.layout(self.bob, self.bob), {str(c.id): {'x': 7.0, 'y': 7.0}})
        self.assertEqual(self.layout(self.alice, self.alice), {str(a.id): {'x': 1.0, 'y': 1.0}})

    def test_only_switches_drawn_in_the_topology_are_read(self):
        a, b, c = self.switches
        self.save(self.alice, self.alice, {str(a.id): {'x': 1, 'y': 1}, str(c.id): {'x': 3, 'y': 3}})
        # c is bob's, with no Link to alice's: not drawn in her Topology. Released, a is not drawn either.
        Reservation.objects.filter(switch=a).delete()
        self.assertEqual(self.layout(self.alice, self.alice), {})

    def test_forgetting_removes_the_saved_positions(self):
        a = self.switches[0]
        self.save(self.alice, self.alice, {str(a.id): {'x': 1, 'y': 1}})
        self.save(self.bob, self.bob, {str(self.switches[2].id): {'x': 7, 'y': 7}})
        response = self.forget(self.bob, self.alice)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.layout(self.alice, self.alice), {})
        self.assertEqual(SwitchPosition.objects.filter(owner=self.bob).count(), 1)  # bob's own stays

    def test_a_topology_not_shared_with_the_user_cannot_be_arranged(self):
        a = self.switches[0]
        self.assertEqual(self.save(self.carol, self.alice, {str(a.id): {'x': 1, 'y': 1}}).status_code, 403)
        self.assertEqual(self.save(self.alice, self.bob, {str(a.id): {'x': 1, 'y': 1}}).status_code, 403)
        self.save(self.alice, self.alice, {str(a.id): {'x': 1, 'y': 1}})
        self.assertEqual(self.forget(self.carol, self.alice).status_code, 403)
        self.assertEqual(SwitchPosition.objects.count(), 1)

    def test_an_unknown_owner_is_not_found(self):
        self.assertEqual(self.client_for(self.alice).put('/api/topology/999/layout/', {'positions': {}},
                                                         format='json').status_code, 404)

    def test_an_anonymous_user_is_refused(self):
        response = APIClient().put(f'/api/topology/{self.alice.id}/layout/', {'positions': {}}, format='json')
        self.assertIn(response.status_code, (401, 403))

    def test_positions_must_be_numbers_by_switch_id(self):
        a = self.switches[0]
        for positions in (None, [], {str(a.id): {'x': 1}}, {str(a.id): {'x': 'a', 'y': 1}},
                          {'one': {'x': 1, 'y': 1}},
                          {str(a.id): {'x': True, 'y': 1}}, {str(a.id): [1, 2]}):
            response = self.save(self.alice, self.alice, positions)
            self.assertEqual(response.status_code, 400, positions)
        # 1e400 reads as infinity
        response = self.client_for(self.alice).put(f'/api/topology/{self.alice.id}/layout/',
                                                   f'{{"positions": {{"{a.id}": {{"x": 1e400, "y": 1}}}}}}',
                                                   content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(SwitchPosition.objects.count(), 0)

    def test_switches_that_no_longer_exist_are_left_out(self):
        a = self.switches[0]
        response = self.save(self.alice, self.alice, {str(a.id): {'x': 1, 'y': 1}, '999': {'x': 2, 'y': 2}})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(list(SwitchPosition.objects.values_list('switch_id', flat=True)), [a.id])

    def test_deleting_a_switch_or_a_user_takes_their_positions_along(self):
        a, b, _ = self.switches
        self.save(self.alice, self.alice, {str(a.id): {'x': 1, 'y': 1}, str(b.id): {'x': 2, 'y': 2}})
        Switch.objects.filter(id=a.id).delete()
        self.assertEqual(list(SwitchPosition.objects.values_list('switch_id', flat=True)), [b.id])
        self.alice.delete()
        self.assertEqual(SwitchPosition.objects.count(), 0)
