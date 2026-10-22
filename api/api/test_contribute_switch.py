"""Contribute a Switch (#59): BLab holds an address, gives the workflow, and onboards the Switch once it is Done."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from . import contribute_switch, fake_devices
from .contribute_switch import Advancer, Refused
from .models import Port, Switch, SwitchContribution
from .test_onboarding import Clock

BB = '10.0.0.100'
URL = '/api/contribute/switch/'


@override_settings(BLAB_DEVICES='fake', BLAB_MANAGEMENT_NETWORKS='10.0.0.0/29 via 10.0.0.1')
class ContributeSwitchTest(TestCase):
    def setUp(self):
        self.fake = fake_devices.lab_switches
        self.fake.reset()
        self.bb = fake_devices.backbone
        self.bb.reset()
        self.alice = User.objects.create_user('alice', email='alice@example.com', password='pw')
        self.bob = User.objects.create_user('bob', email='bob@example.com', password='pw')
        # .1 is the gateway, .2 a Switch BLab records, .3 something that answers but BLab doesn't record
        known = Switch.objects.create(mngt_IP='10.0.0.2', model='OS6860', console='TODO', part_number='pn',
                                      hardware_revision='A', serial_number='sn2')
        self.bb.cli(BB, 'interfaces 1/2/1 admin-state disable')
        self.bb.cli(BB, 'interfaces 1/2/2 admin-state disable')
        Port.objects.create(switch=known, port_switch='1/1/1', backbone=BB, port_backbone='1/2/1')
        self.fake.present.add('10.0.0.3')
        self.clock = Clock()
        self.client = APIClient()
        self.client.force_authenticate(self.alice)

    def start(self, user=None, **kwargs):
        return contribute_switch.start(user or self.alice, kwargs.get('model', 'OS6860E-24'),
                                       kwargs.get('has_emp', True), kwargs.get('now', self.clock()))

    def test_it_holds_the_lowest_address_nothing_records_or_answers_on(self):
        self.assertEqual(self.start().mngt_IP, '10.0.0.4')
        self.assertEqual(self.start().mngt_IP, '10.0.0.5', 'an address held is not given twice')

    def test_the_workflow_has_every_command_filled_in(self):
        steps = contribute_switch.workflow(self.start())
        self.assertEqual(steps[0]['commands'], ['modify boot parameters', 'boot empipaddr 10.0.0.4',
                                                'boot empmasklength 29', 'commit boot', 'commit system', 'exit'])
        self.assertIn('ip static-route 10.69.0.0/16 gateway 10.0.0.1 metric 1', steps[1]['commands'])
        self.assertEqual(steps[1]['commands'][-3:], ['aaa authentication ssh "local"',
                                                     'aaa authentication console "local"', 'write memory'])
        self.assertEqual(steps[2]['commands'], ['reload from working no rollback-timeout'])

    def test_an_address_nobody_finished_with_is_free_again_after_24_hours(self):
        self.start()
        self.assertEqual(self.start(now=self.clock() + timedelta(hours=25)).mngt_IP, '10.0.0.4')

    def test_a_user_may_have_three_under_way_and_the_network_may_run_out(self):
        for _ in range(3):
            self.start()
        with self.assertRaisesRegex(Refused, 'finish or cancel one'):
            self.start()
        with self.assertRaisesRegex(Refused, 'no free address left'):
            self.start(self.bob)

    def test_a_switch_without_an_emp_is_not_taken_yet(self):
        with self.assertRaisesRegex(Refused, 'without an EMP'):
            self.start(has_emp=False)

    def test_done_before_it_answers_says_what_to_check(self):
        contribution = self.start()
        with self.assertRaisesRegex(Refused, 'Nothing answers SSH at 10.0.0.4 yet'):
            contribute_switch.done(self.alice, contribution, self.clock())

    def test_done_on_an_address_held_too_long_is_refused(self):
        contribution = self.start()
        self.fake.present.add('10.0.0.4')
        with self.assertRaisesRegex(Refused, 'held for 24 hours only'):
            contribute_switch.done(self.alice, contribution, self.clock() + timedelta(hours=25))

    def test_once_done_the_switch_worker_onboards_it_into_service(self):
        contribution = self.start()
        self.fake.present.add('10.0.0.4')
        self.fake.factory_admin.add('10.0.0.4')
        self.fake.plug('10.0.0.4', '1/1/5', BB, '1/2/2')
        contribute_switch.done(self.alice, contribution, self.clock())
        self.assertEqual(SwitchContribution.objects.get().state, SwitchContribution.ONBOARDING)

        advancer = Advancer(self.clock)
        advancer.work()  # recorded, Golden init installed, reloading
        switch = Switch.objects.get(mngt_IP='10.0.0.4')
        self.assertTrue(switch.out_of_service)
        self.assertNotIn('10.0.0.4', self.fake.factory_admin)
        self.assertEqual(advancer.work(), [], 'not looked at again before LOOK_EVERY')
        self.clock.sleep(30)
        advancer.work()  # back, Ports read, Inspected
        contribution = SwitchContribution.objects.get()
        self.assertEqual((contribution.state, contribution.detail), (SwitchContribution.DONE,
                                                                     'In the lab and in service.'))
        switch.refresh_from_db()
        self.assertFalse(switch.out_of_service)
        self.assertEqual(list(Port.objects.filter(switch=switch).values_list('port_switch', 'port_backbone')),
                         [('1/1/5', '1/2/2')])
        self.assertEqual(contribute_switch.as_dict(contribution, self.clock())['switch'], switch.id)

    def test_first_worker_pass_advances_onboarding_with_a_live_clock(self):
        contribution = self.start()
        self.fake.present.add('10.0.0.4')
        self.fake.factory_admin.add('10.0.0.4')
        contribute_switch.done(self.alice, contribution, self.clock())
        now = [self.clock()]

        def ticking_clock():
            current = now[0]
            now[0] += timedelta(microseconds=1)
            return current

        outcomes = Advancer(ticking_clock).work()

        self.assertTrue(Switch.objects.filter(mngt_IP='10.0.0.4').exists())
        contribution.refresh_from_db()
        self.assertEqual(contribution.state, SwitchContribution.DONE)
        self.assertEqual(contribution.detail, 'In the lab and in service.')
        self.assertEqual(len(outcomes), 1)

    def test_through_the_api(self):
        response = self.client.post(URL, {'model': 'OS6860E-24', 'has_emp': True}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        contribution = response.data['contribution']
        self.assertEqual((contribution['mngt_IP'], contribution['state']), ('10.0.0.4', 'held'))
        self.assertEqual(len(contribution['steps']), 3)
        listed = self.client.get(URL).data
        self.assertEqual([c['id'] for c in listed['contributions']], [contribution['id']])
        self.assertEqual(listed['models'], ['OS6860'])

        self.assertEqual(self.client.post(f"{URL}{contribution['id']}/done/").status_code, 400)
        self.fake.present.add('10.0.0.4')
        response = self.client.post(f"{URL}{contribution['id']}/done/")
        self.assertEqual((response.status_code, response.data['contribution']['state']), (200, 'onboarding'))
        self.assertEqual(self.client.post(f"{URL}{contribution['id']}/cancel/").status_code, 400)

    def test_cancel_frees_the_address_and_only_its_user_sees_it(self):
        contribution = self.start()
        self.client.force_authenticate(self.bob)
        self.assertEqual(self.client.post(f"{URL}{contribution.id}/cancel/").status_code, 404)
        self.assertEqual(self.client.get(URL).data['contributions'], [])
        self.client.force_authenticate(self.alice)
        self.assertEqual(self.client.post(f"{URL}{contribution.id}/cancel/").status_code, 200)
        self.assertFalse(SwitchContribution.objects.exists())
        self.assertEqual(self.start().mngt_IP, '10.0.0.4')

    def onboarding(self, **plug):
        contribution = self.start()
        self.fake.present.add('10.0.0.4')
        contribute_switch.done(self.alice, contribution, self.clock())
        return Advancer(self.clock)

    def test_one_that_isnt_clean_says_why_and_is_inspected_again_every_10_minutes(self):
        self.fake.cable('10.0.0.4', '1/1/7')
        advancer = self.onboarding()
        advancer.work()
        self.clock.sleep(30)
        advancer.work()
        contribution = SwitchContribution.objects.get()
        self.assertEqual(contribution.state, SwitchContribution.ONBOARDING)
        self.assertIn('Unwanted cable: 1/1/7', contribution.detail)
        self.assertIn('every 10 minutes', contribution.detail)
        inspections = Switch.objects.get(mngt_IP='10.0.0.4').events.filter(kind='inspection').count()
        self.clock.sleep(60)
        advancer.work()
        self.assertEqual(Switch.objects.get(mngt_IP='10.0.0.4').events.filter(kind='inspection').count(), inspections)
        self.fake.unplug('10.0.0.4', '1/1/7')
        self.clock.sleep(600)
        advancer.work()
        self.assertEqual(SwitchContribution.objects.get().state, SwitchContribution.DONE)

    def test_one_that_cant_be_recorded_goes_back_to_the_user(self):
        advancer = self.onboarding()
        self.fake.refused.add('10.0.0.4')  # say admin's password was changed meanwhile
        advancer.work()
        contribution = SwitchContribution.objects.get()
        self.assertEqual(contribution.state, SwitchContribution.HELD)
        self.assertIn("BLab couldn't take it in", contribution.detail)
        self.assertFalse(Switch.objects.filter(mngt_IP='10.0.0.4').exists())

    def test_a_switch_an_admin_deleted_meanwhile_is_onboarded_afresh(self):
        self.fake.slow_to_reload.add('10.0.0.4')
        advancer = self.onboarding()
        advancer.work()
        Switch.objects.get(mngt_IP='10.0.0.4').delete()
        self.fake.slow_to_reload.discard('10.0.0.4')
        for _ in range(3):
            self.clock.sleep(30)
            advancer.work()
        self.assertEqual(SwitchContribution.objects.get().state, SwitchContribution.DONE)

    def test_a_model_other_than_the_one_given_is_recorded_as_the_switch_says(self):
        contribution = self.start(model='OS6900')
        self.fake.present.add('10.0.0.4')
        contribution = contribute_switch.done(self.alice, contribution, self.clock())
        self.assertIn('Found a OS6860-48 at 10.0.0.4 (you said OS6900', contribution.detail)

    def test_an_expired_one_says_so_and_a_chassis_gets_its_note(self):
        contribution = self.start(model='OS9907')
        self.assertIn("each CMM's console", contribute_switch.workflow(contribution)[0]['note'])
        self.assertTrue(contribute_switch.as_dict(contribution, self.clock() + timedelta(hours=25))['expired'])
        self.assertFalse(contribute_switch.as_dict(contribution, self.clock())['expired'])
