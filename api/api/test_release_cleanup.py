"""
Every Release Cleans up (#19): the Switch worker reloads the Switch, Inspects it and
Quarantines it in the holder's name if it isn't clean; Re-check, Out of service, and what
users are refused meanwhile. Against fake devices.
"""
from datetime import timedelta
from io import StringIO

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import fake_devices
from .admin import QuarantineAdmin, SwitchAdmin
from .lab_switch import BANNER_PATH
from .models import (PendingCleanup, PermanentCable, Port, Quarantine, Reservation, Switch, SwitchEvent,
                     TopologyShare)
from .release import release
from .switch_worker import LOOK_AGAIN_AFTER, RELOAD_GRACE, RELOAD_TIMEOUT, SwitchWorker

RELOAD = 'reload from working no rollback-timeout'


def in_a_week():
    """An end date a Reservation may have (api.reservations)."""
    return (timezone.now() + timedelta(days=7)).isoformat()


class Clock:
    def __init__(self):
        self.at = timezone.now()

    def __call__(self):
        return self.at

    def advance(self, delta: timedelta):
        self.at += delta


def make_switch(n=1, **fields):
    return Switch.objects.create(mngt_IP=f'10.0.0.{n}', model='OS6860', console='TODO', part_number='pn',
                                 hardware_revision='A', serial_number=f'sn{n}', **fields)


@override_settings(BLAB_DEVICES='fake')
class CleanupTestCase(TestCase):
    def setUp(self):
        self.fake = fake_devices.lab_switches
        self.fake.reset()
        fake_devices.backbone.reset()
        self.alice = User.objects.create_user('alice', password='pw')
        self.bob = User.objects.create_user('bob', password='pw')
        self.switch = make_switch()
        self.clock = Clock()
        self.worker = SwitchWorker(now=self.clock)

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def reserve(self, user=None, switch=None):
        return Reservation.objects.create(switch=switch or self.switch, user=user or self.alice)

    def release(self, user=None):
        reservation = Reservation.objects.get(switch=self.switch)
        result = release(reservation, user or reservation.user)
        self.assertTrue(result.released, result.failures)

    def run_cleanup(self):
        """Lets the worker reload the Switch and Inspect it once it is back."""
        self.worker.work()
        self.clock.advance(RELOAD_GRACE)
        return self.worker.work()

    def kinds(self, switch=None):
        return list((switch or self.switch).events.order_by('at', 'id').values_list('kind', 'ok'))


