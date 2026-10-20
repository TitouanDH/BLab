"""
The admin's actions (#62): Release and Force release, Inspect now, Clean up now and Un-name the
holder of a Quarantine, as the Django admin runs them. Against fake devices.
"""
from unittest import mock

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import Client, RequestFactory, override_settings

from . import fake_devices, links
from .admin import QuarantineAdmin, ReservationAdmin, SwitchAdmin
from .models import PendingCleanup, Port, Quarantine, Reservation, Switch, SwitchEvent
from .quarantine import put_in_quarantine, quarantine_naming
from .release import NotAllowed, release
from .test_release_cleanup import CleanupTestCase, make_switch

BACKBONE = '10.0.0.100'


@override_settings(BLAB_DEVICES='fake')
class AdminActionsTestCase(CleanupTestCase):
    def setUp(self):
        super().setUp()
        self.root = User.objects.create_superuser('root', email='root@example.com', password='pw')
        self.other = make_switch(2)
        self.ports = [Port.objects.create(switch=s, port_switch='1/1/1', backbone=BACKBONE, port_backbone=f'1/1/{n}')
                      for n, s in enumerate([self.switch, self.other], start=1)]

    def request(self):
        request = RequestFactory().post('/')
        request.user = self.root
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def messages(self, request):
        return [str(m) for m in request._messages]

    def act(self, admin_class, model, action, queryset):
        request = self.request()
        getattr(admin_class(model, AdminSite()), action)(request, queryset)
        return self.messages(request)

    def reserve_linked(self):
        reservation = self.reserve()
        self.reserve(self.bob, self.other)
        links.connect(*self.ports)
        return reservation

    def svlans(self):
        return [Port.objects.get(pk=p.pk).svlan for p in self.ports]


class AdminReleaseTest(AdminActionsTestCase):

    def test_release_tears_the_links_down_and_cleans_up_naming_nobody(self):
        self.reserve_linked()
        said = self.act(ReservationAdmin, Reservation, 'admin_release', Reservation.objects.filter(switch=self.switch))
        self.assertFalse(Reservation.objects.filter(switch=self.switch).exists())
        self.assertEqual(self.svlans(), [None, None])
        self.assertIsNone(PendingCleanup.objects.get(switch=self.switch).holder)
        self.assertIn('Released 10.0.0.1 (held by alice)', said[0])
        self.assertEqual(self.switch.events.first().reasons, ['released by root (admin)'])

    def test_a_quarantine_after_an_admin_release_names_nobody(self):
        self.reserve()
        self.act(ReservationAdmin, Reservation, 'admin_release', Reservation.objects.all())
        self.fake.cable(self.switch.mngt_IP, '1/1/5')
        self.run_cleanup()
        self.assertIsNone(Quarantine.objects.get(switch=self.switch).holder)
        self.assertIsNone(quarantine_naming(self.alice))

    def test_a_link_that_cant_be_torn_down_keeps_the_reservation(self):
        self.reserve_linked()
        fake_devices.backbone.fail_on('no ethernet-service', times=10)
        said = self.act(ReservationAdmin, Reservation, 'admin_release', Reservation.objects.filter(switch=self.switch))
        self.assertTrue(Reservation.objects.filter(switch=self.switch).exists())
        self.assertEqual(self.svlans(), [1001, 1001])
        self.assertIn('stays reserved', said[0])

    def test_force_release_forgets_the_link_and_cleans_up(self):
        self.reserve_linked()
        fake_devices.backbone.fail_on('no ethernet-service', times=10)
        said = self.act(SwitchAdmin, Switch, 'force_release', Switch.objects.filter(pk=self.switch.pk))
        self.assertFalse(Reservation.objects.filter(switch=self.switch).exists())
        self.assertEqual(self.svlans(), [None, None])
        self.assertTrue(PendingCleanup.objects.filter(switch=self.switch).exists())
        self.assertIn('forgot 1 Link(s), left as Orphans: SVLAN 1001', said[0])
        self.assertIn('forgotten: SVLAN 1001', self.switch.events.first().warnings[0])

    def test_a_forced_release_refused_by_a_link_made_meanwhile_still_lists_what_it_forgot(self):
        self.reserve_linked()
        fake_devices.backbone.fail_on('no ethernet-service', times=10)
        with mock.patch('api.release.links.has_links', return_value=True), self.assertLogs('api.release', 'ERROR'):
            said = self.act(SwitchAdmin, Switch, 'force_release', Switch.objects.filter(pk=self.switch.pk))
        self.assertTrue(Reservation.objects.filter(switch=self.switch).exists())
        self.assertIn('stays reserved: A Link was made', said[0])
        self.assertNotIn('Force release forgets', said[0])
        self.assertIn('forgot 1 Link(s), left as Orphans: SVLAN 1001', said[0])
        event = self.switch.events.get(kind=SwitchEvent.RELEASE)
        self.assertFalse(event.ok)
        self.assertIn('forgotten: SVLAN 1001', event.warnings[0])

    def test_force_release_from_the_reservation_list(self):
        self.reserve_linked()
        fake_devices.backbone.fail_on('no ethernet-service', times=10)
        said = self.act(ReservationAdmin, Reservation, 'force_release', Reservation.objects.filter(switch=self.switch))
        self.assertFalse(Reservation.objects.filter(switch=self.switch).exists())
        self.assertIsNone(PendingCleanup.objects.get(switch=self.switch).holder)
        self.assertIn('Released 10.0.0.1', said[0])

    def test_a_reserved_switch_isnt_deleted_here(self):
        self.reserve()
        client = Client()
        client.force_login(self.root)
        response = client.post(f'/admin/api/switch/{self.switch.pk}/delete/', {'post': 'yes'})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Reservation.objects.filter(switch=self.switch).exists())

    def test_a_switch_that_isnt_reserved_is_skipped(self):
        said = self.act(SwitchAdmin, Switch, 'admin_release', Switch.objects.filter(pk=self.switch.pk))
        self.assertEqual(said, ['10.0.0.1 is not reserved: nothing to Release'])
        self.assertFalse(PendingCleanup.objects.exists())

    def test_only_an_admin_releases_as_one(self):
        reservation = self.reserve()
        with self.assertRaises(NotAllowed):
            release(reservation, self.bob, by_admin=True)
        with self.assertRaises(NotAllowed):
            release(reservation, self.alice, force=True)  # a forced Release is an admin's

    def test_a_link_changed_meanwhile_isnt_forgotten(self):
        self.reserve_linked()
        fake_devices.backbone.fail_on('no ethernet-service', times=10)
        stale = links.links_for([self.switch])[0]
        Port.objects.filter(pk=self.ports[1].pk).update(svlan=None)  # torn down another way meanwhile
        self.assertFalse(links._forget(stale))
        self.assertEqual(Port.objects.get(pk=self.ports[0].pk).svlan, 1001)

    def test_a_reservation_cant_be_deleted_in_the_admin(self):
        reservation = self.reserve()
        admin = ReservationAdmin(Reservation, AdminSite())
        self.assertFalse(admin.has_delete_permission(self.request(), reservation))
        self.assertNotIn('delete_selected', admin.get_actions(self.request()))


