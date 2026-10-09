"""
Switch accounts and the secret admin password (#21, docs/adr/0004): created on Reserve and
Share, removed on Release (before the Cleanup) and Unshare, retried when a Switch can't be
reached, and an Inspection that fails on an account left behind. Against fake devices.
"""
from datetime import timedelta
from unittest.mock import patch

import paramiko
from django.contrib.auth.models import User
from django.test import SimpleTestCase, override_settings

from . import backbone as backbone_module
from . import fake_devices, lab_switch as lab_switch_module
from .inspection import ACCOUNT_LEFT, inspect, parse_user_names
from .lab_switch import CommandResult, LabSwitch, Session
from .models import Quarantine, Reservation, SwitchAccount, SwitchEvent, TopologyShare
from .quarantine import recheck
from .switch_accounts import RETRY_AFTER, account_name, accounts_for, new_password, sync, sync_due
from .test_release_cleanup import RELOAD, CleanupTestCase, in_a_week, make_switch

# show user on an OS6900, AOS 8.9.107.R02 (10.69.144.132), shortened
SHOW_USER = """User name = test,
  Password expiration     = None,
  Read/Write for domains  = All ,
  SSH allowed    = YES
User name = admin,
  Read/Write for domains  = All ,
  SSH allowed    = YES
User name = default (*),
  Read/Write for domains  = None,
  SSH allowed    = NO,
(*)Note:
  The default user is not an active user account.

User name = ankushov,
  SSH allowed    = YES
"""


class NamesAndPasswordsTest(SimpleTestCase):
    def user(self, username, id=7):
        return User(id=id, username=username)

    def test_a_name_aos_takes_is_used_as_is(self):
        for name in ('alice', 'Jean-Luc.D', 'a_b', 'bob@corp', 'x+y', 'a' * 63):
            self.assertEqual(account_name(self.user(name)), name)

    def test_a_name_aos_refuses_gets_one_from_the_user_id(self):
        for name in ('élodie', 'a' * 64, ''):
            self.assertEqual(account_name(self.user(name)), 'blab-7')

    def test_nobody_is_ever_given_admin_or_another_users_name(self):
        for name in ('admin', 'Admin', 'DEFAULT', 'blab-3'):
            self.assertEqual(account_name(self.user(name)), 'blab-7')

    def test_passwords_follow_the_aos_password_policy(self):
        for _ in range(200):
            password = new_password('ab')
            self.assertEqual(len(password), 16)
            self.assertTrue(any(c.isupper() for c in password) and any(c.islower() for c in password))
            self.assertTrue(any(c.isdigit() for c in password) and any(not c.isalnum() for c in password))
            self.assertNotIn('ab', password.lower())
            self.assertRegex(password, r'^[A-Za-z0-9.@%=-]+$')  # no quote, space or '!' for the CLI

    def test_show_user_lists_every_local_user(self):
        self.assertEqual(parse_user_names(SHOW_USER), ['test', 'admin', 'default', 'ankushov'])
        self.assertIsNone(parse_user_names('ERROR: Command not allowed on slave \n\n'))


class StubSession(Session):
    def __init__(self, answers):
        self.answers = answers
        self.sent = []

    def run(self, cmd):
        self.sent.append(cmd)
        return self.answers.get(cmd.split()[0 if cmd.startswith('user') else 2], CommandResult(0))