class SwitchWorkerTest(CleanupTestCase):

    def test_a_clean_release(self):
        self.reserve()
        self.release()
        self.worker.work()
        sent = [cmd for ip, cmd in self.fake.commands]
        self.assertLess(sent.index(f'write {BANNER_PATH}'), sent.index(RELOAD))
        self.assertIn('reserved by : nobody', self.fake.written['10.0.0.1'][BANNER_PATH])
        self.assertEqual(self.fake.reloads, ['10.0.0.1'])
        # Not Inspected before the Switch had time to go down
        self.assertEqual(self.worker.work(), [])
        self.assertFalse(self.switch.events.filter(kind=SwitchEvent.INSPECTION).exists())

        self.clock.advance(RELOAD_GRACE)
        self.assertEqual(self.worker.work(), ['Cleaned up 10.0.0.1: clean'])
        self.assertEqual(self.kinds(), [(SwitchEvent.RELEASE, True), (SwitchEvent.CLEANUP, True),
                                        (SwitchEvent.INSPECTION, True)])
        self.assertFalse(PendingCleanup.objects.exists())
        self.assertFalse(Quarantine.objects.exists())
        self.assertEqual(self.client_for(self.bob).post('/api/reserve/', {'switch': self.switch.id, 'end_date': in_a_week()}).status_code, 201)

    def test_an_unwanted_cable_quarantines_the_switch_in_the_holders_name(self):
        Port.objects.create(switch=self.switch, port_switch='1/1/1', backbone='10.0.0.100', port_backbone='1/1/1')
        PermanentCable.objects.create(switch=self.switch, port='1/1/2')
        self.fake.cable('10.0.0.1', '1/1/1', '1/1/2', '1/1/5')  # only 1/1/5 is unwanted
        self.reserve()
        self.release(self.alice)
        outcome = self.run_cleanup()
        self.assertEqual(outcome, ['Cleaned up 10.0.0.1: not clean, Quarantined naming alice: Unwanted cable: 1/1/5'])
        quarantine = Quarantine.objects.get()
        self.assertEqual((quarantine.switch, quarantine.holder, quarantine.reasons, quarantine.lifted_at),
                         (self.switch, self.alice, ['Unwanted cable: 1/1/5'], None))
        self.assertEqual(self.kinds()[-2:], [(SwitchEvent.INSPECTION, False), (SwitchEvent.QUARANTINE, False)])
        self.assertEqual(self.switch.events.get(kind=SwitchEvent.QUARANTINE).user, self.alice)

    def test_the_holder_is_named_even_when_someone_they_share_with_releases(self):
        self.fake.cable('10.0.0.1', '1/1/5')
        self.reserve(self.alice)
        TopologyShare.objects.create(owner=self.alice, target=self.bob)
        self.release(self.bob)
        self.run_cleanup()
        self.assertEqual(Quarantine.objects.get().holder, self.alice)

    def test_a_vc_found_after_cleanup_quarantines_too(self):
        self.fake.chassis['10.0.0.1'] = 2
        self.reserve()
        self.release()
        self.run_cleanup()
        self.assertEqual(Quarantine.objects.get().reasons, ['in a VC: chassis 1, 2'])

    def test_it_waits_for_the_switch_to_come_back(self):
        self.reserve()
        self.release()
        self.worker.work()
        self.fake.unreachable.add('10.0.0.1')
        self.clock.advance(RELOAD_GRACE)
        self.assertEqual(self.worker.work(), [])
        self.clock.advance(timedelta(minutes=5))
        self.assertEqual(self.worker.work(), [])
        self.assertFalse(self.switch.events.filter(kind=SwitchEvent.INSPECTION).exists())
        self.fake.unreachable.clear()
        self.clock.advance(timedelta(minutes=1))
        self.assertEqual(self.worker.work(), ['Cleaned up 10.0.0.1: clean'])

    def test_a_switch_that_never_comes_back_is_quarantined_once_the_timeout_passes(self):
        self.reserve()
        self.release()
        self.worker.work()
        self.fake.refused.add('10.0.0.1')  # say, init's config lost BLab's login
        self.clock.advance(RELOAD_TIMEOUT - timedelta(seconds=1))
        self.assertEqual(self.worker.work(), [])
        self.clock.advance(LOOK_AGAIN_AFTER)  # the next look, past the timeout
        self.worker.work()
        quarantine = Quarantine.objects.get()
        self.assertEqual(quarantine.reasons, ['login refused'])
        # No user can fix a Switch BLab can't reach: an admin clears it
        self.assertIsNone(quarantine.holder)

    def test_a_cleanup_that_cannot_reload_inspects_the_switch_as_it_is(self):
        self.fake.cable('10.0.0.1', '1/1/5')
        self.fake.fail_on('ls working/')
        self.reserve()
        self.release()
        with self.assertLogs('api.switch_worker', 'ERROR'):
            self.worker.work()
        self.assertEqual(self.fake.reloads, [])
        cleanup = self.switch.events.get(kind=SwitchEvent.CLEANUP)
        self.assertFalse(cleanup.ok)
        self.assertIn('ls working/', cleanup.reasons[0])
        self.worker.work()  # no reload to wait for
        quarantine = Quarantine.objects.get()
        self.assertEqual(quarantine.reasons, ['Unwanted cable: 1/1/5'])
        # The Cleanup never ran: what is left may not be the holder's doing
        self.assertIsNone(quarantine.holder)

    def test_a_failed_banner_does_not_stop_the_cleanup(self):
        self.fake.fail_on('write')
        self.reserve()
        self.release()
        with self.assertLogs('api.models', 'ERROR'):
            self.worker.work()
        self.assertEqual(self.fake.reloads, ['10.0.0.1'])
        self.assertEqual(self.switch.events.get(kind=SwitchEvent.CLEANUP).warnings,
                         ["the banner couldn't be updated"])

    def test_a_switch_reserved_again_before_its_cleanup_is_left_alone(self):
        # Only production's code from before this change can reserve it meanwhile
        self.reserve()
        self.release()
        self.reserve(self.bob)
        with self.assertLogs('api.switch_worker', 'WARNING'):
            self.worker.work()
        self.assertEqual(self.fake.commands, [])
        self.assertFalse(PendingCleanup.objects.exists())
        self.assertFalse(self.switch.events.get(kind=SwitchEvent.CLEANUP).ok)

    def test_a_switch_without_management_ip_is_not_cleaned_up(self):
        self.switch.mngt_IP = 'Not available'
        self.switch.save()
        self.reserve()
        self.release()
        with self.assertLogs('api.switch_worker', 'WARNING'):
            self.worker.work()
        self.assertEqual(self.fake.commands, [])
        self.assertFalse(PendingCleanup.objects.exists())
        self.assertFalse(Quarantine.objects.exists())

    def test_one_broken_cleanup_does_not_stop_the_others(self):
        other = make_switch(2)
        self.reserve()
        self.reserve(switch=other)
        self.release()
        release(Reservation.objects.get(switch=other), self.alice)
        PendingCleanup.objects.filter(switch=self.switch).update(started_at=timezone.now())  # next_inspection_at left empty
        with self.assertLogs('api.switch_worker', 'ERROR'):
            outcomes = self.worker.work()
        self.assertIn('Cleanup of 10.0.0.2: reloading', outcomes)

    def test_a_deleted_holder_leaves_the_quarantine_to_an_admin(self):
        self.fake.cable('10.0.0.1', '1/1/5')
        self.reserve(self.bob)
        self.release()
        self.bob.delete()  # main's code doesn't know PendingCleanup: the holder id dangles
        self.run_cleanup()
        self.assertIsNone(Quarantine.objects.get().holder)

    def test_the_command_runs_one_cycle(self):
        self.reserve()
        self.release()
        out = StringIO()
        call_command('switch_worker', '--once', stdout=out)
        self.assertIn('Cleanup of 10.0.0.1: reloading', out.getvalue())


