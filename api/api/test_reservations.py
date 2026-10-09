"""
Reservation limits and Renewal (#20): the end date is required and at most 14 days away,
a Renewal pushes it back by 7 days at most twice, and admins may set any end date.
"""
import importlib
from datetime import timedelta
from io import StringIO

from django.apps import apps
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.utils import timezone

from .admin import ReservationAdmin
from .models import PendingCleanup, Reservation, TopologyShare
from .reservations import MAX_LENGTH, RENEWAL, cap_unbounded
from .test_release_cleanup import CleanupTestCase, make_switch

cap_migration = importlib.import_module('api.migrations.0007_cap_existing_reservations')


class LimitsTestCase(CleanupTestCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user('root', password='pw', is_staff=True)

    def post_reserve(self, user, end_date, switch=None):
        data = {'switch': (switch or self.switch).id}
        if end_date is not None:
            data['end_date'] = end_date if isinstance(end_date, str) else end_date.isoformat()
        return self.client_for(user).post('/api/reserve/', data, format='json')

    def post_renew(self, user, switch=None):
        return self.client_for(user).post('/api/renew/', {'switch': (switch or self.switch).id}, format='json')

    def reservation(self):
        return Reservation.objects.get(switch=self.switch)


class ReserveLimitsTest(LimitsTestCase):
    def test_an_end_date_is_required(self):
        response = self.post_reserve(self.alice, None)
        self.assertEqual(response.status_code, 400)
        self.assertIn('end date is required', response.data['detail'])
        self.assertEqual(self.post_reserve(self.alice, 'next tuesday').status_code, 400)
        self.assertFalse(Reservation.objects.exists())

    def test_the_end_date_must_be_in_the_future(self):
        response = self.post_reserve(self.alice, timezone.now() - timedelta(minutes=1))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Reservation.objects.exists())

    def test_at_most_14_days(self):
        response = self.post_reserve(self.alice, timezone.now() + MAX_LENGTH + timedelta(hours=1))
        self.assertEqual(response.status_code, 400)
        self.assertIn('14 days at most', response.data['detail'])
        self.assertFalse(Reservation.objects.exists())

        end = timezone.now() + MAX_LENGTH
        response = self.post_reserve(self.alice, end)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(response.data['admin_exception'])
        reservation = self.reservation()
        self.assertEqual(reservation.end_date, end)
        self.assertEqual(reservation.renewals, 0)
        self.assertFalse(reservation.admin_exception)

    def test_a_naive_end_date_is_taken_in_the_servers_time_zone(self):
        end = (timezone.now() + timedelta(days=3)).replace(tzinfo=None, microsecond=0)
        self.assertEqual(self.post_reserve(self.alice, end.isoformat()).status_code, 201)
        self.assertEqual(self.reservation().end_date, timezone.make_aware(end))

    def test_an_admin_may_set_any_end_date_and_it_is_an_admin_exception(self):
        end = timezone.now() + timedelta(days=60)
        response = self.post_reserve(self.admin, end)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data['admin_exception'])
        self.assertTrue(self.reservation().admin_exception)
        self.assertEqual(self.reservation().end_date, end)

    def test_an_admin_within_the_limits_is_no_exception(self):
        self.assertEqual(self.post_reserve(self.admin, timezone.now() + timedelta(days=7)).status_code, 201)
        self.assertFalse(self.reservation().admin_exception)

    def test_an_admin_still_needs_a_future_end_date(self):
        self.assertEqual(self.post_reserve(self.admin, None).status_code, 400)
        self.assertEqual(self.post_reserve(self.admin, timezone.now() - timedelta(days=1)).status_code, 400)

    def test_the_same_switch_can_be_reserved_again_after_a_release(self):
        self.assertEqual(self.post_reserve(self.alice, timezone.now() + timedelta(days=7)).status_code, 201)
        self.release()
        self.run_cleanup()
        self.assertFalse(PendingCleanup.objects.exists())
        response = self.post_reserve(self.alice, timezone.now() + timedelta(days=7))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.reservation().renewals, 0)


