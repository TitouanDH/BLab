"""
A Release's Cleanup splits a VC member back to standalone (#120): its own flash is prepared through
its own IP, its VC's master reloads that member alone, and once it stands alone, running
certified/, it is reloaded from working/. Against fake devices that behave as .138/.139 did on
2026-11-05: a slave refuses every reload, a master's `reload chassis-id N all` reloads member N
alone, which boots certified/.
"""
from django.test import override_settings

from .lab_switch import VC_TOPOLOGY_COMMAND
from .models import PendingCleanup, Quarantine, Reservation, SwitchEvent
from .quarantine import VC_NOT_SPLIT
from .switch_worker import AFTER_SPLIT, CERTIFIED, RELOAD_GRACE, SPLIT, request_cleanup
from .test_release_cleanup import CleanupTestCase, make_switch

RELOAD = 'reload from working no rollback-timeout'


@override_settings(BLAB_DEVICES='fake', BLAB_CLEAN_STATE_MODE='enforce')
class VcSplitTest(CleanupTestCase):
    def setUp(self):
        super().setUp()
        self.master = self.switch  # 10.0.0.1, chassis 1
        self.slave = make_switch(2)  # 10.0.0.2, chassis 2, the same model
        self.fake.form_vc(self.master.mngt_IP, self.slave.mngt_IP)

    def commands(self, ip):
        return [cmd for at, cmd in self.fake.commands if at == ip]

    def cleanup_event(self, switch):
        return switch.events.get(kind=SwitchEvent.CLEANUP)

    def split(self, switch):
        """Lets the worker split the Switch: its chassis reload, then its reload from working/, then its Inspection."""
        outcomes = self.worker.work()
        for _ in range(2):
            self.clock.advance(RELOAD_GRACE)
            outcomes += self.worker.work()
        return outcomes

    def assert_standalone_and_clean(self, switch):
        self.assertEqual(self.fake.vc_members(switch.mngt_IP), {switch.mngt_IP})
        self.assertNotIn(switch.mngt_IP, self.fake.runs_from)  # back on working/, so certify took it
        inspection = switch.last_inspection()
        self.assertTrue(inspection.ok, inspection.reasons)
        self.assertFalse(Quarantine.objects.filter(switch=switch).exists())
        self.assertFalse(PendingCleanup.objects.filter(switch=switch).exists())
        self.assertIn(CERTIFIED, self.cleanup_event(switch).reasons)

    def test_a_released_slave_is_split_by_its_master_which_is_never_reloaded(self):
        Reservation.objects.create(switch=self.master, user=self.bob)
        request_cleanup(self.slave, self.alice)
        outcomes = self.split(self.slave)
        self.assertEqual(outcomes, [
            "Cleanup of 10.0.0.2: reloading. Split from its VC: 'reload chassis-id 2 all' sent by its master 10.0.0.1",
            'Cleanup of 10.0.0.2: split from its VC, reloading from working/',
            'Cleaned up 10.0.0.2: clean'])
        # Its own flash through its own IP, certified/ included: the chassis reload boots it
        slave = self.commands('10.0.0.2')
        self.assertLess(slave.index('cp -r init/* working/'), slave.index('cp -r init/* certified/'))
        self.assertIn(RELOAD, slave)  # once it stood alone, its own master
        self.assertIn('reload chassis-id 2 all', self.commands('10.0.0.1'))
        self.assertNotIn(RELOAD, self.commands('10.0.0.1'))
        self.assertEqual(self.fake.reloads, ['10.0.0.2', '10.0.0.2'])
        self.assert_standalone_and_clean(self.slave)
        self.assertTrue(any(note.startswith(AFTER_SPLIT) for note in self.cleanup_event(self.slave).reasons))
        self.assertEqual(self.fake.vc_members('10.0.0.1'), {'10.0.0.1'})

    def test_a_released_master_reloads_its_own_chassis_and_the_slave_takes_over(self):
        Reservation.objects.create(switch=self.slave, user=self.bob)
        request_cleanup(self.master, self.alice)
        self.split(self.master)
        self.assertIn('reload chassis-id 1 all', self.commands('10.0.0.1'))
        # Never reload from working while it is a VC's master: that reloads every member
        self.assertEqual(self.fake.reloads, ['10.0.0.1', '10.0.0.1'])
        self.assert_standalone_and_clean(self.master)
        self.assertNotIn('10.0.0.2', self.fake.vc_masters)  # the slave is now its own master, still up
        self.assertEqual(self.commands('10.0.0.2'), [])

    def test_a_member_whose_master_isnt_blabs_is_quarantined_naming_nobody(self):
        # As .131, whose master is 10.69.144.176: BLab sends nothing to it, and doesn't reload .131
        self.fake.form_vc('10.0.0.9', self.slave.mngt_IP)
        request_cleanup(self.slave, self.alice)
        self.worker.work()
        self.worker.work()
        self.assertEqual(self.commands('10.0.0.9'), [])
        self.assertEqual(self.fake.reloads, [])
        self.assertNotIn('cp -r init/* working/', self.commands('10.0.0.2'))
        quarantine = Quarantine.objects.get(switch=self.slave)
        self.assertIsNone(quarantine.holder)
        self.assertIn(VC_NOT_SPLIT, quarantine.reasons[0])
        self.assertIn('2c:fa:a2:00:00:09', quarantine.reasons[0])

    def test_a_slaves_refusal_is_not_taken_for_a_reload(self):
        # Even when it is sent the reload: the role is read first, and only the master's session reloads it
        Reservation.objects.create(switch=self.master, user=self.bob)
        request_cleanup(self.slave, self.alice)
        self.worker.work()
        self.assertNotIn(RELOAD, self.commands('10.0.0.2'))
        self.assertEqual(self.fake.reloads, ['10.0.0.2'])

    def test_one_vc_split_at_a_time(self):
        # Both members released: while the slave's split is under way, AOS may still list it on the
        # master, whose own Cleanup then waits rather than racing it through the same master
        two = self.fake.show(self.master.mngt_IP, VC_TOPOLOGY_COMMAND)
        request_cleanup(self.slave, self.alice)
        self.worker.work()
        self.fake.outputs[self.master.mngt_IP] = {VC_TOPOLOGY_COMMAND: two}
        request_cleanup(self.master, self.alice)
        self.worker.work()
        self.assertIsNone(PendingCleanup.objects.get(switch=self.master).started_at)
        self.assertNotIn('cp -r init/* working/', self.commands('10.0.0.1'))
        self.clock.advance(RELOAD_GRACE)
        self.worker.work()  # the slave reloads from working/; the master still waits
        self.assertIsNone(PendingCleanup.objects.get(switch=self.master).started_at)
        self.clock.advance(RELOAD_GRACE)
        self.worker.work()  # the slave's Cleanup is done
        self.assertFalse(PendingCleanup.objects.filter(switch=self.slave).exists())
        del self.fake.outputs[self.master.mngt_IP]  # it stands alone by now
        self.assertEqual(self.worker.work(), ['Cleanup of 10.0.0.1: reloading'])
        self.assertIn(RELOAD, self.commands('10.0.0.1'))

    def test_a_member_that_rejoins_the_vc_is_not_reloaded_from_working(self):
        # certified/ without the Golden VC setup (it can't be copied): the member boots back into the VC
        Reservation.objects.create(switch=self.master, user=self.bob)
        self.fake.fail_on('cp -r init/* certified/', ip='10.0.0.2', exit_status=1)
        request_cleanup(self.slave, self.alice)
        self.worker.work()
        self.clock.advance(RELOAD_GRACE)
        self.worker.work()
        self.assertNotIn(RELOAD, self.commands('10.0.0.2') + self.commands('10.0.0.1'))
        self.assertEqual(self.fake.vc_members('10.0.0.2'), {'10.0.0.1', '10.0.0.2'})

    def test_the_last_member_of_a_vc_of_one_takes_the_normal_path(self):
        self.fake.form_vc(self.slave.mngt_IP)
        request_cleanup(self.slave, self.alice)
        self.assertEqual(self.worker.work(), ['Cleanup of 10.0.0.2: reloading'])
        self.assertFalse(any(note.startswith(SPLIT) for note in self.cleanup_event(self.slave).reasons))