class RefusedWhileQuarantinedTest(CleanupTestCase):

    def quarantine(self, holder=None, switch=None):
        return Quarantine.objects.create(switch=switch or self.switch, holder=holder, reasons=['Unwanted cable: 1/1/5'])

    def post_reserve(self, user, switch):
        return self.client_for(user).post('/api/reserve/', {'switch': switch.id, 'end_date': in_a_week()}, format='json')

    def test_a_switch_being_cleaned_up_cant_be_reserved(self):
        self.reserve()
        self.release()
        response = self.post_reserve(self.bob, self.switch)
        self.assertEqual(response.status_code, 400)
        self.assertIn('Being Cleaned up', response.data['detail'])

    def test_a_quarantined_switch_cant_be_reserved(self):
        self.quarantine(self.alice)
        response = self.post_reserve(self.bob, self.switch)
        self.assertEqual(response.status_code, 400)
        self.assertIn('In Quarantine: Unwanted cable: 1/1/5', response.data['detail'])

    def test_a_user_named_in_a_quarantine_cant_reserve_another_switch(self):
        other = make_switch(2)
        self.quarantine(self.alice)
        response = self.post_reserve(self.alice, other)
        self.assertEqual(response.status_code, 403)
        self.assertIn('Quarantine of 10.0.0.1 names you', response.data['detail'])
        self.assertFalse(Reservation.objects.exists())
        # Others are not held back
        self.assertEqual(self.post_reserve(self.bob, other).status_code, 201)

    def test_a_lifted_quarantine_holds_nobody_back(self):
        self.quarantine(self.alice)
        Quarantine.objects.update(lifted_at=timezone.now())
        self.assertEqual(self.post_reserve(self.alice, self.switch).status_code, 201)

    def test_an_out_of_service_switch_cant_be_reserved(self):
        self.switch.out_of_service_reason = 'PSU broken'
        self.switch.save()
        response = self.post_reserve(self.alice, self.switch)
        self.assertEqual(response.status_code, 400)
        self.assertIn('Out of service: PSU broken', response.data['detail'])

    def test_the_switch_list_says_why_a_switch_cant_be_reserved(self):
        other = make_switch(2, out_of_service_reason='PSU broken')
        self.quarantine(self.alice)
        third = make_switch(3)
        switches = {s['id']: s for s in self.client_for(self.bob).get('/api/list_switch/').data['switchs']}
        self.assertEqual(switches[self.switch.id]['unavailable']['state'], 'quarantine')
        self.assertEqual(switches[other.id]['unavailable'],
                         {'state': 'out_of_service', 'reason': 'Out of service: PSU broken'})
        self.assertIsNone(switches[third.id]['unavailable'])


