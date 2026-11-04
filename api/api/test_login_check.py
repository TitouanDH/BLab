"""
The Login check (#92, api.login_check): after a reload BLab didn't start, into another AOS
release, admin may take only a factory password and the Switch accounts may be gone. The
Switch worker gives admin the secret password again and makes the accounts again. And My lab's
Re-create my account. Against fake devices.
"""
from unittest.mock import patch

from django.test import override_settings

from . import login_check
from .login_check import CHECK_EVERY, LOOK_AGAIN_AFTER, LoginCheck
from .models import PendingCleanup, Reservation, SwitchAccount
from .switch_accounts import sync
from .test_release_cleanup import CleanupTestCase, make_switch


class LoginCheckTest(CleanupTestCase):
    def setUp(self):
        super().setUp()
        self.check = LoginCheck(self.clock)
        self.ip = self.switch.mngt_IP

    def reserved_with_account(self):
        Reservation.objects.create(switch=self.switch, user=self.alice)
        self.assertEqual(sync(self.switch), [])
        self.assertIn('alice', self.fake.users[self.ip])

    def reload_into_another_release(self, factory_admin=True):
        """A member upgrades from the CLI and reloads: the other release's table has no Switch account."""
        self.fake.users[self.ip] = {}
        self.fake.password_history[self.ip] = {}  # that table's own
        if factory_admin:
            self.fake.factory_admin.add(self.ip)

    def test_admin_with_only_a_factory_password_gets_the_secret_one_and_the_accounts_are_made_again(self):
        self.reserved_with_account()
        self.reload_into_another_release()
        self.fake.commands.clear()
        outcomes = self.check.work()
        self.assertNotIn(self.ip, self.fake.factory_admin)
        self.assertIn('alice', self.fake.users[self.ip])
        self.assertIn((self.ip, 'user admin password ...'), self.fake.commands)
        self.assertEqual(len(outcomes), 1)
        self.assertIn(login_check.ADMIN_SECURED, outcomes[0])
        self.assertTrue(SwitchAccount.objects.get(user=self.alice).created)

    def test_accounts_gone_while_admin_kept_the_secret_password_are_made_again(self):
        self.reserved_with_account()
        self.reload_into_another_release(factory_admin=False)
        outcomes = self.check.work()
        self.assertIn('alice', self.fake.users[self.ip])
        self.assertEqual(outcomes, [f"Logins of {self.ip}: Switch accounts gone, made again: alice"])
        # admin was never touched
        self.assertNotIn((self.ip, 'user admin password ...'), self.fake.commands)

    def test_an_account_made_before_su_was_allowed_is_given_it(self):
        # #95: accounts made without SU converge without a migration
        self.reserved_with_account()
        self.fake.su[self.ip].discard('alice')
        outcomes = self.check.work()
        self.assertIn('alice', self.fake.su[self.ip])
        self.assertEqual(outcomes, [f"Logins of {self.ip}: Switch accounts without SU allowed, or read-only with it, made again: alice"])
        self.assertTrue(SwitchAccount.objects.get(user=self.alice).created)

    def test_a_release_that_doesnt_say_leaves_su_alone(self):
        self.fake.without_su.add(self.ip)
        self.reserved_with_account()
        self.fake.commands.clear()
        self.assertEqual(self.check.work(), [])
        self.assertEqual([c for _, c in self.fake.commands if c.startswith('user')], [])

    def test_a_switch_with_its_logins_is_left_alone_and_checked_again_later(self):
        self.reserved_with_account()
        self.fake.commands.clear()
        self.assertEqual(self.check.work(), [])
        self.assertEqual([c for _, c in self.fake.commands if c.startswith('user')], [])
        self.reload_into_another_release()
        self.assertEqual(self.check.work(), [])  # not due yet
        self.clock.advance(CHECK_EVERY)
        self.assertEqual(len(self.check.work()), 1)
        self.assertIn('alice', self.fake.users[self.ip])

    def test_an_unreserved_switch_only_gets_admin_secured(self):
        self.fake.factory_admin.add(self.ip)
        self.check.work()
        self.assertNotIn(self.ip, self.fake.factory_admin)
        self.assertEqual(self.fake.users.get(self.ip, {}), {})

    def test_out_of_service_and_cleaning_up_switches_are_left_alone(self):
        other = make_switch(2, out_of_service_reason='leased')
        self.fake.factory_admin.update({self.ip, other.mngt_IP})
        PendingCleanup.objects.create(switch=self.switch)
        self.assertEqual(self.check.work(), [])
        self.assertEqual(self.fake.factory_admin, {self.ip, other.mngt_IP})

    def test_aos_not_ready_for_user_commands_is_looked_at_again_soon(self):
        self.reserved_with_account()
        self.reload_into_another_release()
        self.fake.users_not_ready.add(self.ip)
        outcomes = self.check.work()
        self.assertIn('ready state', outcomes[0])
        self.fake.users_not_ready.discard(self.ip)
        self.clock.advance(LOOK_AGAIN_AFTER)
        self.check.work()
        self.assertNotIn(self.ip, self.fake.factory_admin)
        self.assertIn('alice', self.fake.users[self.ip])

    def test_the_switch_worker_runs_it(self):
        self.reserved_with_account()
        self.reload_into_another_release()
        self.worker.check_logins()
        self.assertNotIn(self.ip, self.fake.factory_admin)
        self.assertIn('alice', self.fake.users[self.ip])

    def test_an_unreachable_switch_says_so_and_waits(self):
        self.fake.unreachable.add(self.ip)
        self.fake.factory_admin.add(self.ip)
        outcomes = self.check.work()
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(self.check.work(), [])


@override_settings(BLAB_DEVICES='fake')
class RecreateMyAccountTest(CleanupTestCase):
    def test_the_worker_makes_my_account_again(self):
        Reservation.objects.create(switch=self.switch, user=self.alice)
        sync(self.switch)
        self.fake.users[self.switch.mngt_IP] = {}  # lost on its own
        self.fake.password_history[self.switch.mngt_IP] = {}
        response = self.client_for(self.alice).post(f'/api/switch_accounts/{self.switch.id}/recreate/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['switch_account']['state'], 'pending')
        self.worker.sync_accounts()
        self.assertIn('alice', self.fake.users[self.switch.mngt_IP])
        self.assertTrue(SwitchAccount.objects.get(user=self.alice).created)

    def test_only_on_a_switch_i_may_work_on(self):
        Reservation.objects.create(switch=self.switch, user=self.alice)
        sync(self.switch)
        response = self.client_for(self.bob).post(f'/api/switch_accounts/{self.switch.id}/recreate/')
        self.assertEqual(response.status_code, 404)
        self.assertTrue(SwitchAccount.objects.get(user=self.alice).created)


class OlderAdminFirstTest(CleanupTestCase):
    def setUp(self):
        super().setUp()
        self.check = LoginCheck(self.clock)
        self.ip = self.switch.mngt_IP

    def test_a_switch_whose_last_login_took_an_older_password_is_looked_at_sooner(self):
        self.assertEqual(self.check.work(), [])  # checked: not due for CHECK_EVERY
        self.fake.factory_admin.add(self.ip)
        with patch.object(login_check, 'took_older_admin_password', lambda ip: ip == self.ip):
            self.assertEqual(self.check.work(), [])  # at most once a minute
            self.clock.advance(LOOK_AGAIN_AFTER)
            self.assertEqual(len(self.check.work()), 1)
        self.assertNotIn(self.ip, self.fake.factory_admin)
