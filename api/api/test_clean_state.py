"""
Clean state (#38): the Baseline, Unwanted files, other local users and the Golden init. The
Inspection reports them, the Cleanup repairs them, and BLAB_CLEAN_STATE_MODE=report only says
what it would do. Against fake devices.
"""
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings

from . import clean_state, fake_devices
from .clean_state import Extras, find, golden_init_config, in_baseline, parse_users, removable
from .inspection import inspect
from .lab_switch import INIT_CONFIG_PATH, LabSwitch, LabSwitchError, Repair, banner_text
from .models import BaselineExtra, Quarantine, Reservation, SwitchEvent
from .sweep import Sweeper
from .test_release_cleanup import CleanupTestCase, make_switch
from .test_sweep import paris

# What 10.69.144.132 held on 2026-10-12 (the issue), and what AOS writes itself there
LEFT_BY_USERS = ['.bash_history', '.python_history', 'running.cfg', '.ssh/known_hosts', '.ssh/known_hosts.old',
                 'python/RUNT.py', 'python/runt2.py', 'python/SET_RUNT_TRHLD.sh',
                 'pmd/pmd-cliwrapper-01.09.2008-13.01.23', 'aijaz', 'init/vcsetup.cfg']
AOS_OWN = ['swlog_chassis1', 'swlog_chassis1.0', 'command.log', 'agcmm-memlog', 'hwinfo', 'cportal_lighttpd.log',
           'vcboot.cfg.1.err', 'license.dat', 'python/default_broker_vso.py', 'pmd/work', 'init/Yos.img',
           'init/pkg', 'init/vcboot.cfg', 'system', 'switch', 'working', 'certified', 'tech_support_complete.tar']

SHOW_USER_810 = """User name = test,
  Read/Write for domains  = All ,
  SSH/SSH-PKIX allowed    = YES,
  SU allowed    = NO,
  Password refresh next login    = NO,,
User name = admin,
  Read/Write for domains  = All ,
  SU allowed    = YES,
  Super-user password    = Set
User name = ovtest,
  SU allowed    = YES,
User name = default (*),
  SU allowed    = NO,
"""


class BaselineTest(SimpleTestCase):
    def test_what_users_leave_is_outside_the_baseline_and_what_aos_writes_inside(self):
        for path in LEFT_BY_USERS:
            self.assertFalse(in_baseline(path), path)
            self.assertTrue(removable(path), path)
        for path in AOS_OWN:
            self.assertTrue(in_baseline(path), path)
            self.assertFalse(removable(path), path)

    def test_a_pattern_matches_in_its_own_directory_only(self):
        self.assertTrue(in_baseline('mac_tracker.log'))
        for path in ('python/replicate.py', '.ssh/x.log', 'pmd/core.err', 'init/license.dat'):
            self.assertFalse(in_baseline(path), path)

    def test_without_a_model_init_is_neither_compared_nor_rewritten(self):
        found = find('', {d: [] for d in clean_state.SCANNED}, {'admin': True}, 'anything', Extras())
        self.assertEqual(found.init_changes, [])

    def test_extras_join_the_baseline(self):
        self.assertTrue(in_baseline('aijaz', ['aijaz']))
        self.assertTrue(in_baseline('python/monitor.py', ['python/mon*']))

    def test_only_entries_of_the_scanned_directories_can_be_removed(self):
        for path in ('', '/flash/x', 'switch/x.db', 'system/userTable9', 'working/vcboot.cfg', '../etc',
                     'python/../init', 'a/b/c', 'x*', "it's", 'init', 'init/', '.ssh'):
            self.assertFalse(removable(path), path)

    def test_show_user_with_and_without_su(self):
        self.assertEqual(parse_users(SHOW_USER_810), {'test': False, 'admin': True, 'ovtest': True, 'default': False})
        self.assertEqual(parse_users('User name = admin,\n  SSH allowed = YES\n'), {'admin': None})
        self.assertIsNone(parse_users('ERROR: whatever'))

    def test_findings(self):
        listings = {'': ['.bash_history', 'init', 'swlog_chassis1', 'aijaz'], '.ssh': ['known_hosts'], 'python': [],
                    'pmd': ['work'], 'init': ['Yos.img', 'vcboot.cfg', 'vcsetup.cfg']}
        found = find('OS6900-X48C6', listings, parse_users(SHOW_USER_810), golden_init_config('OS6900-X48C6'),
                     Extras(files=['aijaz'], logins=['ovtest']))
        self.assertEqual(found.files, ['.bash_history', '.ssh/known_hosts', 'init/vcsetup.cfg'])
        self.assertEqual(found.logins, ['test'])
        self.assertEqual(found.su, [])  # ovtest is the lab managers'
        self.assertEqual(found.init_lacks, ['pkg/'])
        self.assertEqual(found.init_changes, [])
        self.assertEqual(found.unreadable, [])

    def test_init_compared_with_the_golden_init_of_the_model(self):
        golden = golden_init_config('OS6900-X48C6')
        # Trailing spaces, blank lines and comments, as on 10.69.144.132, are not changes
        same = '! made by prepare_switches\n' + golden.replace('"local"', '"local" ').replace('\n\n', '\n')
        self.assertEqual(clean_state.init_differences(same, 'OS6900-X48C6'), [])
        changed = golden + 'vlan 10 admin-state enable\n'
        self.assertEqual(clean_state.init_differences(changed, 'OS6900-X48C6'), ['+ vlan 10 admin-state enable'])
        renamed = clean_state.init_differences(golden_init_config('OS6900-10_69_144_143'), 'OS6900-C32E')
        self.assertIn('- system name "OS6900-C32E"', renamed)
        unreadable = find('m', {d: [] for d in clean_state.SCANNED}, None, None, Extras())
        self.assertEqual(unreadable.unreadable, ['show user'])
        self.assertEqual(unreadable.init_lacks, ['an AOS image (*.img)', 'pkg/', 'vcboot.cfg'])

    def test_the_banner_no_longer_tells_users_to_clean_up_themselves(self):
        self.assertNotIn('cp init', banner_text([]))


