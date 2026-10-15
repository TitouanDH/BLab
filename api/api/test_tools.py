"""
Tools (#56, CONTEXT.md): the IXIA out of the Switch records. An admin marks a record as a Tool,
with its Ports; it is then never reserved, Cleaned up, Inspected or Swept, and has no Switch
accounts. Its ports are linked to like a Switch's, first come first served, and the Link ends
with the Release of the Switch at its other end. Against fake devices.
"""
from django.contrib.auth.models import User
from django.test import override_settings

from . import tools
from .models import (NO_MANAGEMENT_IP, PendingCleanup, Port, Quarantine, Reservation, Restore, Snapshot, Switch,
                     SwitchAccount, SwitchEvent, Tool)
from .projects import create
from .snapshots import project_links
from .sweep import candidates
from .test_projects import ProjectsTestCase
from .test_release_cleanup import make_switch
from .test_snapshots import SnapshotTestCase
from .test_switch_password import ON


def make_ixia(n=9):
    """The IXIA as it was recorded: a Switch without a management IP, with two cabled ports."""
    ixia = Switch.objects.create(mngt_IP=NO_MANAGEMENT_IP, model='Ixia XGS2', console='', part_number='',
                                 hardware_revision='', serial_number=f'ixia{n}')
    for i in (1, 2):
        Port.objects.create(switch=ixia, port_switch=f'1/{i}', backbone='10.0.0.100', port_backbone=f'1/2/{i}')
    return ixia


class ToolsTestCase(ProjectsTestCase):
    def setUp(self):
        super().setUp()
        self.ixia = make_ixia()
        self.admin = User.objects.create_superuser('root', email='root@example.com', password='pw')

    def mark(self):
        tools.mark_tool(self.ixia, self.admin)
        self.ixia.refresh_from_db()

    def connect(self, user, port_a, port_b):
        return self.call(user, 'post', 'connect/', {'portA': port_a.id, 'portB': port_b.id})

    def tool_port(self, i=1):
        return Port.objects.get(switch=self.ixia, port_switch=f'1/{i}')


@override_settings(**ON)
class MarkAsToolTest(ToolsTestCase):

    def test_a_tool_moves_out_of_the_switches_with_its_ports(self):
        self.mark()
        self.assertTrue(self.ixia.is_tool)
        self.assertNotIn(self.ixia, Switch.objects.all())
        self.assertEqual(list(Tool.objects.all()), [self.ixia])
        self.assertEqual(Port.objects.filter(switch=self.ixia).count(), 2)

    def test_marking_a_reserved_record_ends_its_reservation_but_keeps_its_links(self):
        # How the IXIA may be today: reserved, linked, and with a Cleanup and a Quarantine pending
        project = self.reserved().project
        Reservation.objects.create(switch=self.ixia, user=self.alice, project=project)
        self.assertEqual(self.connect(self.alice, self.ports[0], self.tool_port()).status_code, 200)
        PendingCleanup.objects.create(switch=self.ixia, holder=self.bob)
        Quarantine.objects.create(switch=self.ixia, holder=self.bob, reasons=['unreachable: no management IP'])

        done = tools.mark_tool(self.ixia, self.admin)

        self.assertEqual(len(done), 3, done)
        self.assertFalse(Reservation.objects.filter(switch=self.ixia).exists())
        self.assertFalse(PendingCleanup.objects.filter(switch=self.ixia).exists())
        self.assertIsNone(self.ixia.open_quarantine())  # bob may reserve again
        self.assertEqual(self.reserve(self.bob, self.switches[1]).status_code, 201)
        self.ports[0].refresh_from_db()
        self.assertIsNotNone(self.ports[0].svlan)
        self.assertEqual(self.tool_port().svlan, self.ports[0].svlan)

    def test_a_record_with_switch_accounts_is_refused(self):
        SwitchAccount.objects.create(switch=self.ixia, user=self.alice, name='alice', password='x', created=True)
        with self.assertRaises(tools.ToolError):
            tools.mark_tool(self.ixia, self.admin)
        self.ixia.refresh_from_db()
        self.assertFalse(self.ixia.is_tool)

    def test_back_to_a_switch_only_without_links(self):
        self.mark()
        self.reserved()
        self.assertEqual(self.connect(self.alice, self.ports[0], self.tool_port()).status_code, 200)
        with self.assertRaises(tools.ToolError):
            tools.mark_switch(self.ixia, self.admin)
        self.release()
        tools.mark_switch(self.ixia, self.admin)
        self.assertIn(self.ixia, Switch.objects.all())

    def test_the_admin_marks_it_and_edits_its_ports(self):
        client = self.client_for(self.admin)
        client.force_login(self.admin)
        response = client.post('/admin/api/switch/', {'action': 'mark_tool', '_selected_action': [self.ixia.id]})
        self.assertEqual(response.status_code, 302)
        self.ixia.refresh_from_db()
        self.assertTrue(self.ixia.is_tool)
        self.assertEqual(client.get(f'/admin/api/tool/{self.ixia.id}/change/').status_code, 200)
        port = self.tool_port()
        response = client.post(f'/admin/api/port/{port.id}/change/', {
            'switch': self.ixia.id, 'port_switch': '1/1', 'backbone': '10.0.0.100', 'port_backbone': '1/2/7',
            'status': 'DOWN'})
        self.assertEqual(response.status_code, 302, response.content[:2000])
        self.assertEqual(self.tool_port().port_backbone, '1/2/7')


