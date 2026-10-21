"""
The lab's AOS release (#48): a Cleanup installs the latest GA of the Switch's family into init/
(and certified/, where the Cleanup's check of init/ finds its copy), and goes on with the image
it has if that fails. Against fake devices and a fake image server.
"""
import os
import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import aos_release, fake_devices
from .inspection import inspect_and_record
from .lab_switch import NEW_IMAGE_SUFFIX, Microcode, lab_switch, parse_microcode
from .models import Quarantine, Switch, SwitchEvent
from .switch_worker import RELOAD_GRACE, SwitchWorker, request_cleanup

LAB = '8.11.162.R01'  # the latest GA in the vendored ga_latest.json, for every family but tor
OLD = fake_devices.AOS_RELEASE  # what a fake switch runs: 8.10.86.R04
MICROCODE_132 = """   /flash/init
   Package           Release                 Size      Description          Secure Boot
-----------------+-------------------------+---------+---------------------+------------
Yos.img           8.10.86.R04               241594635 Alcatel-Lucent OS     Yes
"""


class FamiliesAndReleases(SimpleTestCase):
    def test_a_model_has_the_family_tsbuddy_gives_it(self):
        for model, family in [('OS6860-48', 'shasta'), ('OS6860', 'shasta'), ('OS6860E-24', 'shasta'),
                              ('OS6860N-P48M', 'shasta_n'), ('OS6900-X48C6', 'yukon'), ('OS6900-V72', 'yukon'),
                              ('OS6900-X72', 'tor'), ('OS6900-C32E', None), ('OS9907', 'medora'),
                              ('OS6865-P16X', 'everest'), ('OS6570M-12', 'whitney'), ('OS2260-P10', None), ('', None)]:
            with self.subTest(model):
                self.assertEqual(aos_release.family(model), family)

    def test_the_lab_release_is_the_latest_ga_of_the_family(self):
        self.assertEqual(aos_release.lab_release('OS6900-X48C6'), LAB)
        self.assertEqual(aos_release.lab_release('OS6900-X72'), '8.9.94.R04')  # Tor stops at 8.9
        self.assertIsNone(aos_release.lab_release('OS2260-P10'))

    def test_compatible_releases_are_the_family_ga_releases_newest_first(self):
        releases = aos_release.compatible_releases('OS6900-X48C6')
        self.assertEqual(releases[:3], [LAB, '8.10.86.R04', '8.10.93.R03'])
        self.assertNotIn(LAB, aos_release.compatible_releases('OS6900-X72'))  # N/S for tor
        self.assertEqual(aos_release.compatible_releases('OS2260'), [])

    def test_an_image_is_found_where_tsbuddy_downloads_it(self):
        self.assertEqual(aos_release.Source(LAB, 'yukon', True).path, 'OS_8_11_162_R01/yukon/Release/secure')
        self.assertEqual(aos_release.Source('8.10.105.R02', 'shasta', False).url('Uos.img'),
                         'http://10.46.4.37/bop/images/OS_8_10_105_R02/shasta/Release/Uos.img')

    def test_show_microcode_says_the_release_and_whether_it_is_secure_boot(self):
        self.assertEqual(parse_microcode(MICROCODE_132), Microcode('8.10.86.R04', True))
        self.assertEqual(parse_microcode(MICROCODE_132.replace('Yes', 'No')), Microcode('8.10.86.R04', False))
        older = 'Yos.img           8.9.107.R02               241594635 Alcatel-Lucent OS\n'
        self.assertEqual(parse_microcode(older), Microcode('8.9.107.R02', None))
        self.assertIsNone(parse_microcode('ERROR: system has not reached the ready state'))


class Clock:
    def __init__(self):
        self.at = timezone.now()

    def __call__(self):
        return self.at


