"""Missing cables (#57): a Port whose link is down, found by the Inspection and shown on the Topology."""
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from . import fake_devices, links, missing_cables
from .backbone import parse_links_up
from .inspection import inspect
from .link_worker import LinkWorker
from .models import Port, Quarantine, Reservation
from .quarantine import recheck
from .test_release_cleanup import CleanupTestCase
from .tests import make_switches

BB = '10.0.0.100'
ENFORCE = override_settings(BLAB_MISSING_CABLE_MODE='enforce')
REPORT = override_settings(BLAB_MISSING_CABLE_MODE='report')

# 10.69.144.130, 2026-10-20
ALIAS = """ Chas/
 Slot/     Admin     Link        WTR      WTS       Alias
 Port      Status   Status      (sec)    (msec)
--------+----------+---------+----------+----------+-----------------------
 1/1/1     enable     down    0          0          ""
 1/1/2     disable    down    0          0          ""
 1/2/7     enable     up      0          0          ""
"""


class ParsingTest(SimpleTestCase):
    def test_each_ports_link_from_show_interfaces_alias(self):
        self.assertEqual(parse_links_up(ALIAS), {'1/1/1': False, '1/1/2': False, '1/2/7': True})

    def test_an_answer_without_ports_lists_none(self):
        self.assertEqual(parse_links_up('ERROR: Invalid entry'), {})


