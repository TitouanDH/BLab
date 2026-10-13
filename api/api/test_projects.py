"""
Projects replace Topology sharing (#41, CONTEXT.md, docs/adr/0007), behind BLAB_PROJECTS: Reserve
into a Project, who may work on its Switches, the Invite link (join), leave and remove with
their Switch accounts, renewing the link, archiving, Links that never cross Projects, a
Project's Topology and layout, the conversion of Shares, and nothing changed with it off.
Against fake devices.
"""
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import override_settings

from . import fake_devices, switch_password
from .models import (Port, Project, ProjectMember, ProjectSwitchPosition, Reservation, SwitchAccount,
                     SwitchPosition, TopologyShare)
from .projects import convert_shares
from .test_release_cleanup import CleanupTestCase, in_a_week, make_switch
from .test_switch_password import ON

GOOD = 'Lab-Pass1word'
OFF = dict(BLAB_PROJECTS=False)


class ProjectsTestCase(CleanupTestCase):
    """Alice, bob and carol, who have each chosen a Switch password; three Switches, each with a cabled Port."""

    def setUp(self):
        super().setUp()
        self.carol = User.objects.create_user('carol', email='carol@example.com', password='pw')
        self.switches = [self.switch, make_switch(2), make_switch(3)]
        self.ports = [Port.objects.create(switch=s, port_switch='1/1/1', backbone='10.0.0.100',
                                          port_backbone=f'1/1/{i}') for i, s in enumerate(self.switches, 1)]
        with self.settings(**ON):
            for user in (self.alice, self.bob, self.carol):
                switch_password.choose(user, GOOD)

    def users_on(self, switch=None):
        return set(self.fake.users.get((switch or self.switch).mngt_IP, {}))

    def call(self, user, method, path, body=None):
        return getattr(self.client_for(user), method)(f'/api/{path}', body, format='json')

    def reserve(self, user=None, switch=None, **choice):
        response = self.call(user or self.alice, 'post', 'reserve/',
                             {'switch': (switch or self.switch).id, 'end_date': in_a_week(), **choice})
        return response

    def reserved(self, user=None, switch=None, **choice):
        response = self.reserve(user, switch, **choice)
        self.assertEqual(response.status_code, 201, response.data)
        return Reservation.objects.get(switch=switch or self.switch)

    def join(self, user, project):
        response = self.call(user, 'post', f'join/{project.invite_token}/')
        self.assertEqual(response.status_code, 200, response.data)
        return response


@override_settings(**ON)
class ReserveIntoProjectTest(ProjectsTestCase):

    def test_a_first_reserve_makes_a_project_for_the_user(self):
        response = self.reserve()
        self.assertEqual(response.status_code, 201, response.data)
        project = Project.objects.get()
        self.assertEqual((project.name, project.owner, response.data['project']['name']),
                         ("alice's lab", self.alice, "alice's lab"))
        reservation = Reservation.objects.get()
        self.assertEqual((reservation.user, reservation.project), (self.alice, project))  # she stays the holder
        self.assertEqual(list(project.members.values_list('user__username', flat=True)), ['alice'])

    def test_with_no_choice_the_last_used_project_is_used(self):
        first = self.reserved().project
        other = self.reserved(switch=self.switches[1], new_project={'name': 'Case 42', 'reference': 'CS-42'}).project
        self.assertEqual((other.name, other.reference), ('Case 42', 'CS-42'))
        self.assertEqual(self.call(self.alice, 'get', 'projects/').data['last_used'], other.id)
        self.assertEqual(self.reserved(switch=self.switches[2]).project, other)
        self.assertNotEqual(first, other)

    def test_reserve_into_a_chosen_project(self):
        project = self.reserved().project
        self.join(self.bob, project)
        reservation = self.reserved(self.bob, self.switches[1], project=project.id)
        self.assertEqual((reservation.user, reservation.project), (self.bob, project))
        self.assertEqual(self.call(self.bob, 'get', 'projects/').data['last_used'], project.id)

    def test_a_project_that_isnt_yours_or_is_archived_is_refused(self):
        project = self.reserved().project
        self.assertEqual(self.reserve(self.bob, self.switches[1], project=project.id).status_code, 404)
        empty = Project.objects.create(name='Old', owner=self.bob)
        ProjectMember.objects.create(project=empty, user=self.bob)
        self.call(self.bob, 'post', f'projects/{empty.id}/archive/')
        response = self.reserve(self.bob, self.switches[1], project=empty.id)
        self.assertEqual(response.status_code, 400)
        self.assertIn('archived', response.data['detail'])
        self.assertEqual(self.reserve(self.bob, self.switches[1], new_project={'name': '  '}).status_code, 400)
        self.assertFalse(Reservation.objects.filter(switch=self.switches[1]).exists())

    def test_a_refused_reserve_makes_no_project(self):
        self.reserved(self.bob)
        self.assertEqual(self.reserve(self.alice, new_project={'name': 'Mine'}).status_code, 400)
        self.assertFalse(Project.objects.filter(name='Mine').exists())