class InspectNowTest(AdminActionsTestCase):

    def inspect_now(self, admin_class=SwitchAdmin, model=Switch, queryset=None):
        return self.act(admin_class, model, 'inspect_now',
                        queryset if queryset is not None else Switch.objects.filter(pk=self.switch.pk))

    def test_a_free_switch_that_isnt_clean_is_quarantined_naming_nobody(self):
        self.fake.cable(self.switch.mngt_IP, '1/1/5')
        said = self.inspect_now()
        self.assertIsNone(Quarantine.objects.get(switch=self.switch, lifted_at__isnull=True).holder)
        self.assertIn('Quarantined naming nobody', said[0])
        self.assertEqual(self.switch.events.order_by('at', 'id').first().user, self.root)

    def test_a_clean_switch_has_its_quarantine_lifted_from_the_quarantine_list(self):
        put_in_quarantine(self.switch, self.alice, ['Unwanted cable: 1/1/5'])
        said = self.inspect_now(QuarantineAdmin, Quarantine, Quarantine.objects.all())
        self.assertIsNotNone(Quarantine.objects.get().lifted_at)
        self.assertIn('Quarantine lifted', said[0])
        self.assertEqual(self.switch.events.get(kind=SwitchEvent.QUARANTINE_LIFTED).user, self.root)

    def test_a_reserved_switch_is_only_recorded(self):
        self.reserve()
        self.fake.unreachable.add(self.switch.mngt_IP)
        said = self.inspect_now()
        self.assertFalse(Quarantine.objects.exists())
        self.assertEqual(self.kinds(), [(SwitchEvent.INSPECTION, False)])
        self.assertIn('reserved, so only recorded', said[0])

    def test_an_out_of_service_switch_is_only_recorded(self):
        Switch.objects.filter(pk=self.switch.pk).update(out_of_service_reason='PSU broken')
        self.fake.cable(self.switch.mngt_IP, '1/1/5')
        self.inspect_now()
        self.assertFalse(Quarantine.objects.exists())

    def test_refused_during_a_cleanup(self):
        PendingCleanup.objects.create(switch=self.switch)
        said = self.inspect_now()
        self.assertIn('is being Cleaned up', said[0])
        self.assertEqual(self.kinds(), [])