class LabSwitchRepairTest(SimpleTestCase):
    IP = '10.0.0.1'

    def setUp(self):
        self.fake = fake_devices.FakeLabSwitches()
        self.switch = LabSwitch(self.IP, self.fake.connect)

    def sent(self):
        return [cmd for _, cmd in self.fake.commands]

    def test_repair_removes_users_and_files_and_rewrites_init_before_restoring_it(self):
        files = self.fake.files(self.IP)
        files[''] |= {'.bash_history', 'aijaz'}
        files['aijaz'] = {'vcsetup.cfg'}
        files['init'].add('vcsetup.cfg')
        self.fake.users[self.IP] = {'test': 'x', 'ovtest': 'y'}
        warnings = self.switch.restore_init_and_reload(Repair(
            users=['test', 'ovtest'], files=['.bash_history', 'aijaz', 'init/vcsetup.cfg'], init_config='golden\n'))
        self.assertEqual(warnings, [])
        self.assertEqual(self.fake.users[self.IP], {})
        self.assertEqual(self.fake.written[self.IP][INIT_CONFIG_PATH], 'golden\n')
        self.assertNotIn('aijaz', files)
        self.assertEqual(files[''], set(self.fake.FLASH))
        self.assertEqual(files['working'], {'Uos.img', 'pkg', 'vcboot.cfg'})
        sent = self.sent()
        self.assertIn("rm -rf -- /flash/.bash_history", sent)
        self.assertLess(sent.index(f'write {INIT_CONFIG_PATH}'), sent.index('cp -r init/* working/'))
        self.assertEqual(self.fake.reloads, [self.IP])

    def test_admin_and_baseline_paths_are_never_touched(self):
        warnings = self.switch.restore_init_and_reload(Repair(users=['admin', 'Default'], files=['init/pkg', 'system']))
        self.assertEqual(len(warnings), 4)
        self.assertFalse([cmd for cmd in self.sent() if 'user' in cmd or cmd.startswith('rm -rf --')])
        self.assertEqual(self.fake.reloads, [self.IP])

    def test_a_file_that_cannot_be_removed_is_a_warning(self):
        self.fake.fail_on('rm -rf -- /flash/running.cfg', exit_status=1)
        warnings = self.switch.restore_init_and_reload(Repair(files=['running.cfg']))
        self.assertEqual(len(warnings), 1)
        self.assertIn('/flash/running.cfg', warnings[0])
        self.assertEqual(self.fake.reloads, [self.IP])

    def test_init_that_cannot_be_rewritten_stops_the_cleanup(self):
        self.fake.fail_on('write init/vcboot.cfg')
        with self.assertRaises(LabSwitchError):
            self.switch.restore_init_and_reload(Repair(init_config='golden\n'))
        self.assertEqual(self.fake.reloads, [])

    def test_init_without_an_image_stops_the_cleanup_before_working_is_emptied(self):
        self.fake.files(self.IP)['init'].discard('Uos.img')
        self.fake.files(self.IP)['working'] = {'Uos.img', 'pkg', 'vcboot.cfg'}
        with self.assertRaisesRegex(LabSwitchError, 'init/ on 10.0.0.1 lacks .img'):
            self.switch.restore_init_and_reload()
        self.assertEqual(self.fake.files(self.IP)['working'], {'Uos.img', 'pkg', 'vcboot.cfg'})
        self.assertNotIn('rm -rf working/*', self.sent())
        self.assertEqual(self.fake.reloads, [])


