"""
The Switch worker moves several Switches on at once (#39, docs/adr/0005): up to
BLAB_SWITCH_WORKER_THREADS steps of Cleanups and Restores run on threads of their own, never
two of the same Switch, and nothing waits queued once every thread is taken.
"""
import threading
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from . import fake_devices, switch_worker
from .models import PendingCleanup, Project, Restore, RestoreSwitch, Switch
from .restore import RestoreWorker
from .switch_worker import Steps, SwitchWorker

WAIT = 5  # seconds a test waits on a thread before it fails rather than hangs


def make_switch(n, **fields):
    return Switch.objects.create(mngt_IP=f'10.0.0.{n}', model='OS6860', console='TODO', part_number='pn',
                                 hardware_revision='A', serial_number=f'sn{n}', **fields)


class Held:
    """Steps that block until released, and remember which Switch each ran for, on which thread."""

    def __init__(self):
        self.go = threading.Event()
        self.started = []
        self.lock = threading.Lock()
        self.all_started = threading.Condition(self.lock)

    def step(self, name):
        def run():
            with self.lock:
                self.started.append((name, threading.current_thread().name))
                self.all_started.notify_all()
            if not self.go.wait(WAIT):
                raise AssertionError(f'{name} was never released')
            return f'{name} done'
        return run

    def wait_started(self, count):
        with self.lock:
            if not self.all_started.wait_for(lambda: len(self.started) >= count, WAIT):
                raise AssertionError(f'only {len(self.started)} of {count} steps started')

    def names(self):
        with self.lock:
            return sorted(name for name, _ in self.started)


class StepsTest(SimpleTestCase):
    def test_steps_run_at_once_up_to_the_threads(self):
        steps = Steps(threads=3)
        together = threading.Barrier(3, timeout=WAIT)  # broken unless all three run at the same time

        def step(n):
            return lambda: f'{n} after {together.wait()}'

        for n in range(3):
            self.assertTrue(steps.run(n, step(n)))
        self.assertEqual(len(steps.done(wait=True)), 3)

    def test_once_every_thread_is_taken_a_switch_waits_for_a_later_cycle(self):
        steps, held = Steps(threads=2), Held()
        self.assertTrue(steps.run(1, held.step('one')))
        self.assertTrue(steps.run(2, held.step('two')))
        self.assertFalse(steps.run(3, held.step('three')))
        held.go.set()
        self.assertEqual(sorted(steps.done(wait=True)), ['one done', 'two done'])
        self.assertTrue(steps.run(3, held.step('three')))
        self.assertEqual(steps.done(wait=True), ['three done'])
        self.assertEqual(held.names(), ['one', 'three', 'two'])

    def test_a_switch_never_has_two_steps_running(self):
        steps, held = Steps(threads=4), Held()
        self.assertTrue(steps.run(1, held.step('first')))
        self.assertFalse(steps.run(1, held.step('second')))  # threads are free, the Switch isn't
        self.assertEqual(steps.busy(), {1})
        held.go.set()
        self.assertEqual(steps.done(wait=True), ['first done'])
        self.assertEqual(steps.busy(), set())
        self.assertTrue(steps.run(1, held.step('second')))
        steps.done(wait=True)
        self.assertEqual(held.names(), ['first', 'second'])

    def test_with_one_thread_each_step_runs_at_once_in_the_callers(self):
        steps, ran_on = Steps(threads=1), []
        self.assertTrue(steps.run(1, lambda: ran_on.append(threading.current_thread()) or 'done'))
        self.assertEqual(ran_on, [threading.current_thread()])
        self.assertEqual(steps.busy(), set())
        self.assertEqual(steps.done(), ['done'])

    def test_a_thread_closes_its_database_connection_after_each_step(self):
        steps = Steps(threads=2)
        with mock.patch.object(switch_worker, 'connection') as connection:
            steps.run(1, lambda: 'done')
            steps.done(wait=True)
        connection.close.assert_called_once_with()

    def test_outcomes_are_handed_over_once(self):
        steps = Steps(threads=2)
        steps.run(1, lambda: 'done')
        steps.run(2, lambda: None)  # nothing happened: nothing to say
        self.assertEqual(steps.done(wait=True), ['done'])
        self.assertEqual(steps.done(), [])