@override_settings(BLAB_DEVICES='fake', BLAB_AOS_RELEASE_MODE='enforce')
class CleanupInstallsTheLabRelease(TestCase):
    IP = '10.0.0.1'

    def setUp(self):
        self.cache = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.cache, ignore_errors=True)
        cache = override_settings(BLAB_AOS_IMAGE_CACHE=self.cache)
        cache.enable()
        self.addCleanup(cache.disable)
        self.fake = fake_devices.lab_switches
        self.fake.reset()
        fake_devices.backbone.reset()
        self.server = fake_devices.image_server
        self.server.reset()
        self.switch = Switch.objects.create(mngt_IP=self.IP, model='OS6860-48', console='TODO', part_number='pn',
                                            hardware_revision='A', serial_number='sn1')
        self.alice = User.objects.create_user('alice', email='alice@example.com', password='pw')
        self.clock = Clock()
        self.worker = SwitchWorker(now=self.clock)

    def clean_up(self):
        request_cleanup(self.switch, self.alice)
        self.worker.work()
        self.clock.at += RELOAD_GRACE
        self.worker.work()
        return self.switch.events.filter(kind=SwitchEvent.CLEANUP).latest('at')

    def release_in(self, directory):
        return self.fake.release_of(self.IP, f'{directory}/Uos.img')

    def test_a_cleanup_installs_the_lab_release_and_the_switch_boots_it(self):
        cleanup = self.clean_up()
        self.assertTrue(cleanup.ok, cleanup.reasons)
        self.assertIn(f'AOS {LAB} installed (was {OLD})', cleanup.warnings)
        for directory in ('init', 'certified', 'working'):
            self.assertEqual(self.release_in(directory), LAB, directory)
        self.assertEqual(self.fake.reloads, [self.IP])
        self.switch.refresh_from_db()
        self.assertEqual(self.switch.aos_release, LAB)  # recorded by the Inspection after the Cleanup
        self.assertFalse(Quarantine.objects.exists())
        # The Release/ image: shasta has no Secure Boot variant
        self.assertEqual(self.server.fetched, ['http://10.46.4.37/bop/images/OS_8_11_162_R01/shasta/Release/Uos.img'])
        self.assertFalse(any(NEW_IMAGE_SUFFIX in name for d in ('init', 'certified')
                             for name in self.fake.files(self.IP)[d]))

    def test_the_image_is_checked_on_the_switch_before_it_takes_the_place_of_init_s(self):
        self.clean_up()
        sent = [cmd for ip, cmd in self.fake.commands]
        put, md5 = sent.index(f'put init/Uos.img{NEW_IMAGE_SUFFIX}'), sent.index(f'md5sum init/Uos.img{NEW_IMAGE_SUFFIX}')
        moved = sent.index(f'mv -f init/Uos.img{NEW_IMAGE_SUFFIX} init/Uos.img')
        into_certified = sent.index(f'mv -f certified/Uos.img{NEW_IMAGE_SUFFIX} certified/Uos.img')
        self.assertLess(put, md5)
        self.assertLess(md5, into_certified)
        self.assertLess(into_certified, moved)  # init/ last
        self.assertLess(moved, sent.index('cp -r init/* working/'))

    def test_a_switch_that_has_the_lab_release_is_not_installed_again(self):
        self.fake.releases[self.IP] = LAB
        cleanup = self.clean_up()
        self.assertEqual(cleanup.warnings, [])
        self.assertEqual(self.server.fetched, [])
        self.assertFalse(any(cmd.startswith('put ') for _, cmd in self.fake.commands))

    def test_an_image_already_downloaded_is_not_downloaded_again(self):
        self.clean_up()
        other = Switch.objects.create(mngt_IP='10.0.0.2', model='OS6860E-24', console='TODO', part_number='pn',
                                      hardware_revision='A', serial_number='sn2')
        self.assertEqual(aos_release.install_lab_release(other), [f'AOS {LAB} installed (was {OLD})'])
        self.assertEqual(len(self.server.fetched), 1)

    @override_settings(BLAB_AOS_RELEASE_MODE='report')
    def test_in_report_mode_a_cleanup_only_says_what_it_would_install(self):
        cleanup = self.clean_up()
        self.assertIn(f'Would install AOS {LAB} (BLAB_AOS_RELEASE_MODE=report): init/ has {OLD}', cleanup.warnings)
        self.assertEqual(self.release_in('init'), OLD)
        self.assertEqual(self.server.fetched, [])

    def assert_cleaned_up_with_the_old_release(self, cleanup, why):
        self.assertTrue(cleanup.ok, cleanup.reasons)
        note = next((w for w in cleanup.warnings if w.startswith(f'AOS {LAB} not installed, Cleaned up with {OLD}: ')),
                    None)
        self.assertIsNotNone(note, cleanup.warnings)
        self.assertIn(why, note)
        for directory in ('init', 'certified', 'working'):
            self.assertEqual(self.release_in(directory), OLD, directory)
            self.assertFalse(any(NEW_IMAGE_SUFFIX in name for name in self.fake.files(self.IP)[directory]), directory)
        self.assertEqual(self.fake.reloads, [self.IP])
        self.assertFalse(Quarantine.objects.exists())

    def test_an_unreachable_image_server_leaves_the_switch_on_its_release(self):
        self.server.unreachable = True
        self.assert_cleaned_up_with_the_old_release(self.clean_up(), 'cannot reach the image server')

    def test_an_image_that_does_not_match_its_sha256_is_not_kept_nor_installed(self):
        self.server.damaged.add('Uos.img')
        self.assert_cleaned_up_with_the_old_release(self.clean_up(), "doesn't match its sha256")
        self.assertEqual([name for _, _, names in os.walk(self.cache) for name in names], [])

    def test_a_copy_to_the_switch_cut_short_is_removed(self):
        self.fake.cut_short[self.IP] = {f'init/Uos.img{NEW_IMAGE_SUFFIX}': 1000}
        self.assert_cleaned_up_with_the_old_release(self.clean_up(), "isn't")

    def test_a_copy_into_certified_cut_short_leaves_init_as_it_was(self):
        self.fake.cut_short[self.IP] = {f'certified/Uos.img{NEW_IMAGE_SUFFIX}': 1000}
        self.assert_cleaned_up_with_the_old_release(self.clean_up(), 'a copy cut short')

    def test_a_copy_that_does_not_match_the_md5_is_removed(self):
        self.fake.fail_on('md5sum', exit_status=1)
        self.assert_cleaned_up_with_the_old_release(self.clean_up(), 'md5sum')

    def test_a_switch_without_room_for_the_release_keeps_its_own(self):
        self.fake.free[self.IP] = 10 * 2**20
        self.assert_cleaned_up_with_the_old_release(self.clean_up(), 'MB free')

    def test_a_switch_whose_init_release_cannot_be_read_is_cleaned_up_as_it_is(self):
        self.fake.outputs[self.IP] = {'show microcode init': 'ERROR: system has not reached the ready state'}
        cleanup = self.clean_up()
        self.assertTrue(cleanup.ok, cleanup.reasons)
        self.assertTrue(any(w.startswith('AOS release not checked: ') for w in cleanup.warnings), cleanup.warnings)
        self.assertEqual(self.release_in('init'), OLD)

    def test_a_model_of_no_known_family_is_left_alone(self):
        Switch.objects.filter(id=self.switch.id).update(model='OS2260-P10')
        self.switch.refresh_from_db()
        self.assertEqual(self.clean_up().warnings, [])
        self.assertFalse(any(cmd.startswith('show microcode init') for _, cmd in self.fake.commands))

    def test_the_secure_boot_variant_is_installed_where_the_family_has_one(self):
        self.fake.files(self.IP)['init'] = {'Yos.img', 'pkg', 'vcboot.cfg'}
        self.fake.files(self.IP)['certified'] = {'Yos.img', 'pkg', 'vcboot.cfg'}
        Switch.objects.filter(id=self.switch.id).update(model='OS6900-X48C6')
        self.switch.refresh_from_db()
        self.assertEqual(aos_release.install_lab_release(self.switch), [f'AOS {LAB} installed (was {OLD})'])
        self.assertEqual(self.server.fetched, [
            'http://10.46.4.37/bop/images/OS_8_11_162_R01/yukon/Release/secure/Yos.img'])

    def test_a_switch_running_a_non_secure_boot_image_keeps_to_that_variant(self):
        self.fake.files(self.IP)['init'] = {'Yos.img', 'pkg', 'vcboot.cfg'}
        Switch.objects.filter(id=self.switch.id).update(model='OS6900-X48C6')
        self.switch.refresh_from_db()
        self.fake.outputs[self.IP] = {'show microcode init': MICROCODE_132.replace('Yes', 'No')}
        aos_release.install_lab_release(self.switch)
        self.assertEqual(self.server.fetched, ['http://10.46.4.37/bop/images/OS_8_11_162_R01/yukon/Release/Yos.img'])

    def test_install_puts_a_chosen_release_in_any_directory(self):
        """What #49 builds on: a member's chosen release, into working/ only."""
        aos_release.install(self.switch, '8.10.105.R02', ['working'])
        self.assertEqual(self.release_in('working'), '8.10.105.R02')
        self.assertEqual(self.release_in('init'), OLD)
        self.assertEqual(lab_switch(self.IP).microcode('working').release, '8.10.105.R02')

    def test_an_inspection_records_the_release_the_switch_runs(self):
        self.fake.releases[self.IP] = '8.9.107.R02'
        inspect_and_record(self.switch)
        self.switch.refresh_from_db()
        self.assertEqual(self.switch.aos_release, '8.9.107.R02')

    def test_lab_status_shows_each_switch_release_and_the_lab_release_of_each_family(self):
        Switch.objects.filter(id=self.switch.id).update(aos_release=OLD)
        client = APIClient()
        client.force_authenticate(self.alice)
        data = client.get('/api/lab_status/').data
        row = next(s for s in data['switches'] if s['id'] == self.switch.id)
        self.assertEqual((row['aos_release'], row['lab_release']), (OLD, LAB))
        self.assertEqual(data['lab_releases'], [{'family': 'shasta', 'release': LAB, 'models': ['OS6860-48']}])