class Dirty:
    """Leaves on a fake switch what users left on 10.69.144.132."""

    @staticmethod
    def leave(fake, ip):
        files = fake.files(ip)
        files[''] |= {'.bash_history', 'running.cfg'}
        files['python'] |= {'RUNT.py'}
        files['pmd'] |= {'pmd-cliwrapper-01.09.2008-13.01.23'}
        files['.ssh'] |= {'known_hosts'}
        fake.users[ip] = {'test': 'x', 'ovtest': 'y'}
        fake.su[ip] = {'ovtest'}
        fake.written.setdefault(ip, {})[INIT_CONFIG_PATH] = fake.init_config(ip) + 'vlan 10 admin-state enable\n'


@override_settings(BLAB_DEVICES='fake')
class InspectionCleanStateTest(TestCase):
    IP = '10.0.0.1'

    def setUp(self):
        self.fake = fake_devices.lab_switches
        self.fake.reset()
        self.switch = make_switch(1)
        self.switch.model = 'OS6900-X48C6'
        self.switch.save()

    def test_a_clean_switch_is_clean_in_either_mode(self):
        for mode in ('report', 'enforce'):
            with self.subTest(mode=mode), override_settings(BLAB_CLEAN_STATE_MODE=mode):
                result = inspect(self.switch)
                self.assertEqual((result.reasons, result.warnings), ([], []))

    @override_settings(BLAB_CLEAN_STATE_MODE='report')
    def test_report_mode_only_warns(self):
        Dirty.leave(self.fake, self.IP)
        result = inspect(self.switch)
        self.assertTrue(result.clean)
        warnings = '\n'.join(result.warnings)
        self.assertIn('Unwanted file: .bash_history, running.cfg, .ssh/known_hosts, python/RUNT.py, pmd/pmd-', warnings)
        self.assertIn("local users BLab didn't create: test, ovtest", warnings)
        self.assertIn('SU allowed for: ovtest', warnings)
        self.assertIn('init differs from its Golden init: 1 line(s): + vlan 10 admin-state enable', warnings)

    @override_settings(BLAB_CLEAN_STATE_MODE='enforce')
    def test_enforce_mode_fails_the_inspection(self):
        Dirty.leave(self.fake, self.IP)
        result = inspect(self.switch)
        self.assertFalse(result.clean)
        self.assertEqual(len(result.reasons), 4)
        self.assertEqual(len(result.repairable), 3)  # SU goes with its user, but isn't repaired by itself
        self.assertTrue(any(r.startswith('init differs') for r in result.repairable))

    @override_settings(BLAB_CLEAN_STATE_MODE='enforce')
    def test_the_lab_managers_extras_are_not_unwanted(self):
        Dirty.leave(self.fake, self.IP)
        self.fake.written[self.IP].pop(INIT_CONFIG_PATH)
        self.fake.users[self.IP] = {'observium': 'x'}
        self.fake.su[self.IP] = set()
        for value in ('.bash_history', 'running.cfg', '.ssh/*', 'python/RUNT.py', 'pmd/pmd-*'):
            BaselineExtra.objects.create(kind=BaselineExtra.FILE, value=value)
        BaselineExtra.objects.create(kind=BaselineExtra.LOGIN, value='observium')
        result = inspect(self.switch)
        self.assertEqual((result.reasons, result.warnings), ([], []))

    @override_settings(BLAB_CLEAN_STATE_MODE='enforce')
    def test_a_reserved_switch_is_left_to_its_users_but_su_is_noticed(self):
        Dirty.leave(self.fake, self.IP)
        Reservation.objects.create(switch=self.switch, user=User.objects.create_user('alice', password='pw'))
        result = inspect(self.switch)
        self.assertTrue(result.clean)
        self.assertIn('SU allowed for: ovtest', result.warnings)
        self.assertFalse([w for w in result.warnings if w.startswith(('Unwanted', 'init differs'))])

    @override_settings(BLAB_CLEAN_STATE_MODE='enforce')
    def test_a_directory_that_cannot_be_listed_is_unreadable(self):
        self.fake.fail_on('ls -a /flash/python', exit_status=1)
        result = inspect(self.switch)
        self.assertIn('unreadable: ls /flash/python', result.reasons)

    @override_settings(BLAB_CLEAN_STATE_MODE='enforce')
    def test_a_missing_directory_is_empty(self):
        del self.fake.files(self.IP)['pmd']
        self.fake.files(self.IP)[''].discard('pmd')
        self.assertTrue(inspect(self.switch).clean)

    def test_the_admin_validates_extras(self):
        with self.assertRaises(ValidationError):
            BaselineExtra(kind=BaselineExtra.FILE, value='../etc').clean()
        extra = BaselineExtra(kind=BaselineExtra.FILE, value=' /tools/ ')
        extra.clean()
        self.assertEqual(extra.value, 'tools')


