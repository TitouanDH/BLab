"""
Guest logins (#96, docs/adr/0004): with BLAB_GUEST_LOGINS on, a member adds a Guest to a Project,
which gets a read-write Switch account, SU allowed, on the Project's Switches until Release or
until it is deleted; Inspections and the Sweep name the member who added it. Against fake devices.
"""
from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework.test import APIClient

from . import projects, switch_password
from .inspection import inspect
from .login_check import LoginCheck
from .models import BaselineExtra, Guest, GuestAccount, ProjectMember, Reservation
from .switch_accounts import sync, sync_due
from .test_release_cleanup import CleanupTestCase, make_switch
from .test_switch_password import ON

PASSWORD = 'Guest-Pass1'


@override_settings(BLAB_DEVICES='fake', BLAB_GUEST_LOGINS=True, **ON)
class GuestLoginsTest(CleanupTestCase):
    def setUp(self):
        super().setUp()
        self.project = projects.create(self.alice, 'Demo')
        ProjectMember.objects.create(project=self.project, user=self.bob)
        self.carol = User.objects.create_user('carol', email='carol@example.com', password='pw')
        ProjectMember.objects.create(project=self.project, user=self.carol)
        for user, password in ((self.alice, 'First-pass1'), (self.bob, 'Second-pass2'), (self.carol, 'Third-pass3')):
            switch_password.choose(user, password)
        self.ip = self.switch.mngt_IP
        Reservation.objects.create(switch=self.switch, user=self.alice, project=self.project)
        sync(self.switch)

    def add(self, user, name='visitor', password=PASSWORD):
        return self.client_for(user).post(f'/api/projects/{self.project.id}/guests/',
                                          {'name': name, 'password': password}, format='json')

    def delete(self, user, guest):
        return self.client_for(user).delete(f'/api/projects/{self.project.id}/guests/{guest.id}/')

    def users_on(self, switch=None):
        return self.fake.users.get((switch or self.switch).mngt_IP, {})

    def sync_all(self):
        for _ in range(20):
            if not sync_due(self.clock()):
                return
        self.fail('sync_due never caught up')

    def test_a_member_adds_a_guest_who_gets_a_read_write_account_with_su(self):
        response = self.add(self.bob)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.users_on()['visitor'], PASSWORD)
        self.assertIn('visitor', self.fake.su[self.ip])
        self.assertNotIn('visitor', self.fake.read_only.get(self.ip, set()))
        guests = response.data['project']['guests']
        self.assertEqual([(g['name'], g['added_by']['username'], g['may_delete']) for g in guests],
                         [('visitor', 'bob', True)])
        account = GuestAccount.objects.get()
        self.assertTrue(account.created)
        self.assertEqual(account.added_by, self.bob)
        # Stored encrypted
        self.assertNotIn(PASSWORD, Guest.objects.get().encrypted)
        # Nothing more to do afterwards
        self.fake.commands.clear()
        self.assertEqual(sync_due(self.clock()), [])
        self.assertEqual(self.fake.commands, [])

    def test_a_switch_reserved_into_the_project_later_gets_it_and_others_dont(self):
        self.add(self.bob)
        later, elsewhere = make_switch(2), make_switch(3)
        Reservation.objects.create(switch=later, user=self.bob, project=self.project)
        Reservation.objects.create(switch=elsewhere, user=self.bob)
        self.sync_all()
        self.assertIn('visitor', self.users_on(later))
        self.assertNotIn('visitor', self.users_on(elsewhere))

    def test_names_refused(self):
        BaselineExtra.objects.create(kind=BaselineExtra.LOGIN, value='labmgr')
        self.assertEqual(self.add(self.bob, 'friend').status_code, 201)
        for name in ('alice', 'ALICE', 'Friend', 'admin', 'default', 'blab-12', 'labmgr', 'two words', 'é', ''):
            response = self.add(self.bob, name)
            self.assertEqual(response.status_code, 400, name)
        for password in ('shor1A-', 'nosymbol1A', 'Visitor-pass1', 'all-lower1', 'Has space 1A'):
            response = self.add(self.bob, 'visitor', password)
            self.assertEqual(response.status_code, 400, password)
        self.assertIn("guest's name", self.add(self.bob, 'visitor', 'Visitor-pass1').data['detail'])

    def test_a_new_user_cant_take_a_guests_name(self):
        self.add(self.bob)
        response = APIClient().post('/api/signup/', {'username': 'Visitor', 'email': 'v@example.com',
                                                     'password': 'Pw-123456'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('username', response.data)

    def test_who_may_delete_it(self):
        self.add(self.bob)
        guest = Guest.objects.get()
        self.assertEqual(self.delete(self.carol, guest).status_code, 403)  # neither added it nor holds
        self.assertEqual(self.delete(self.alice, guest).status_code, 200)  # holds a Switch of the Project
        self.assertNotIn('visitor', self.users_on())
        self.assertFalse(GuestAccount.objects.exists())
        self.add(self.bob)
        self.assertEqual(self.delete(self.bob, Guest.objects.get()).status_code, 200)  # added it

    def test_an_account_that_couldnt_be_removed_stays_until_it_is(self):
        self.add(self.bob)
        self.fake.unreachable.add(self.ip)
        self.delete(self.bob, Guest.objects.get())
        self.assertEqual(GuestAccount.objects.get().guest, None)
        self.fake.unreachable.discard(self.ip)
        GuestAccount.objects.update(tried_at=None)  # its retry is due
        self.sync_all()
        self.assertNotIn('visitor', self.users_on())
        self.assertFalse(GuestAccount.objects.exists())

    def test_release_removes_it_and_the_cleanup_finds_the_switch_clean(self):
        self.add(self.bob)
        self.release()
        self.run_cleanup()
        self.assertNotIn('visitor', self.users_on())
        self.assertFalse(GuestAccount.objects.exists())
        self.assertTrue(Guest.objects.exists())  # still the Project's, for its next Switch
        self.assertNotIn('visitor', ' '.join(inspect(self.switch).reasons))

    def test_an_account_left_after_release_names_who_added_it(self):
        self.add(self.bob)
        Reservation.objects.filter(switch=self.switch).delete()
        reasons = ' '.join(inspect(self.switch).reasons)
        self.assertIn('visitor (guest of bob)', reasons)

    def test_the_login_check_makes_a_lost_one_again(self):
        self.add(self.bob)
        del self.fake.users[self.ip]['visitor']
        del self.fake.password_history[self.ip]['visitor']
        outcomes = LoginCheck(self.clock).work()
        self.assertEqual(len(outcomes), 1, outcomes)
        self.assertEqual(self.users_on()['visitor'], PASSWORD)

    def test_reserved_inspection_doesnt_flag_its_su(self):
        self.add(self.bob)
        self.assertFalse([w for w in inspect(self.switch).warnings if 'visitor' in w])

    @override_settings(BLAB_GUEST_LOGINS=False)
    def test_off_no_guests_and_nothing_sent(self):
        self.fake.commands.clear()
        self.assertEqual(self.add(self.bob).status_code, 404)
        response = self.client_for(self.alice).get(f'/api/projects/{self.project.id}/')
        self.assertIsNone(response.data['guests'])
        self.assertEqual(sync_due(self.clock()), [])
        self.assertEqual(sync(self.switch), [])
        self.assertEqual(self.fake.commands, [])
