"""
Config of a Project's Switches (#44): the live config (show configuration snapshot, read as
admin) and each Snapshot's, for the members of the Switch's Project and for admins, read-only;
live reads rate-limited per Switch; diffs against the Golden init and between any two. Against
fake devices.
"""
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.test import SimpleTestCase, override_settings

from . import switch_config
from .clean_state import golden_init_config
from .lab_switch import RUNNING_CONFIG_COMMAND as LIVE_COMMAND, WORKING_CONFIG_PATH
from .models import NO_MANAGEMENT_IP, LiveConfig, Reservation
from .release import release
from .switch_config import LIVE_READ_INTERVAL, diff
from .test_projects import OFF
from .test_snapshots import SnapshotTestCase, config
from .test_switch_password import ON

def running(model='OS6860', extra='vlan 10 admin-state enable\n'):
    """A running config: the Golden init of the model, as AOS orders it (another order), with extra lines."""
    lines = golden_init_config(model).strip().splitlines()
    return '! System Service:\n' + '\n'.join(reversed(lines)) + '\n! VLAN:\n' + extra


class SwitchConfigTestCase(SnapshotTestCase):
    """Alice's Project holds the three Switches, bob is a member, carol is not; dora is an admin."""

    def setUp(self):
        super().setUp()
        self.dora = User.objects.create_user('dora', email='dora@example.com', password='pw', is_staff=True)
        with self.settings(**ON):
            from . import switch_password
            from .test_switch_password import GOOD
            switch_password.choose(self.dora, GOOD)
        self.fake.running_config[self.switch.mngt_IP] = running()
        self.fake.commands.clear()

    def view(self, user=None, switch=None, **query):
        switch = switch or self.switch
        return self.client_for(user or self.alice).get(f'/api/switches/{switch.id}/config/', query)

    def viewed(self, user=None, switch=None, **query):
        response = self.view(user, switch, **query)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def live_reads(self):
        return [cmd for ip, cmd in self.fake.commands if cmd == LIVE_COMMAND]

    def let_window_pass(self, switch=None):
        LiveConfig.objects.filter(switch=switch or self.switch).update(
            started_at=switch_config.timezone.now() - LIVE_READ_INTERVAL - timedelta(seconds=1))


