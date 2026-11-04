"""#115: Switches onboarded by production's older code get their Contribution's Management port."""
from importlib import import_module

from django.apps import apps
from django.contrib.auth.models import User
from django.test import TestCase

from .models import Onboarding, Switch, SwitchContribution

migration = import_module('api.migrations.0029_management_port_from_contribution')


class ManagementPortFromContributionTests(TestCase):
    def test_a_switch_without_its_management_port_gets_its_contributions(self):
        user = User.objects.create(username='u')
        SwitchContribution.objects.create(user=user, model='OS6560-P48X4', mngt_IP='10.69.144.174', has_emp=False,
                                          management_port='1/1/48', state=SwitchContribution.ONBOARDING)
        SwitchContribution.objects.create(user=user, model='OS6900-X48C6', mngt_IP='10.69.144.175', has_emp=True)
        SwitchContribution.objects.create(user=user, model='OS6450-48', mngt_IP='10.69.144.176', has_emp=False,
                                          management_port='1/48')
        lost = Switch.objects.create(mngt_IP='10.69.144.174', model='OS6560-P48X4')
        with_emp = Switch.objects.create(mngt_IP='10.69.144.175', model='OS6900-X48C6')
        other_model = Switch.objects.create(mngt_IP='10.69.144.176', model='OS6560-P48Z16')  # address reassigned
        kept = Switch.objects.create(mngt_IP='10.69.144.177', model='OS6560-P48Z16', management_port='1/1/47')
        Onboarding.objects.create(mngt_IP='10.69.144.174', step=Onboarding.CABLED, detail='not clean')
        Onboarding.objects.create(mngt_IP='10.69.144.177', step=Onboarding.CABLED, detail='not clean')

        migration.management_port_from_contribution(apps, None)

        self.assertEqual([Switch.objects.get(pk=s.pk).management_port for s in (lost, with_emp, other_model, kept)],
                         ['1/1/48', '', '', '1/1/47'])
        self.assertEqual(Onboarding.objects.get(mngt_IP='10.69.144.174').step, Onboarding.RECORDED)  # reloaded again
        self.assertEqual(Onboarding.objects.get(mngt_IP='10.69.144.177').step, Onboarding.CABLED)