@override_settings(**ON)
class NeverReservedNorCleanedUpTest(ToolsTestCase):

    def setUp(self):
        super().setUp()
        self.mark()

    def test_not_listed_for_reservation_nor_on_the_lab_status(self):
        listed = [s['id'] for s in self.call(self.alice, 'get', 'list_switch/').data['switchs']]
        self.assertNotIn(self.ixia.id, listed)
        status = [s['id'] for s in self.call(self.alice, 'get', 'lab_status/').data['switches']]
        self.assertNotIn(self.ixia.id, status)

    def test_reserve_refuses_it(self):
        self.assertEqual(self.reserve(switch=self.ixia).status_code, 404)
        self.assertFalse(Reservation.objects.filter(switch=self.ixia).exists())

    def test_the_sweep_leaves_it_alone_even_with_a_management_ip(self):
        Switch.everything.filter(pk=self.ixia.pk).update(mngt_IP='10.0.0.9')
        self.assertNotIn(self.ixia.id, [s.id for s in candidates()])

    def test_a_release_ends_its_link_and_cleans_up_only_the_switch(self):
        self.reserved()
        self.assertEqual(self.connect(self.alice, self.ports[0], self.tool_port()).status_code, 200)
        self.release()
        self.assertIsNone(self.tool_port().svlan)
        self.assertEqual(list(PendingCleanup.objects.values_list('switch_id', flat=True)), [self.switch.id])
        self.assertFalse(SwitchEvent.objects.filter(switch=self.ixia).exists())
        self.assertFalse(SwitchAccount.objects.filter(switch=self.ixia).exists())


@override_settings(**ON)
class LinkToAToolTest(ToolsTestCase):

    def setUp(self):
        super().setUp()
        self.mark()

    def test_a_member_links_a_switch_to_a_tool_port(self):
        self.reserved()
        response = self.connect(self.alice, self.ports[0], self.tool_port())
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.tool_port().svlan, Port.objects.get(pk=self.ports[0].pk).svlan)

    def test_whoever_links_first_gets_the_port(self):
        self.reserved()
        self.reserved(self.bob, self.switches[1])  # bob's own Project
        self.assertEqual(self.connect(self.alice, self.ports[0], self.tool_port()).status_code, 200)
        response = self.connect(self.bob, self.ports[1], self.tool_port())
        self.assertEqual(response.status_code, 400)
        self.assertIn('already connected', response.data['detail'])
        # The other port of the Tool is free: one Tool serves several Projects at once
        self.assertEqual(self.connect(self.bob, self.ports[1], self.tool_port(2)).status_code, 200)

    def test_only_who_may_work_on_the_switch_end(self):
        self.reserved()
        self.assertEqual(self.connect(self.bob, self.ports[0], self.tool_port()).status_code, 403)

    def test_two_tools_are_never_linked(self):
        response = self.connect(self.alice, self.tool_port(1), self.tool_port(2))
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.tool_port().svlan)

    def test_a_member_disconnects_it(self):
        self.reserved()
        self.connect(self.alice, self.ports[0], self.tool_port())
        response = self.call(self.alice, 'post', 'disconnect/', {'portA': self.tool_port().id,
                                                                 'portB': self.ports[0].id})
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(self.connect(self.bob, self.ports[0], self.tool_port()).status_code, 403)