@override_settings(**ON)
class LiveConfigTest(SwitchConfigTestCase):

    def test_a_member_sees_the_live_config_by_default(self):
        data = self.viewed(self.bob)
        self.assertEqual(data['switch']['id'], self.switch.id)
        self.assertEqual(data['switch']['project'], {'id': self.project.id, 'name': self.project.name})
        self.assertIsNone(self.viewed(self.dora)['switch']['project'])  # an admin outside it: no link to it
        self.assertEqual(data['shown']['key'], 'live')
        self.assertEqual(data['shown']['config'], running())
        self.assertIsNone(data['shown']['error'])
        self.assertIsNotNone(data['shown']['read_at'])
        self.assertIsNone(data['against'])
        self.assertEqual([s['key'] for s in data['sources']], ['live', 'golden'])

    def test_only_show_commands_reach_the_switch(self):
        self.viewed()
        self.viewed(show='live', against='golden')
        self.assertTrue(self.fake.commands)
        self.assertTrue(all(cmd.startswith('show ') for ip, cmd in self.fake.commands), self.fake.commands)
        self.assertEqual(self.fake.reloads, [])

    def test_live_reads_are_rate_limited_per_switch(self):
        first = self.viewed()['shown']
        self.fake.running_config[self.switch.mngt_IP] = running(extra='vlan 20 admin-state enable\n')
        again = self.viewed(self.bob)['shown']  # another member, within the window: the same read
        self.assertEqual(len(self.live_reads()), 1)
        self.assertEqual((again['config'], again['read_at']), (first['config'], first['read_at']))
        self.assertEqual(again['next_read_at'], first['next_read_at'])

        self.let_window_pass()
        fresh = self.viewed()['shown']
        self.assertEqual(len(self.live_reads()), 2)
        self.assertIn('vlan 20', fresh['config'])

    def test_each_switch_has_its_own_window(self):
        self.viewed()
        self.viewed(switch=self.switches[1])
        self.assertEqual(len(self.live_reads()), 2)

    def test_a_switch_that_cannot_be_read_says_why_and_counts_against_the_window(self):
        self.fake.unreachable.add(self.switch.mngt_IP)
        shown = self.viewed()['shown']
        self.assertIsNone(shown['config'])
        self.assertTrue(shown['error'])
        self.viewed()
        self.assertEqual(len(LiveConfig.objects.all()), 1)
        self.assertEqual(self.viewed()['shown']['error'], shown['error'])  # not read again within the window

    def test_an_unexpected_failure_is_recorded_too(self):
        with mock.patch('api.lab_switch.LabSwitch.running_config', side_effect=RuntimeError('boom')),                 self.assertLogs('api.switch_config', 'ERROR'):
            shown = self.viewed()['shown']
        self.assertEqual((shown['config'], shown['reading']), (None, False))
        self.assertIn('boom', shown['error'])

    def test_a_read_under_way_is_not_started_twice(self):
        LiveConfig.objects.create(switch=self.switch, started_at=switch_config.timezone.now())
        shown = self.viewed()['shown']
        self.assertEqual((shown['config'], shown['error'], shown['reading']), (None, None, True))
        self.assertEqual(self.live_reads(), [])

    def test_someone_outside_the_project_may_not_see_it(self):
        self.assertEqual(self.view(self.carol).status_code, 403)
        self.assertEqual(self.live_reads(), [])

    def test_an_admin_sees_any_switch(self):
        data = self.viewed(self.dora)
        self.assertEqual(data['shown']['key'], 'live')
        release(Reservation.objects.get(switch=self.switch), self.alice)
        self.let_window_pass()
        self.assertEqual(self.viewed(self.dora)['shown']['key'], 'live')

    def test_a_switch_without_a_management_ip_has_no_live_config(self):
        self.switch.mngt_IP = NO_MANAGEMENT_IP
        self.switch.save()
        data = self.viewed(self.dora)
        self.assertNotIn('live', [s['key'] for s in data['sources']])

    def test_an_unknown_source_is_not_found(self):
        self.assertEqual(self.view(show='snapshot-999').status_code, 404)
        self.assertEqual(self.view(show='live', against='nonsense').status_code, 404)

    def test_an_unknown_switch_is_not_found(self):
        self.assertEqual(self.client_for(self.alice).get('/api/switches/9999/config/').status_code, 404)


@override_settings(**ON)
class GoldenInitDiffTest(SwitchConfigTestCase):

    def test_the_golden_init_of_the_switch_model(self):
        shown = self.viewed(show='golden')['shown']
        self.assertEqual(shown['config'], golden_init_config('OS6860'))
        self.assertEqual(self.live_reads(), [])

    def test_live_against_the_golden_init(self):
        self.fake.running_config[self.switch.mngt_IP] = running().replace('command-log enable\n', '')
        data = self.viewed(show='live', against='golden')
        self.assertEqual(data['against']['key'], 'golden')
        changes = [(row['op'], row['line']) for row in data['diff']['rows'] if row['op'] != ' ']
        # Lines AOS only put in another order are no difference
        self.assertEqual(sorted(changes), [('+', 'vlan 10 admin-state enable'), ('-', 'command-log enable')])
        self.assertEqual((data['diff']['added'], data['diff']['removed']), (1, 1))

    def test_a_switch_without_a_model_has_no_golden_init(self):
        self.switch.model = ''
        self.switch.save()
        data = self.viewed()
        self.assertNotIn('golden', [s['key'] for s in data['sources']])
        self.assertEqual(self.view(show='golden').status_code, 404)


