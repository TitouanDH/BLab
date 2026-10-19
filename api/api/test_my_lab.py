"""
My lab (#42): every Switch the user may work on, grouped by Project (or, with Projects off, by
Topology: their own Reservations and each Topology shared with them), each with its Links, its
Ghost Links and the user's Switch account; and the Quarantines naming them. Against fake devices.
"""
from django.test import override_settings
from django.utils import timezone

from . import links
from .models import Project, Quarantine, Reservation, SwitchAccount, TopologyShare
from .test_projects import OFF, ProjectsTestCase
from .test_switch_password import ON


class MyLabTestCase(ProjectsTestCase):
    """Alice, bob and carol; three Switches, each with a cabled Port."""

    def my_lab(self, user=None):
        response = self.call(user or self.alice, 'get', 'my_lab/')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def rows(self, user=None):
        """Every Switch on user's My lab, by id."""
        return {s['id']: s for g in self.my_lab(user)['groups'] for s in g['switches']}

    def switch_ids(self, group):
        return [s['id'] for s in group['switches']]


@override_settings(**OFF)
class WithoutProjectsTest(MyLabTestCase):

    def test_my_reservations_then_one_group_per_topology_shared_with_me(self):
        self.reserved(self.alice, self.switches[0])
        self.reserved(self.bob, self.switches[1])
        self.reserved(self.carol, self.switches[2])  # not shared with alice
        TopologyShare.objects.create(owner=self.bob, target=self.alice)

        lab = self.my_lab()
        self.assertFalse(lab['projects'])
        self.assertEqual([(g['kind'], g['holder']['username'], self.switch_ids(g)) for g in lab['groups']],
                         [('holder', 'alice', [self.switches[0].id]), ('holder', 'bob', [self.switches[1].id])])

    def test_a_row_says_what_working_on_the_switch_needs(self):
        reservation = self.reserved(self.alice, self.switches[0])
        [row] = self.my_lab()['groups'][0]['switches']
        self.assertEqual((row['model'], row['mngt_IP'], row['holder'], row['holder_id']),
                         ('OS6860', self.switches[0].mngt_IP, 'alice', self.alice.id))
        self.assertEqual((row['renewals_left'], row['admin_exception']), (2, False))
        self.assertEqual(row['end_date'], reservation.end_date)
        self.assertEqual((row['links'], row['ghost_links']), (0, 0))
        self.assertEqual((row['account']['name'], row['account']['state']), ('alice', 'ready'))

    def test_a_topology_shared_with_me_shows_even_while_it_holds_nothing(self):
        TopologyShare.objects.create(owner=self.bob, target=self.alice)
        lab = self.my_lab()
        self.assertEqual([(g['holder']['username'], g['switches']) for g in lab['groups']],
                         [('alice', []), ('bob', [])])


@override_settings(**ON)
class WithProjectsTest(MyLabTestCase):

    def setUp(self):
        super().setUp()
        self.case = self.reserved(self.alice, self.switches[0], new_project={'name': 'Case 7'}).project
        self.join(self.bob, self.case)
        self.reserved(self.bob, self.switches[1], project=self.case.id)
        self.bench = Project.objects.create(name='Bench', owner=self.alice, invite_token='tok-bench')
        self.bench.members.create(user=self.alice)
        self.reserved(self.carol, self.switches[2])  # carol's own Project

    def test_one_group_per_active_project_of_mine_with_every_switch_reserved_into_it(self):
        lab = self.my_lab()
        self.assertTrue(lab['projects'])
        self.assertEqual([(g['kind'], g['project']['name'], self.switch_ids(g)) for g in lab['groups']],
                         [('project', 'Bench', []), ('project', 'Case 7', [s.id for s in self.switches[:2]])])

    def test_a_project_comes_with_its_members_and_for_the_owner_its_invite_link(self):
        case = next(g['project'] for g in self.my_lab()['groups'] if g['project']['id'] == self.case.id)
        self.assertEqual([m['username'] for m in case['members']], ['alice', 'bob'])
        self.assertEqual(case['invite_token'], self.case.invite_token)
        case = next(g['project'] for g in self.my_lab(self.bob)['groups'] if g['project']['id'] == self.case.id)
        self.assertIsNone(case['invite_token'])

    def test_bob_sees_alices_switch_with_his_own_login(self):
        row = self.rows(self.bob)[self.switches[0].id]
        self.assertEqual((row['holder'], row['account']['name']), ('alice', 'bob'))
        self.assertTrue(row['account']['switch_password'])
        self.assertIsNone(row['account']['password'])

    def test_an_archived_project_is_left_out(self):
        self.bench.archived_at = timezone.now()
        self.bench.save()
        self.assertEqual([g['project']['name'] for g in self.my_lab()['groups']], ['Case 7'])

    def test_a_switch_account_not_created_yet_says_why(self):
        SwitchAccount.objects.filter(user=self.alice, switch=self.switches[1]).update(created=False, error='unreachable')
        rows = self.rows()
        self.assertEqual((rows[self.switches[1].id]['account']['state'], rows[self.switches[1].id]['account']['error']),
                         ('failed', 'unreachable'))
        self.assertEqual(rows[self.switches[0].id]['account']['state'], 'ready')

    def test_a_reservation_outside_any_project_is_grouped_by_its_holder(self):
        # Made by production's older code, before the conversion (docs/adr/0002)
        Reservation.objects.filter(switch=self.switches[0]).update(project=None)
        groups = self.my_lab()['groups']
        self.assertEqual([(g['kind'], self.switch_ids(g)) for g in groups],
                         [('project', []), ('project', [self.switches[1].id]), ('holder', [self.switches[0].id])])
        self.assertEqual(groups[2]['holder']['username'], 'alice')

    def test_links_and_ghost_links_are_counted_on_each_end(self):
        link = links.connect(self.ports[0], self.ports[1])
        rows = self.rows()
        self.assertEqual([(rows[s.id]['links'], rows[s.id]['ghost_links']) for s in self.switches[:2]],
                         [(1, 0), (1, 0)])
        link.record_ghost("This Link is not carried by backbone 10.0.0.100: its Service there is missing.",
                          timezone.now())
        rows = self.rows()
        self.assertEqual([(rows[s.id]['links'], rows[s.id]['ghost_links']) for s in self.switches[:2]],
                         [(1, 1), (1, 1)])

    def test_a_link_being_disconnected_is_not_counted(self):
        links.connect(self.ports[0], self.ports[1]).request_teardown()
        self.assertEqual(self.rows()[self.switches[0].id]['links'], 0)

    def test_the_quarantines_naming_me_and_only_those(self):
        Reservation.objects.filter(switch=self.switches[2]).delete()
        Quarantine.objects.create(switch=self.switches[2], holder=self.alice, reasons=['Unwanted cable on 1/1/5'])
        lab = self.my_lab()
        [named] = lab['quarantines']
        self.assertEqual((named['id'], named['model'], named['quarantine']['reasons']),
                         (self.switches[2].id, 'OS6860', ['Unwanted cable on 1/1/5']))
        self.assertNotIn('mngt_IP', named)  # she may no longer work on it
        self.assertFalse(named['cleaning_up'])
        self.assertEqual(self.my_lab(self.bob)['quarantines'], [])