class CleanUpNowTest(AdminActionsTestCase):

    def clean_up_now(self):
        return self.act(SwitchAdmin, Switch, 'clean_up_now', Switch.objects.filter(pk=self.switch.pk))

    def test_a_free_switch_is_cleaned_up_naming_nobody(self):
        said = self.clean_up_now()
        self.assertIsNone(PendingCleanup.objects.get(switch=self.switch).holder)
        self.assertIn('will be Cleaned up', said[0])
        self.assertEqual(self.switch.events.get(kind=SwitchEvent.CLEANUP).user, self.root)
        self.fake.cable(self.switch.mngt_IP, '1/1/5')
        self.run_cleanup()
        self.assertIsNone(Quarantine.objects.get(switch=self.switch).holder)

    def test_an_out_of_service_switch_may_be_cleaned_up(self):
        Switch.objects.filter(pk=self.switch.pk).update(out_of_service_reason='PSU broken')
        self.clean_up_now()
        self.assertTrue(PendingCleanup.objects.filter(switch=self.switch).exists())

    def test_refused_on_a_reserved_switch(self):
        self.reserve()
        said = self.clean_up_now()
        self.assertFalse(PendingCleanup.objects.exists())
        self.assertIn('is reserved: Release it', said[0])

    def test_refused_when_already_cleaning_up(self):
        PendingCleanup.objects.create(switch=self.switch, holder=self.alice)
        said = self.clean_up_now()
        self.assertEqual(PendingCleanup.objects.get().holder, self.alice)
        self.assertIn('already being Cleaned up', said[0])


class QuarantineManagementTest(AdminActionsTestCase):

    def test_unname_lets_the_holder_reserve_again_and_keeps_it_open(self):
        put_in_quarantine(self.switch, self.alice, ['Unwanted file: /flash/x.py'])
        said = self.act(QuarantineAdmin, Quarantine, 'unname', Quarantine.objects.all())
        quarantine = Quarantine.objects.get()
        self.assertIsNone(quarantine.holder)
        self.assertIsNone(quarantine.lifted_at)
        self.assertIsNone(quarantine_naming(self.alice))
        self.assertIn('The Quarantine of 10.0.0.1 no longer names alice', said[0])
        self.assertEqual((self.switch.events.first().ok, self.switch.events.first().user), (None, self.root))

    def test_clean_up_now_from_the_quarantine_list(self):
        put_in_quarantine(self.switch, None, ['Missing cable: 1/1/1'])
        said = self.act(QuarantineAdmin, Quarantine, 'clean_up_now', Quarantine.objects.all())
        self.assertIsNone(PendingCleanup.objects.get(switch=self.switch).holder)
        self.assertIn('will be Cleaned up', said[0])

    def test_a_quarantine_isnt_opened_by_hand(self):
        self.assertFalse(QuarantineAdmin(Quarantine, AdminSite()).has_add_permission(self.request()))

    def test_unname_refuses_one_that_names_nobody(self):
        put_in_quarantine(self.switch, None, ['Missing cable: 1/1/1'])
        said = self.act(QuarantineAdmin, Quarantine, 'unname', Quarantine.objects.all())
        self.assertIn('names nobody already', said[0])

    def test_the_list_shows_open_quarantines_unless_asked(self):
        put_in_quarantine(self.switch, None, ['Missing cable: 1/1/1'])
        lifted = put_in_quarantine(self.other, None, ['Missing cable: 1/1/2'])
        self.act(QuarantineAdmin, Quarantine, 'lift', Quarantine.objects.filter(pk=lifted.pk))
        admin = QuarantineAdmin(Quarantine, AdminSite())
        for query, expected in [({}, [self.switch]), ({'state': 'lifted'}, [self.other]),
                                ({'state': 'all'}, [self.switch, self.other])]:
            request = RequestFactory().get('/', query)
            request.user = self.root
            shown = admin.get_changelist_instance(request).get_queryset(request)
            self.assertEqual(sorted(q.switch_id for q in shown), sorted(s.id for s in expected), query)


class AdminPagesTest(AdminActionsTestCase):

    def test_the_lists_show_with_their_actions(self):
        self.reserve()
        put_in_quarantine(self.other, self.alice, ['Unwanted cable: 1/1/5'])
        self.client.force_login(self.root)
        for url, shown in [('/admin/api/switch/', ['Reserved by', 'Clean up now']),
                           ('/admin/api/reservation/', ['Force release']),
                           ('/admin/api/quarantine/', ['Unwanted cable: 1/1/5', 'Un-name the holder'])]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            for text in shown:
                self.assertContains(response, text)
