"""
Read-only accounts (#91, docs/adr/0004): with BLAB_READ_ONLY_ACCOUNTS on, every user with a
Switch password has a read-only Switch account on every lab Switch they may not work on, made a
few at a time by the Switch worker; it turns read-write while they may work on the Switch and
read-only again at Release, and Cleanups, Inspections and the Login check leave it be. Against
fake devices.
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import override_settings

from . import switch_accounts, switch_password
from .login_check import LoginCheck
from .models import BaselineExtra, ProjectMember, Reservation, SwitchAccount
from .switch_accounts import accounts_for, sync, sync_due
from .test_release_cleanup import CleanupTestCase, in_a_week, make_switch
from .test_switch_password import GOOD, ON

OTHER = 'Other-pass9'


@override_settings(BLAB_DEVICES='fake', BLAB_READ_ONLY_ACCOUNTS=True, BLAB_CLEAN_STATE_MODE='enforce', **ON)
class ReadOnlyAccountsTest(CleanupTestCase):
    def setUp(self):
        super().setUp()
        switch_password.choose(self.alice, GOOD)
        switch_password.choose(self.bob, OTHER)
        self.ip = self.switch.mngt_IP

    def users_on(self, switch=None):
        return self.fake.users.get((switch or self.switch).mngt_IP, {})

    def read_only_on(self, switch=None):
        return self.fake.read_only.get((switch or self.switch).mngt_IP, set())

    def sync_all(self):
        """The Switch worker's account passes, until nothing is left to do."""
        for _ in range(50):
            if not sync_due(self.clock()):
                return
        self.fail('sync_due never caught up')

    def reserve_through_api(self, user, switch=None):
        response = self.client_for(user).post('/api/reserve/', {'switch': (switch or self.switch).id,
                                                                 'end_date': in_a_week()}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_every_user_with_a_switch_password_gets_one_on_every_switch_in_service(self):
        other = make_switch(2)
        make_switch(3, out_of_service_reason='broken')
        User.objects.create_user('carol', email='carol@example.com', password='pw')  # no Switch password yet
        self.sync_all()
        for switch in (self.switch, other):
            self.assertEqual(self.users_on(switch), {'alice': GOOD, 'bob': OTHER})
            self.assertEqual(self.read_only_on(switch), {'alice', 'bob'})
            self.assertEqual(self.fake.su.get(switch.mngt_IP, set()), set())
        self.assertEqual(self.users_on(make_switch(4)), {})
        self.assertEqual(self.fake.users.get('10.0.0.3', {}), {})  # Out of service: left alone
        self.assertIn((self.ip, 'user alice password ... read-only all su disable'),
                      [(ip, cmd.replace(GOOD, '...')) for ip, cmd in self.fake.commands])
        self.assertTrue(all(a.read_only and a.created for a in SwitchAccount.objects.all()))

    @override_settings(BLAB_READ_ONLY_ACCOUNTS=False)
    def test_off_nobody_gets_one(self):
        self.assertEqual(sync_due(self.clock()), [])
        self.assertEqual(self.fake.commands, [])

    def test_they_reach_the_lab_a_few_at_a_time(self):
        make_switch(2)
        User.objects.create_user('carol', email='carol@example.com', password='pw')
        switch_password.choose(User.objects.get(username='carol'), 'Third-pass9')
        with patch.object(switch_accounts, 'READ_ONLY_PER_PASS', 2):
            self.assertEqual(len(sync_due(self.clock())), 1)  # one Switch per cycle
            self.assertEqual(len(self.users_on()), 2)
            self.assertEqual(self.users_on(make_switch(5)), {})
            sync_due(self.clock())
            self.assertEqual(set(self.users_on()), {'alice', 'bob', 'carol'})

    def test_reserving_turns_the_holders_read_only(self):
        self.sync_all()
        data = self.reserve_through_api(self.alice)
        self.assertEqual(data['switch_account']['state'], 'ready')
        self.assertEqual(self.read_only_on(), {'bob'})
        self.assertIn('alice', self.fake.su[self.ip])
        self.assertEqual(self.users_on()['alice'], GOOD)
        account = SwitchAccount.objects.get(user=self.alice)
        self.assertFalse(account.read_only)

    def test_reserving_before_the_read_only_account_is_made_leaves_the_others_to_the_worker(self):
        self.reserve_through_api(self.alice)
        self.assertEqual(set(self.users_on()), {'alice'})
        self.sync_all()
        self.assertEqual(self.read_only_on(), {'bob'})

    def test_a_promoted_account_is_not_ready_until_it_is_read_write(self):
        self.sync_all()
        Reservation.objects.create(switch=self.switch, user=self.alice)  # through code that doesn't sync
        self.assertEqual(accounts_for(self.alice)[0]['state'], 'pending')

    def test_release_turns_it_read_only_and_the_cleanup_and_inspection_leave_it(self):
        self.sync_all()
        self.reserve_through_api(self.alice)
        self.release()
        self.run_cleanup()
        self.assertEqual(self.users_on(), {'alice': GOOD, 'bob': OTHER})
        self.assertEqual(self.read_only_on(), {'alice', 'bob'})
        self.assertNotIn('alice', self.fake.su.get(self.ip, set()))
        self.assertTrue(SwitchAccount.objects.get(user=self.alice).read_only)
        inspection = self.switch.last_inspection()
        self.assertTrue(inspection.ok, inspection.reasons)

    def test_a_read_write_account_left_still_fails_the_inspection(self):
        self.sync_all()
        self.reserve_through_api(self.alice)
        Reservation.objects.all().delete()  # gone without a Release: her account is still read-write
        from .inspection import ACCOUNT_LEFT, inspect
        result = inspect(self.switch)
        self.assertIn(ACCOUNT_LEFT + 'alice', result.reasons)

    def test_leaving_the_project_turns_it_read_only_at_once(self):
        other = make_switch(2)
        self.reserve_through_api(self.alice)
        reservation = Reservation.objects.get(switch=self.switch)
        project = reservation.project
        ProjectMember.objects.create(project=project, user=self.bob)
        sync(self.switch)
        self.assertNotIn('bob', self.read_only_on())
        self.assertEqual(self.client_for(self.bob).post(f'/api/projects/{project.id}/leave/').status_code, 200)
        self.assertIn('bob', self.read_only_on())
        self.assertEqual(self.users_on(other), {})  # the others wait for the worker

    def test_a_new_switch_password_reaches_the_read_only_accounts_through_the_worker(self):
        self.sync_all()
        self.client_for(self.bob).post('/api/switch_password/', {'password': 'Newer-pass9'}, format='json')
        self.assertEqual(self.users_on()['bob'], OTHER)
        self.sync_all()
        self.assertEqual(self.users_on()['bob'], 'Newer-pass9')
        self.assertIn('bob', self.read_only_on())

    def test_a_lab_managers_login_is_never_touched(self):
        BaselineExtra.objects.create(kind=BaselineExtra.LOGIN, value='bob')
        self.sync_all()
        self.assertEqual(set(self.users_on()), {'alice'})

    def test_the_login_check_makes_a_lost_one_again_and_takes_su_back(self):
        self.sync_all()
        check = LoginCheck(self.clock)
        del self.fake.users[self.ip]['alice']
        del self.fake.password_history[self.ip]['alice']
        self.fake.read_only[self.ip].discard('alice')
        self.fake.su.setdefault(self.ip, set()).add('bob')
        outcomes = check.work()
        self.assertEqual(len(outcomes), 1, outcomes)
        self.assertEqual(self.read_only_on(), {'alice', 'bob'})
        self.assertNotIn('bob', self.fake.su[self.ip])

    def test_a_read_only_login_is_no_change_for_the_sweep(self):
        from .sweep import logins_since_reload
        self.sync_all()
        self.fake.log_in(self.ip, 'bob', '10.63.33.110')
        self.assertEqual(logins_since_reload(self.switch), ([], []))
        self.fake.log_in(self.ip, 'test', '10.63.33.110')
        self.assertEqual(logins_since_reload(self.switch), ([], ['test from 10.63.33.110']))

    def test_my_lab_says_how_to_log_in_read_only(self):
        self.assertEqual(self.client_for(self.alice).get('/api/my_lab/').data['read_only_login'], 'alice')
        carol = User.objects.create_user('carol', email='carol@example.com', password='pw')
        with override_settings(BLAB_PROJECTS=False):
            self.assertIsNone(self.client_for(carol).get('/api/my_lab/').data['read_only_login'])
        with override_settings(BLAB_READ_ONLY_ACCOUNTS=False):
            self.assertIsNone(self.client_for(self.alice).get('/api/my_lab/').data['read_only_login'])
