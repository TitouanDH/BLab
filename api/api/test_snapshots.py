"""
Snapshots and Restore onto the same Switches (#45, CONTEXT.md), behind BLAB_PROJECTS: Save with a
note, the Snapshot before every Release (one per wind-down), the last 10 kept, Restore of free
and busy Switches with per-Switch progress, the AOS release warning, members only, and nothing
with Projects off. Against fake devices.
"""
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

from .fake_devices import UPTIME
from .lab_switch import WORKING_CONFIG_PATH, WORKING_VC_SETUP_PATH
from .models import (PendingCleanup, Port, Project, ProjectSwitchPosition, Quarantine, Reservation, Restore,
                     RestoreSwitch, Snapshot, SnapshotSwitch)
from .release import expire, release
from . import snapshots
from .snapshots import KEPT, prune
from .switch_worker import LOOK_AGAIN_AFTER, RELOAD_GRACE, RELOAD_TIMEOUT
from .test_projects import OFF, ProjectsTestCase
from .test_switch_password import ON

RELOAD = 'reload from working no rollback-timeout'


def config(name):
    return f'! Chassis:\nsystem name "{name}"\n! VLAN:\nvlan 10 admin-state enable\n'


class SnapshotTestCase(ProjectsTestCase):
    """Alice's Project holds the three Switches, Linked 1-2, each with a saved config of its own; bob is a member."""

    def setUp(self):
        super().setUp()
        with self.settings(**ON):
            self.project = self.reserved().project
            for switch in self.switches[1:]:
                self.reserved(switch=switch)
            self.join(self.bob, self.project)
            response = self.call(self.alice, 'post', 'connect/', {'portA': self.ports[0].id, 'portB': self.ports[1].id})
            self.assertEqual(response.status_code, 200, response.data)
        for n, switch in enumerate(self.switches, 1):
            self.fake.written.setdefault(switch.mngt_IP, {})[WORKING_CONFIG_PATH] = config(f'mine-{n}')
        ProjectSwitchPosition.objects.create(project=self.project, switch=self.switch, x=10, y=20)

    def snapshots_of(self, user=None):
        return self.call(user or self.alice, 'get', f'projects/{self.project.id}/snapshots/')

    def save(self, note='', user=None):
        return self.call(user or self.alice, 'post', f'projects/{self.project.id}/snapshots/', {'note': note})

    def saved(self, note=''):
        response = self.save(note)
        self.assertEqual(response.status_code, 201, response.data)
        return Snapshot.objects.get(pk=response.data['id'])

    def release_all(self):
        for switch in self.switches:
            result = release(Reservation.objects.get(switch=switch), self.alice)
            self.assertTrue(result.released, result.failures)

    def finish_cleanups(self):
        """Lets the Switch worker Clean every released Switch up, so they are free again."""
        self.worker.work()
        self.clock.advance(RELOAD_GRACE)
        self.worker.work()
        self.assertFalse(PendingCleanup.objects.exists())

    def restore_path(self, snapshot):
        return f'projects/{self.project.id}/snapshots/{snapshot.id}/restore/'

    def run_restore(self):
        """Lets the Switch worker load and reload every Switch, then look once they are back."""
        self.worker.restores()
        self.clock.advance(RELOAD_GRACE)
        return self.worker.restores()


