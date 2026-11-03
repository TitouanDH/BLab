"""
A member's AOS install (#49): whoever may work on a reserved Switch installs another release of its
family into working/ and reboots into it, through the Switch worker; a failed install leaves the
Switch as it was; the Switch isn't Released meanwhile but by an admin's forced Release, whose
Cleanup waits for it; and the next Cleanup brings back the Golden init's release. Against fake
devices and a fake image server.
"""
import shutil
import tempfile
from datetime import timedelta

from django.test import override_settings

from . import aos_release, fake_devices
from .models import AosInstall, PendingCleanup, Quarantine, Reservation, SwitchEvent
from .release import release
from .switch_worker import RELOAD_GRACE, RELOAD_TIMEOUT
from .test_projects import ProjectsTestCase
from .test_switch_password import ON

OLD = fake_devices.AOS_RELEASE  # what a fake switch runs: 8.10.86.R04, in init/ as in working/
CHOSEN = '8.10.105.R02'  # another GA of the OS6860's family (shasta)


def use_a_temporary_image_cache(test):
    """Images downloaded in a test go into a directory of its own, removed after it."""
    cache = tempfile.mkdtemp()
    test.addCleanup(shutil.rmtree, cache, ignore_errors=True)
    settings = override_settings(BLAB_AOS_IMAGE_CACHE=cache)
    settings.enable()
    test.addCleanup(settings.disable)


class AosInstallTestCase(ProjectsTestCase):
    """Alice holds the Switch, an OS6860, in a Project of hers that bob is a member of; carol isn't."""

    def setUp(self):
        super().setUp()
        use_a_temporary_image_cache(self)
        self.server = fake_devices.image_server
        self.server.reset()
        self.ip = self.switch.mngt_IP
        with self.settings(**ON):
            self.reservation = self.reserved()
            self.join(self.bob, self.reservation.project)
        self.path = f'/api/switches/{self.switch.id}/aos_release/'

    def ask(self, release=CHOSEN, user=None):
        return self.client_for(user or self.alice).post(self.path, {'release': release}, format='json')

    def asked(self, release=CHOSEN):
        response = self.ask(release)
        self.assertEqual(response.status_code, 202, response.data)
        return AosInstall.objects.get(pk=response.data['id'])

    def step(self):
        """One cycle of the Switch worker's installs; the install as it is after."""
        self.worker.installs()
        return AosInstall.objects.latest('requested_at')

    def release_in(self, directory):
        return self.fake.release_of(self.ip, f'{directory}/Uos.img')


@override_settings(**ON)
class AskTest(AosInstallTestCase):

    def test_the_dialog_offers_the_releases_of_the_switch_s_family(self):
        response = self.client_for(self.alice).get(self.path)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['releases'], aos_release.compatible_releases('OS6860'))
        self.assertIn(CHOSEN, response.data['releases'])
        self.assertEqual(response.data['lab_release'], aos_release.lab_release('OS6860'))
        self.assertIsNone(response.data['install'])

    def test_any_member_may_install_and_nobody_else(self):
        self.assertEqual(self.client_for(self.carol).get(self.path).status_code, 403)
        self.assertEqual(self.ask(user=self.carol).status_code, 403)
        self.assertFalse(AosInstall.objects.exists())
        self.assertEqual(self.ask(user=self.bob).status_code, 202)

    def test_a_switch_nobody_holds_is_refused(self):
        self.reservation.delete()
        self.assertEqual(self.ask().status_code, 403)

    def test_a_release_of_another_family_is_refused(self):
        response = self.ask('8.6.189.R01')  # 8.6R01 has no shasta GA
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('not an AOS release BLab can install', response.data['detail'])
        self.assertEqual(self.ask('').status_code, 400)

    def test_an_out_of_service_switch_is_refused(self):
        self.switch.out_of_service_reason = 'PSU broken'
        self.switch.save()
        response = self.ask()
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('Out of service: PSU broken', response.data['detail'])

    def test_one_install_at_a_time(self):
        self.asked()
        response = self.ask('8.10.93.R03')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('already being installed', response.data['detail'])