class RecheckTest(CleanupTestCase):

    def setUp(self):
        super().setUp()
        self.fake.cable('10.0.0.1', '1/1/5')
        self.reserve()
        self.release()
        self.run_cleanup()
        self.assertTrue(Quarantine.objects.filter(lifted_at__isnull=True).exists())

    def recheck(self, user):
        return self.client_for(user).post('/api/recheck/', {'switch': self.switch.id}, format='json')

    def test_anyone_can_recheck_and_it_lifts_the_quarantine_once_clean(self):
        response = self.recheck(self.bob)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['clean'])
        self.assertIn('Unwanted cable: 1/1/5', response.data['detail'])
        self.assertIsNone(Quarantine.objects.get().lifted_at)

        self.fake.cabled.clear()  # alice unplugged it
        response = self.recheck(self.bob)
        self.assertTrue(response.data['clean'])
        self.assertIsNotNone(Quarantine.objects.get().lifted_at)
        lifted = self.switch.events.get(kind=SwitchEvent.QUARANTINE_LIFTED)
        self.assertEqual(lifted.user, self.bob)
        self.assertEqual(self.switch.events.filter(kind=SwitchEvent.INSPECTION).first().user, self.bob)
        # alice may reserve again
        self.assertEqual(self.client_for(self.alice).post('/api/reserve/', {'switch': self.switch.id, 'end_date': in_a_week()}).status_code, 201)

    def test_recheck_on_a_switch_not_in_quarantine_is_refused(self):
        Quarantine.objects.update(lifted_at=timezone.now())
        self.assertEqual(self.recheck(self.bob).status_code, 400)

    def test_recheck_waits_for_a_cleanup_in_progress(self):
        PendingCleanup.objects.create(switch=self.switch, holder=self.alice)
        response = self.recheck(self.bob)
        self.assertEqual(response.status_code, 400)
        self.assertIn('being Cleaned up', response.data['detail'])

    def test_recheck_leaves_out_of_service_alone(self):
        self.switch.out_of_service_reason = 'PSU broken'
        self.switch.save()
        self.fake.cabled.clear()
        self.assertTrue(self.recheck(self.bob).data['clean'])
        self.switch.refresh_from_db()
        self.assertEqual(self.switch.out_of_service_reason, 'PSU broken')

    def test_the_lab_status_page_shows_the_quarantine(self):
        other = make_switch(2, out_of_service_reason='PSU broken')
        data = {s['id']: s for s in self.client_for(self.bob).get('/api/lab_status/').data['switches']}
        quarantined = data[self.switch.id]
        self.assertEqual(quarantined['quarantine']['holder'], 'alice')
        self.assertEqual(quarantined['quarantine']['reasons'], ['Unwanted cable: 1/1/5'])
        self.assertFalse(quarantined['cleaning_up'])
        self.assertEqual(quarantined['history'][0]['kind'], SwitchEvent.QUARANTINE)
        self.assertEqual(quarantined['history'][0]['user'], 'alice')
        self.assertEqual(data[other.id]['out_of_service']['reason'], 'PSU broken')
        self.assertIsNone(data[other.id]['quarantine'])

    def test_an_admin_lifts_a_quarantine(self):
        admin = User.objects.create_superuser('root', password='pw')
        request = RequestFactory().post('/')
        request.user = admin
        QuarantineAdmin(Quarantine, AdminSite()).lift(request, Quarantine.objects.all())
        self.assertIsNotNone(Quarantine.objects.get().lifted_at)
        self.assertEqual(self.switch.events.first().kind, SwitchEvent.QUARANTINE_LIFTED)