class UpdateAccountsTest(SimpleTestCase):
    """LabSwitch.update_accounts: AOS prints its refusals with exit status 0."""

    def switch_answering(self, **answers):
        session = StubSession(answers)

        class Connect:
            def __call__(self, ip):
                return self

            def __enter__(self):
                return session

            def __exit__(self, *exc):
                return False
        return LabSwitch('10.0.0.1', Connect()), session

    def test_an_error_line_is_a_failure_even_with_exit_status_0(self):
        switch, _ = self.switch_answering(user=CommandResult(0, 'ERROR: Command not allowed on slave \n\n'))
        failed = switch.update_accounts({'alice': 'Abcdefg1-x'}, [])
        self.assertIn('not allowed on slave', failed['alice'])
        self.assertNotIn('Abcdefg1-x', failed['alice'])  # never the password in a message

    def test_removing_an_account_already_gone_is_fine(self):
        switch, session = self.switch_answering(bob=CommandResult(0, 'ERROR: Unknown user\n'))
        self.assertEqual(switch.update_accounts({}, ['bob']), {})
        self.assertEqual(session.sent, ['no user bob'])

    def test_admin_is_never_created_nor_removed(self):
        switch, session = self.switch_answering()
        failed = switch.update_accounts({'admin': 'Abcdefg1-x'}, ['Admin', 'default'])
        self.assertEqual(set(failed), {'admin', 'Admin', 'default'})
        self.assertEqual(session.sent, [])

    def test_the_command_is_the_one_checked_on_aos_8(self):
        switch, session = self.switch_answering()
        self.assertEqual(switch.update_accounts({'alice': 'Abcdefg1-x'}, ['bob']), {})
        self.assertEqual(session.sent, ['no user bob', 'user alice password "Abcdefg1-x" read-write all'])


class AdminPasswordsTest(SimpleTestCase):
    """BLab logs in to lab Switches as admin with each of BLAB_SWITCH_ADMIN_PASSWORDS in turn."""

    def setUp(self):
        lab_switch_module._password_that_worked.clear()
        self.addCleanup(lab_switch_module._password_that_worked.clear)

    def ssh_taking(self, password):
        tried = []

        class Client:
            def set_missing_host_key_policy(self, policy):
                pass

            def connect(self, ip, port, username, password, **kwargs):
                tried.append((username, password))
                if password != taken:
                    raise paramiko.AuthenticationException('Authentication failed.')

            def close(self):
                pass
        taken = password
        patcher = patch.object(lab_switch_module.paramiko, 'SSHClient', Client)
        patcher.start()
        self.addCleanup(patcher.stop)
        return tried

    @override_settings(BLAB_SWITCH_ADMIN_PASSWORDS=['switch', 'Switch@123'])
    def test_each_password_is_tried_and_the_one_that_worked_comes_first_next_time(self):
        tried = self.ssh_taking('Switch@123')
        lab_switch_module.ssh_connect('10.0.0.1')
        self.assertEqual(tried, [('admin', 'switch'), ('admin', 'Switch@123')])
        tried.clear()
        lab_switch_module.ssh_connect('10.0.0.1')
        self.assertEqual(tried, [('admin', 'Switch@123')])

    @override_settings(BLAB_SWITCH_ADMIN_PASSWORDS=['switch', 'Switch@123'])
    def test_every_password_refused_is_a_refusal(self):
        self.ssh_taking('secret')
        with self.assertRaises(paramiko.AuthenticationException):
            lab_switch_module.ssh_connect('10.0.0.1')

    def test_a_given_password_is_the_only_one_tried(self):
        tried = self.ssh_taking('x')
        lab_switch_module.ssh_connect('10.0.0.1', 'tester', 'x')
        self.assertEqual(tried, [('tester', 'x')])

    @override_settings(BLAB_BACKBONE_USERNAME='bb', BLAB_BACKBONE_PASSWORD='bbpw',
                       BLAB_SWITCH_ADMIN_PASSWORDS=['other'])
    def test_backbones_keep_their_own_credentials(self):
        urls = []

        class Answer:
            headers = {'Set-Cookie': 'wv_sess=1; path=/'}

            def raise_for_status(self):
                pass
        session = type('Session', (), {})()
        session.cookies = type('Jar', (), {'clear': lambda self: None})()
        session.get = lambda url, **kwargs: urls.append(url) or Answer()
        with patch.object(backbone_module, '_session', return_value=session):
            backbone_module.get_cookie('10.0.0.100')
        self.assertIn('username=bb&password=bbpw', urls[0])