@override_settings(**ON)
class SaveTest(SnapshotTestCase):

    def test_save_holds_each_switch_config_release_links_and_layout(self):
        self.fake.releases[self.switches[2].mngt_IP] = '8.9.107.R02'
        self.fake.written[self.switch.mngt_IP][WORKING_VC_SETUP_PATH] = 'virtual-chassis chassis-id 1\n'
        self.fake.files(self.switch.mngt_IP)['working'].add('vcsetup.cfg')
        snapshot = self.saved('before the upgrade test')
        self.assertEqual((snapshot.reason, snapshot.note, snapshot.taken_by), ('save', 'before the upgrade test', self.alice))
        entries = {e.switch_id: e for e in snapshot.switches.all()}
        self.assertEqual(set(entries), {s.id for s in self.switches})
        self.assertEqual(entries[self.switch.id].vcboot, config('mine-1'))
        self.assertEqual(entries[self.switch.id].vcsetup, 'virtual-chassis chassis-id 1\n')
        self.assertIsNone(entries[self.switches[1].id].vcsetup)
        self.assertEqual(entries[self.switches[2].id].aos_release, '8.9.107.R02')
        self.assertEqual(entries[self.switch.id].aos_release, '8.10.86.R04')
        self.assertEqual(snapshot.links, [[{'switch': self.switch.id, 'port': '1/1/1'},
                                           {'switch': self.switches[1].id, 'port': '1/1/1'}]])
        self.assertEqual(snapshot.layout, {str(self.switch.id): {'x': 10.0, 'y': 20.0}})
        # Only reads: nothing written, nothing reloaded
        self.assertEqual(self.fake.reloads, [])

    def test_the_listing_gives_no_config_and_is_for_members_only(self):
        self.saved('a note')
        listing = self.snapshots_of(self.bob)
        self.assertEqual(listing.status_code, 200)
        [shown] = listing.data['snapshots']
        self.assertEqual((shown['note'], shown['taken_by'], shown['reason'], shown['links']), ('a note', 'alice', 'save', 1))
        self.assertEqual(len(shown['switches']), 3)
        self.assertNotIn('mine-1', str(listing.data))
        self.assertEqual(listing.data['kept'], KEPT)
        self.assertIsNone(listing.data['restore'])
        self.assertEqual(self.snapshots_of(self.carol).status_code, 404)
        self.assertEqual(self.save(user=self.carol).status_code, 404)

    def test_an_unreachable_switch_is_recorded_with_why(self):
        self.fake.unreachable.add(self.switches[2].mngt_IP)
        snapshot = self.saved()
        entry = snapshot.switches.get(switch=self.switches[2])
        self.assertIsNone(entry.vcboot)
        self.assertIn('Cannot connect', entry.read_error)
        self.assertFalse(self.snapshots_of().data['snapshots'][0]['switches'][2]['restorable'])

    def test_a_note_too_long_or_a_project_without_switches_is_refused(self):
        self.assertEqual(self.save('x' * 201).status_code, 400)
        Reservation.objects.filter(project=self.project).delete()
        response = self.save()
        self.assertEqual(response.status_code, 400)
        self.assertIn('nothing to save', response.data['detail'])

    def test_the_last_10_are_kept(self):
        first = [self.saved(f'save {n}') for n in range(KEPT + 2)]
        kept = list(Snapshot.objects.filter(project=self.project).values_list('note', flat=True))
        self.assertEqual(len(kept), KEPT)
        self.assertNotIn('save 0', kept)
        self.assertNotIn('save 1', kept)
        self.assertFalse(SnapshotSwitch.objects.filter(snapshot_id=first[0].id).exists())

    def test_a_snapshot_a_restore_reads_is_not_pruned_until_it_is_done(self):
        oldest = self.saved('oldest')
        restore = Restore.objects.create(project=self.project, snapshot=oldest, started_by=self.alice)
        for n in range(KEPT):
            Snapshot.objects.create(project=self.project, reason=Snapshot.RELEASE)
        self.assertEqual(prune(self.project), 0)
        self.assertTrue(Snapshot.objects.filter(pk=oldest.pk).exists())
        Restore.objects.filter(pk=restore.pk).update(finished_at=timezone.now())
        self.assertEqual(prune(self.project), 1)
        self.assertFalse(Snapshot.objects.filter(pk=oldest.pk).exists())