class OutOfServiceTest(CleanupTestCase):

    def save_in_admin(self, reason):
        admin = User.objects.create_superuser(f'root{reason}', password='pw')
        request = RequestFactory().post('/')
        request.user = admin
        self.switch.out_of_service_reason = reason
        SwitchAdmin(Switch, AdminSite()).save_model(request, self.switch, form=None, change=True)
        self.switch.refresh_from_db()

    def test_an_admin_sets_and_lifts_it_and_the_history_says_so(self):
        self.save_in_admin('PSU broken')
        self.assertTrue(self.switch.out_of_service)
        self.assertIsNotNone(self.switch.out_of_service_since)
        self.save_in_admin('  ')
        self.assertFalse(self.switch.out_of_service)
        self.assertIsNone(self.switch.out_of_service_since)
        self.assertEqual(self.kinds(), [(SwitchEvent.OUT_OF_SERVICE, False), (SwitchEvent.BACK_IN_SERVICE, True)])

    def test_cleanup_does_not_quarantine_an_out_of_service_switch(self):
        self.fake.cable('10.0.0.1', '1/1/5')
        self.reserve()
        self.release()
        self.switch.out_of_service_reason = 'PSU broken'
        self.switch.save()
        self.assertEqual(self.run_cleanup(), ['Cleaned up 10.0.0.1: not clean, but Out of service, so left alone'])
        self.assertFalse(Quarantine.objects.exists())


class ReleaseCheckTest(CleanupTestCase):

    def setUp(self):
        super().setUp()
        Port.objects.create(switch=self.switch, port_switch='1/1/1', backbone='10.0.0.100', port_backbone='1/1/1')
        self.reserve()

    def check(self, user):
        return self.client_for(user).get(f'/api/release_check/{self.switch.id}/')

    def test_it_lists_the_cables_that_would_be_unwanted(self):
        self.fake.cable('10.0.0.1', '1/1/1', '1/1/6', '1/1/5')
        response = self.check(self.alice)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['unwanted_cables'], ['1/1/5', '1/1/6'])
        self.assertEqual([cmd for ip, cmd in self.fake.commands], ['show interfaces'])

    def test_nothing_to_unplug(self):
        self.assertEqual(self.check(self.alice).data['unwanted_cables'], [])

    def test_a_switch_it_cant_read(self):
        self.fake.unreachable.add('10.0.0.1')
        with self.assertLogs('api.views', 'WARNING'):
            response = self.check(self.alice)
        self.assertIsNone(response.data['unwanted_cables'])
        self.assertIn("couldn't read", response.data['detail'])

    def test_only_who_may_release_may_ask(self):
        self.assertEqual(self.check(self.bob).status_code, 403)
