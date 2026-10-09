"""
The nightly Sweep (#22): at 02:00 Paris, every Switch that isn't reserved is Cleaned up if
it changed, Inspected, and Quarantined or lifted; a report only when something is wrong.
Against fake devices.
"""
from datetime import datetime, timedelta
from io import StringIO
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from . import fake_devices, sweep
from .lab_switch import LOGIN_LOG_COMMAND, CommandResult, LabSwitch, LabSwitchError, Session
from .models import PendingCleanup, Quarantine, Reservation, Switch, Sweep, SwitchAccount, SwitchEvent
from .quarantine import put_in_quarantine
from .switch_worker import RELOAD_GRACE, SwitchWorker
from .test_release_cleanup import Clock, make_switch

PARIS = ZoneInfo('Europe/Paris')
BLAB = '10.69.144.180'  # settings.BLAB_SOURCE_ADDRESSES by default


def paris(*args) -> datetime:
    return datetime(*args, tzinfo=PARIS)


class WhenTests(TestCase):
    def test_due_from_two_in_the_morning_paris_time_until_six(self):
        self.assertFalse(sweep.due(paris(2026, 10, 9, 1, 59)))
        self.assertTrue(sweep.due(paris(2026, 10, 9, 2, 0)))
        self.assertTrue(sweep.due(paris(2026, 10, 9, 5, 59)))
        self.assertFalse(sweep.due(paris(2026, 10, 9, 6, 0)))
        self.assertFalse(sweep.due(paris(2026, 10, 9, 15, 0)))  # a worker started in the day waits for the night

    def test_once_a_night(self):
        Sweep.objects.create(started_at=paris(2026, 10, 9, 2, 1), finished_at=paris(2026, 10, 9, 2, 30))
        self.assertFalse(sweep.due(paris(2026, 10, 9, 3, 0)))
        self.assertTrue(sweep.due(paris(2026, 10, 10, 2, 0)))

    def test_paris_time_through_daylight_saving(self):
        self.assertEqual(sweep.last_start_time(paris(2026, 7, 1, 12)).utcoffset(), timedelta(hours=2))
        self.assertTrue(sweep.due(datetime(2026, 7, 1, 0, 0, tzinfo=ZoneInfo('UTC'))))   # 02:00 in summer
        self.assertTrue(sweep.due(datetime(2026, 12, 1, 1, 0, tzinfo=ZoneInfo('UTC'))))  # 02:00 in winter
        self.assertFalse(sweep.due(datetime(2026, 12, 1, 0, 30, tzinfo=ZoneInfo('UTC'))))


class LoginLog(Session):
    def __init__(self, result):
        self.result = result

    def run(self, cmd):
        assert cmd == LOGIN_LOG_COMMAND
        return self.result


def logins(result):
    from contextlib import contextmanager

    @contextmanager
    def connect(ip):
        yield LoginLog(result)
    return LabSwitch('10.0.0.1', connect).logins_since_boot()


class LoginLogTests(TestCase):
    def test_reads_the_logins_as_aos_logs_them(self):
        log = ("2008 Jan 12 01:31:32.825 OS6900-X48C6 swlogd SES AAA INFO: Login by admin from 10.69.144.180 "
               "through SSH Success [in LoginAaaSession::handleLoginResult()]\n"
               "2008 Jan 12 05:28:27.469 OS6900-X48C6 swlogd SES AAA INFO: Login by alice from 10.63.33.110 "
               "through SSH Success [in LoginAaaSession::handleLoginResult()]\n")
        found = logins(CommandResult(0, log))
        self.assertEqual([(l.user, l.address, l.through) for l in found],
                         [('admin', BLAB, 'SSH'), ('alice', '10.63.33.110', 'SSH')])

    def test_no_login_is_grep_finding_nothing(self):
        self.assertEqual(logins(CommandResult(1, '')), [])

    def test_an_unreadable_log_raises(self):
        with self.assertRaises(LabSwitchError):
            logins(CommandResult(2, '', 'grep: /flash/swlog_chassis1: No such file or directory'))

    def test_a_refusal_with_exit_status_0_raises(self):
        with self.assertRaisesRegex(LabSwitchError, 'not allowed on slave'):
            logins(CommandResult(0, 'ERROR: Command not allowed on slave\n'))

    def test_failed_logins_are_not_logins(self):
        log = ("2020 Jan 12 01:53:23.217 X swlogd SES AAA INFO: Login by admin from 10.69.6.78 through SSH "
               "Failed [in LoginAaaSession::handleLoginResult()]\n")
        self.assertEqual(logins(CommandResult(0, log)), [])