@override_settings(**ON)
class BeforeReleaseTest(SnapshotTestCase):

    def test_a_release_snapshots_the_switch_and_its_links_before_tearing_them_down(self):
        release(Reservation.objects.get(switch=self.switch), self.bob)
        snapshot = Snapshot.objects.get(project=self.project)
        self.assertEqual((snapshot.reason, snapshot.taken_by), ('release', self.bob))
        self.assertEqual([e.switch_id for e in snapshot.switches.all()], [self.switch.id])
        self.assertEqual(snapshot.switches.get().vcboot, config('mine-1'))
        self.assertEqual(len(snapshot.links), 1)
        self.assertEqual(snapshot.layout, {str(self.switch.id): {'x': 10.0, 'y': 20.0}})
        self.assertIsNone(Port.objects.get(pk=self.ports[0].pk).svlan)  # torn down after

    def test_releasing_the_project_switch_after_switch_makes_one_snapshot(self):
        self.release_all()
        [snapshot] = Snapshot.objects.filter(project=self.project)
        self.assertEqual({e.switch_id for e in snapshot.switches.all()}, {s.id for s in self.switches})
        self.assertEqual(len(snapshot.links), 1)  # once, though both its ends were released

    def test_a_save_or_a_new_reservation_in_between_starts_another(self):
        release(Reservation.objects.get(switch=self.switch), self.alice)
        self.saved()
        release(Reservation.objects.get(switch=self.switches[1]), self.alice)
        self.finish_cleanups()
        self.reserved(switch=self.switch, project=self.project.id)
        release(Reservation.objects.get(switch=self.switches[2]), self.alice)
        reasons = list(Snapshot.objects.filter(project=self.project).order_by('taken_at', 'id')
                       .values_list('reason', flat=True))
        self.assertEqual(reasons, ['release', 'save', 'release', 'release'])

    def test_expiry_snapshots_too_and_an_unreachable_switch_doesnt_stop_the_release(self):
        self.fake.unreachable.add(self.switch.mngt_IP)
        result = expire(Reservation.objects.get(switch=self.switch))
        self.assertTrue(result.released)
        entry = Snapshot.objects.get(project=self.project).switches.get()
        self.assertIsNone(Snapshot.objects.get(project=self.project).taken_by)
        self.assertIn('Cannot connect', entry.read_error)

    def test_a_second_release_at_once_leaves_the_snapshot_as_the_first_found_it(self):
        stale = Reservation.objects.get(switch=self.switch)
        release(Reservation.objects.get(switch=self.switch), self.alice)
        entry = Snapshot.objects.get(project=self.project).switches.get()
        self.fake.written[self.switch.mngt_IP][WORKING_CONFIG_PATH] = 'wiped by its Cleanup'
        self.assertIsNone(snapshots.before_release(stale, None))  # (#68)
        self.assertEqual(Snapshot.objects.get(project=self.project).switches.get().vcboot, entry.vcboot)

    def test_nothing_with_projects_off(self):
        with self.settings(**OFF):
            release(Reservation.objects.get(switch=self.switch), self.alice)
        self.assertFalse(Snapshot.objects.exists())

    def test_release_snapshots_count_towards_the_last_10(self):
        for n in range(KEPT):
            self.saved(f'save {n}')
        release(Reservation.objects.get(switch=self.switch), self.alice)
        self.assertEqual(Snapshot.objects.filter(project=self.project).count(), KEPT)
        self.assertFalse(Snapshot.objects.filter(note='save 0').exists())