@override_settings(**ON)
class InstallTest(AosInstallTestCase):

    def test_the_switch_worker_installs_the_release_into_working_and_boots_it(self):
        install = self.asked()
        self.assertEqual(install.state, AosInstall.WAITING)
        install = self.step()
        self.assertEqual(install.state, AosInstall.RELOADING, install.detail)
        self.assertEqual(install.previous_release, OLD)
        self.assertEqual(self.release_in('working'), CHOSEN)
        for directory in ('init', 'certified'):  # left for the next Cleanup
            self.assertEqual(self.release_in(directory), OLD, directory)
        self.assertEqual(self.fake.reloads, [self.ip])
        self.clock.advance(RELOAD_GRACE)
        install = self.step()
        self.assertEqual(install.state, AosInstall.DONE, install.detail)
        self.assertEqual(install.detail, f'Back, running AOS {CHOSEN}.')
        self.assertIsNotNone(install.finished_at)
        self.switch.refresh_from_db()
        self.assertEqual(self.switch.aos_release, CHOSEN)
        shown = self.client_for(self.alice).get(self.path).data['install']
        self.assertEqual((shown['state'], shown['release'], shown['under_way']), ('done', CHOSEN, False))

    def test_the_switch_accounts_are_made_again_in_the_release_it_boots(self):
        self.assertEqual(self.users_on(), {'alice', 'bob'})
        self.asked()
        self.step()
        # Removed from the release it leaves, whose users table the Cleanup brings back (#74, .132 2026-11-03)
        self.assertEqual(self.users_on(), set())
        self.worker.sync_accounts()  # nor made again in it while it may not have gone down yet
        self.assertEqual(self.users_on(), set())
        # The release booted has its own local users table (#74), without them nor their password history
        self.fake.users[self.ip], self.fake.password_history[self.ip] = {}, {}
        self.clock.advance(RELOAD_GRACE)
        self.step()
        self.assertEqual(self.users_on(), {'alice', 'bob'})

    def test_it_waits_until_aos_takes_user_commands_to_make_the_accounts(self):
        # On .132 (2026-11-03), user commands were refused a while after show interfaces read
        self.asked()
        self.step()
        self.fake.users[self.ip], self.fake.password_history[self.ip] = {}, {}
        self.fake.users_not_ready.add(self.ip)
        self.clock.advance(RELOAD_GRACE)
        self.assertEqual(self.step().state, AosInstall.RELOADING)
        self.fake.users_not_ready.clear()
        self.clock.advance(RELOAD_GRACE)
        install = self.step()
        self.assertEqual(install.state, AosInstall.DONE, install.detail)
        self.assertEqual(install.warnings, [])
        self.assertEqual(self.users_on(), {'alice', 'bob'})

    def test_progress_shows_on_the_switch_in_its_topology(self):
        self.asked()
        self.step()
        topology = self.client_for(self.bob).get(f'/api/projects/{self.reservation.project_id}/topology/').data
        row = next(s for s in topology['switches'] if s['id'] == self.switch.id)
        self.assertEqual((row['aos_install']['release'], row['aos_install']['state']), (CHOSEN, 'reloading'))

    def test_a_failed_install_stays_on_its_row_for_an_hour(self):
        self.server.unreachable = True
        self.asked()
        install = self.step()
        self.assertEqual(install.state, AosInstall.FAILED)

        def shown():
            topology = self.client_for(self.bob).get(f'/api/projects/{self.reservation.project_id}/topology/').data
            return next(s for s in topology['switches'] if s['id'] == self.switch.id)['aos_install']

        self.assertEqual((shown()['state'], shown()['under_way']), ('failed', False))
        AosInstall.objects.filter(pk=install.pk).update(finished_at=install.finished_at - timedelta(hours=1, minutes=1))
        self.assertIsNone(shown())

    def test_a_failed_download_leaves_the_switch_as_it_was(self):
        self.server.unreachable = True
        self.asked()
        install = self.step()
        self.assertEqual(install.state, AosInstall.FAILED)
        self.assertIn(f"Couldn't install AOS {CHOSEN}", install.detail)
        self.assertIn(f'still runs {OLD}', install.detail)
        self.assertEqual(self.fake.reloads, [])
        self.assertEqual(self.release_in('working'), OLD)
        self.assertFalse(Quarantine.objects.exists())

    def test_a_copy_cut_short_leaves_working_as_it_was(self):
        self.fake.cut_short[self.ip] = {'working/Uos.img.blab-new': 10}
        self.asked()
        install = self.step()
        self.assertEqual(install.state, AosInstall.FAILED, install.detail)
        self.assertEqual(self.release_in('working'), OLD)
        self.assertNotIn('Uos.img.blab-new', self.fake.files(self.ip)['working'])
        self.assertEqual(self.fake.reloads, [])

    def test_a_switch_that_already_runs_the_release_is_not_reloaded(self):
        self.asked(OLD)
        install = self.step()
        self.assertEqual(install.state, AosInstall.DONE)
        self.assertEqual(self.fake.reloads, [])
        self.assertEqual(self.server.fetched, [])

    def test_a_switch_that_comes_back_in_another_release_fails(self):
        self.asked()
        self.step()
        self.fake.image_releases[self.ip]['working/Uos.img'] = OLD  # AOS fell back to certified/
        self.clock.advance(RELOAD_GRACE)
        install = self.step()
        self.assertEqual(install.state, AosInstall.FAILED)
        self.assertIn(f'running AOS {OLD}, not {CHOSEN}', install.detail)

    def test_a_switch_that_does_not_come_back_fails(self):
        self.asked()
        self.step()
        self.fake.unreachable.add(self.ip)
        self.clock.advance(RELOAD_TIMEOUT)
        install = self.step()
        self.assertEqual(install.state, AosInstall.FAILED)
        self.assertIn("Didn't come back", install.detail)

    def test_a_switch_it_cannot_reach_yet_is_tried_again_for_a_while(self):
        # On .132 (2026-11-03), a login right after a Cleanup failed once: "Error reading SSH protocol banner"
        self.fake.unreachable.add(self.ip)
        self.asked()
        install = self.step()
        self.assertEqual(install.state, AosInstall.WAITING)
        self.assertIn("Couldn't reach the Switch yet", install.detail)
        self.fake.unreachable.clear()
        self.assertEqual(self.step().state, AosInstall.RELOADING)

    def test_a_switch_it_cannot_reach_for_minutes_fails(self):
        self.fake.unreachable.add(self.ip)
        self.asked()
        self.clock.advance(timedelta(minutes=6))
        install = self.step()
        self.assertEqual(install.state, AosInstall.FAILED)
        self.assertIn("Couldn't read the Switch's AOS release", install.detail)
        self.assertEqual(self.fake.reloads, [])

    def test_an_install_cut_short_by_a_redeploy_starts_over(self):
        install = self.asked()
        AosInstall.objects.filter(pk=install.pk).update(state=AosInstall.INSTALLING)
        install = self.step()
        self.assertEqual(install.state, AosInstall.RELOADING, install.detail)
        self.assertEqual(self.release_in('working'), CHOSEN)