@override_settings(BLAB_DEVICES='fake', BLAB_SWEEP_MODE='enforce')
class SweepTests(TestCase):
    def setUp(self):
        self.fake = fake_devices.lab_switches
        self.fake.reset()
        self.alice = User.objects.create_user('alice', password='pw')
        self.clock = Clock()
        self.clock.at = paris(2026, 10, 9, 2, 0)
        self.worker = SwitchWorker(now=self.clock)
        self.sweeper = sweep.Sweeper(now=self.clock)

    def run_sweep(self):
        """Runs worker cycles, as the Switch worker does, until the Sweep is finished."""
        outcomes = []
        for _ in range(50):
            outcomes += self.sweeper.step() + self.worker.work()
            if Sweep.objects.filter(finished_at__isnull=False).exists():
                return outcomes
            self.clock.advance(RELOAD_GRACE)
        self.fail(f"the Sweep never finished: {outcomes}")

    def change_config(self, switch):
        self.fake.running_config[switch.mngt_IP] = fake_devices.INIT_CONFIG + 'vlan 10 admin-state enable\n'

    def last_sweep(self) -> Sweep:
        return Sweep.objects.get()

    def test_a_changed_switch_is_cleaned_up_and_a_clean_one_left_alone(self):
        changed, clean = make_switch(1), make_switch(2)
        self.change_config(changed)
        outcomes = self.run_sweep()
        self.assertEqual(self.fake.reloads, [changed.mngt_IP], outcomes)
        self.assertNotIn(changed.mngt_IP, self.fake.running_config)  # back to init
        kinds = list(changed.events.order_by('at', 'id').values_list('kind', 'ok'))
        self.assertEqual(kinds, [(SwitchEvent.INSPECTION, True), (SwitchEvent.CLEANUP, True),
                                 (SwitchEvent.INSPECTION, True)])
        self.assertIn('config differs from init', changed.events.order_by('at', 'id').first().warnings[0])
        self.assertEqual(list(clean.events.values_list('kind', 'ok')), [(SwitchEvent.INSPECTION, True)])
        self.assertFalse(Quarantine.objects.exists())
        done = self.last_sweep()
        self.assertEqual(done.problems, [])
        self.assertEqual(sorted(done.swept), [changed.id, clean.id])
        self.assertEqual(done.cleanups_asked, [changed.id])
        self.assertIsNone(sweep.last_report())

    def test_someone_other_than_blab_logging_in_is_a_change(self):
        visited, blab_only = make_switch(1), make_switch(2)
        self.fake.log_in(visited.mngt_IP, 'admin', '10.63.33.110')
        self.fake.log_in(blab_only.mngt_IP, 'admin', BLAB)
        self.run_sweep()
        self.assertEqual(self.fake.reloads, [visited.mngt_IP])
        why = visited.events.order_by('at', 'id').first().warnings
        self.assertEqual(why, ['logged in since its last reload by someone other than BLab: admin from 10.63.33.110'])

    def test_a_switch_account_logging_in_is_someone_other_than_blab(self):
        switch = make_switch(1)
        self.fake.log_in(switch.mngt_IP, 'alice', BLAB)
        self.run_sweep()
        self.assertEqual(self.fake.reloads, [switch.mngt_IP])

    def test_an_unreadable_login_log_is_judged_on_the_config(self):
        switch = make_switch(1)
        self.fake.fail_on(LOGIN_LOG_COMMAND, exit_status=2)
        self.run_sweep()
        self.assertEqual(self.fake.reloads, [])
        self.assertEqual(self.last_sweep().problems, [])

    def test_a_quarantined_switch_that_now_passes_is_lifted(self):
        switch = make_switch(1)
        put_in_quarantine(switch, self.alice, ['Unwanted cable: 1/1/5'])  # unplugged since
        self.run_sweep()
        self.assertIsNone(switch.open_quarantine())
        self.assertEqual(self.fake.reloads, [])
        lifted = switch.events.get(kind=SwitchEvent.QUARANTINE_LIFTED)
        self.assertEqual((lifted.reasons, lifted.user), (['the Sweep found it clean'], None))
        self.assertIn('lifted 1 Quarantine(s)', self.last_sweep().summary)

    def test_a_quarantined_switch_that_changed_is_cleaned_up_then_lifted(self):
        switch = make_switch(1)
        put_in_quarantine(switch, None, ['in a VC: chassis 1, 2'])
        self.change_config(switch)
        self.run_sweep()
        self.assertEqual(self.fake.reloads, [switch.mngt_IP])
        self.assertIsNone(switch.open_quarantine())
        self.assertEqual(switch.events.get(kind=SwitchEvent.QUARANTINE_LIFTED).reasons,
                         ['the Inspection after its Cleanup found it clean'])

    def test_one_that_fails_is_quarantined_naming_nobody_and_reported(self):
        switch = make_switch(1)
        self.fake.cable(switch.mngt_IP, '1/1/5')
        self.run_sweep()
        quarantine = switch.open_quarantine()
        self.assertEqual((quarantine.holder, quarantine.reasons), (None, ['Unwanted cable: 1/1/5']))
        self.assertEqual(self.fake.reloads, [])
        report = sweep.last_report()
        self.assertEqual(report.problems, ['10.0.0.1 is in Quarantine (for an admin): Unwanted cable: 1/1/5'])

    def test_a_changed_one_still_failing_after_its_cleanup_is_quarantined_naming_nobody(self):
        switch = make_switch(1)
        self.change_config(switch)
        self.fake.cable(switch.mngt_IP, '1/1/5')
        self.run_sweep()
        self.assertEqual(self.fake.reloads, [switch.mngt_IP])
        self.assertIsNone(switch.open_quarantine().holder)
        self.assertEqual(Quarantine.objects.count(), 1)

    def test_a_quarantine_still_failing_stays_as_it_is(self):
        switch = make_switch(1)
        put_in_quarantine(switch, self.alice, ['Unwanted cable: 1/1/5'])
        self.fake.cable(switch.mngt_IP, '1/1/5')
        self.run_sweep()
        self.assertEqual(Quarantine.objects.get().holder, self.alice)
        self.assertEqual(sweep.last_report().problems,
                         ['10.0.0.1 is in Quarantine (names alice): Unwanted cable: 1/1/5'])

    def test_an_unreachable_switch_is_quarantined_without_a_cleanup(self):
        switch = make_switch(1)
        self.fake.unreachable.add(switch.mngt_IP)
        self.run_sweep()
        self.assertEqual(self.fake.reloads, [])
        self.assertIsNone(switch.open_quarantine().holder)

    def test_leaves_reserved_out_of_service_and_unreachable_by_design_switches_alone(self):
        reserved = make_switch(1)
        Reservation.objects.create(switch=reserved, user=self.alice)
        broken = make_switch(2, out_of_service_reason='PSU dead')
        no_ip = Switch.objects.create(mngt_IP='Not available', model='m', console='c', part_number='p',
                                      hardware_revision='h', serial_number='s')
        cleaning = make_switch(3)
        PendingCleanup.objects.create(switch=cleaning, holder=self.alice)
        for switch in (reserved, broken, cleaning):
            self.change_config(switch)
        self.sweeper.step()
        sweep_ = self.last_sweep()
        self.assertEqual(sweep_.swept, [])
        self.assertFalse(SwitchEvent.objects.filter(switch__in=[reserved, broken, no_ip]).exists())

    def test_a_switch_account_left_behind_is_removed_before_the_inspection(self):
        switch = make_switch(1)
        SwitchAccount.objects.create(switch=switch, user=self.alice, name='alice', password='Pw.12345abc', created=True)
        self.fake.users[switch.mngt_IP] = {'alice': 'Pw.12345abc'}
        self.run_sweep()
        self.assertFalse(SwitchAccount.objects.exists())
        self.assertFalse(Quarantine.objects.exists())

    def test_one_switch_failing_does_not_stop_the_sweep(self):
        first, second = make_switch(1), make_switch(2)
        real_sync = sweep.switch_accounts.sync

        def sync(switch, now=None):
            if switch == first:
                raise RuntimeError('boom')
            return real_sync(switch, now)
        sweep.switch_accounts.sync = sync
        try:
            self.run_sweep()
        finally:
            sweep.switch_accounts.sync = real_sync
        self.assertEqual(sweep.last_report().problems, ['Sweep could not look at 10.0.0.1: boom'])
        self.assertTrue(second.events.filter(kind=SwitchEvent.INSPECTION).exists())

    def test_a_few_switches_per_cycle_and_out_of_time_after_four_hours(self):
        for n in range(1, 6):
            make_switch(n)
        put_in_quarantine(Switch.objects.get(mngt_IP='10.0.0.5'), None, ['Unwanted cable: 1/1/5'])
        self.sweeper.step()
        self.assertEqual(len(self.last_sweep().swept), sweep.SWITCHES_PER_STEP)
        self.clock.advance(sweep.SWEEP_WINDOW)
        self.sweeper.step()
        done = self.last_sweep()
        self.assertIsNotNone(done.finished_at)
        # Not looked at: not counted as swept, and its Quarantine isn't this Sweep's finding
        self.assertEqual(done.problems, ['Not swept, out of time: 10.0.0.4, 10.0.0.5'])
        self.assertIn('Swept 3 Switch(es)', done.summary)

    def test_counts_the_quarantines_it_lifted_and_only_those(self):
        self.clock = Clock()  # real time: the lifts are stamped with it
        self.worker, self.sweeper = SwitchWorker(now=self.clock), sweep.Sweeper(now=self.clock)
        direct, cleaned, rechecked = make_switch(1), make_switch(2), make_switch(3)
        for switch in (direct, cleaned):
            put_in_quarantine(switch, None, ['Unwanted cable: 1/1/5'])
        self.change_config(cleaned)
        lifted_by_hand = put_in_quarantine(rechecked, None, ['Unwanted cable: 1/1/5'])
        lifted_by_hand.lifted_at = self.clock()
        lifted_by_hand.save()
        sweep.start(self.clock())
        self.run_sweep()
        self.assertFalse(Quarantine.objects.filter(lifted_at__isnull=True).exists())
        self.assertIn('lifted 2 Quarantine(s)', self.last_sweep().summary)

    def test_one_sweep_under_way_at_a_time(self):
        first = sweep.start()
        self.assertEqual(sweep.start(), first)
        self.assertEqual(self.sweeper.step()[-1][:14], 'Sweep finished')  # carries that one on
        self.assertEqual(Sweep.objects.count(), 1)

    def test_a_switch_reserved_while_it_was_read_is_left_alone(self):
        switch = make_switch(1)
        self.change_config(switch)
        real_inspect = sweep.inspect

        def inspect(s):
            Reservation.objects.create(switch=s, user=self.alice)
            return real_inspect(s)
        sweep.inspect = inspect
        try:
            self.run_sweep()
        finally:
            sweep.inspect = real_inspect
        self.assertFalse(PendingCleanup.objects.exists())
        self.assertFalse(SwitchEvent.objects.exists())

    def test_the_lab_status_page_shows_the_report_only_when_something_is_wrong(self):
        client = APIClient()
        client.force_authenticate(self.alice)
        make_switch(1)
        self.run_sweep()
        self.assertIsNone(client.get('/api/lab_status/').data['sweep'])
        self.fake.cable('10.0.0.1', '1/1/5')
        self.clock.at = paris(2026, 10, 10, 2, 0)
        Sweep.objects.all().delete()
        self.run_sweep()
        report = client.get('/api/lab_status/').data['sweep']
        self.assertEqual(report['problems'], ['10.0.0.1 is in Quarantine (for an admin): Unwanted cable: 1/1/5'])
        self.assertIn('Swept 1 Switch(es)', report['summary'])

    def test_the_sweep_command_starts_one_now_for_the_worker(self):
        call_command('sweep', stdout=StringIO())
        call_command('sweep', stdout=StringIO())  # one under way: not a second
        self.assertEqual(Sweep.objects.filter(finished_at__isnull=True).count(), 1)

    def test_the_switch_worker_runs_the_sweep_when_it_is_due(self):
        make_switch(1)
        out = StringIO()
        with self.settings(BLAB_DEVICES='fake'):
            from .management.commands import switch_worker
            command = switch_worker.Command(stdout=out)
            command.cycle(SwitchWorker(now=lambda: paris(2026, 10, 9, 2, 5)))
        self.assertIn('Sweep started', out.getvalue())
        self.assertIsNotNone(self.last_sweep().finished_at)