@override_settings(**ON)
class SnapshotConfigTest(SwitchConfigTestCase):

    def test_a_snapshot_config_for_the_members(self):
        snapshot = self.saved('before the test')
        data = self.viewed(self.bob)
        keys = [s['key'] for s in data['sources']]
        self.assertEqual(keys, ['live', 'golden', f'snapshot-{snapshot.id}'])
        entry = data['sources'][2]
        self.assertEqual((entry['note'], entry['reason'], entry['project']['id']),
                         ('before the test', 'save', self.project.id))
        shown = self.viewed(self.bob, show=f'snapshot-{snapshot.id}')['shown']
        self.assertEqual(shown['config'], config('mine-1'))
        self.assertIsNone(shown['vcsetup'])

    def test_snapshots_are_newest_first(self):
        older, newer = self.saved('one'), self.saved('two')
        keys = [s['key'] for s in self.viewed()['sources']]
        self.assertEqual(keys[2:], [f'snapshot-{newer.id}', f'snapshot-{older.id}'])

    def test_between_two_snapshots(self):
        older = self.saved('one')
        self.fake.written[self.switch.mngt_IP][WORKING_CONFIG_PATH] = config('mine-1') + 'vlan 20 admin-state enable\n'
        newer = self.saved('two')
        data = self.viewed(show=f'snapshot-{newer.id}', against=f'snapshot-{older.id}')
        changes = [(row['op'], row['line']) for row in data['diff']['rows'] if row['op'] != ' ']
        self.assertEqual(changes, [('+', 'vlan 20 admin-state enable')])
        self.assertEqual(self.live_reads(), [])

    def test_others_do_not_see_the_snapshots(self):
        snapshot = self.saved()
        self.assertEqual(self.view(self.carol, show=f'snapshot-{snapshot.id}').status_code, 403)

    def test_an_admin_sees_every_snapshot(self):
        snapshot = self.saved()
        self.assertIn(f'snapshot-{snapshot.id}', [s['key'] for s in self.viewed(self.dora)['sources']])

    def test_once_released_the_members_still_see_its_snapshots_but_not_live(self):
        snapshot = self.saved()
        release(Reservation.objects.get(switch=self.switch), self.alice)
        data = self.viewed(self.bob)
        keys = [s['key'] for s in data['sources']]
        self.assertNotIn('live', keys)
        self.assertIn(f'snapshot-{snapshot.id}', keys)
        self.assertEqual(data['shown']['key'], keys[keys.index('golden') + 1])  # the newest Snapshot
        self.assertEqual(self.view(self.bob, show='live').status_code, 404)

    def test_a_switch_the_snapshot_could_not_read(self):
        self.fake.unreachable.add(self.switch.mngt_IP)
        snapshot = self.saved()
        self.fake.unreachable.discard(self.switch.mngt_IP)
        shown = self.viewed(show=f'snapshot-{snapshot.id}')['shown']
        self.assertIsNone(shown['config'])
        self.assertTrue(shown['error'])


@override_settings(**OFF)
class WithProjectsOffTest(SwitchConfigTestCase):

    def test_the_holder_sees_the_live_config_and_no_snapshot(self):
        snapshot = self.saved_with_projects_on()
        data = self.viewed()
        self.assertEqual([s['key'] for s in data['sources']], ['live', 'golden'])
        self.assertEqual(self.view(show=f'snapshot-{snapshot.id}').status_code, 404)

    def saved_with_projects_on(self):
        with self.settings(**ON):
            return self.saved()


class DiffTest(SimpleTestCase):

    def ops(self, old, new):
        return [(row['op'], row['line']) for row in diff(old, new)['rows']]

    def test_added_and_removed_lines_in_place(self):
        self.assertEqual(self.ops('a\nb\nc\n', 'a\nc\nd\n'), [(' ', 'a'), ('-', 'b'), (' ', 'c'), ('+', 'd')])

    def test_comments_blank_lines_and_trailing_spaces_do_not_count(self):
        self.assertEqual(self.ops('! one\na\n\nb  \n', '! two\na\nb\n'), [(' ', 'a'), (' ', 'b')])

    def test_a_moved_line_is_no_difference(self):
        result = diff('a\nb\nc\n', 'c\na\nb\n')
        self.assertEqual([row['op'] for row in result['rows']], [' ', ' ', ' '])
        self.assertEqual([row['line'] for row in result['rows']], ['c', 'a', 'b'])  # in the newer order
        self.assertEqual((result['added'], result['removed'], result['reordered']), (0, 0, True))

    def test_a_line_kept_once_of_twice_is_removed_once(self):
        self.assertEqual(self.ops('a\nx\nb\nx\n', 'x\na\nb\n'), [(' ', 'x'), (' ', 'a'), (' ', 'b'), ('-', 'x')])

    def test_the_same(self):
        result = diff('a\nb\n', 'a\nb\n')
        self.assertEqual((result['added'], result['removed'], result['reordered']), (0, 0, False))
