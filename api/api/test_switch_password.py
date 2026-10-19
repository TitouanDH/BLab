"""
Switch passwords (#40, CONTEXT.md, docs/adr/0004): chosen at first login behind BLAB_PROJECTS,
checked against the rules, stored encrypted, pushed to every Switch account the user has, and
never shown again. Against fake devices.
"""
import os
import runpy
from datetime import timedelta
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import switch_password
from .lab_switch import CommandResult, LabSwitch
from .models import SwitchAccount, SwitchPassword, TopologyShare
from .switch_accounts import RETRY_AFTER, accounts_for
from .test_release_cleanup import CleanupTestCase, in_a_week, make_switch
from .test_switch_accounts import StubSession

KEY = Fernet.generate_key().decode()
OLD_KEY = Fernet.generate_key().decode()
ON = dict(BLAB_PROJECTS=True, BLAB_SWITCH_PASSWORD_KEYS=[KEY])
GOOD = 'Lab-Pass1word'
SETTINGS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend', 'settings.py')


class RulesTest(SimpleTestCase):
    user = User(id=7, username='alice')

    def broken(self, password, user=None):
        return switch_password.problems(password, user or self.user)

    def test_a_password_that_follows_every_rule_is_taken(self):
        for password in (GOOD, 'Aa1#%&()*+,-./:;<=>?@[\\]^_`{|}', 'Xy9$' * 2, 'A' * 27 + 'b1~'):
            self.assertEqual(self.broken(password), [], password)

    def test_each_rule_says_what_is_missing(self):
        cases = {
            'Ab1-xyz': 'It must be 8 to 30 characters long.',
            'Ab1-' + 'x' * 27: 'It must be 8 to 30 characters long.',
            'lab-pass1word': 'It must hold an upper case letter.',
            'LAB-PASS1WORD': 'It must hold a lower case letter.',
            'Lab-Password': 'It must hold a digit.',
            'LabPass1word': 'It must hold a symbol.',
            'Lab-Pass1word!': "It may only hold English letters, digits and symbols, without spaces, ! \" or '.",
            'Lab Pass1word': "It may only hold English letters, digits and symbols, without spaces, ! \" or '.",
            'Lab-"Pass1word': "It may only hold English letters, digits and symbols, without spaces, ! \" or '.",
            "Lab-'Pass1word": "It may only hold English letters, digits and symbols, without spaces, ! \" or '.",
            'Lab-Pàss1word': "It may only hold English letters, digits and symbols, without spaces, ! \" or '.",
            'My-ALICE-pw1': 'It must not contain your username.',
        }
        for password, why in cases.items():
            self.assertIn(why, self.broken(password), password)

    def test_the_labs_old_passwords_are_refused_in_any_case(self):
        for password in ('Switch@123', 'SWITCH@123', 'switch@123', 'Switch', 'ADMIN'):
            self.assertIn("It must not be the lab's old password.", self.broken(password), password)

    def test_the_switch_account_name_counts_as_the_username(self):
        user = User(id=12, username='élodie')  # her Switch account is blab-12
        self.assertIn('It must not contain your username.', self.broken('Blab-12-xyZ', user))


@override_settings(**ON)
class EncryptionTest(TestCase):
    def test_round_trip_and_nothing_readable_is_stored(self):
        alice = User.objects.create_user('alice', email='alice@example.com', password='pw')
        switch_password.choose(alice, GOOD)
        row = SwitchPassword.objects.get(user=alice)
        self.assertNotIn(GOOD, row.encrypted)
        self.assertEqual(switch_password.decrypt(row.encrypted), GOOD)
        self.assertEqual(switch_password.current([alice.id])[alice.id][0], GOOD)

    def test_the_key_can_change_keeping_the_old_one_to_decrypt(self):
        with override_settings(BLAB_SWITCH_PASSWORD_KEYS=[OLD_KEY]):
            encrypted = switch_password.encrypt(GOOD)
        with override_settings(BLAB_SWITCH_PASSWORD_KEYS=[KEY, OLD_KEY]):
            self.assertEqual(switch_password.decrypt(encrypted), GOOD)

    def test_one_no_key_opens_is_left_out(self):
        alice = User.objects.create_user('alice', email='alice@example.com', password='pw')
        with override_settings(BLAB_SWITCH_PASSWORD_KEYS=[OLD_KEY]):
            switch_password.choose(alice, GOOD)
        self.assertEqual(switch_password.current([alice.id]), {})

    @override_settings(BLAB_PROJECTS=False)
    def test_off_nobody_has_one(self):
        alice = User.objects.create_user('alice', email='alice@example.com', password='pw')
        SwitchPassword.objects.create(user=alice, encrypted=switch_password.encrypt(GOOD), changed_at=timezone.now())
        self.assertEqual(switch_password.current([alice.id]), {})