@override_settings(**ON)
class WhoMayWorkTest(ProjectsTestCase):

    def setUp(self):
        super().setUp()
        self.project = self.reserved().project  # alice holds Switch 1 in "alice's lab"

    def test_every_member_may_renew_release_and_see_the_details(self):
        self.assertEqual(self.call(self.bob, 'post', 'renew/', {'switch': self.switch.id}).status_code, 403)
        listed = {s['id']: s for s in self.call(self.bob, 'get', 'list_switch/').data['switchs']}
        self.assertNotIn('mngt_IP', listed[self.switch.id])

        self.join(self.bob, self.project)
        self.assertEqual(self.call(self.bob, 'post', 'renew/', {'switch': self.switch.id}).status_code, 200)
        listed = {s['id']: s for s in self.call(self.bob, 'get', 'list_switch/').data['switchs']}
        self.assertEqual(listed[self.switch.id]['mngt_IP'], self.switch.mngt_IP)
        rows = {r['switch']: r for r in self.call(self.bob, 'get', 'list_reservation/').data}
        self.assertEqual((rows[self.switch.id]['may_work'], rows[self.switch.id]['project_name']),
                         (True, "alice's lab"))
        self.assertEqual(self.call(self.bob, 'post', 'release/', {'switch': self.switch.id}).status_code, 200)

    def test_a_share_no_longer_lets_anyone_work_on_a_switch_in_a_project(self):
        TopologyShare.objects.create(owner=self.alice, target=self.carol)
        self.assertEqual(self.call(self.carol, 'post', 'renew/', {'switch': self.switch.id}).status_code, 403)
        self.assertNotIn('carol', self.users_on())

    def test_a_reservation_without_a_project_keeps_the_share_rule_until_converted(self):
        Reservation.objects.filter(switch=self.switch).update(project=None)
        TopologyShare.objects.create(owner=self.alice, target=self.carol)
        self.assertEqual(self.call(self.carol, 'post', 'renew/', {'switch': self.switch.id}).status_code, 200)
        self.assertEqual(self.call(self.bob, 'post', 'renew/', {'switch': self.switch.id}).status_code, 403)

    def test_sharing_endpoints_are_gone(self):
        self.assertEqual(self.call(self.alice, 'post', 'share_topology/', {'target_username': 'bob'}).status_code, 404)
        self.assertEqual(self.call(self.alice, 'get', 'list_shared_topologies/').status_code, 404)
        self.assertFalse(TopologyShare.objects.exists())

    def test_a_users_own_topology_is_theirs_alone(self):
        TopologyShare.objects.create(owner=self.alice, target=self.bob)
        self.assertEqual(self.call(self.bob, 'get', f'topology/{self.alice.id}/').status_code, 403)
        self.assertEqual(self.call(self.alice, 'get', f'topology/{self.alice.id}/').status_code, 200)