@override_settings(BLAB_DEVICES='fake', BLAB_MISSING_CABLE_MODE='report')
class InspectionTest(TestCase):
    """A Switch nobody holds: its Ports' UNIs are disabled at rest, so the Inspection enables them a moment."""
    IP = '10.0.0.1'

    def setUp(self):
        self.switch_fake = fake_devices.lab_switches
        self.switch_fake.reset()
        self.bb = fake_devices.backbone
        self.bb.reset()
        [self.switch] = make_switches(1)
        self.port = self.make_port('1/1/1', '1/2/1')
        self.alice = User.objects.create_user('alice', email='alice@example.com', password='pw')

    def make_port(self, port, uni):
        self.bb.cli(BB, f'interfaces {uni} admin-state disable')  # as a UNI without a Link is
        return Port.objects.create(switch=self.switch, port_switch=port, backbone=BB, port_backbone=uni)

    def sent_to_backbone(self):
        return [cmd for ip, cmd in self.bb.commands if not cmd.startswith('show')]

    def test_a_cabled_port_is_found_by_enabling_its_uni_for_a_moment(self):
        self.bb.commands.clear()
        result = inspect(self.switch)
        self.assertTrue(result.clean, result.reasons)
        self.assertEqual(result.warnings, [])
        self.assertEqual(self.sent_to_backbone(), ['interfaces 1/2/1 admin-state enable',
                                                   'interfaces 1/2/1 admin-state disable'])
        self.assertIn('1/2/1', self.bb.disabled[BB])

    def test_a_port_up_already_needs_no_uni_enabled(self):
        self.bb.cli(BB, 'interfaces 1/2/1 admin-state enable')
        self.bb.commands.clear()
        self.assertTrue(inspect(self.switch).clean)
        self.assertEqual(self.bb.commands, [])

    def test_a_missing_cable_is_only_a_warning_in_report_mode(self):
        self.switch_fake.unplug(self.IP, '1/1/1')
        result = inspect(self.switch)
        self.assertTrue(result.clean)
        self.assertEqual(result.warnings, ['Missing cable (BLAB_MISSING_CABLE_MODE=report): '
                                           '1/1/1 (UNI 1/2/1 on backbone 10.0.0.100)'])
        self.assertIn('1/2/1', self.bb.disabled[BB], 'the UNI is disabled again')

    @ENFORCE
    def test_a_missing_cable_fails_the_inspection_in_enforce_mode(self):
        self.make_port('1/1/3', '1/2/3')
        self.make_port('1/1/2', '1/2/2')
        self.switch_fake.unplug(self.IP, '1/1/3', '1/1/2')
        result = inspect(self.switch)
        self.assertFalse(result.clean)
        self.assertEqual(result.reasons, ['Missing cable: 1/1/2 (UNI 1/2/2 on backbone 10.0.0.100), '
                                          '1/1/3 (UNI 1/2/3 on backbone 10.0.0.100)'])
        self.assertEqual(self.bb.disabled[BB], {'1/2/1', '1/2/2', '1/2/3'})

    @ENFORCE
    def test_a_backbone_it_cant_read_is_unreadable_not_a_missing_cable(self):
        self.switch_fake.unplug(self.IP, '1/1/1')
        self.bb.fail_on('show configuration snapshot interface', ip=BB)
        result = inspect(self.switch)
        self.assertEqual(result.reasons, ['unreadable: backbone 10.0.0.100 (looking for Missing cables)'])

    @ENFORCE
    def test_a_uni_it_cant_enable_is_disabled_again_with_the_others(self):
        self.make_port('1/1/2', '1/2/2')
        self.bb.fail_on('interfaces 1/2/2 admin-state enable')
        result = inspect(self.switch)
        self.assertEqual(result.reasons, ['unreadable: backbone 10.0.0.100 (looking for Missing cables)'])
        self.assertEqual(self.bb.disabled[BB], {'1/2/1', '1/2/2'})

    def test_a_uni_it_cant_disable_again_is_a_warning(self):
        self.bb.fail_on('interfaces 1/2/1 admin-state disable')
        result = inspect(self.switch)
        self.assertTrue(result.clean)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("UNI 1/2/1 on backbone 10.0.0.100 was enabled to look for Missing cables and couldn't "
                      "be disabled again", result.warnings[0])

    @ENFORCE
    def test_a_link_left_on_a_switch_nobody_holds_is_left_to_the_link_worker(self):
        # A teardown under way: its UNI already disabled. Neither enabled nor a Missing cable
        Port.objects.filter(id=self.port.id).update(svlan=1001)
        self.bb.commands.clear()
        self.assertTrue(inspect(self.switch).clean)
        self.assertEqual(self.sent_to_backbone(), [])

    @ENFORCE
    def test_a_port_show_interfaces_does_not_list_is_unreadable_not_missing(self):
        self.make_port('1/9/1', '1/2/9')  # a module that is gone, or a Port recorded with a typo
        self.switch_fake.outputs[self.IP] = {'show interfaces': "Chassis/Slot/Port 1/1/1 :\n Operational Status : up,\n"}
        result = inspect(self.switch)
        self.assertEqual(result.reasons, ['unreadable: show interfaces port 1/9/1 (looking for Missing cables)'])

    def test_a_uni_a_connect_took_meanwhile_stays_enabled(self):
        def connect_meanwhile():
            Port.objects.filter(id=self.port.id).update(svlan=1001)
            return {}
        self.switch_fake.unplug(self.IP, '1/1/1')
        missing_cables.find(self.switch, {'1/1/1': 'down'}, reserved=False, read_states=connect_meanwhile)
        self.assertNotIn('1/2/1', self.bb.disabled[BB])

    @override_settings(BLAB_MISSING_CABLE_MODE='off')
    def test_off_enables_nothing_and_finds_nothing_on_a_switch_nobody_holds(self):
        self.switch_fake.unplug(self.IP, '1/1/1')
        self.bb.commands.clear()
        result = inspect(self.switch)
        self.assertEqual((result.reasons, result.warnings), ([], []))
        self.assertEqual(self.bb.commands, [])

    @ENFORCE
    def test_a_switch_without_ports_has_no_missing_cable(self):
        Port.objects.all().delete()
        self.assertTrue(inspect(self.switch).clean)

    @ENFORCE
    def test_on_a_reserved_switch_only_a_links_missing_cable_is_shown(self):
        Reservation.objects.create(switch=self.switch, user=self.alice)
        self.make_port('1/1/2', '1/2/2')
        self.bb.cli(BB, 'interfaces 1/2/2 admin-state enable')
        Port.objects.filter(port_switch='1/1/2').update(svlan=1001)
        self.switch_fake.unplug(self.IP, '1/1/2')
        self.bb.commands.clear()
        result = inspect(self.switch)
        self.assertTrue(result.clean)
        self.assertEqual(result.warnings, ['Missing cable: 1/1/2 (UNI 1/2/2 on backbone 10.0.0.100)'])
        self.assertEqual(self.sent_to_backbone(), [], 'nothing is enabled on a held Switch')