class StageSettingTest(SimpleTestCase):
    """The key is only needed with BLAB_PROJECTS on: off, a stage without it starts as before."""

    def load(self, **env):
        clean = {k: v for k, v in os.environ.items() if k not in ('BLAB_PROJECTS', 'BLAB_SWITCH_PASSWORD_KEY')}
        with patch.dict(os.environ, {**clean, **env}, clear=True):
            return runpy.run_path(SETTINGS)

    def test_off_by_default_and_no_key_needed(self):
        settings = self.load()
        self.assertFalse(settings['BLAB_PROJECTS'])
        self.assertEqual(settings['BLAB_SWITCH_PASSWORD_KEYS'], [])
        self.assertFalse(self.load(BLAB_PROJECTS='off')['BLAB_PROJECTS'])

    def test_on_needs_a_valid_key(self):
        with self.assertRaisesRegex(ValueError, 'BLAB_SWITCH_PASSWORD_KEY'):
            self.load(BLAB_PROJECTS='on')
        with self.assertRaises(ValueError):
            self.load(BLAB_PROJECTS='on', BLAB_SWITCH_PASSWORD_KEY='not-a-key')
        settings = self.load(BLAB_PROJECTS='on', BLAB_SWITCH_PASSWORD_KEY=f'{KEY}, {OLD_KEY}')
        self.assertTrue(settings['BLAB_PROJECTS'])
        self.assertEqual(settings['BLAB_SWITCH_PASSWORD_KEYS'], [KEY, OLD_KEY])

    def test_a_value_it_doesnt_know_stops_startup(self):
        with self.assertRaises(ValueError):
            self.load(BLAB_PROJECTS='maybe')


class QuotingTest(SimpleTestCase):
    """The user command goes through a shell on the Switch (10.69.144.132): every symbol must reach AOS as is."""

    def test_symbols_are_quoted_for_the_shell_and_never_shown(self):
        session = StubSession({})
        switch = LabSwitch('10.0.0.1', lambda ip: _Use(session))
        password = 'Ab1$(x)`\\;|&*'
        self.assertEqual(switch.update_accounts({'alice': password}, []).failed, {})
        self.assertEqual(session.sent, ["user alice password 'Ab1$(x)`\\;|&*' read-write all", 'write memory'])

    def test_a_password_aos_had_lately_is_set_by_making_the_account_afresh(self):
        history = CommandResult(0, 'ERROR: New password can not be the same as your current password '
                                   'or any of your previous 3 passwords\n')
        answers = iter([history, CommandResult(0), CommandResult(0), CommandResult(0)])

        class Session(StubSession):
            def run(self, cmd):
                self.sent.append(cmd)
                return next(answers)
        session = Session({})
        switch = LabSwitch('10.0.0.1', lambda ip: _Use(session))
        self.assertEqual(switch.update_accounts({'alice': GOOD}, []).failed, {})
        self.assertEqual(session.sent, [f'user alice password {GOOD} read-write all', 'no user alice',
                                        f'user alice password {GOOD} read-write all', 'write memory'])


class _Use:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, *exc):
        return False