@override_settings(**ON)
class LinksStayInAProjectTest(ProjectsTestCase):

    def connect(self, user, a, b):
        return self.call(user, 'post', 'connect/', {'portA': a.id, 'portB': b.id})

    def test_connect_refuses_a_link_across_projects(self):
        project = self.reserved().project
        self.join(self.bob, project)
        self.reserved(self.alice, self.switches[1], new_project={'name': 'Other'})
        response = self.connect(self.alice, self.ports[0], self.ports[1])
        self.assertEqual(response.status_code, 400)
        self.assertIn('never cross Projects', response.data['detail'])
        self.assertIsNone(Port.objects.get(pk=self.ports[0].pk).svlan)

        # Bob reserves into alice's Project: the same Topology, so the Link is made
        self.reserved(self.bob, self.switches[2], project=project.id)
        response = self.connect(self.alice, self.ports[0], self.ports[2])
        self.assertEqual(response.status_code, 200, response.data)


@override_settings(**ON)
class MembersTest(ProjectsTestCase):

    def setUp(self):
        super().setUp()
        self.project = self.reserved().project
        self.reserved(switch=self.switches[1])  # the last used one: the same Project

    def test_the_join_page_shows_the_project_then_join_gives_switch_accounts(self):
        response = self.call(self.bob, 'get', f'join/{self.project.invite_token}/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data['project']['name'], response.data['project']['owner'], response.data['member']),
                         ("alice's lab", 'alice', False))
        self.assertEqual([s['id'] for s in response.data['project']['switches']], [s.id for s in self.switches[:2]])
        self.assertNotIn('bob', self.users_on())  # only seeing the page changes nothing

        response = self.join(self.bob, self.project)
        self.assertTrue(response.data['member'])
        self.assertEqual(self.users_on(), {'alice', 'bob'})
        self.assertEqual(self.users_on(self.switches[1]), {'alice', 'bob'})
        self.assertEqual(self.fake.users[self.switch.mngt_IP]['bob'], GOOD)  # his Switch password
        self.assertIn('already a member', self.join(self.bob, self.project).data['detail'])
        self.assertEqual(ProjectMember.objects.filter(user=self.bob).count(), 1)

    def test_a_project_whose_owner_is_gone_still_opens(self):
        Project.objects.filter(pk=self.project.pk).update(owner_id=9999)  # deleted by code that doesn't know Projects
        response = self.call(self.bob, 'get', f'join/{self.project.invite_token}/')
        self.assertEqual((response.status_code, response.data['project']['owner']), (200, None))
        self.assertEqual(self.call(self.alice, 'get', 'projects/').data['projects'][0]['owner'], None)

    def test_a_logged_out_visitor_is_refused(self):
        from rest_framework.test import APIClient
        self.assertIn(APIClient().get(f'/api/join/{self.project.invite_token}/').status_code, (401, 403))

    def test_renewing_the_link_stops_the_old_one(self):
        old = self.project.invite_token
        self.assertEqual(self.call(self.bob, 'post', f'projects/{self.project.id}/invite/').status_code, 404)
        self.join(self.bob, self.project)
        self.assertEqual(self.call(self.bob, 'post', f'projects/{self.project.id}/invite/').status_code, 403)
        response = self.call(self.alice, 'post', f'projects/{self.project.id}/invite/')
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.data['invite_token'], old)
        self.assertEqual(self.call(self.carol, 'get', f'join/{old}/').status_code, 404)
        self.assertEqual(self.call(self.carol, 'post', f'join/{old}/').status_code, 404)
        self.assertEqual(self.call(self.carol, 'get', f'join/{response.data["invite_token"]}/').status_code, 200)

    def test_only_the_owner_sees_the_invite_link(self):
        self.join(self.bob, self.project)
        mine = self.call(self.alice, 'get', 'projects/').data['projects'][0]
        his = self.call(self.bob, 'get', 'projects/').data['projects'][0]
        self.assertEqual(mine['invite_token'], self.project.invite_token)
        self.assertIsNone(his['invite_token'])
        self.assertEqual((his['switch_count'], [m['username'] for m in his['members']], his['is_owner']),
                         (2, ['alice', 'bob'], False))
        self.assertEqual(self.call(self.carol, 'get', 'projects/').data['projects'], [])
        self.assertEqual(self.call(self.carol, 'get', f'projects/{self.project.id}/').status_code, 404)

    def test_leaving_removes_the_members_switch_accounts(self):
        self.join(self.bob, self.project)
        response = self.call(self.bob, 'post', f'projects/{self.project.id}/leave/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.users_on(), {'alice'})
        self.assertFalse(SwitchAccount.objects.filter(user=self.bob).exists())
        self.assertEqual(self.call(self.bob, 'post', 'renew/', {'switch': self.switch.id}).status_code, 403)

    def test_the_owner_and_a_holder_cannot_leave(self):
        self.assertEqual(self.call(self.alice, 'post', f'projects/{self.project.id}/leave/').status_code, 400)
        self.join(self.bob, self.project)
        self.reserved(self.bob, self.switches[2], project=self.project.id)
        response = self.call(self.bob, 'post', f'projects/{self.project.id}/leave/')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Release them first', response.data['detail'])

    def test_the_owner_removes_a_member_and_their_switch_accounts(self):
        self.join(self.bob, self.project)
        self.join(self.carol, self.project)
        path = f'projects/{self.project.id}/members/{self.carol.id}/'
        self.assertEqual(self.call(self.bob, 'delete', path).status_code, 403)
        self.assertEqual(self.call(self.alice, 'delete', f'projects/{self.project.id}/members/{self.alice.id}/').status_code, 400)
        response = self.call(self.alice, 'delete', path)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.users_on(), {'alice', 'bob'})
        self.assertEqual(self.call(self.alice, 'delete', path).status_code, 404)

    def test_a_member_holding_a_switch_cannot_be_removed(self):
        self.join(self.bob, self.project)
        self.reserved(self.bob, self.switches[2], project=self.project.id)
        response = self.call(self.alice, 'delete', f'projects/{self.project.id}/members/{self.bob.id}/')
        self.assertEqual(response.status_code, 400)
        self.assertIn('bob still holds', response.data['detail'])