@override_settings(**ON)
class RestoreTest(SnapshotTestCase):

    def setUp(self):
        super().setUp()
        self.release_all()
        self.snapshot = Snapshot.objects.get(project=self.project)
        self.finish_cleanups()
        self.fake.commands.clear()
        self.fake.reloads.clear()

    def test_the_plan_tells_free_busy_and_unavailable_switches_apart(self):
        end = timezone.now() + timedelta(days=3)
        Reservation.objects.create(switch=self.switches[1], user=self.carol, end_date=end)
        Quarantine.objects.create(switch=self.switches[2], reasons=['Unwanted cable: 1/1/5'])
        response = self.call(self.bob, 'get', self.restore_path(self.snapshot))
        self.assertEqual(response.status_code, 200, response.data)
        states = {s['id']: s for s in response.data['switches']}
        self.assertEqual(states[self.switch.id]['state'], 'free')
        self.assertEqual(states[self.switch.id]['current_release'], '8.10.86.R04')
        self.assertEqual((states[self.switches[1].id]['state'], states[self.switches[1].id]['free_on']), ('busy', end))
        self.assertEqual(states[self.switches[2].id]['state'], 'unavailable')
        self.assertIn('Quarantine', states[self.switches[2].id]['detail'])
        self.assertEqual(response.data['links'], 1)
        self.assertEqual(self.fake.reloads, [])  # only reads

    def test_the_plan_warns_of_another_aos_release(self):
        self.fake.releases[self.switch.mngt_IP] = '8.10.99.R01'
        states = {s['id']: s for s in self.call(self.alice, 'get', self.restore_path(self.snapshot)).data['switches']}
        self.assertTrue(states[self.switch.id]['release_differs'])
        self.assertEqual(states[self.switch.id]['snapshot_release'], '8.10.86.R04')
        self.assertFalse(states[self.switches[1].id]['release_differs'])

    def test_restore_reserves_the_free_switches_loads_each_config_and_builds_the_links(self):
        end = timezone.now() + timedelta(days=3)
        Reservation.objects.create(switch=self.switches[2], user=self.carol, end_date=end)
        response = self.call(self.bob, 'post', self.restore_path(self.snapshot))
        self.assertEqual(response.status_code, 201, response.data)
        states = {s['id']: s for s in response.data['switches']}
        self.assertEqual(states[self.switch.id]['state'], 'waiting')
        self.assertEqual((states[self.switches[2].id]['state'], states[self.switches[2].id]['free_on']), ('left_out', end))
        for switch in self.switches[:2]:
            reservation = Reservation.objects.get(switch=switch)
            self.assertEqual((reservation.user, reservation.project), (self.bob, self.project))
        self.assertEqual(ProjectSwitchPosition.objects.get(project=self.project, switch=self.switch).x, 10.0)

        self.worker.restores()
        self.assertEqual(sorted(self.fake.reloads), sorted(s.mngt_IP for s in self.switches[:2]))
        self.assertEqual(self.fake.written[self.switch.mngt_IP][WORKING_CONFIG_PATH], config('mine-1'))
        progress = {s['id']: s['state'] for s in self.snapshots_of().data['restore']['switches']}
        self.assertEqual(progress[self.switch.id], 'reloading')
        self.assertIn(f'user bob password', ' '.join(cmd for ip, cmd in self.fake.commands if ip == self.switch.mngt_IP))

        self.clock.advance(RELOAD_GRACE)
        outcomes = self.worker.restores()
        self.assertIn('Restore', outcomes[-1])
        restore = self.snapshots_of().data['restore']
        self.assertIsNotNone(restore['finished_at'])
        self.assertEqual({s['id']: s['state'] for s in restore['switches']},
                         {self.switch.id: 'done', self.switches[1].id: 'done', self.switches[2].id: 'left_out'})
        self.assertEqual([link['outcome'] for link in restore['links']], ['built'])
        a, b = (Port.objects.get(pk=p.pk) for p in self.ports[:2])
        self.assertIsNotNone(a.svlan)
        self.assertEqual(a.svlan, b.svlan)
        # The Switch runs the Snapshot's config; init/ is left for the next Cleanup
        self.assertEqual(self.fake.show(self.switch.mngt_IP, 'show configuration snapshot'), config('mine-1'))
        self.assertNotIn('write init/vcboot.cfg', [cmd for ip, cmd in self.fake.commands])

    def test_a_link_to_a_switch_left_out_is_not_built(self):
        Reservation.objects.create(switch=self.switches[1], user=self.carol, end_date=timezone.now() + timedelta(days=1))
        self.call(self.alice, 'post', self.restore_path(self.snapshot))
        self.run_restore()
        restore = Restore.objects.get()
        self.assertEqual([link['outcome'] for link in restore.links], ['left_out'])
        self.assertIsNone(Port.objects.get(pk=self.ports[0].pk).svlan)

    def test_only_the_chosen_switches(self):
        response = self.call(self.alice, 'post', self.restore_path(self.snapshot), {'switches': [self.switch.id]})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(list(Reservation.objects.values_list('switch_id', flat=True)), [self.switch.id])
        rows = {r.switch_id: r for r in RestoreSwitch.objects.all()}
        self.assertEqual(rows[self.switches[1].id].detail, 'Not chosen.')

    def test_a_switch_already_in_the_project_has_its_config_replaced(self):
        self.reserved(switch=self.switch, project=self.project.id)
        self.call(self.alice, 'post', self.restore_path(self.snapshot), {'switches': [self.switch.id]})
        self.run_restore()
        self.assertEqual(RestoreSwitch.objects.get(switch=self.switch).state, RestoreSwitch.DONE)
        self.assertEqual(Reservation.objects.filter(switch=self.switch).count(), 1)

    def test_another_aos_release_is_a_warning_in_its_progress(self):
        self.fake.releases[self.switch.mngt_IP] = '8.10.99.R01'
        self.call(self.alice, 'post', self.restore_path(self.snapshot))
        self.run_restore()
        row = RestoreSwitch.objects.get(switch=self.switch)
        self.assertEqual(row.state, RestoreSwitch.DONE)
        self.assertIn('AOS release differs', row.warnings[0])

    def test_a_switch_that_doesnt_come_back_fails(self):
        self.call(self.alice, 'post', self.restore_path(self.snapshot), {'switches': [self.switch.id]})
        self.worker.restores()
        self.fake.unreachable.add(self.switch.mngt_IP)
        self.clock.advance(RELOAD_GRACE)
        self.worker.restores()
        self.assertEqual(RestoreSwitch.objects.get(switch=self.switch).state, RestoreSwitch.RELOADING)
        self.clock.advance(RELOAD_TIMEOUT + LOOK_AGAIN_AFTER)
        self.worker.restores()
        row = RestoreSwitch.objects.get(switch=self.switch)
        self.assertEqual(row.state, RestoreSwitch.FAILED)
        self.assertIn("Didn't come back", row.detail)
        self.assertIsNotNone(Restore.objects.get().finished_at)

    def test_a_switch_that_answers_but_isnt_ready_is_waited_for(self):
        self.call(self.alice, 'post', self.restore_path(self.snapshot), {'switches': [self.switch.id]})
        self.worker.restores()
        self.fake.outputs[self.switch.mngt_IP] = {'show interfaces': 'ERROR: system has not reached the ready state'}
        self.clock.advance(RELOAD_GRACE)
        self.worker.restores()
        self.assertEqual(RestoreSwitch.objects.get(switch=self.switch).state, RestoreSwitch.RELOADING)
        del self.fake.outputs[self.switch.mngt_IP]
        self.clock.advance(LOOK_AGAIN_AFTER)
        self.worker.restores()
        self.assertEqual(RestoreSwitch.objects.get(switch=self.switch).state, RestoreSwitch.DONE)
        self.assertIn('alice', self.users_on())  # the Switch accounts are there once it is back

    def test_a_switch_that_hasnt_rebooted_or_isnt_ready_yet_is_waited_for(self):
        ip = self.switch.mngt_IP
        self.fake.uptime[ip] = UPTIME  # up long since its Cleanup
        self.fake.slow_to_reload.add(ip)
        self.call(self.alice, 'post', self.restore_path(self.snapshot), {'switches': [self.switch.id]})
        self.worker.restores()
        self.clock.advance(RELOAD_GRACE)
        self.worker.restores()  # still running as it was: AOS 8.10 copies images first (#50)
        self.assertEqual(RestoreSwitch.objects.get(switch=self.switch).state, RestoreSwitch.RELOADING)
        self.fake.come_up(ip)
        self.fake.not_ready.add(ip)
        self.clock.advance(LOOK_AGAIN_AFTER)
        self.worker.restores()
        self.assertEqual(RestoreSwitch.objects.get(switch=self.switch).state, RestoreSwitch.RELOADING)
        self.fake.not_ready.clear()
        self.clock.advance(LOOK_AGAIN_AFTER)
        self.worker.restores()
        self.assertEqual(RestoreSwitch.objects.get(switch=self.switch).state, RestoreSwitch.DONE)

    def test_a_switch_that_never_reboots_fails_and_says_so(self):
        self.fake.uptime[self.switch.mngt_IP] = UPTIME
        self.fake.slow_to_reload.add(self.switch.mngt_IP)
        self.call(self.alice, 'post', self.restore_path(self.snapshot), {'switches': [self.switch.id]})
        self.worker.restores()
        self.clock.advance(RELOAD_TIMEOUT + LOOK_AGAIN_AFTER)
        self.worker.restores()
        row = RestoreSwitch.objects.get(switch=self.switch)
        self.assertEqual(row.state, RestoreSwitch.FAILED)
        self.assertIn("Didn't come back within 20 minutes of its reload: the reload didn't happen", row.detail)

    def test_a_vc_setup_the_snapshot_didnt_have_is_removed(self):
        ip = self.switch.mngt_IP
        self.fake.files(ip)['working'].add('vcsetup.cfg')
        self.call(self.alice, 'post', self.restore_path(self.snapshot), {'switches': [self.switch.id]})
        self.worker.restores()
        self.assertNotIn('vcsetup.cfg', self.fake.files(ip)['working'])
        sent = [cmd for i, cmd in self.fake.commands if i == ip]
        self.assertLess(sent.index('rm -f working/vcsetup.cfg'), sent.index(RELOAD))

    def test_a_switch_released_while_reloading_fails(self):
        self.call(self.alice, 'post', self.restore_path(self.snapshot), {'switches': [self.switch.id]})
        self.worker.restores()
        release(Reservation.objects.get(switch=self.switch), self.alice)
        self.clock.advance(RELOAD_GRACE)
        self.worker.restores()
        row = RestoreSwitch.objects.get(switch=self.switch)
        self.assertEqual((row.state, row.detail), (RestoreSwitch.FAILED, 'Released during its Restore: BLab Cleans it up.'))

    def test_a_switch_released_before_its_turn_fails_without_a_reload(self):
        self.call(self.alice, 'post', self.restore_path(self.snapshot), {'switches': [self.switch.id]})
        release(Reservation.objects.get(switch=self.switch), self.alice)
        self.worker.restores()
        self.assertEqual(RestoreSwitch.objects.get(switch=self.switch).state, RestoreSwitch.FAILED)
        self.assertEqual(self.fake.reloads, [])

    def test_members_only_one_at_a_time_and_not_while_named_in_a_quarantine(self):
        self.assertEqual(self.call(self.carol, 'get', self.restore_path(self.snapshot)).status_code, 404)
        self.assertEqual(self.call(self.carol, 'post', self.restore_path(self.snapshot)).status_code, 404)
        Quarantine.objects.create(switch=self.switches[2], holder=self.bob, reasons=['Unwanted cable: 1/1/5'])
        response = self.call(self.bob, 'post', self.restore_path(self.snapshot))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Reservation.objects.exists())
        self.assertEqual(self.call(self.alice, 'post', self.restore_path(self.snapshot)).status_code, 201)
        response = self.call(self.alice, 'post', self.restore_path(self.snapshot))
        self.assertEqual(response.status_code, 400)
        self.assertIn('under way', response.data['detail'])
        self.assertEqual(self.save().status_code, 400)  # nor Save meanwhile

    def test_nothing_to_restore_or_an_archived_project_is_refused(self):
        for switch in self.switches:
            Reservation.objects.create(switch=switch, user=self.carol, end_date=timezone.now() + timedelta(days=1))
        response = self.call(self.alice, 'post', self.restore_path(self.snapshot))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Restore.objects.exists())
        Reservation.objects.all().delete()
        Project.objects.filter(pk=self.project.pk).update(archived_at=timezone.now())
        self.assertEqual(self.call(self.alice, 'post', self.restore_path(self.snapshot)).status_code, 400)

    def test_endpoints_are_not_found_with_projects_off(self):
        with self.settings(**OFF):
            self.assertEqual(self.snapshots_of().status_code, 404)
            self.assertEqual(self.call(self.alice, 'post', self.restore_path(self.snapshot)).status_code, 404)