@override_settings(**ON)
class OnboardingGateTest(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user('alice', email='alice@example.com', password='pw')
        self.client = APIClient()
        self.client.force_authenticate(self.alice)

    def test_every_page_waits_for_the_switch_password(self):
        response = self.client.get('/api/list_switch/')
        self.assertEqual((response.status_code, response.data['code']), (403, 'switch_password_required'))
        account = self.client.get('/api/account/').data
        self.assertEqual((account['switch_password_enabled'], account['switch_password_set']), (True, False))

        response = self.client.post('/api/switch_password/', {'password': GOOD}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn(GOOD, str(response.data))  # never given back
        self.assertEqual(self.client.get('/api/list_switch/').status_code, 200)
        self.assertTrue(self.client.get('/api/account/').data['switch_password_set'])

    def test_login_says_what_is_missing(self):
        response = APIClient().post('/api/login/', {'username': 'alice', 'password': 'pw'}, format='json')
        self.assertEqual((response.data['user']['switch_password_enabled'],
                          response.data['user']['switch_password_set']), (True, False))

    def test_the_email_comes_first(self):
        self.alice.email = ''
        self.alice.save()
        response = self.client.post('/api/switch_password/', {'password': GOOD}, format='json')
        self.assertEqual((response.status_code, response.data['code']), (403, 'email_required'))
        self.assertEqual(self.client.get('/api/list_switch/').data['code'], 'email_required')

    def test_a_refused_password_says_every_rule_it_breaks_and_saves_nothing(self):
        response = self.client.post('/api/switch_password/', {'password': 'switch'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn("It must not be the lab's old password.", response.data['password'])
        self.assertIn('It must hold a digit.', response.data['password'])
        for body in ({}, {'password': 12345678}):
            self.assertEqual(self.client.post('/api/switch_password/', body, format='json').status_code, 400)
        self.assertFalse(SwitchPassword.objects.exists())

    @override_settings(BLAB_PROJECTS=False)
    def test_off_nothing_changes(self):
        self.assertEqual(self.client.get('/api/list_switch/').status_code, 200)
        self.assertEqual(self.client.post('/api/switch_password/', {'password': GOOD}, format='json').status_code, 404)
        self.assertFalse(SwitchPassword.objects.exists())


@override_settings(BLAB_DEVICES='fake', **ON)
class PushTest(CleanupTestCase):
    def users_on(self, switch=None):
        return self.fake.users.get((switch or self.switch).mngt_IP, {})

    def choose(self, password=GOOD, user=None):
        response = self.client_for(user or self.alice).post('/api/switch_password/', {'password': password},
                                                            format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def reserve_through_api(self, user, switch=None):
        response = self.client_for(user).post('/api/reserve/', {'switch': (switch or self.switch).id,
                                                                 'end_date': in_a_week()}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def reserve_before_choosing(self, switch=None):
        with override_settings(BLAB_PROJECTS=False):  # an account made with a random password
            return self.reserve_through_api(self.alice, switch)

    def test_choosing_it_sets_it_on_every_account_the_user_has(self):
        other = make_switch(2)
        TopologyShare.objects.create(owner=self.alice, target=self.bob)
        self.reserve_before_choosing()
        self.reserve_before_choosing(other)
        bobs = self.users_on()['bob']

        self.assertEqual(self.choose()['failed'], [])
        self.assertEqual(self.users_on()['alice'], GOOD)
        self.assertEqual(self.users_on(other)['alice'], GOOD)
        self.assertEqual(self.users_on()['bob'], bobs)  # nobody else's
        mine = accounts_for(self.alice)
        self.assertEqual([(a['state'], a['password'], a['switch_password']) for a in mine],
                         [('ready', None, True)] * 2)

    def test_new_accounts_get_it_and_it_is_never_shown(self):
        self.choose()
        account = self.reserve_through_api(self.alice)['switch_account']
        self.assertEqual((account['state'], account['password'], account['switch_password']), ('ready', None, True))
        self.assertEqual(self.users_on()['alice'], GOOD)
        listed = self.client_for(self.alice).get('/api/switch_accounts/').data['switch_accounts']
        self.assertNotIn(GOOD, str(listed))

    def test_an_account_it_couldnt_reach_is_retried_by_the_worker(self):
        self.reserve_before_choosing()
        random_one = self.users_on()['alice']
        self.fake.unreachable.add(self.switch.mngt_IP)
        failed = self.choose()['failed']
        self.assertEqual([(a['mngt_IP'], a['state']) for a in failed], [(self.switch.mngt_IP, 'failed')])
        self.assertEqual(self.users_on()['alice'], random_one)

        self.fake.unreachable.clear()
        self.clock.advance(RETRY_AFTER + timedelta(minutes=1))
        self.assertEqual(self.worker.sync_accounts(), [f'Switch accounts of {self.switch.mngt_IP}: up to date'])
        self.assertEqual(self.users_on()['alice'], GOOD)
        self.assertEqual(accounts_for(self.alice)[0]['state'], 'ready')

    def test_changing_it_back_to_an_earlier_one_works(self):
        self.reserve_before_choosing()
        for password in (GOOD, 'Other-Pass2', GOOD, GOOD):
            self.assertEqual(self.choose(password)['failed'], [], password)
            self.assertEqual(self.users_on()['alice'], password)

    def test_one_chosen_while_an_older_one_was_being_pushed_is_pushed_too(self):
        self.reserve_before_choosing()
        self.choose()
        # As if the push of an older choice finished after this one was saved
        SwitchAccount.objects.filter(user=self.alice).update(switch_password_at=timezone.now() - timedelta(days=1))
        self.assertEqual(accounts_for(self.alice)[0]['state'], 'pending')
        self.fake.users[self.switch.mngt_IP]['alice'] = 'Older-Pass3'
        self.assertEqual(self.worker.sync_accounts(), [f'Switch accounts of {self.switch.mngt_IP}: up to date'])
        self.assertEqual(self.users_on()['alice'], GOOD)

    def test_release_removes_the_account_as_before(self):
        self.choose()
        self.reserve_through_api(self.alice)
        self.release()
        self.run_cleanup()
        self.assertEqual(self.users_on(), {})
        self.assertFalse(SwitchAccount.objects.exists())

    @override_settings(BLAB_PROJECTS=False)
    def test_off_accounts_keep_random_passwords_shown_as_before(self):
        SwitchPassword.objects.create(user=self.alice, encrypted='x', changed_at=timezone.now())
        account = self.reserve_through_api(self.alice)['switch_account']
        self.assertEqual((account['state'], account['switch_password']), ('ready', False))
        self.assertEqual(self.users_on()['alice'], account['password'])