@override_settings(**ON)
class ArchiveTest(ProjectsTestCase):

    def test_archive_only_once_the_project_holds_nothing_and_it_kills_the_link(self):
        project = self.reserved().project
        token = project.invite_token
        self.join(self.bob, project)
        self.assertEqual(self.call(self.bob, 'post', f'projects/{project.id}/archive/').status_code, 403)
        response = self.call(self.alice, 'post', f'projects/{project.id}/archive/')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Release them first', response.data['detail'])

        self.release()
        response = self.call(self.alice, 'post', f'projects/{project.id}/archive/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNotNone(response.data['archived_at'])
        self.assertIsNone(response.data['invite_token'])
        self.assertEqual(self.call(self.carol, 'get', f'join/{token}/').status_code, 404)
        self.assertEqual(self.call(self.alice, 'post', f'projects/{project.id}/invite/').status_code, 400)
        # Never deleted, still listed; Reserve no longer picks it
        self.assertEqual(Project.objects.count(), 1)
        self.assertIsNone(self.call(self.alice, 'get', 'projects/').data['last_used'])
        self.assertNotEqual(self.reserved(switch=self.switches[1]).project, project)

    def test_creating_a_project_without_reserving(self):
        response = self.call(self.alice, 'post', 'projects/', {'name': 'Lab tests', 'reference': ''})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual((response.data['name'], response.data['is_owner'], response.data['switch_count']),
                         ('Lab tests', True, 0))
        self.assertEqual(self.call(self.alice, 'post', 'projects/', {'name': 'x' * 101}).status_code, 400)


@override_settings(**ON)
class ProjectTopologyTest(ProjectsTestCase):

    def test_members_read_and_arrange_the_projects_topology(self):
        project = self.reserved().project
        path = f'projects/{project.id}/'
        self.assertEqual(self.call(self.bob, 'get', path + 'topology/').status_code, 404)
        self.assertEqual(self.call(self.bob, 'put', path + 'layout/', {'positions': {}}).status_code, 404)
        self.join(self.bob, project)
        self.reserved(self.bob, self.switches[1], project=project.id)
        positions = {str(self.switch.id): {'x': 1, 'y': 2}}
        self.assertEqual(self.call(self.bob, 'put', path + 'layout/', {'positions': positions}).status_code, 200)
        data = self.call(self.alice, 'get', path + 'topology/').data
        self.assertEqual([s['id'] for s in data['switches'] if s['in_topology']], [s.id for s in self.switches[:2]])
        self.assertEqual(data['layout'], {str(self.switch.id): {'x': 1.0, 'y': 2.0}})
        self.assertTrue(data['may_work'])
        self.assertEqual(data['project']['name'], "alice's lab")
        self.assertEqual(self.call(self.alice, 'delete', path + 'layout/').status_code, 200)
        self.assertFalse(ProjectSwitchPosition.objects.exists())
        self.assertEqual(self.call(self.alice, 'put', path + 'layout/', {'positions': []}).status_code, 400)


class ConversionTest(ProjectsTestCase):
    """The Shares and layouts of today, made into Projects: run with Projects off or on."""

    def setUp(self):
        super().setUp()
        self.reserve_row(self.alice, self.switches[0])
        self.reserve_row(self.alice, self.switches[1])
        self.reserve_row(self.bob, self.switches[2])
        TopologyShare.objects.create(owner=self.alice, target=self.bob)
        TopologyShare.objects.create(owner=self.alice, target=self.carol)
        TopologyShare.objects.create(owner=self.carol, target=self.alice)  # carol holds nothing
        SwitchPosition.objects.create(owner=self.alice, switch=self.switches[0], x=5, y=6)

    def reserve_row(self, user, switch):
        return Reservation.objects.create(switch=switch, user=user)

    def test_one_project_per_holder_with_shares_as_members_and_the_layout(self):
        out = StringIO()
        call_command('convert_shares_to_projects', stdout=out)
        alices = Project.objects.get(owner=self.alice)
        self.assertEqual(alices.name, "alice's lab")
        self.assertEqual(set(alices.members.values_list('user__username', flat=True)), {'alice', 'bob', 'carol'})
        self.assertEqual(set(alices.reservations.values_list('switch_id', flat=True)),
                         {self.switches[0].id, self.switches[1].id})
        bobs = Project.objects.get(owner=self.bob)
        self.assertEqual(list(bobs.members.values_list('user__username', flat=True)), ['bob'])
        self.assertFalse(Project.objects.filter(owner=self.carol).exists())
        self.assertEqual(list(ProjectSwitchPosition.objects.values_list('project', 'switch', 'x', 'y')),
                         [(alices.id, self.switches[0].id, 5.0, 6.0)])
        self.assertTrue(TopologyShare.objects.exists())  # removed in a later step
        self.assertIn('2 Project(s) created, 3 Reservation(s) moved, 2 member(s) added', out.getvalue())

    def test_running_it_twice_changes_nothing_more(self):
        convert_shares()
        before = (list(Project.objects.values_list('id', 'name', 'owner')),
                  list(ProjectMember.objects.values_list('project', 'user')),
                  list(ProjectSwitchPosition.objects.values_list('project', 'switch')))
        done = convert_shares()
        self.assertEqual((done.projects_created, done.reservations_moved, done.members_added, done.positions_copied),
                         (0, 0, 0, 0))
        after = (list(Project.objects.values_list('id', 'name', 'owner')),
                 list(ProjectMember.objects.values_list('project', 'user')),
                 list(ProjectSwitchPosition.objects.values_list('project', 'switch')))
        self.assertEqual(before, after)

    def test_a_reservation_made_since_goes_into_the_same_project(self):
        convert_shares()
        Reservation.objects.filter(switch=self.switches[1]).delete()
        self.reserve_row(self.alice, self.switches[1])  # by older code, without a Project
        convert_shares()
        self.assertEqual(Project.objects.filter(owner=self.alice).count(), 1)
        self.assertEqual(Reservation.objects.get(switch=self.switches[1]).project.owner, self.alice)

    @override_settings(**ON)
    def test_a_lab_someone_else_joined_is_not_used_again(self):
        # Alice's first Reserve with Projects on made "alice's lab", and dave joined it
        dave = User.objects.create_user('dave', email='dave@example.com', password='pw')
        lab = Project.objects.create(name="alice's lab", owner=self.alice, invite_token='t')
        ProjectMember.objects.create(project=lab, user=self.alice)
        ProjectMember.objects.create(project=lab, user=dave)
        convert_shares()
        moved = Reservation.objects.get(switch=self.switches[0]).project
        self.assertNotEqual(moved, lab)  # dave gains nothing he couldn't work on before
        self.assertEqual((moved.name, moved.owner), ("alice's lab", self.alice))
        self.assertFalse(lab.members.filter(user=self.bob).exists())
        convert_shares()  # and again: nothing more
        self.assertEqual(Project.objects.filter(owner=self.alice).count(), 2)

    def test_a_dry_run_changes_nothing(self):
        out = StringIO()
        call_command('convert_shares_to_projects', '--dry-run', stdout=out)
        self.assertIn('Would have 2 Project(s) created', out.getvalue())
        self.assertFalse(Project.objects.exists())
        self.assertFalse(Reservation.objects.exclude(project=None).exists())

    @override_settings(**ON)
    def test_who_may_work_on_each_switch_stays_the_same(self):
        from .switch_accounts import entitled
        before = {s.id: entitled(s) for s in self.switches}
        convert_shares()
        self.assertEqual({s.id: entitled(s) for s in self.switches}, before)


@override_settings(**OFF)
class ProjectsOffTest(ProjectsTestCase):
    """With Projects off, everything behaves as before them."""

    def test_project_endpoints_are_not_found(self):
        project = Project.objects.create(name='p', owner=self.alice, invite_token='t')
        for method, path in (('get', 'projects/'), ('post', 'projects/'), ('get', f'projects/{project.id}/'),
                             ('get', f'projects/{project.id}/topology/'), ('post', f'projects/{project.id}/invite/'),
                             ('post', f'projects/{project.id}/archive/'), ('get', 'join/t/'), ('post', 'join/t/')):
            self.assertEqual(self.call(self.alice, method, path, {}).status_code, 404, path)

    def test_reserve_makes_no_project_and_shares_still_work(self):
        response = self.reserve(project=12345, new_project={'name': 'x'})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn('project', response.data)
        self.assertIsNone(Reservation.objects.get().project)
        self.assertFalse(Project.objects.exists())
        self.assertEqual(self.call(self.alice, 'post', 'share_topology/', {'target_username': 'bob'}).status_code, 201)
        self.assertEqual(self.users_on(), {'alice', 'bob'})
        self.assertNotIn('project', self.call(self.bob, 'get', 'list_reservation/').data[0])

    def test_members_of_a_converted_project_get_nothing_while_off(self):
        project = Project.objects.create(name='p', owner=self.alice, invite_token='t')
        ProjectMember.objects.create(project=project, user=self.carol)
        Reservation.objects.create(switch=self.switch, user=self.alice, project=project)
        self.assertEqual(self.call(self.carol, 'post', 'renew/', {'switch': self.switch.id}).status_code, 403)