@override_settings(**ON)
class ReleaseTest(AosInstallTestCase):

    def test_the_switch_is_not_released_while_its_install_is_under_way(self):
        self.asked()
        result = release(self.reservation, self.alice)
        self.assertFalse(result.released)
        self.assertIn(f'AOS {CHOSEN} is being installed', result.refused)
        response = self.client_for(self.bob).post('/api/release/', {'switch': self.switch.id}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertTrue(response.data['detail'].startswith(f'AOS {CHOSEN} is being installed'))
        self.assertTrue(Reservation.objects.filter(pk=self.reservation.pk).exists())
        self.step()
        self.assertFalse(release(self.reservation, self.alice).released)  # reloading: still under way
        self.clock.advance(RELOAD_GRACE)
        self.step()
        self.assertTrue(release(self.reservation, self.alice).released)

    def test_a_forced_release_s_cleanup_waits_for_the_install(self):
        self.alice.is_staff = True
        self.alice.save()
        self.asked()
        self.step()  # reloading
        result = release(self.reservation, self.alice, force=True)
        self.assertTrue(result.released, result.failures)
        self.worker.work()
        self.assertIsNone(PendingCleanup.objects.get(switch=self.switch).started_at)  # waits
        self.clock.advance(RELOAD_GRACE)
        self.step()
        self.assertEqual(AosInstall.objects.get().state, AosInstall.DONE)
        self.run_cleanup()
        self.assertFalse(PendingCleanup.objects.exists())
        self.assertEqual(self.release_in('working'), OLD)

    def test_an_install_not_started_when_the_switch_is_released_fails(self):
        self.alice.is_staff = True
        self.alice.save()
        self.asked()
        release(self.reservation, self.alice, force=True)
        install = self.step()
        self.assertEqual(install.state, AosInstall.FAILED)
        self.assertEqual(self.fake.reloads, [])

    def test_the_cleanup_after_brings_back_the_golden_init_release(self):
        self.asked()
        self.step()
        self.clock.advance(RELOAD_GRACE)
        self.step()
        self.assertEqual(self.release_in('working'), CHOSEN)
        self.assertTrue(release(self.reservation, self.alice).released)
        self.run_cleanup()
        cleanup = self.switch.events.filter(kind=SwitchEvent.CLEANUP).latest('at')
        self.assertTrue(cleanup.ok, cleanup.reasons)
        self.assertEqual(self.release_in('working'), OLD)  # init/'s, copied over working/
        self.switch.refresh_from_db()
        self.assertEqual(self.switch.aos_release, OLD)  # recorded by the Inspection after the Cleanup
