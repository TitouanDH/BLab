"""
Quarantine access (#118): the user a Quarantine names gets a read-write Switch account, SU
included, on the Quarantined Switch while they are named, so they can fix it. Un-naming them or
lifting the Quarantine removes it, and lifting it Cleans the Switch up first, so nothing they did
with that access stays. Against fake devices.
"""
from django.test import override_settings

from . import admin_actions
from .inspection import ACCOUNT_LEFT, inspect
from .models import PendingCleanup, Quarantine, SwitchAccount, SwitchEvent
from .quarantine import lift_by_admin, recheck
from .switch_accounts import accounts_for
from .test_release_cleanup import CleanupTestCase

ON = {'BLAB_DEVICES': 'fake', 'BLAB_QUARANTINE_ACCESS': True}


@override_settings(**ON)
class QuarantineAccessTest(CleanupTestCase):
    def quarantine(self, holder=None):
        return Quarantine.objects.create(switch=self.switch, holder=holder, reasons=['Unwanted cable: 1/1/1'])

    def users_on(self):
        return self.fake.users.get(self.switch.mngt_IP, {})

    def given(self, holder=None):
        """A Quarantine naming holder (alice), whose account the Switch worker has made."""
        quarantine = self.quarantine(holder or self.alice)
        self.worker.sync_accounts()
        return quarantine

    def test_the_named_holder_gets_a_read_write_account_with_su(self):
        quarantine = self.given()
        self.assertIn('alice', self.users_on())
        self.assertIn('alice', self.fake.su[self.switch.mngt_IP])
        account = SwitchAccount.objects.get(switch=self.switch, user=self.alice)
        self.assertEqual((account.created, account.read_only), (True, False))
        quarantine.refresh_from_db()
        self.assertIsNotNone(quarantine.access_given_at)

    @override_settings(BLAB_QUARANTINE_ACCESS=False)
    def test_nothing_while_the_stage_setting_is_off(self):
        self.given()
        self.assertEqual(self.users_on(), {})
        self.assertIsNone(Quarantine.objects.get().access_given_at)

    def test_a_quarantine_naming_nobody_gives_no_access(self):
        self.quarantine(None)
        self.worker.sync_accounts()
        self.assertEqual(self.users_on(), {})

    def test_its_account_is_shown_to_the_holder_only(self):
        self.given()
        [account] = accounts_for(self.alice)
        self.assertEqual((account['switch'], account['name'], account['state'], account['quarantine']),
                         (self.switch.id, 'alice', 'ready', True))
        self.assertEqual(accounts_for(self.bob), [])

    def test_the_inspection_doesnt_count_the_access_as_an_account_left(self):
        self.given()
        self.assertTrue(inspect(self.switch).clean, inspect(self.switch).reasons)

    def test_un_naming_the_holder_removes_the_account(self):
        quarantine = self.given()
        admin_actions.unname(quarantine, self.bob)
        self.worker.sync_accounts()
        self.assertEqual(self.users_on(), {})
        self.assertFalse(SwitchAccount.objects.exists())
        self.assertEqual(accounts_for(self.alice), [])

    def test_a_clean_recheck_cleans_it_up_before_lifting_the_quarantine(self):
        quarantine = self.given()
        self.fake.users[self.switch.mngt_IP]['alice'] = 'x'  # still there
        event = recheck(self.switch, self.alice)
        self.assertTrue(event.ok)
        quarantine.refresh_from_db()
        self.assertIsNone(quarantine.lifted_at)  # not Free yet
        self.assertTrue(PendingCleanup.objects.filter(switch=self.switch).exists())
        self.assertEqual(accounts_for(self.alice), [])  # the access ends with the Cleanup

        self.run_cleanup()
        self.assertNotIn('alice', self.users_on())  # removed before the reload
        self.assertEqual(self.fake.reloads, [self.switch.mngt_IP])
        quarantine.refresh_from_db()
        self.assertIsNotNone(quarantine.lifted_at)
        self.assertFalse(PendingCleanup.objects.exists())

    def test_the_recheck_answer_says_it_is_cleaned_up_first(self):
        self.given()
        response = self.client_for(self.alice).post('/api/recheck/', {'switch': self.switch.id}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['clean'])
        self.assertIn('Cleans it up', response.data['detail'])

    def test_a_cleanup_that_doesnt_find_it_clean_gives_the_access_back(self):
        quarantine = self.given()
        recheck(self.switch, self.alice)
        self.fake.fail_on('no user alice')  # the account can't be removed: left after the Cleanup
        self.run_cleanup()
        quarantine.refresh_from_db()
        self.assertIsNone(quarantine.lifted_at)
        last = self.switch.events.filter(kind=SwitchEvent.INSPECTION).order_by('-at', '-id').first()
        self.assertIn(ACCOUNT_LEFT + 'alice', last.reasons)
        self.fake.failures.clear()
        self.assertEqual([a['state'] for a in accounts_for(self.alice)], ['ready'])  # theirs again

    def test_a_quarantine_never_given_access_is_lifted_at_once(self):
        quarantine = self.quarantine(None)
        self.assertTrue(recheck(self.switch, self.bob).ok)
        quarantine.refresh_from_db()
        self.assertIsNotNone(quarantine.lifted_at)
        self.assertFalse(PendingCleanup.objects.exists())

    def test_an_admins_lift_cleans_it_up_too(self):
        quarantine = self.given()
        lift_by_admin(quarantine, self.bob)
        quarantine.refresh_from_db()
        self.assertIsNotNone(quarantine.lifted_at)
        pending = PendingCleanup.objects.get(switch=self.switch)
        self.assertIsNone(pending.holder)  # an admin's lift, like an admin's Release, names nobody

    def test_an_admins_clean_inspection_cleans_it_up_too(self):
        quarantine = self.given()
        outcome = admin_actions.inspect_now(self.switch, self.bob)
        self.assertIn('Cleaning it up before lifting its Quarantine', outcome)
        quarantine.refresh_from_db()
        self.assertIsNone(quarantine.lifted_at)
        self.assertTrue(PendingCleanup.objects.filter(switch=self.switch).exists())

    def test_an_out_of_service_switch_is_left_alone(self):
        self.switch.out_of_service_reason = 'broken fan'
        self.switch.save()
        self.given()
        self.assertEqual(self.users_on(), {})

    def test_my_lab_shows_the_access_with_the_quarantine(self):
        self.given()
        response = self.client_for(self.alice).get('/api/my_lab/')
        self.assertEqual(response.status_code, 200, response.data)
        [named] = response.data['quarantines']
        self.assertEqual((named['account']['name'], named['account']['state']), ('alice', 'ready'))
        self.assertEqual(response.data['groups'][0]['switches'] if response.data['groups'] else [], [])

    def test_on_an_out_of_service_switch_it_is_lifted_at_once_its_return_cleans_it_up(self):
        quarantine = self.given()
        self.switch.out_of_service_reason = 'broken fan'
        self.switch.save()
        self.assertTrue(recheck(self.switch, self.alice).ok)
        quarantine.refresh_from_db()
        self.assertIsNotNone(quarantine.lifted_at)
        self.assertFalse(PendingCleanup.objects.exists())