@override_settings(BLAB_DEVICES='fake', BLAB_SWEEP_MODE='report')
class ReportOnlyTests(TestCase):
    """The default: the Sweep reads and reports what it would do, and changes nothing."""
    def setUp(self):
        self.fake = fake_devices.lab_switches
        self.fake.reset()
        self.clock = Clock()
        self.clock.at = paris(2026, 10, 9, 2, 0)
        self.sweeper = sweep.Sweeper(now=self.clock)

    def test_reports_what_it_would_do_and_does_none_of_it(self):
        changed, visited, cabled, clean_now, clean = (make_switch(n) for n in range(1, 6))
        self.fake.running_config[changed.mngt_IP] = fake_devices.INIT_CONFIG + 'vlan 10 admin-state enable\n'
        self.fake.log_in(visited.mngt_IP, 'alice', '10.63.33.110')
        self.fake.cable(cabled.mngt_IP, '1/1/7')
        put_in_quarantine(clean_now, None, ['Unwanted cable: 1/1/5'])
        for _ in range(3):
            self.sweeper.step()
        done = Sweep.objects.get()
        self.assertIsNotNone(done.finished_at)
        self.assertEqual(self.fake.reloads, [])
        self.assertFalse(PendingCleanup.objects.exists())
        self.assertEqual(Quarantine.objects.filter(lifted_at__isnull=True).count(), 1)  # still clean_now's
        self.assertEqual(done.problems, [
            'Would Clean up 10.0.0.1: config differs from init: 1 line(s) added, 0 removed: + vlan 10 admin-state enable',
            'Would Clean up 10.0.0.2: logged in since its last reload by someone other than BLab: alice from 10.63.33.110',
            'Would Quarantine 10.0.0.3, naming nobody: Unwanted cable: 1/1/7',
            'Would lift the Quarantine of 10.0.0.4: it is clean',
            '10.0.0.4 is in Quarantine (for an admin): Unwanted cable: 1/1/5',
        ])
        self.assertTrue(done.summary.startswith('Report only (BLAB_SWEEP_MODE=report)'))
        self.assertEqual(list(clean.events.values_list('kind', 'ok')), [(SwitchEvent.INSPECTION, True)])


class DefaultModeTests(TestCase):
    def test_report_is_the_default(self):
        import os
        from django.conf import settings
        self.assertNotIn('BLAB_SWEEP_MODE', os.environ)
        self.assertEqual(settings.BLAB_SWEEP_MODE, 'report')
        self.assertFalse(sweep.enforcing())