@override_settings(BLAB_DEVICES='fake')
class ParallelCleanupsTest(TestCase):
    """The worker's cycle with real rows; each step itself is replaced, so threads touch no database."""

    def setUp(self):
        fake_devices.lab_switches.reset()
        self.switches = [make_switch(n) for n in range(1, 4)]
        for switch in self.switches:
            PendingCleanup.objects.create(switch=switch)
        self.worker = SwitchWorker(threads=2)
        self.held = Held()
        self.advance = mock.patch.object(SwitchWorker, 'advance', autospec=True,
                                         side_effect=lambda _, pending: self.held.step(pending.switch.mngt_IP)())
        self.advance.start()
        self.addCleanup(self.advance.stop)
        self.addCleanup(self.held.go.set)  # a failing test mustn't leave threads waiting

    def test_cleanups_run_on_as_many_threads_and_none_twice(self):
        self.assertEqual(self.worker.work(), [])
        self.held.wait_started(2)
        self.worker.work()  # the next cycle, both still running: nothing more starts
        self.assertEqual(self.held.names(), ['10.0.0.1', '10.0.0.2'])  # the oldest requests first
        self.assertEqual(len({thread for _, thread in self.held.started}), 2)
        self.held.go.set()
        self.assertEqual(sorted(self.worker.steps.done(wait=True)), ['10.0.0.1 done', '10.0.0.2 done'])
        # Both now reloading, not due for a while: they take no thread from the third
        later = timezone.now() + timedelta(minutes=2)
        PendingCleanup.objects.exclude(switch=self.switches[2]).update(started_at=timezone.now(),
                                                                       next_inspection_at=later)
        self.worker.work()
        self.assertEqual(self.worker.steps.done(wait=True), ['10.0.0.3 done'])
        self.assertEqual(self.held.names(), ['10.0.0.1', '10.0.0.2', '10.0.0.3'])

    def test_a_restore_waits_while_its_switch_has_a_cleanup_step_running(self):
        user = User.objects.create_user('alice', password='pw')
        project = Project.objects.create(name='p', owner=user)
        restore = Restore.objects.create(project=project)
        row = RestoreSwitch.objects.create(restore=restore, switch=self.switches[0], state=RestoreSwitch.WAITING)
        PendingCleanup.objects.exclude(switch=self.switches[0]).delete()
        self.worker.work()
        self.held.wait_started(1)
        with mock.patch.object(RestoreWorker, 'advance') as restore_advance:
            self.worker.restores()
        restore_advance.assert_not_called()  # its Switch is taken
        restore.refresh_from_db()
        self.assertIsNone(restore.finished_at)
        row.refresh_from_db()
        self.assertEqual(row.state, RestoreSwitch.WAITING)

    def test_switch_accounts_and_banners_leave_alone_a_switch_whose_step_runs(self):
        PendingCleanup.objects.exclude(switch=self.switches[0]).delete()
        self.worker.work()
        self.held.wait_started(1)
        PendingCleanup.objects.all().delete()  # say a Restore's step, which isn't a Cleanup
        Switch.objects.update(banner_tried_at=timezone.now() - timedelta(hours=1))
        with mock.patch('api.switch_accounts.sync_due', return_value=[]) as sync_due, \
                mock.patch.object(Switch, 'changeBanner', autospec=True, return_value=True) as banner:
            self.worker.sync_banners()
            self.worker.sync_accounts()
        self.assertEqual(sorted(call.args[0].mngt_IP for call in banner.call_args_list), ['10.0.0.2', '10.0.0.3'])
        self.assertEqual(sync_due.call_args.kwargs['skip'], {self.switches[0].id})


@override_settings(BLAB_DEVICES='fake')
class ParallelRestoresTest(TestCase):
    def setUp(self):
        fake_devices.lab_switches.reset()
        user = User.objects.create_user('alice', password='pw')
        self.restore = Restore.objects.create(project=Project.objects.create(name='p', owner=user))
        self.rows = [RestoreSwitch.objects.create(restore=self.restore, switch=make_switch(n),
                                                  state=RestoreSwitch.WAITING) for n in range(1, 4)]
        self.worker = SwitchWorker(threads=3)
        self.held = Held()
        patch = mock.patch.object(RestoreWorker, 'advance', autospec=True,
                                  side_effect=lambda _, row: self.held.step(row.switch.mngt_IP)())
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.held.go.set)

    def test_a_restores_switches_load_at_once_and_its_links_wait_for_them_all(self):
        self.worker.restores()
        self.held.wait_started(3)
        self.assertEqual(self.held.names(), ['10.0.0.1', '10.0.0.2', '10.0.0.3'])
        # Say every Switch is settled, while their steps still run: the Links wait for the steps
        RestoreSwitch.objects.update(state=RestoreSwitch.DONE)
        self.worker.restores()
        self.restore.refresh_from_db()
        self.assertIsNone(self.restore.finished_at)
        self.held.go.set()
        self.worker.steps.done(wait=True)
        self.worker.restores()
        self.restore.refresh_from_db()
        self.assertIsNotNone(self.restore.finished_at)