@override_settings(BLAB_DEVICES='fake', BLAB_MISSING_CABLE_MODE='enforce')
class QuarantineTest(CleanupTestCase):
    """The Quarantine a Missing cable causes names nobody; plugging it back and a Re-check lift it."""

    def setUp(self):
        super().setUp()
        fake_devices.backbone.cli(BB, 'interfaces 1/2/1 admin-state disable')
        self.port = Port.objects.create(switch=self.switch, port_switch='1/1/1', backbone=BB, port_backbone='1/2/1')

    def test_after_a_cleanup_it_names_nobody(self):
        self.fake.unplug('10.0.0.1', '1/1/1')
        self.reserve()
        self.release(self.alice)
        self.assertEqual(self.run_cleanup(), [
            'Cleaned up 10.0.0.1: not clean, Quarantined naming nobody, for an admin: '
            'Missing cable: 1/1/1 (UNI 1/2/1 on backbone 10.0.0.100)'])
        self.assertIsNone(Quarantine.objects.get().holder)

    def test_with_an_unwanted_cable_too_it_names_the_holder(self):
        self.fake.unplug('10.0.0.1', '1/1/1')
        self.fake.cable('10.0.0.1', '1/1/5')
        self.reserve()
        self.release(self.alice)
        self.run_cleanup()
        self.assertEqual(Quarantine.objects.get().holder, self.alice)

    def test_an_unreachable_switch_names_nobody(self):
        self.reserve()
        self.release(self.alice)
        self.worker.work()
        self.fake.unreachable.add('10.0.0.1')
        self.clock.advance(self.worker.timeout)
        self.worker.work()
        self.assertIsNone(Quarantine.objects.get().holder)

    def test_plugging_it_back_and_a_recheck_lift_it(self):
        self.fake.unplug('10.0.0.1', '1/1/1')
        self.reserve()
        self.release(self.alice)
        self.run_cleanup()
        self.fake.unplugged.clear()
        recheck(self.switch, self.bob)
        self.assertIsNotNone(Quarantine.objects.get().lifted_at)

    def test_an_admin_removing_the_port_and_a_recheck_lift_it(self):
        self.fake.unplug('10.0.0.1', '1/1/1')
        self.reserve()
        self.release(self.alice)
        self.run_cleanup()
        self.port.delete()
        recheck(self.switch, self.bob)
        self.assertIsNotNone(Quarantine.objects.get().lifted_at)


@override_settings(BLAB_DEVICES='fake')
class TopologyTest(TestCase):
    """On a held Switch, the Link worker shows a Link's Missing cable on its Topology, like a Ghost Link."""

    def setUp(self):
        self.bb = fake_devices.backbone
        self.bb.reset()
        fake_devices.lab_switches.reset()
        self.alice = User.objects.create_user('alice', email='alice@example.com', password='pw')
        self.client = APIClient()
        self.client.force_authenticate(self.alice)
        self.switches = make_switches(2)
        ports = []
        for n, switch in enumerate(self.switches):
            Reservation.objects.create(switch=switch, user=self.alice)
            self.bb.cli(BB, f'interfaces 1/2/{n + 1} admin-state disable')
            ports.append(Port.objects.create(switch=switch, port_switch='1/1/5', backbone=BB,
                                             port_backbone=f'1/2/{n + 1}'))
        links.connect(*ports)
        self.worker = LinkWorker()

    def topology_link(self):
        [link] = self.client.get(f'/api/topology/{self.alice.id}/').data['links']
        return link

    def test_a_links_missing_cable_is_shown_once_seen_twice(self):
        fake_devices.lab_switches.unplug(self.switches[1].mngt_IP, '1/1/5')
        self.worker.reconcile()
        self.assertIsNone(self.topology_link()['ghost_reason'])
        self.worker.reconcile()
        self.assertEqual(self.topology_link()['ghost_reason'],
                         f"Missing cable: 1/1/5 on OS6860 ({self.switches[1].mngt_IP}) has no link to backbone {BB} "
                         "port 1/2/2. Check the cable, or tell an admin.")

    def test_the_lab_status_page_shows_it_on_that_switch_only(self):
        fake_devices.lab_switches.unplug(self.switches[1].mngt_IP, '1/1/5')
        self.worker.reconcile()
        self.worker.reconcile()
        rows = {row['id']: row for row in self.client.get('/api/lab_status/').data['switches']}
        self.assertEqual(rows[self.switches[0].id]['missing_cables'], [])
        [line] = rows[self.switches[1].id]['missing_cables']
        self.assertTrue(line.startswith(f"Missing cable: 1/1/5 on OS6860 ({self.switches[1].mngt_IP}) has no link"))

    def test_it_is_cleared_once_plugged_back(self):
        fake_devices.lab_switches.unplug(self.switches[1].mngt_IP, '1/1/5')
        self.worker.reconcile()
        self.worker.reconcile()
        fake_devices.lab_switches.unplugged.clear()
        self.worker.reconcile()
        self.assertIsNone(self.topology_link()['ghost_reason'])

    def test_a_cabled_link_shows_nothing(self):
        self.worker.reconcile()
        self.worker.reconcile()
        self.assertIsNone(self.topology_link()['ghost_reason'])

    def test_a_backbone_that_cant_list_its_links_keeps_what_was_recorded(self):
        fake_devices.lab_switches.unplug(self.switches[1].mngt_IP, '1/1/5')
        self.worker.reconcile()
        self.worker.reconcile()
        self.bb.fail_on('show interfaces alias')
        with self.assertLogs('api.reconcile', 'WARNING'):
            self.worker.reconcile()
        self.assertTrue(self.topology_link()['ghost_reason'].startswith('Missing cable: 1/1/5'))