class CleanupCleanStateTest(CleanupTestCase):
    IP = '10.0.0.1'

    def setUp(self):
        super().setUp()
        self.switch.model = 'OS6900-X48C6'
        self.switch.save()
        Dirty.leave(self.fake, self.IP)
        self.reserve()
        self.release()

    def cleanup_event(self):
        return self.switch.events.get(kind=SwitchEvent.CLEANUP)

    @override_settings(BLAB_CLEAN_STATE_MODE='enforce')
    def test_a_cleanup_leaves_nothing_of_the_reservation(self):
        self.run_cleanup()
        files = self.fake.files(self.IP)
        self.assertEqual(files[''], set(self.fake.FLASH))
        self.assertEqual((files['python'], files['pmd'], files['.ssh']), ({'default_broker_vso.py'}, {'work'}, set()))
        self.assertEqual(self.fake.users[self.IP], {})
        self.assertEqual(self.fake.init_config(self.IP), clean_state.golden_init_config('OS6900-X48C6'))
        self.assertTrue(self.switch.last_inspection().ok, self.switch.last_inspection().reasons)
        self.assertFalse(Quarantine.objects.exists())
        self.assertFalse([cmd for _, cmd in self.fake.commands if 'user admin' in cmd])

    @override_settings(BLAB_CLEAN_STATE_MODE='enforce')
    def test_an_unchanged_init_is_not_rewritten(self):
        self.fake.written[self.IP].pop(INIT_CONFIG_PATH)
        self.run_cleanup()
        self.assertNotIn(f'write {INIT_CONFIG_PATH}', [cmd for _, cmd in self.fake.commands])

    @override_settings(BLAB_CLEAN_STATE_MODE='enforce')
    def test_what_survives_the_cleanup_quarantines_naming_nobody(self):
        self.fake.fail_on('rm -rf -- /flash/running.cfg', exit_status=1)
        self.run_cleanup()
        self.assertIn("Couldn't remove /flash/running.cfg", self.cleanup_event().warnings[-1])
        quarantine = Quarantine.objects.get()
        self.assertIsNone(quarantine.holder)  # BLab failed, not the holder
        self.assertEqual(quarantine.reasons, ['Unwanted file: running.cfg'])

    @override_settings(BLAB_CLEAN_STATE_MODE='report')
    def test_report_mode_only_says_what_it_would_remove(self):
        self.run_cleanup()
        self.assertIn('.bash_history', self.fake.files(self.IP)[''])
        self.assertIn('test', self.fake.users[self.IP])
        would = [w for w in self.cleanup_event().warnings if w.startswith('Would remove')]
        self.assertEqual(len(would), 1, self.cleanup_event().warnings)
        self.assertIn('local users test, ovtest', would[0])
        self.assertIn('init/vcboot.cfg rewritten from the Golden init', would[0])
        self.assertFalse([cmd for _, cmd in self.fake.commands if cmd.startswith(('rm -rf --', 'no user'))])
        self.assertTrue(self.switch.last_inspection().ok)


@override_settings(BLAB_DEVICES='fake', BLAB_SWEEP_MODE='enforce', BLAB_CLEAN_STATE_MODE='enforce')
class SweepCleanStateTest(TestCase):
    def test_the_sweep_cleans_up_a_switch_left_dirty(self):
        from .switch_worker import RELOAD_GRACE, SwitchWorker
        from .test_release_cleanup import Clock
        fake = fake_devices.lab_switches
        fake.reset()
        switch = make_switch(1)
        fake.files(switch.mngt_IP)[''].add('running.cfg')
        clock = Clock()
        clock.at = paris(2026, 10, 9, 2, 0)
        sweeper, worker = Sweeper(now=clock), SwitchWorker(now=clock)
        for _ in range(10):
            sweeper.step()
            worker.work()
            clock.advance(RELOAD_GRACE)
        self.assertEqual(fake.reloads, [switch.mngt_IP])
        self.assertNotIn('running.cfg', fake.files(switch.mngt_IP)[''])
        self.assertFalse(Quarantine.objects.exists())
        self.assertTrue(switch.last_inspection().ok)