class RenewalTest(LimitsTestCase):
    def setUp(self):
        super().setUp()
        self.end = timezone.now() + timedelta(days=10)
        Reservation.objects.create(switch=self.switch, user=self.alice, end_date=self.end)

    def test_the_holder_renews_twice_then_no_more(self):
        response = self.post_renew(self.alice)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['renewals_left'], 1)
        self.assertEqual(self.reservation().end_date, self.end + RENEWAL)

        response = self.post_renew(self.alice)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['renewals_left'], 0)
        self.assertEqual(response.data['end_date'], self.end + 2 * RENEWAL)

        response = self.post_renew(self.alice)
        self.assertEqual(response.status_code, 400)
        self.assertIn('already been Renewed 2 times', response.data['detail'])
        reservation = self.reservation()
        self.assertEqual(reservation.end_date, self.end + 2 * RENEWAL)
        self.assertEqual(reservation.renewals, 2)

    def test_a_user_the_topology_is_shared_with_may_renew(self):
        TopologyShare.objects.create(owner=self.alice, target=self.bob)
        self.assertEqual(self.post_renew(self.bob).status_code, 200)
        self.assertEqual(self.reservation().renewals, 1)

    def test_nobody_else_may_renew_not_even_an_admin(self):
        for user in (self.bob, self.admin):
            response = self.post_renew(user)
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.reservation().renewals, 0)
        self.assertEqual(self.reservation().end_date, self.end)

    def test_an_admin_exception_is_not_renewed(self):
        Reservation.objects.update(admin_exception=True)
        response = self.post_renew(self.alice)
        self.assertEqual(response.status_code, 400)
        self.assertIn('ask an admin', response.data['detail'])
        self.assertEqual(self.reservation().end_date, self.end)

    def test_an_expired_reservation_is_not_renewed(self):
        Reservation.objects.update(end_date=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.post_renew(self.alice).status_code, 400)
        self.assertEqual(self.reservation().renewals, 0)

    def test_an_unreserved_switch_is_not_renewed(self):
        self.assertEqual(self.post_renew(self.alice, make_switch(2)).status_code, 400)

    def test_the_renewals_left_are_shown_everywhere(self):
        self.post_renew(self.alice)
        listed = self.client_for(self.bob).get('/api/list_reservation/').data[0]
        self.assertEqual((listed['renewals'], listed['renewals_left'], listed['admin_exception']), (1, 1, False))

        status_rows = self.client_for(self.bob).get('/api/lab_status/').data['switches']
        self.assertEqual((status_rows[0]['renewals_left'], status_rows[0]['admin_exception']), (1, False))
        make_switch(2)
        status_rows = self.client_for(self.bob).get('/api/lab_status/').data['switches']
        self.assertEqual((status_rows[1]['renewals_left'], status_rows[1]['admin_exception']), (None, None))

        own = self.client_for(self.alice).get(f'/api/topology/{self.alice.id}/').data['switches'][0]
        self.assertEqual(own['reservation']['renewals_left'], 1)
        self.assertEqual(own['reservation']['end_date'], listed['end_date'])

    def test_an_admin_exception_is_shown_with_no_renewals_left(self):
        Reservation.objects.update(admin_exception=True)
        status_row = self.client_for(self.bob).get('/api/lab_status/').data['switches'][0]
        self.assertEqual((status_row['renewals_left'], status_row['admin_exception']), (0, True))

    def admin_sets_end_date(self, end_date):
        model_admin = ReservationAdmin(Reservation, AdminSite())
        request = RequestFactory().post('/')
        request.user = self.admin
        reservation = self.reservation()
        form_class = model_admin.get_form(request, reservation)
        data = {'switch': self.switch.id, 'user': self.alice.id, 'renewals': 0,
                'end_date_0': end_date.strftime('%Y-%m-%d'), 'end_date_1': end_date.strftime('%H:%M:%S')}
        form = form_class(data, instance=reservation)
        self.assertTrue(form.is_valid(), form.errors)
        model_admin.save_model(request, form.save(commit=False), form, change=True)
        return self.reservation()

    def test_an_admin_setting_an_end_date_beyond_the_limits_makes_an_admin_exception(self):
        self.assertTrue(self.admin_sets_end_date(timezone.now() + timedelta(days=60)).admin_exception)

    def test_an_admin_setting_an_end_date_within_the_limits_makes_none(self):
        self.assertFalse(self.admin_sets_end_date(timezone.now() + timedelta(days=2)).admin_exception)


class CapTest(TestCase):
    """Reservations from before the limits, or from production's older code, get now + 14 days."""

    def setUp(self):
        self.now = timezone.now()
        self.alice = User.objects.create_user('alice', password='pw')
        self.switches = [make_switch(n) for n in range(1, 8)]

    def reserve(self, n, end_date, **fields):
        return Reservation.objects.create(switch=self.switches[n], user=self.alice, end_date=end_date, **fields).pk

    def end_dates(self):
        return dict(Reservation.objects.values_list('pk', 'end_date'))

    def make_old_reservations(self):
        limit = self.now + MAX_LENGTH
        return {
            'no end date': (self.reserve(0, None), limit),
            'too far': (self.reserve(1, self.now + timedelta(days=60)), limit),
            'within': (self.reserve(2, self.now + timedelta(days=3)), self.now + timedelta(days=3)),
            'expired': (self.reserve(3, self.now - timedelta(days=1)), self.now - timedelta(days=1)),
            'renewed': (self.reserve(4, self.now + timedelta(days=20), renewals=1), self.now + timedelta(days=20)),
            'admin exception': (self.reserve(5, self.now + timedelta(days=90), admin_exception=True),
                                self.now + timedelta(days=90)),
        }

    def assert_capped(self, expected, tolerance=timedelta(0)):
        end_dates = self.end_dates()
        for what, (pk, end_date) in expected.items():
            self.assertLessEqual(abs(end_dates[pk] - end_date), tolerance, what)

    def test_cap_unbounded(self):
        expected = self.make_old_reservations()
        self.assertEqual(cap_unbounded(self.now), 2)
        self.assert_capped(expected)
        self.assertEqual(cap_unbounded(self.now), 0)  # it can run again

    def test_the_data_migration_caps_the_same_ones(self):
        expected = self.make_old_reservations()
        cap_migration.cap_existing_reservations(apps, None)
        self.assert_capped(expected, tolerance=timedelta(minutes=1))

    def test_expiry_caps_them_on_every_cycle(self):
        pk = self.reserve(0, None)
        out = StringIO()
        call_command('expire_reservations', '--once', stdout=out)
        self.assertIn('Gave 1 Reservation(s) an end date', out.getvalue())
        self.assertIsNotNone(Reservation.objects.get(pk=pk).end_date)