@override_settings(BLAB_DEVICES='fake')
class SwitchAccountsTest(CleanupTestCase):
    def users_on(self, switch=None):
        return self.fake.users.get((switch or self.switch).mngt_IP, {})

    def reserve_through_api(self, user, switch=None):
        response = self.client_for(user).post('/api/reserve/', {'switch': (switch or self.switch).id,
                                                                 'end_date': in_a_week()}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def test_reserve_creates_the_holders_account_and_shows_them_its_password(self):
        data = self.reserve_through_api(self.alice)
        account = data['switch_account']
        self.assertEqual((account['name'], account['state']), ('alice', 'ready'))
        self.assertEqual(self.users_on(), {'alice': account['password']})

    def test_reserve_creates_an_account_for_each_user_the_topology_is_shared_with(self):
        TopologyShare.objects.create(owner=self.alice, target=self.bob)
        self.reserve_through_api(self.alice)
        self.assertEqual(set(self.users_on()), {'alice', 'bob'})

    def test_share_and_unshare_create_and_remove_the_targets_accounts(self):
        other = make_switch(2)
        self.reserve_through_api(self.alice)
        self.reserve_through_api(self.alice, other)
        client = self.client_for(self.alice)
        self.assertEqual(client.post('/api/share_topology/', {'target_username': 'bob'}, format='json').status_code, 201)
        self.assertIn('bob', self.users_on())
        self.assertIn('bob', self.users_on(other))
        share = TopologyShare.objects.get()

        # The target may end the share too
        self.assertEqual(self.client_for(self.bob).delete(f'/api/unshare_topology/{share.id}/').status_code, 200)
        self.assertEqual(set(self.users_on()), {'alice'})
        self.assertEqual(set(self.users_on(other)), {'alice'})
        self.assertFalse(SwitchAccount.objects.filter(user=self.bob).exists())

    def test_release_removes_the_accounts_before_the_cleanup(self):
        TopologyShare.objects.create(owner=self.alice, target=self.bob)
        self.reserve_through_api(self.alice)
        self.release()
        self.run_cleanup()
        commands = [cmd for _, cmd in self.fake.commands]
        self.assertLess(max(commands.index('no user alice'), commands.index('no user bob')), commands.index(RELOAD))
        self.assertEqual(self.users_on(), {})
        self.assertFalse(SwitchAccount.objects.exists())
        self.assertTrue(self.switch.last_inspection().ok)

    def test_each_user_sees_only_their_own_accounts(self):
        TopologyShare.objects.create(owner=self.alice, target=self.bob)
        self.reserve_through_api(self.alice)
        mine = self.client_for(self.bob).get('/api/switch_accounts/').data['switch_accounts']
        self.assertEqual([(a['name'], a['holder'], a['state']) for a in mine], [('bob', 'alice', 'ready')])
        self.assertEqual(mine[0]['password'], self.users_on()['bob'])
        carol = User.objects.create_user('carol', email='carol@example.com', password='pw')
        self.assertEqual(self.client_for(carol).get('/api/switch_accounts/').data['switch_accounts'], [])

    def test_a_switch_that_cant_be_reached_doesnt_stop_the_reservation_and_is_retried(self):
        self.fake.unreachable.add(self.switch.mngt_IP)
        account = self.reserve_through_api(self.alice)['switch_account']
        self.assertEqual(account['state'], 'failed')
        self.assertIsNone(account['password'])
        self.assertTrue(Reservation.objects.filter(switch=self.switch).exists())

        self.fake.unreachable.clear()
        self.assertEqual(self.worker.sync_accounts(), [])  # not before RETRY_AFTER
        self.clock.advance(RETRY_AFTER + timedelta(minutes=1))
        self.assertEqual(self.worker.sync_accounts(), [f'Switch accounts of {self.switch.mngt_IP}: up to date'])
        self.assertEqual(accounts_for(self.alice)[0]['state'], 'ready')
        self.assertIn('alice', self.users_on())

    def test_a_reservation_made_without_an_account_gets_one_from_the_worker(self):
        # As production's code from before this change reserves
        self.reserve()
        self.worker.sync_accounts()
        self.assertIn('alice', self.users_on())

    def test_an_inspection_fails_on_an_account_left_once_the_switch_is_not_reserved(self):
        self.reserve_through_api(self.alice)
        self.fake.users[self.switch.mngt_IP]['test'] = 'x'  # made by hand, not by BLab
        self.assertTrue(inspect(self.switch).clean)  # reserved: it is the holder's
        Reservation.objects.all().delete()
        result = inspect(self.switch)
        self.assertEqual(result.reasons, [ACCOUNT_LEFT + 'alice'])
        self.assertEqual(result.warnings, ["local users BLab didn't create: test"])

    def test_an_account_blab_couldnt_remove_quarantines_naming_nobody_until_it_is_removed(self):
        self.reserve_through_api(self.alice)
        self.release()
        self.fake.fail_on('no user alice')
        with self.assertLogs('api.switch_accounts', 'WARNING'):
            self.run_cleanup()
        self.assertEqual(self.fake.reloads, [self.switch.mngt_IP])  # the Cleanup still happens
        cleanup = self.switch.events.get(kind=SwitchEvent.CLEANUP)
        self.assertIn("Couldn't remove the Switch account alice", cleanup.warnings[0])
        quarantine = Quarantine.objects.get()
        self.assertEqual(quarantine.reasons, [ACCOUNT_LEFT + 'alice'])
        self.assertIsNone(quarantine.holder)  # BLab's failure, not alice's

        self.clock.advance(RETRY_AFTER)
        self.worker.sync_accounts()
        self.assertEqual(self.users_on(), {})
        self.assertTrue(recheck(self.switch, self.bob).ok)
        self.assertIsNotNone(Quarantine.objects.get().lifted_at)

    def test_a_deleted_users_name_taken_again_keeps_the_new_users_account(self):
        old = User.objects.create_user('dave', email='dave@example.com', password='pw')
        TopologyShare.objects.create(owner=self.alice, target=old)
        self.reserve_through_api(self.alice)
        User.objects.filter(id=old.id).delete()  # its share goes with it, its account row stays
        new = User.objects.create_user('dave', email='dave@example.com', password='pw')
        TopologyShare.objects.create(owner=self.alice, target=new)
        self.assertEqual(sync(self.switch), [])
        self.assertEqual(list(SwitchAccount.objects.filter(name='dave').values_list('user_id', flat=True)), [new.id])
        self.assertEqual(self.users_on()['dave'], SwitchAccount.objects.get(name='dave').password)
        self.assertNotIn('no user dave', [cmd for _, cmd in self.fake.commands])

    def test_an_account_whose_row_an_overlapping_sync_deleted_is_not_forgotten(self):
        self.reserve()
        real = LabSwitch.update_accounts

        def meanwhile(switch, create, remove):
            failed = real(switch, create, remove)
            SwitchAccount.objects.all().delete()  # as an overlapping sync that saw no Reservation would
            return failed
        with patch.object(LabSwitch, 'update_accounts', meanwhile), self.assertLogs('api.switch_accounts', 'WARNING'):
            self.assertEqual(sync(self.switch), [])
        account = SwitchAccount.objects.get()
        self.assertTrue(account.created)
        self.assertEqual(self.users_on(), {'alice': account.password})

    def test_a_deleted_users_account_is_removed(self):
        self.reserve_through_api(self.alice)
        TopologyShare.objects.create(owner=self.alice, target=self.bob)
        sync(self.switch)
        TopologyShare.objects.all().delete()
        User.objects.filter(id=self.bob.id).delete()
        self.assertEqual(sync_due(self.clock()), [f'Switch accounts of {self.switch.mngt_IP}: up to date'])
        self.assertEqual(set(self.users_on()), {'alice'})
