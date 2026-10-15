"""Ghost Links recorded by the Link worker's Reconcile, cleared once carried, shown on the Topology."""
from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import fake_devices, links
from .backbone import Backbone, backbone
from .link_worker import LinkWorker
from .models import Port, Reservation
from .tests import make_switches


@override_settings(BLAB_DEVICES='fake')
class GhostLinkRecordTest(TestCase):
    BB, BB2 = '10.0.0.100', '10.0.0.200'

    def setUp(self):
        self.fake = fake_devices.backbone
        self.fake.reset()
        self.alice = User.objects.create_user('alice', email='alice@example.com', password='pw')
        self.client = APIClient()
        self.client.force_authenticate(self.alice)
        self.switches = make_switches(2)
        for switch in self.switches:
            Reservation.objects.create(switch=switch, user=self.alice)
        self.a = self.port(0, '1/1/1')
        self.b = self.port(1, '1/1/2')
        self.worker = LinkWorker()

    def port(self, switch, uni, bb=BB):
        self.fake.cli(bb, f'interfaces {uni} admin-state disable')  # as an unlinked UNI is
        return Port.objects.create(switch=self.switches[switch], port_switch=f'1/1/{switch + 5}',
                                   backbone=bb, port_backbone=uni)

    def topology_link(self):
        [link] = self.client.get(f'/api/topology/{self.alice.id}/').data['links']
        return link

    def make_ghost(self):
        link = links.connect(self.a, self.b)
        backbone(self.BB).remove_service(link.svlan)
        return link

    def test_a_ghost_link_is_recorded_once_seen_twice_with_why_and_when(self):
        self.make_ghost()
        self.worker.reconcile()
        self.assertIsNone(self.topology_link()['ghost_reason'], 'a Link being built must not show as a ghost')

        self.worker.reconcile()
        link = self.topology_link()
        self.assertEqual(link['ghost_reason'],
                         f"This Link is not carried by backbone {self.BB}: its Service there is missing.")
        self.assertIsNotNone(link['ghost_seen_at'])

    def test_recording_ghost_links_sends_nothing_but_reads_to_the_backbones(self):
        self.make_ghost()
        sent = len(self.fake.commands)
        self.worker.reconcile()
        self.worker.reconcile()
        self.assertTrue(self.topology_link()['ghost_reason'])
        self.assertTrue(all(cmd.startswith('show') for _, cmd in self.fake.commands[sent:]))

    def test_it_is_cleared_as_soon_as_a_reconcile_finds_the_link_carried(self):
        link = self.make_ghost()
        self.worker.reconcile()
        self.worker.reconcile()
        Backbone(self.BB, self.fake.cli).configure_service(link.svlan, 'blab_1001', ['1/1/1', '1/1/2'])

        self.worker.reconcile()
        link = self.topology_link()
        self.assertEqual((link['ghost_reason'], link['ghost_seen_at']), (None, None))
        self.assertFalse(Port.objects.filter(ghost_svlan__isnull=False).exists())

    def test_it_is_cleared_when_the_repair_builds_the_link_again(self):
        self.make_ghost()
        self.worker.reconcile()
        self.worker.reconcile()
        call_command('audit_links', '--repair', stdout=StringIO())
        self.assertIsNone(self.topology_link()['ghost_reason'])

    def test_a_disabled_uni_names_the_switch_port_it_faces(self):
        links.connect(self.a, self.b)
        self.fake.cli(self.BB, 'interfaces 1/1/2 admin-state disable')
        self.worker.reconcile()
        self.worker.reconcile()
        self.assertEqual(self.topology_link()['ghost_reason'],
                         f"This Link is not carried by backbone {self.BB}: the backbone port facing "
                         f"1/1/6 on OS6860 (10.0.0.2) is disabled.")

    def test_a_link_on_an_unreachable_backbone_keeps_what_was_recorded(self):
        self.make_ghost()
        self.worker.reconcile()
        self.worker.reconcile()
        self.fake.fail_on('show', ip=self.BB, times=10)
        self.worker.reconcile()
        self.assertTrue(self.topology_link()['ghost_reason'])

    def test_each_backbone_not_carrying_it_gives_its_reason(self):
        c, d = self.port(0, '1/1/7', self.BB), self.port(1, '1/1/8', self.BB2)
        link = links.connect(c, d)
        backbone(self.BB).remove_service(link.svlan)
        backbone(self.BB2).remove_service(link.svlan)
        self.worker.reconcile()
        self.worker.reconcile()
        [shown] = self.client.get(f'/api/topology/{self.alice.id}/').data['links']
        self.assertEqual(shown['ghost_reason'].splitlines(), [
            f"This Link is not carried by backbone {self.BB}: its Service there is missing.",
            f"This Link is not carried by backbone {self.BB2}: its Service there is missing.",
        ])

    def test_disconnecting_forgets_it(self):
        link = self.make_ghost()
        self.worker.reconcile()
        self.worker.reconcile()
        links.disconnect(link)
        self.assertFalse(Port.objects.filter(ghost_reason__isnull=False).exists())

    def test_a_port_relinked_by_older_code_on_another_svlan_shows_no_old_reason(self):
        self.make_ghost()
        self.worker.reconcile()
        self.worker.reconcile()
        # Production's older code knows nothing of the ghost fields
        Port.objects.filter(id__in=[self.a.id, self.b.id]).update(svlan=1002)
        self.assertIsNone(self.topology_link()['ghost_reason'])

    def test_a_ghost_link_not_seen_again_for_a_while_is_no_longer_shown(self):
        # Whoever Reconciles now may not record Ghost Links (production's older Link worker)
        self.make_ghost()
        self.worker.reconcile()
        self.worker.reconcile()
        Port.objects.update(ghost_seen_at=timezone.now() - links.GHOST_SHOWN_FOR - timedelta(minutes=1))
        self.assertIsNone(self.topology_link()['ghost_reason'])