@override_settings(**ON)
class ToolOnTheTopologyTest(ToolsTestCase):

    def setUp(self):
        super().setUp()
        self.mark()
        self.project = self.reserved().project

    def topology(self, user=None, project=None):
        response = self.call(user or self.alice, 'get', f'projects/{(project or self.project).id}/topology/')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_a_tool_is_drawn_once_a_link_reaches_it(self):
        self.assertNotIn(self.ixia.id, [s['id'] for s in self.topology()['switches']])
        self.connect(self.alice, self.ports[0], self.tool_port())
        drawn = {s['id']: s for s in self.topology()['switches']}
        self.assertTrue(drawn[self.ixia.id]['is_tool'])
        self.assertFalse(drawn[self.ixia.id]['in_topology'])

    def test_the_new_link_form_gets_every_free_tool_port(self):
        topology = self.topology()
        self.assertEqual([t['id'] for t in topology['tools']], [self.ixia.id])
        self.assertEqual({p['port_switch'] for p in topology['tool_ports']}, {'1/1', '1/2'})
        self.connect(self.alice, self.ports[0], self.tool_port())
        other = create(self.bob, 'Case 7')
        self.assertEqual({p['port_switch'] for p in self.topology(self.bob, other)['tool_ports']}, {'1/2'})

    def test_its_place_on_the_layout_is_kept(self):
        self.connect(self.alice, self.ports[0], self.tool_port())
        response = self.call(self.alice, 'put', f'projects/{self.project.id}/layout/',
                             {'positions': {str(self.ixia.id): {'x': 10, 'y': 20}}})
        self.assertIn(response.status_code, (200, 204), response.data)
        self.assertEqual(self.topology()['layout'][str(self.ixia.id)], {'x': 10.0, 'y': 20.0})

    def test_a_snapshot_keeps_the_link_to_the_tool_only_with_a_switch_of_the_project(self):
        self.connect(self.alice, self.ports[0], self.tool_port())
        self.assertEqual(project_links([self.switch.id]),
                         [sorted([{'switch': self.switch.id, 'port': '1/1/1'}, {'switch': self.ixia.id, 'port': '1/1'}],
                                 key=lambda end: (end['switch'], end['port']))])
        self.assertEqual(project_links([self.switches[1].id]), [])


@override_settings(**ON)
class RestoreALinkToAToolTest(SnapshotTestCase):
    """The Snapshot of alice's Project holds a Link from its third Switch to the IXIA's port 1/1."""

    def setUp(self):
        super().setUp()
        admin = User.objects.create_superuser('root', email='root@example.com', password='pw')
        self.ixia = make_ixia()
        tools.mark_tool(self.ixia, admin)
        self.tool_port = Port.objects.get(switch=self.ixia, port_switch='1/1')
        response = self.call(self.alice, 'post', 'connect/', {'portA': self.ports[2].id, 'portB': self.tool_port.id})
        self.assertEqual(response.status_code, 200, response.data)
        self.release_all()
        self.snapshot = Snapshot.objects.get(project=self.project)
        self.assertEqual(len(self.snapshot.links), 2)
        self.finish_cleanups()

    def outcomes(self):
        response = self.call(self.alice, 'post', f'projects/{self.project.id}/snapshots/{self.snapshot.id}/restore/')
        self.assertEqual(response.status_code, 201, response.data)
        self.run_restore()
        return {link['ends'][-1]['switch']: link['outcome'] for link in Restore.objects.get().links}

    def test_the_link_is_built_again_while_the_tool_port_is_free(self):
        self.assertEqual(self.outcomes()[self.ixia.id], 'built')
        self.tool_port.refresh_from_db()
        self.assertEqual(self.tool_port.svlan, Port.objects.get(pk=self.ports[2].pk).svlan)

    def test_a_tool_port_someone_took_meanwhile_is_left_to_them(self):
        other = make_switch(4)
        other_port = Port.objects.create(switch=other, port_switch='1/1/1', backbone='10.0.0.100', port_backbone='1/1/4')
        Reservation.objects.create(switch=other, user=self.carol, project=create(self.carol, 'Case 9'))
        response = self.call(self.carol, 'post', 'connect/', {'portA': other_port.id, 'portB': self.tool_port.id})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.outcomes()[self.ixia.id], 'failed')
        self.tool_port.refresh_from_db()
        self.assertEqual(self.tool_port.svlan, Port.objects.get(pk=other_port.pk).svlan)
