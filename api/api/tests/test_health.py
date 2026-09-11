from django.test import TestCase
from django.contrib.auth.models import User

from api.models import HealthFinding, Switch
from api.services.health import inspect_switch


class HealthInspectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='tester', password='test-password')
        self.switch = Switch.objects.create(
            mngt_IP='192.0.2.10',
            model='ALE-test',
            console='ssh',
            part_number='test-part',
            hardware_revision='test-revision',
            serial_number='test-serial',
            platform='UNKNOWN',
        )

    def test_unknown_platform_is_not_marked_healthy(self):
        check = inspect_switch(self.switch, requested_by=self.user)

        self.assertEqual(check.status, 'ERROR')
        self.switch.refresh_from_db()
        self.assertEqual(self.switch.health_state, 'UNKNOWN')
        self.assertTrue(
            HealthFinding.objects.filter(
                switch=self.switch,
                code='COMMANDS_NOT_CONFIGURED',
            ).exists()
        )
